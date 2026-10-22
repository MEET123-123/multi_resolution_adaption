"""Template/model composition planning with an independently validated solver."""
import argparse
import json
from pathlib import Path
from multires_app.reconstruction.workflow import prepare,run,load
from multires_app.reconstruction.templates import catalog


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('templates')
    p=sub.add_parser('prepare');p.add_argument('project',type=Path);p.add_argument('--target',required=True);p.add_argument('--state',default='state-000');p.add_argument('--out',type=Path,required=True)
    p=sub.add_parser('run');p.add_argument('request',type=Path);p.add_argument('--config',type=Path);p.add_argument('--advice',type=Path);p.add_argument('--template');p.add_argument('--rounds',type=int,default=2);p.add_argument('--no-package',action='store_true')
    args=parser.parse_args()
    if args.command=='templates':result=catalog()
    elif args.command=='prepare':result=prepare(args.project,args.target,args.state,args.out)
    else:
        result=run(args.request,config=load(args.config) if args.config else None,advice=load(args.advice) if args.advice else None,
                   template=args.template,rounds=args.rounds,compile_package=not args.no_package,progress=lambda s:print(s,flush=True))
        result={k:result[k] for k in ('status','provider','target','state','selected','package','readbackPassed','directory') if k in result}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':main()
