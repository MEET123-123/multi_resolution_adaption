"""Render phone, prepare a generation request, consume/reference or call a plugin."""
import argparse
import importlib
import json
from pathlib import Path


def main():
    from multires_app.preview.reference_workflow import prepare,complete,generate,index_report,batch
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('project',type=Path);p.add_argument('--target',required=True);p.add_argument('--state',required=True);p.add_argument('--out',type=Path,required=True)
    p=sub.add_parser('complete');p.add_argument('request',type=Path);p.add_argument('--image',type=Path,required=True);p.add_argument('--provider',default='external-reference');p.add_argument('--minimum-confidence',type=float,default=.78)
    p.add_argument('--generation-metadata',type=Path,help='Actual provider/prompt metadata, saved with this attempt')
    p.add_argument('--replay-job',type=Path,help='Re-solve from previous automatic evidence without new CV inference')
    p=sub.add_parser('generate');p.add_argument('request',type=Path);p.add_argument('--generator',required=True,help='Trusted local module:callable')
    p=sub.add_parser('report');p.add_argument('--root',type=Path,required=True)
    p=sub.add_parser('batch');p.add_argument('plan',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--generator',help='Trusted local module:callable; optional with referenceImage files')
    p.add_argument('--retry',action='store_true');p.add_argument('--limit',type=int)
    args=parser.parse_args()
    if args.command=='batch':
        provider=None
        if args.generator:
            module,name=args.generator.rsplit(':',1);provider=getattr(importlib.import_module(module),name)
        if args.limit is not None and args.limit<1:parser.error('--limit must be positive')
        result=batch(args.plan,args.out,generator=provider,retry=args.retry,limit=args.limit)
    elif args.command=='report':result=index_report(args.root)
    elif args.command=='prepare':result=prepare(args.project,args.target,args.state,args.out)
    elif args.command=='complete':result=complete(args.request,args.image,provider=args.provider,minimum_confidence=args.minimum_confidence,
        generation_metadata=json.loads(args.generation_metadata.read_text(encoding='utf8')) if args.generation_metadata else None,replay_job=args.replay_job)
    else:
        module,name=args.generator.rsplit(':',1);result=generate(args.request,getattr(importlib.import_module(module),name))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
