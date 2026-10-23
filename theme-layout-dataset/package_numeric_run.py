"""Package selected weights, replay code and audit reports; omit base/raw themes."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from local_finetune import read,save,sha

ROOT=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);args=parser.parse_args();run=Path(args.run).resolve()
    archive=run/'numeric-layout-v2.zip'
    if archive.exists():raise ValueError('preserve the existing package')
    if read(run/'progress.json').get('status')!='complete':raise ValueError('training incomplete')
    if read(run/'guarded-test/report.json')['status']!='pass':raise ValueError('test execution gate failed')
    if read(run/'guarded-validation/report.json')['status']!='pass':raise ValueError('validation execution gate failed')
    if not read(run/'source-integrity.json')['unchanged']:raise ValueError('source data changed')
    config=read(run/'configuration.json')
    if sha(run/'training-script.py')!=config['scriptSha256']:raise ValueError('executed training source snapshot mismatch')
    if sha(ROOT.parent/'src/multires_app/learning/geometry_head.py')!=config['decoderSha256']:raise ValueError('decoder changed since training')
    files={}
    def include(path,name):
        if name in files:raise ValueError('duplicate archive path')
        files[name]=path
    for path in (run/'adapter').rglob('*'):
        if path.is_file():include(path,path.relative_to(run).as_posix())
    for name in ('geometry-head.pt','selection.json','configuration.json','comparison.json','training-result.json',
                 'data-audit.json','group-audit.jsonl','geometry-validation-history.json','multimodal-validation-history.json',
                 'reload-verification.json','source-integrity.json','image-ablation.json','verification.json',
                 'test-copy_source.json','test-contain_anchor.json','test-geometry_head.json',
                 'test-predictions.json','validation-predictions.json','replay-source-inputs.jsonl',
                 'REPORT.md','ADAPTER_README.md','training-script.py',
                 'guarded-test/report.json','guarded-test/accepted-predictions.json',
                 'guarded-validation/report.json','guarded-validation/accepted-predictions.json'):
        include(run/name,name)
    selection=read(run/'selection.json')
    selected_name='test-selected_multimodal.json' if selection['selected']=='multimodal' else 'test-selected_geometry.json'
    include(run/selected_name,selected_name)
    include(Path(read(run/'guarded-test/report.json')['finalRollout'])/'report.json','guarded-test/final-rollout-report.json')
    for path in (ROOT.parent/'src').rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':include(path,'code/src/'+path.relative_to(ROOT.parent/'src').as_posix())
    for name in ('local_finetune.py','optimize_layout.py','predict_numeric_layout.py','check_numeric_rollout.py','guard_numeric_proposals.py','package_numeric_run.py'):
        include(ROOT/name,'code/theme-layout-dataset/'+name)
    include(ROOT/'08_第二轮优化与复现.md','code/theme-layout-dataset/08_第二轮优化与复现.md')
    for name in ('pyproject.toml',):include(ROOT.parent/name,'code/'+name)
    hashes={name:sha(path) for name,path in files.items()}
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as package:
        for name,path in sorted(files.items()):package.write(path,name)
        package.writestr('file-sha256.json',json.dumps(hashes,ensure_ascii=False,indent=2)+'\n')
    with zipfile.ZipFile(archive) as package:
        if package.testzip():raise ValueError('ZIP CRC failure')
        for name,expected in hashes.items():
            if hashlib.sha256(package.read(name)).hexdigest()!=expected:raise ValueError('archive checksum mismatch: '+name)
    save(run/'archive.json',{'archive':str(archive),'sha256':sha(archive),'bytes':archive.stat().st_size,
         'files':len(files)+1,'verified':True,'baseWeightsIncluded':False,'sourceThemesIncluded':False,
         'dataRequirement':'unchanged auto-pairs-v1 server-bundle; source caches required only for execution replay',
         'contract':read(run/'selection.json')['contract']})
    print(json.dumps(read(run/'archive.json'),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
