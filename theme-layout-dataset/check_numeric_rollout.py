"""Replay numeric proposals through the actual anchor constraint solver.

Reuses verified v1 source caches in memory. Does not overwrite projects or HWT.
This is renderer/geometry validation, not native-device acceptance.
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'src'))
from local_finetune import read,rows,save,sha


def run(args):
    from multires_app.learning.algorithm import LearnedAnchor
    from multires_app.pipeline.scene_clusters import schedule_device
    from multires_app.learning.render import render
    from multires_app.learning.dataset import actual_boxes
    from multires_app.learning.contract import normalized
    from multires_app.evaluation.checks import GeometryEvaluator,ContractEvaluator
    from multires_app.evaluation.core import Context
    bundle=Path(args.bundle).resolve();out=Path(args.out).resolve();workers=Path(args.workers).resolve()
    if out.exists():raise ValueError('use a new rollout output folder')
    predictions=read(args.predictions);samples=[s for s in rows(bundle/'samples.jsonl')
        if (s['split']==args.split if args.split else s['requestKey'] in predictions)]
    if not samples:raise ValueError('no rollout samples')
    grouped=defaultdict(list)
    for sample in samples:grouped[(sample['sourceSha256'],sample['manifest'])].append(sample)
    metadata={}
    for path in workers.glob('*/worker.json'):
        meta=read(path);metadata[(meta['sourceSha256'],meta['manifest'])]=meta
    cases=[];events=[];input_hashes={};checks_summary=Counter()
    for key,wanted in grouped.items():
        meta=metadata[key];project_path=Path(meta['project']);before=sha(project_path);input_hashes[str(project_path)]=before
        project=read(project_path);sampled={x['device']:set(x['states']) for x in meta['sampling']};selected=set().union(*sampled.values())
        project['sourceDevice']['layouts']=[l for l in project['sourceDevice']['layouts'] if l['state'] in selected]
        project['devices']=[d for d in project['devices'] if d['profile']['id'] in sampled]
        by_request={s['requestKey']:s for s in wanted}
        by_state={(s['device'],s['state']):s for s in wanted}
        for device in project['devices']:
            device['layouts']=[l for l in device['layouts'] if l['state'] in sampled[device['profile']['id']]]
        for device in list(project['devices']):
            project['devices']=[device];algorithm=LearnedAnchor(predictions)
            schedule_device(project,device,algorithm);ctx=Context(project,project_path)
            local=[]
            for evaluator in (GeometryEvaluator(),ContractEvaluator()):
                for ob in evaluator.evaluate(ctx):
                    local.append({k:getattr(ob,k) for k in ('rule','status','severity','members','residual','state')})
            for event in algorithm.events:
                if event['key'] in by_request:events.append({'requestKey':event['key'],'status':event['status']})
            for layout in device['layouts']:
                proposal=layout.get('adaptationPlan',{}).get('layoutProposal',{});sample=by_state.get((device['profile']['id'],layout['state']))
                if sample is None:continue
                relevant=[ob for ob in local if ob['state']==layout['state']]
                for ob in relevant:checks_summary[ob['status']]+=1
                report=render(project,device,layout,project_path.parent/'assets',out/'images'/(sample['id']+'.png'))
                bounds=actual_boxes(project,layout,layout['adaptationPlan']['groups']);region=sample['request']['safeRegion']
                original_map=read(bundle/sample['evidence'])['groupMap']
                answer={gid:normalized(bounds[internal],region) for gid,internal in original_map.items()}
                diffs=[abs(v-sample['label']['answer']['groups'][gid][i]) for gid,box in answer.items() for i,v in enumerate(box)]
                cases.append({'id':sample['id'],'state':sample['state'],'device':sample['device'],'render':report,'checks':relevant,
                              'expectedRequestKey':sample['requestKey'],'actualRequestKey':proposal.get('key'),
                              'requestMatched':proposal.get('key')==sample['requestKey'],'proposalStatus':proposal.get('status'),
                              'mae1000AfterSolver':sum(diffs)/len(diffs),'answer':answer})
                print(json.dumps({'rollout':sample['id'],'render':report['status'],'badChecks':sum(c['status'] in ('fail','unknown','review') for c in relevant)}),flush=True)
        if sha(project_path)!=before:raise RuntimeError('source cache changed')
    if len(cases)!=len(samples):raise RuntimeError(f'request-key coverage mismatch: {len(cases)}/{len(samples)}')
    result={'samples':len(samples),'matched':len(cases),'events':dict(Counter(c['proposalStatus'] for c in cases)),
            'exactRequestMatches':sum(c['requestMatched'] for c in cases),
            'checks':dict(checks_summary),'renderStatuses':dict(Counter(c['render']['status'] for c in cases)),
            'meanTeacherMAE1000AfterSolver':sum(c['mae1000AfterSolver'] for c in cases)/len(cases),
            'inputProjectSha256':input_hashes,'nativeValidation':'not_measured','cases':cases}
    save(out/'report.json',result);print(json.dumps({k:v for k,v in result.items() if k not in ('cases','inputProjectSha256')},ensure_ascii=False,indent=2))


def main():
    p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--workers',required=True);p.add_argument('--predictions',required=True);p.add_argument('--out',required=True)
    p.add_argument('--split',choices=('train','validation','test'))
    run(p.parse_args())


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    main()
