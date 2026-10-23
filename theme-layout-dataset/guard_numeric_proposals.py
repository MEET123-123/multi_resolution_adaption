"""Gate a proposal map on executable source constraints, never teacher MAE.

Reject failing scene proposals and replay, since parent layout changes can alter
later requests. Unknown request keys use LearnedAnchor's existing fallback.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from local_finetune import read,save,sha

ROOT=Path(__file__).resolve().parent


def rejected(report):
    return [case for case in report['cases'] if case['render']['status']=='incomplete' or
            not case['checks'] or any(check['status'] in ('fail','unknown','review') for check in case['checks'])]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--bundle',required=True);parser.add_argument('--workers',required=True)
    parser.add_argument('--predictions',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--split',choices=('train','validation','test'),default='test');args=parser.parse_args()
    out=Path(args.out).resolve()
    if out.exists():raise ValueError('use a new gate output folder')
    out.mkdir(parents=True);predictions=read(args.predictions);history=[];reasons={}
    for attempt in range(1,5):
        current=out/f'proposals-pass-{attempt}.json';save(current,predictions);rollout=out/f'pass-{attempt}'
        command=[sys.executable,str(ROOT/'check_numeric_rollout.py'),'--bundle',args.bundle,'--workers',args.workers,
                 '--predictions',str(current),'--out',str(rollout),'--split',args.split]
        with (out/f'pass-{attempt}.log').open('w',encoding='utf8') as log:
            process=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT.parent)
        if process.returncode:raise RuntimeError(f'rollout failed, see {out}/pass-{attempt}.log')
        report=read(rollout/'report.json');bad=rejected(report)
        history.append({'attempt':attempt,'proposalCount':len(predictions),'badScenes':len(bad),'events':report['events']})
        print(json.dumps(history[-1]),flush=True)
        if not bad:
            save(out/'accepted-predictions.json',predictions)
            save(out/'report.json',{'status':'pass','split':args.split,'inputPredictionSha256':sha(args.predictions),
                 'gateUsesTeacherCoordinates':False,'history':history,'rejectedRequests':reasons,
                 'finalRollout':str(rollout),'checks':report['checks'],'events':report['events'],
                 'samples':report['samples'],'renders':report['renderStatuses'],'nativeValidation':'not_measured'})
            return
        for case in bad:
            # Never transfer an answer between changed keys/group inventories.
            for key in (case['expectedRequestKey'],case['actualRequestKey']):
                if key in predictions:
                    reasons[key]={'id':case['id'],'failedChecks':[c for c in case['checks'] if c['status'] in ('fail','unknown','review')]}
                    del predictions[key]
        if attempt==3:predictions={}  # Last attempt verifies pure solver fallback.
    save(out/'report.json',{'status':'fail','history':history,'rejectedRequests':reasons})
    raise RuntimeError('baseline execution still fails; do not publish a proposal map')


if __name__=='__main__':main()
