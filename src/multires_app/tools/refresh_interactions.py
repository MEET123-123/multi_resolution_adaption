"""Rebuild interaction graphs and layouts from verified local theme caches."""
import argparse
import copy
import hashlib
import json
import shutil
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from multires_app.engine.scene_engine import load_model
from multires_app.interaction.animation_semantics import scene_animation_names
from multires_app.interaction.interaction_semantics import initialize
from multires_app.media.media_layout import reference
from multires_app.pipeline.scene_pipeline import create_model_project, export_script


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def audit(path):
    p=json.loads(path.read_text(encoding='utf8'));model=p['model'];model['assetNames']=list(p['assets'])
    vp=p['sourceDevice']['viewport'];rt=initialize(model,vp['width'],vp['height'])
    ref=reference(rt,p['assets'],p['nodeOrder'],p['assetOrder']) or {}
    old=p['sourceDevice']['layouts'][0].get('mediaLayout',{})
    names=scene_animation_names(model)
    return {'project':path.parent.parent.name,'source':p['source'],'sceneAnimationVariables':sorted(names),
            'stoppedInitialAnimationValues':{n:rt.state[n] for n in names if n in rt.state},
            'inactiveInitialNodes':len(ref.get('inactiveSceneNodeIndices',[])),
            'initialReferenceChanged':old.get('rows',[])!=ref.get('rows',[]) or
                old.get('inactiveSceneNodeIndices',[])!=ref.get('inactiveSceneNodeIndices',[]),
            'classificationChanged':set(old.get('pageAnimationVariables',[]))!=names,
            'oldStates':len(p['graph']['states'])}


def rebuild(path,expected_hash=None):
    """Stage a full rebuild, keep the previous scenes directory recoverable."""
    path=path.resolve();folder=path.parent;workspace=folder.parent.resolve()
    if folder.name!='scenes' or not folder.is_relative_to(workspace):raise ValueError('expected a scenes/project.json cache')
    p=json.loads(path.read_text(encoding='utf8'));source=Path(p['source'])
    before=digest(source)
    if expected_hash and before!=expected_hash:raise ValueError('source changed; use a fresh conversion instead of a cached rebuild')
    model=load_model(source,p['model']['file'])
    if model['nodes']!=p['model']['nodes']:raise ValueError('cached script differs from the source; fresh conversion required')
    if p['model'].get('inputs'):model['inputs']=copy.deepcopy(p['model']['inputs'])
    profiles=[]
    for d in p['devices']:
        if d.get('manualComponentAnchors') or any(c.get('layout') for c in p['components']):
            raise ValueError('manual layout overrides need explicit configuration before a full rebuild')
        profile=copy.deepcopy(d['profile']);orientation=profile.pop('orientation')
        profile['id']=profile['id'].removesuffix('-'+orientation)
        profile['baseOrientation']=orientation;profile['orientations']=[orientation];profiles.append(profile)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    stage=workspace/('scenes-rebuilding-interactions-'+stamp)
    backup=workspace/('scenes-before-interactions-'+stamp)
    for target in (stage,backup):
        if not target.resolve().is_relative_to(workspace):raise ValueError('rebuild path escaped its theme directory')
    # Cache use never relaxes intake limits for a new archive. The source
    # manifest and every cached resource are checked before reuse.
    for meta in p['assets'].values():
        url=urlsplit(meta['url']);asset=(folder/unquote(url.path)).resolve()
        if not asset.is_relative_to((folder/'assets').resolve()) or not asset.is_file():raise ValueError('invalid cached asset path')
        expected=parse_qs(url.query).get('v',[None])[0]
        if expected and not digest(asset).startswith(expected):raise ValueError('cached asset content changed: '+str(asset))
    shutil.copytree(folder/'assets',stage/'assets')
    coverage=p['graph'].get('coverage',{})
    report=create_model_project(model,copy.deepcopy(p['assets']),p['sourceProfile'],{'profiles':profiles},stage,
        p.get('algorithmSpec','anchor'),coverage.get('maxStates',96),coverage.get('maxDepth',8),
        archive_validation=p.get('archiveValidation'))
    built=json.loads((stage/'project.json').read_text(encoding='utf8'))
    if p.get('variantProjects'):built['variantProjects']=p['variantProjects']
    (stage/'project.json').write_text(json.dumps(built,ensure_ascii=False,separators=(',',':')),encoding='utf8')
    if digest(source)!=before:raise ValueError('source changed during rebuild')
    folder.rename(backup)
    try:stage.rename(folder)
    except Exception:
        backup.rename(folder);raise
    # Generated resource manifests store absolute paths, so export once more
    # at the final location before any package is assembled.
    try:
        report['exports']=[export_script(built,d['profile']['id'],folder/'scripts'/(d['profile']['id']+'.xml')) for d in built['devices']]
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    except Exception:
        folder.rename(stage);backup.rename(folder);raise
    result={'project':workspace.name,'states':len(built['graph']['states']),'devices':len(built['devices']),
            'sourceUnchanged':digest(source)==before,'backup':str(backup),
            'sceneAnimationVariables':built['graph']['semantics'].get('sceneAnimationVariables',[])}
    print(json.dumps(result,ensure_ascii=True),flush=True)
    return result


def process(path,hashes,audit_only):
    try:
        result=audit(path)
        if not audit_only:result['rebuild']=rebuild(path,hashes.get(result['source']))
        print(json.dumps(result,ensure_ascii=True),flush=True);return result
    except Exception as exc:
        error={'project':path.parent.parent.name,'error':str(exc)}
        print(json.dumps(error,ensure_ascii=True),flush=True);return error


def main():
    parser=argparse.ArgumentParser(description='按通用交互动画语义重建已有主题缓存；保留旧 scenes 目录。')
    parser.add_argument('--root',type=Path,default=Path('theme/adaptation'))
    parser.add_argument('--only',nargs='+');parser.add_argument('--audit-only',action='store_true')
    parser.add_argument('--workers',type=int,choices=[1,2,3],default=1)
    args=parser.parse_args();paths=sorted(args.root.glob('*/scenes/project.json'))
    if args.only:paths=[p for p in paths if p.parent.parent.name in args.only]
    records=json.loads((args.root/'batch-summary.json').read_text(encoding='utf8')) if (args.root/'batch-summary.json').exists() else []
    hashes={r['source']:r['sha256'] for r in records}
    results=[]
    if args.workers==1:
        results=[process(path,hashes,args.audit_only) for path in paths]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(process,path,hashes,args.audit_only) for path in paths]):results.append(future.result())
    report=Path('artifacts/reports/interaction-animation-'+('audit' if args.audit_only else 'rebuild')+'.json')
    report.parent.mkdir(parents=True,exist_ok=True);report.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
    if any('error' in r for r in results):raise SystemExit(1)


if __name__=='__main__':main()
