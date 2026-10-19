"""Refresh rendering from cached human traces, then re-export and verify candidates."""
import argparse
import json
import hashlib
from urllib.parse import quote
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from multires_app.engine.scene_engine import load_model,extract_assets,VISUAL
from multires_app.interaction.interaction_semantics import replay_human_state,analyze_interactions
from multires_app.pipeline.source_comparison import build_source_device,packed_snapshot
from multires_app.pipeline.scene_pipeline import load_scene_algorithm,export_script,export_package
from multires_app.pipeline.scene_clusters import schedule_device
from multires_app.preview.scene_previewer import build_scene_preview
from multires_app.tools.batch_convert import validate,validate_live_project,write_index,digest


def refresh_project(path):
    p=json.loads(path.read_text(encoding='utf-8'));out=path.parent
    model=load_model(Path(p['source']),p['model']['file'])
    if p['model'].get('inputs'):model['inputs']=p['model']['inputs']
    p['model']=model;p['assets']=extract_assets(Path(p['source']),model,out);p['assetOrder']=list(p['assets'])
    p['graph']['semantics']=analyze_interactions(model);p['formatVersion']=4
    media_keys=lambda k:k.endswith((':asset',':srcExp',':native-glyph-size',':render',':export-size')) or ':glyph:' in k
    diagnostics={k:v for k,v in p['diagnostics'].items() if not media_keys(k)}
    algorithm=load_scene_algorithm(p.get('algorithmSpec','reflow'));states=p['graph']['states']
    unresolved={};clock_nodes=set()
    for d in p['devices']:
        cache={};layouts=[]
        for state in states:
            rt=replay_human_state(model,d['viewport']['width'],d['viewport']['height'],state,p['assets'],verify_hit=False,cache=cache)
            snapshot=packed_snapshot(p,rt,state['id']);layouts.append(snapshot);diagnostics.update(rt.issues)
            for row in snapshot['rows']:
                uid=p['nodeOrder'][row[0]];node=model['nodes'][uid]
                if node['tag']=='Time' and row[6]>=0:clock_nodes.add(uid)
                if row[6]<0 and node['tag'] in ('Image','Video','Time') and (row[9] or node['attributes'].get('srcExp')):
                    asset=row[9];kind='external-resource' if 'sdcard/' in asset or asset.startswith('/data/') else 'unresolved-script-reference'
                    if Path(asset).name in ('.png','.jpg','.jpeg'):kind='unselected-dynamic-image'
                    unresolved[(uid,asset)]={'node':uid,'path':asset,'kind':kind,'sourceExpression':node['attributes'].get('srcExp',node['attributes'].get('src',''))}
        d['layouts']=layouts;schedule_device(p,d,algorithm)
    p['sourceDevice']=build_source_device(p);model.pop('assetNames',None);p['diagnostics']=diagnostics
    p['limitations']=[s for s in p['limitations'] if '视频' not in s]+['视频使用浏览器本地解码；原生播放控制、遮罩、完整动画时序仍需原生引擎验收。','系统图标、用户相册等外部资源不在主题包中，预览会列出缺失引用。']
    for key in list(p):
        if key.startswith('_'):p.pop(key)
    temporary=path.with_suffix('.json.refreshing');temporary.write_text(json.dumps(p,ensure_ascii=False,separators=(',',':')),encoding='utf-8');temporary.replace(path)
    build_scene_preview(p,out/'preview.html')
    report=json.loads((out/'report.json').read_text(encoding='utf-8'))
    report['exports']=[export_script(p,d['profile']['id'],out/'scripts'/(d['profile']['id']+'.xml')) for d in p['devices']]
    report['layoutClustering']={d['profile']['id']:d['layoutClusters']['summary'] for d in p['devices']};report['diagnosticCount']=len(diagnostics)
    report['sourceComparison']={'states':len(states),'viewport':p['sourceDevice']['viewport']}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    audit={'project':out.parent.name,'assetCount':len(p['assets']),'embeddedVideos':sum(a.get('kind')=='video' for a in p['assets'].values()),
           'bitmapClockNodes':len(clock_nodes),'unresolvedReferences':list(unresolved.values()),'sourceComparisonStates':len(states)}
    (out/'media-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in audit.items() if k!='unresolvedReferences'},ensure_ascii=True),flush=True)
    return audit


def repack_record(root,record):
    out=root/record['slug']/'scenes';path=out/'project.json';packages=[]
    for target in ('tablet-mock-16-10-landscape','dual-fold-mock-inner-portrait'):
        package=out/'packages'/(target+'.hwt');export_package(path,target,package);packages.append(package)
    result=validate(out,packages);extras=[]
    for variant in record.get('variants',[]):extras.append(validate(root/variant['slug']/'scenes',packages))
    if extras:
        result['variantValidations']=extras
        for key in ('checkedElementSnapshots','skippedElementSnapshots','zipEntriesChecked','failures'):result[key]+=sum(e[key] for e in extras)
        (out/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    record['validation']=result;record['liveValidation']=validate_live_project(out)
    record['liveValidation']['variants']=[validate_live_project(root/v['slug']/'scenes') for v in record.get('variants',[])]
    report=json.loads((out/'report.json').read_text(encoding='utf-8'));record['layoutClustering']=report['layoutClustering'];record['diagnosticCount']=report['diagnosticCount']
    for field in ('states','transitions','devices','coverage'):
        if field in report:record[field]=report[field]
    for variant in record.get('variants',[]):
        variant_report=json.loads((root/variant['slug']/'scenes/report.json').read_text(encoding='utf-8'))
        for field in ('states','transitions','devices','coverage'):
            if field in variant_report:variant[field]=variant_report[field]
    record['sourceUnchanged']=digest(Path(record['source']))==record['sha256']
    record['status']='validated' if not result['failures'] and not record['liveValidation']['failures'] and not any(e['failures'] for e in record['liveValidation']['variants']) else 'needs-review'
    print(json.dumps({'verified':record['name'],'status':record['status'],'geometryIssues':result['failures']},ensure_ascii=True),flush=True)
    return record


def refresh_ui(path):
    p=json.loads(path.read_text(encoding='utf-8'))
    for name,meta in p['assets'].items():
        h=hashlib.sha256()
        with (path.parent/'assets'/name).open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
        meta['url']='assets/'+quote(name,safe='/')+'?v='+h.hexdigest()[:12]
    temporary=path.with_suffix('.json.refreshing');temporary.write_text(json.dumps(p,ensure_ascii=False,separators=(',',':')),encoding='utf-8');temporary.replace(path)
    build_scene_preview(p,path.parent/'preview.html');print(json.dumps({'ui':path.parent.parent.name}),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--only',nargs='*');parser.add_argument('--workers',type=int,default=3);parser.add_argument('--preview-only',action='store_true');parser.add_argument('--ui-only',action='store_true')
    args=parser.parse_args();root=Path('theme/adaptation');records=json.loads((root/'batch-summary.json').read_text(encoding='utf-8'))
    chosen=[r for r in records if not args.only or any(n in r['name'] or n in r['slug'] for n in args.only)]
    paths=[root/r['slug']/'scenes/project.json' for r in chosen]+[root/v['slug']/'scenes/project.json' for r in chosen for v in r.get('variants',[])]
    audits=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(refresh_ui if args.ui_only else refresh_project,path) for path in paths]):audits.append(future.result())
    if not args.preview_only and not args.ui_only:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(repack_record,root,r) for r in chosen]):
                updated=future.result();records=[updated if r['slug']==updated['slug'] else r for r in records]
                write_index(root,records)
    if not args.ui_only:(root/'media-iteration-report.json').write_text(json.dumps(audits,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
