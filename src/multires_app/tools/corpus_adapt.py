"""Theme-independent corpus adaptation with resumable native-script audits.

The layout algorithm is the existing scene plugin. Reference-image jobs are
separate, explicitly labelled evidence, never synthesized from this baseline.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import html
import json
import os
import shutil
import time
import uuid
import re
import zipfile
from collections import Counter
from pathlib import Path

from multires_app.preview.reference_workflow import digest,save
from multires_app.pipeline.representative_states import select_states

DEFAULT_TARGETS=('tablet-mock-16-10-portrait','tablet-mock-16-10-landscape')


def script_signature(model):
    return hashlib.sha256(json.dumps({'root':model['root'],'nodes':{
        uid:{k:n[k] for k in ('tag','attributes','parent','children')} for uid,n in model['nodes'].items()
    }},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def cache_index(root):
    index={}
    for path in sorted(Path(root).glob('*/scenes/project.json')):
        project=json.loads(path.read_text(encoding='utf8'))
        index.setdefault(str(Path(project['source']).resolve()).casefold(),[]).append({
            'project':str(path.resolve()),'manifest':project['model']['file'],'sha256':digest(path),
            'targets':[d['profile']['id'] for d in project['devices']]})
        del project;gc.collect()
    return index


def ensure_cache(source,caches,targets,out,max_states=96,max_depth=8):
    """Extract newly uploaded packages/variants, reuse matching existing caches."""
    from multires_app.paths import CONFIG_ROOT
    from multires_app.pipeline.scene_pipeline import create_project
    from multires_app.tools.batch_convert import source_profile
    configured=json.loads((CONFIG_ROOT/'device-profiles.example.json').read_text(encoding='utf8'))
    with zipfile.ZipFile(source) as archive:
        manifests=[n for n in archive.namelist() if re.search(r'(?:^|/)(?:ucdlockscreen|lockscreen)/manifest\.xml$',n,re.I)]
    if not manifests:raise ValueError('未发现受支持的锁屏 XML manifest')
    existing={c['manifest']:c for c in caches};result=[];source_sha=digest(source)
    for manifest in manifests:
        cached=existing.get(manifest)
        if cached and set(targets)<=set(cached.get('targets',[])):result.append(cached);continue
        profile=source_profile(source,manifest);profiles=[]
        for p in configured['profiles']:
            directions=[s for s in ('portrait','landscape') if p['id']+'-'+s in targets]
            if not directions:continue
            p={**p,'orientations':directions}
            if p['id']=='dual-fold-mock-inner':p['resolutionPx']={'width':profile['resolutionPx']['width']*2,'height':profile['resolutionPx']['height']}
            profiles.append(p)
        covered={p['id']+'-'+s for p in profiles for s in p['orientations']}
        if set(targets)-covered:raise ValueError('设备配置不存在：'+','.join(sorted(set(targets)-covered)))
        folder=out/'extraction-cache'/source_sha[:16]/hashlib.sha256(manifest.encode()).hexdigest()[:10]/'scenes'
        if not (folder/'project.json').is_file():
            create_project(source,profile,{'profiles':profiles},folder,'anchor',max_states,max_depth,lockscreen_file=manifest)
        path=folder/'project.json'
        result.append({'project':str(path),'manifest':manifest,'sha256':digest(path),'targets':list(targets)})
    return result


def code_digest():
    root=Path(__file__).resolve().parents[1];h=hashlib.sha256()
    for p in sorted(root.rglob('*.py')):h.update(p.relative_to(root).as_posix().encode());h.update(p.read_bytes())
    return h.hexdigest()


def _assets(source,destination):
    def link(a,b):
        try:os.link(a,b)
        except OSError:shutil.copy2(a,b)
    shutil.copytree(source,destination,copy_function=link)


def _save_project(path,project):
    path.write_text(json.dumps({k:v for k,v in project.items() if not k.startswith('_')},ensure_ascii=False,separators=(',',':')),encoding='utf8')


def adapt_cached(cache,targets,scenes,algorithm,audit_states,interaction_space='source'):
    from multires_app.engine.scene_engine import load_model
    from multires_app.pipeline.scene_clusters import schedule_device
    from multires_app.pipeline.scene_pipeline import load_scene_algorithm
    from multires_app.preview.component_refinement import refine_components
    from multires_app.interaction.regional_gestures import split_regional_gestures
    from multires_app.preview.scene_previewer import build_scene_preview
    from multires_app.layout.background_layers import split_background_layers
    path=Path(cache['project']);project=json.loads(path.read_text(encoding='utf8'))
    actual=load_model(Path(project['source']),project['model']['file'])
    if script_signature(actual)!=script_signature(project['model']):raise ValueError('源脚本与提取缓存不一致，必须重新分析，未复用旧状态')
    del actual
    devices={d['profile']['id']:d for d in project['devices']}
    missing=set(targets)-devices.keys()
    if missing:raise ValueError('缓存缺少目标设备：'+', '.join(sorted(missing)))
    project['devices']=[devices[t] for t in targets];del devices;gc.collect()
    if interaction_space not in {'source','target','cached'}:raise ValueError('invalid interaction coordinate policy')
    if interaction_space!='cached':
        previous=project['model'].get('inputs',{}).get('interaction',{}).get('coordinateSpace','target')
        project['model'].setdefault('inputs',{}).setdefault('interaction',{})['coordinateSpace']=interaction_space
        project['model']['layoutSourceViewport']=dict(project['sourceDevice']['viewport'])
        if previous!=interaction_space:
            # Re-evaluate cached human journeys under the selected input
            # policy; geometry from a different screen branch is not reusable.
            from multires_app.pipeline.source_comparison import packed_snapshot
            from multires_app.interaction.interaction_semantics import replay_human_state
            project['model']['assetNames']=list(project['assets'])
            for device in project['devices']:
                vp=device['viewport'];replay_cache={}
                device['layouts']=[packed_snapshot(project,replay_human_state(project['model'],vp['width'],vp['height'],
                    s,project['assets'],verify_hit=False,cache=replay_cache),s['id']) for s in project['graph']['states']]
            project['model'].pop('assetNames',None)
    project['variantProjects']=[];project['anchorIRs']={}
    split_background_layers(project)
    project['algorithmSpec']=algorithm;policy=load_scene_algorithm(algorithm);project['algorithm']=policy.name
    split_regional_gestures(project);refinement=refine_components(project)
    scenes.mkdir(parents=True);_assets(path.parent/'assets',scenes/'assets')
    # Asset cache files are read-only hard links. No source package or prior
    # project is rewritten; generated resources live only under this run.
    records=[]
    for device in project['devices']:
        target=device['profile']['id'];start=time.monotonic()
        record={'target':target,'status':'failed','mode':'constraint-baseline','referenceMatching':False}
        try:
            for key in list(device):
                if key.startswith('manualComponent'):device.pop(key)
            for layout in device['layouts']:
                for key in ('adaptationPlan','aestheticEvaluation','aestheticBeforeTransforms','adaptationReview','layoutRequest'):
                    layout.pop(key,None)
                layout['transforms']={}
            schedule_device(project,device,policy)
            sampling=select_states(project,device,audit_states)
            record.update(status='layout-ready',sampling=sampling,statesLaidOut=len(device['layouts']),
                clustering=device.get('layoutClusters',{}).get('summary',{}),
                automaticScaleBackoff=device.get('automaticScaleBackoff'),
                reviewReasons=dict(Counter(r.get('reason',r.get('kind','unknown')) for l in device['layouts'] for r in l.get('adaptationReview',[]))))
        except Exception as exc:record['error']=str(exc)
        record['seconds']=round(time.monotonic()-start,2);records.append(record)
        print(json.dumps({'stage':'layout','theme':Path(project['source']).stem,'manifest':cache['manifest'],'target':target,'status':record['status'],'error':record.get('error')},ensure_ascii=False),flush=True)
    project['devices']=[d for d in project['devices'] if any(r['target']==d['profile']['id'] and r['status']=='layout-ready' for r in records)]
    _save_project(scenes/'project.json',project)
    if project['devices']:build_scene_preview(project,scenes/'preview.html')
    coverage=project['graph'].get('coverage',{})
    return {'manifest':cache['manifest'],'project':str(scenes/'project.json'),'refinement':refinement,'interactionSpace':interaction_space,
        'coverage':{k:coverage.get(k) for k in ('truncated','interactiveTriggerCount','sampledTriggers','gestureBudgetExceeded')},
        'targets':records}


def adapt_source(source,caches,targets,out,algorithm='anchor',audit_states=6,interaction_space='source'):
    from multires_app.pipeline.scene_pipeline import export_package
    from multires_app.preview.reference_workflow import audit_package_states
    from multires_app.learning.render import render
    before=digest(source);run=out/('attempt-'+uuid.uuid4().hex[:10]);run.mkdir(parents=True)
    result={'schema':'corpus-adaptation-result/1','theme':source.stem,'source':str(source),'sourceSha256':before,
        'mode':'constraint-baseline','manualCoordinates':False,'nativeDeviceTest':False,'manifests':[],'directory':str(run)}
    for i,cache in enumerate(caches):
        try:item=adapt_cached(cache,targets,run/('manifest-'+str(i))/'scenes',algorithm,audit_states,interaction_space)
        except Exception as exc:item={'manifest':cache['manifest'],'targets':[{'target':t,'status':'failed','error':str(exc)} for t in targets]}
        result['manifests'].append(item);gc.collect()
    for target in targets:
        participating=[m for m in result['manifests'] if any(t['target']==target and t['status']=='layout-ready' for t in m['targets'])]
        if len(participating)!=len(caches):
            for m in participating:
                next(t for t in m['targets'] if t['target']==target).update(status='failed',error='其他锁屏变体布局失败，未发布不完整主题包')
            continue
        primary=Path(participating[0]['project']);project=json.loads(primary.read_text(encoding='utf8'))
        project['variantProjects']=[m['project'] for m in participating[1:]];_save_project(primary,project);del project;gc.collect()
        try:
            package=run/'packages'/(target+'.hwt');export_package(primary,target,package)
            for m in participating:
                record=next(t for t in m['targets'] if t['target']==target)
                audits=audit_package_states(m['project'],package,target,record['sampling']['states'])
                evidence=Path(m['project']).parent/'audit'/target;save(evidence/'native-audit.json',audits)
                project=json.loads(Path(m['project']).read_text(encoding='utf8'));device=next(d for d in project['devices'] if d['profile']['id']==target)
                renders=[]
                for sid in record['sampling']['states'][:3]:
                    layout=next(l for l in device['layouts'] if l['state']==sid);source_layout=next(l for l in project['sourceDevice']['layouts'] if l['state']==sid)
                    for label,d,l in [('source',project['sourceDevice'],source_layout),('target',device,layout)]:
                        png=evidence/(sid+'-'+label+'.png');info=render(project,d,l,Path(m['project']).parent/'assets',png)
                        renders.append({'state':sid,'kind':label,'image':str(png),'rendering':info})
                passed=all(a['geometryPassed'] for a in audits)
                rendering_complete=all(e['rendering']['status']=='simulated' for e in renders)
                record.update(status='checks-passed' if passed and not record['reviewReasons'] and rendering_complete else 'needs-review' if passed else 'failed',
                    package=str(package),audits=str(evidence/'native-audit.json'),nativeAuditPassed=passed,
                    renderingComplete=rendering_complete,
                    auditCounts={'states':len(audits),'comparedStableNodes':sum(a['comparedStableNodes'] for a in audits),'failedStates':[a['state'] for a in audits if not a['geometryPassed']]},renders=renders)
                if not passed:record['error']='原生脚本回读/完整交互路径未通过'
                del project,device;gc.collect()
        except Exception as exc:
            for m in participating:next(t for t in m['targets'] if t['target']==target).update(status='failed',error=str(exc))
    result['sourceUnchanged']=digest(source)==before
    if not result['sourceUnchanged']:raise RuntimeError('源主题包发生变化')
    save(run/'result.json',result);save(out/'result.json',result)
    return result


def summarize(out):
    out=Path(out);results=[json.loads(p.read_text(encoding='utf8')) for p in sorted(out.glob('theme-*/result.json'))]
    cases=[];rows=[]
    for r in results:
        for m in r.get('manifests',[]):
            for t in m['targets']:
                if t['status']!='checks-passed':cases.append({'theme':r['theme'],'manifest':m['manifest'],**{k:v for k,v in t.items() if k!='renders'}})
                links=[]
                for label,key in [('HWT','package'),('回读','audits')]:
                    if t.get(key):links.append(f'<a href="{Path(t[key]).as_uri()}">{label}</a>')
                if m.get('project'):links.append(f'<a href="{Path(m["project"]).with_name("preview.html").as_uri()}">交互预览</a>')
                imgs=''.join(f'<img title="{e["state"]} / {e["kind"]}" src="{Path(e["image"]).as_uri()}">' for e in t.get('renders',[])[:2])
                rows.append(f'<tr><td>{html.escape(r["theme"])}</td><td>{html.escape(t["target"])}</td><td>{t["status"]}</td><td>{t.get("statesLaidOut",0)}</td><td>{t.get("auditCounts",{}).get("states",0)}</td><td>{" · ".join(links)}<p>{html.escape(t.get("error", ""))}</p>{imgs}</td></tr>')
    targets=[t for r in results for m in r.get('manifests',[]) for t in m['targets']]
    summary={'themes':len(results),'manifestTargets':len(targets),'statuses':dict(Counter(t['status'] for t in targets)),
        'nativeAuditPassed':sum(bool(t.get('nativeAuditPassed')) for t in targets),
        'statesLaidOut':sum(t.get('statesLaidOut',0) for t in targets),'auditedStates':sum(t.get('auditCounts',{}).get('states',0) for t in targets),
        'sourceUnchanged':all(r.get('sourceUnchanged',False) for r in results),'mode':'constraint-baseline',
        'referenceMatching':False,'manualCoordinates':False,'nativeDeviceTest':False,'exhaustiveInteractionCoverage':False}
    save(out/'summary.json',summary);save(out/'results.json',results)
    (out/'badcases.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases),encoding='utf8')
    page='<meta charset="utf-8"><title>全主题通用适配回归</title><style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}img{height:150px;max-width:48%;object-fit:contain}pre{white-space:pre-wrap}</style><h1>全主题通用布局求解与脚本回读</h1><p>统一算法，无主题专属坐标。此报告是约束算法基线；参考图视觉匹配单独报告。checks-passed 仅表示采样脚本检查通过；所有产物均需视觉与真机验收。状态探索有界，未覆盖全部交互。</p><pre>'+html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre><p><a href="badcases.jsonl">待修复案例</a> · <a href="results.json">完整结果</a></p><table><tr><th>主题</th><th>目标</th><th>状态</th><th>已布局状态</th><th>回读状态</th><th>产物与原始/目标图</th></tr>'+''.join(rows)+'</table>'
    (out/'report.html').write_text(page,encoding='utf8');return summary


def run(themes,projects,out,targets=DEFAULT_TARGETS,algorithm='anchor',audit_states=6,only=None,retry=False,interaction_space='source'):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    index=cache_index(projects);code=code_digest();sources=sorted(Path(themes).resolve().glob('*.hwt'))
    if only:sources=[s for s in sources if any(s.stem==n for n in only)]
    for source in sources:
        slug='theme-'+hashlib.sha256(str(source).casefold().encode()).hexdigest()[:10];folder=out/slug
        caches=index.get(str(source).casefold(),[])
        try:caches=ensure_cache(source,caches,targets,out)
        except Exception as exc:
            save(folder/'result.json',{'theme':source.stem,'source':str(source),'sourceUnchanged':True,'manifests':[{
                'manifest':'extraction','targets':[{'target':t,'status':'failed','error':str(exc)} for t in targets]}]})
            summarize(out);continue
        config={'sourceSha256':digest(source),'caches':caches,'targets':list(targets),'algorithm':algorithm,'auditStates':audit_states,'code':code,'interactionSpace':interaction_space}
        key=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest();record=folder/'result.json'
        if record.exists():
            previous=json.loads(record.read_text(encoding='utf8'))
            if previous.get('fingerprint')==key and not retry:continue
        folder.mkdir(exist_ok=True)
        if not caches:
            result={'theme':source.stem,'source':str(source),'sourceUnchanged':True,'manifests':[{'manifest':'unknown','targets':[{'target':t,'status':'failed','error':'没有已提取缓存，请先 batch-convert'} for t in targets]}]}
        else:result=adapt_source(source,caches,targets,folder,algorithm,audit_states,interaction_space)
        result['fingerprint']=key;result['inputs']=config;save(record,result);summarize(out)
        print(json.dumps({'finished':source.stem,'statuses':[t['status'] for m in result['manifests'] for t in m['targets']]},ensure_ascii=False),flush=True)
        gc.collect()
    return summarize(out)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--themes',type=Path,default=Path('theme'));parser.add_argument('--projects',type=Path,default=Path('theme/adaptation'))
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--targets',nargs='+',default=list(DEFAULT_TARGETS))
    parser.add_argument('--algorithm',default='anchor');parser.add_argument('--audit-states',type=int,default=6)
    parser.add_argument('--interaction-space',choices=['source','target','cached'],default='source',help='source 保持原始脚本的屏幕条件分支；target 采用目标画布条件；cached 保持缓存设置')
    parser.add_argument('--only',nargs='+');parser.add_argument('--retry',action='store_true');parser.add_argument('--summarize',action='store_true')
    args=parser.parse_args()
    if args.audit_states<1:parser.error('--audit-states must be positive')
    result=summarize(args.out) if args.summarize else run(args.themes,args.projects,args.out,args.targets,args.algorithm,args.audit_states,args.only,args.retry,args.interaction_space)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
