"""Recheck changed projects and keep the full batch media evidence."""
import argparse
import hashlib
import json
from pathlib import Path
from tests.validate_media_real import check


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--only',nargs='+',required=True);args=parser.parse_args()
    root=Path('theme/adaptation');path=root/'media-validation.json';report=json.loads(path.read_text(encoding='utf8'))
    results={r['project']:r for r in report['projects']}
    for name in args.only:
        row=check(root/name/'scenes/project.json');results[name]=row
        print(json.dumps({k:row[k] for k in ('project','checkedElementSnapshots','failures')}),flush=True)
    rows=sorted(results.values(),key=lambda r:r['project']);report['projects']=rows
    for field,key in [('devicePlans','devices'),('sourceStates','sourceStates'),('scripts','scripts'),('checkedElementSnapshots','checkedElementSnapshots'),
                      ('invisibleExcludedSnapshots','invisibleExcludedSnapshots'),('skippedSnapshots','skippedSnapshots'),('failures','failures')]:report[field]=sum(r[key] for r in rows)
    records=json.loads((root/'batch-summary.json').read_text(encoding='utf8'))
    report['batchGeometryFailures']=sum(r.get('validation',{}).get('failures',0) for r in records)
    report['batchLiveFailures']=sum(r.get('liveValidation',{}).get('failures',0)+sum(v.get('failures',0) for v in r.get('liveValidation',{}).get('variants',[])) for r in records)
    report['sourceHashesUnchanged']=all(hashlib.sha256(Path(r['source']).read_bytes()).hexdigest()==r['sha256'] for r in records)
    report['candidatePackages']=sum(len(list((root/r['slug']/'scenes/packages').glob('*.hwt'))) for r in records)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    if report['failures'] or not report['sourceHashesUnchanged']:raise SystemExit(1)


if __name__=='__main__':main()
