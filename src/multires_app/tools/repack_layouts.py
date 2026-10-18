"""Package and validate cached solved layouts without running the solver."""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from multires_app.tools.refresh_previews import repack_record
from multires_app.tools.batch_convert import write_index


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--only',nargs='+');parser.add_argument('--exclude',nargs='+',default=[]);parser.add_argument('--workers',type=int,choices=[1,2,3],default=2);args=parser.parse_args()
    root=Path('theme/adaptation');records=json.loads((root/'batch-summary.json').read_text(encoding='utf8'))
    chosen=[r for r in records if (not args.only or r['slug'] in args.only) and r['slug'] not in args.exclude]
    failures=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending={pool.submit(repack_record,root,r):r for r in chosen}
        for future in as_completed(pending):
            try:
                updated=future.result();records=[updated if r['slug']==updated['slug'] else r for r in records];write_index(root,records)
                if updated['status']!='validated' or not updated['sourceUnchanged']:failures.append({'project':updated['slug'],'status':updated['status']})
            except Exception as exc:failures.append({'project':pending[future]['slug'],'error':str(exc)})
    (root/'package-run-errors.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding='utf8')
    if failures:print(json.dumps(failures,ensure_ascii=False),flush=True);raise SystemExit(1)


if __name__=='__main__':main()
