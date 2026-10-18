"""Repeatable conversion and bounded validation of source HWTs; never modifies inputs."""
import argparse
import contextlib
import copy
import hashlib
import html
import io
import json
import math
import re
import traceback
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from xml.etree import ElementTree as ET

from multires_app.engine.scene_engine import load_model, Runtime
from multires_app.pipeline.scene_pipeline import create_project, export_package, relayout_project, state_guard
from multires_app.interaction.interaction_semantics import replay_human_state


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def source_profile(source,manifest=None):
    from PIL import Image
    model=load_model(source,manifest)
    with zipfile.ZipFile(source) as archive:
        names=[n for n in archive.namelist() if 'preview_unlock_0' in n.lower()]
        size=None
        for name in names:
            try:
                with Image.open(io.BytesIO(archive.read(name))) as im:size=im.size
                break
            except Exception:pass
    width=float(model['root'].get('screenWidth',1080))
    height=float(model['root'].get('screenHeight',width*size[1]/size[0] if size else width*2))
    return {'id':'source-inferred','resolutionPx':{'width':width,'height':height},
            'note':'screenWidth/screenHeight 优先；缺失高度使用锁屏预览比例；无预览则假定 1:2，需人工复核。','previewSize':size}


def validate(directory, package_paths, adapted_projects=None):
    project=json.loads((directory/'project.json').read_text(encoding='utf-8'))
    project['model']['assetNames']=list(project['assets'])
    variant_paths=[Path(p) for p in (adapted_projects if adapted_projects is not None else project.get('variantProjects',[]))]
    variants=[(path,json.loads(path.read_text(encoding='utf-8'))) for path in variant_paths]
    adapted_files={project['model']['file']}|{p['model']['file'] for _,p in variants}
    with zipfile.ZipFile(project['source']) as archive:
        alternate=[n for n in archive.namelist() if re.search(r'/(?:ucdlockscreen|lockscreen)/manifest\.xml$',n,re.I) and n not in adapted_files]
    errors=[];skipped=0;checked=0;zip_checked=0;glyph_checked=0;generated_checked=0
    states={s['id']:s for s in project['graph']['states']}
    for path in (directory/'scripts').glob('*.xml'):ET.parse(path)
    for package in package_paths:
        target=package.stem;device=next(d for d in project['devices'] if d['profile']['id']==target)
        from multires_app.media.resource_scaling import manifest_resources
        generated=manifest_resources(directory/'scripts'/(target+'.xml'))['files']
        for variant_path,variant in variants:generated.update(manifest_resources(variant_path.parent/'scripts'/(target+'.xml'))['files'])
        native_assets={**project['assets'],**generated}
        with zipfile.ZipFile(project['source']) as original,zipfile.ZipFile(package) as candidate:
            if candidate.namelist()!=original.namelist()+sorted(generated):raise ValueError('ZIP entries changed outside approved generated resources')
            if candidate.testzip() is not None:raise ValueError('ZIP CRC failed')
            for entry in original.namelist():
                if entry not in adapted_files and original.read(entry)!=candidate.read(entry):raise ValueError('asset changed: '+entry)
                zip_checked+=1
            for name,meta in generated.items():
                if digest(Path(meta['file']))!=meta['sha256'] or hashlib.sha256(candidate.read(name)).hexdigest()!=meta['sha256']:raise ValueError('generated resource digest mismatch: '+name)
                generated_checked+=1
            for variant_path,variant in variants:
                if candidate.read(variant['model']['file'])!=(variant_path.parent/'scripts'/(target+'.xml')).read_bytes():raise ValueError('variant script mismatch')
        adapted=load_model(package,project['model']['file'])
        for index in sorted({0,len(device['layouts'])//2,len(device['layouts'])-1}):
            layout=device['layouts'][index]
            source=replay_human_state(project['model'],device['viewport']['width'],device['viewport']['height'],states[layout['state']],project['assets'],verify_hit=False)
            runtime=Runtime(adapted,device['viewport']['width'],device['viewport']['height'],source.state,time_ms=source.time_ms)
            runtime.system.update(source.system)
            snapshots=runtime.snapshot(native_assets);rendered={e['id']:e for e in snapshots}
            from multires_app.media.resource_scaling import glyph_nodes
            glyph_ids=set(glyph_nodes(project))
            for uid in glyph_ids:
                expected_rows=[r for r in layout['rows'] if project['nodeOrder'][r[0]]==uid]
                actual_rows=[e for e in snapshots if e['id']==uid and e['visible'] and e['asset'] in native_assets]
                if not expected_rows:continue
                if len(expected_rows)!=len(actual_rows):errors.append({'target':target,'state':layout['state'],'node':uid,'reason':'native glyph count mismatch'});continue
                t=layout['transforms'].get(project['nodeComponents'][uid],{'sx':1,'sy':1,'tx':0,'ty':0});tolerance=1+len(expected_rows)*.51
                for row,actual_glyph in zip(expected_rows,actual_rows):
                    expected=[row[1]*t['sx']+t['tx'],row[2]*t['sy']+t['ty'],row[3]*t['sx'],row[4]*t['sy']];glyph_checked+=1
                    for field,value in zip(('x','y','w','h'),expected):
                        if abs(value-actual_glyph['rect'][field])>tolerance:errors.append({'target':target,'state':layout['state'],'node':uid,'field':field,'reason':'glyph resource scaling mismatch','expected':value,'actual':actual_glyph['rect'][field]})
            for row in layout['rows']:
                uid=project['nodeOrder'][row[0]];node=project['model']['nodes'][uid]
                if uid in glyph_ids:continue
                if (node['tag'] in ('Time','Date','Number') and node['attributes'].get('src')) or any(uid+':'+a in project['diagnostics'] for a in ('x','y','w','h')):skipped+=1;continue
                t=layout['transforms'].get(project['nodeComponents'][uid],{'sx':1,'sy':1,'tx':0,'ty':0})
                expected=[row[1]*t['sx']+t['tx'],row[2]*t['sy']+t['ty'],row[3]*t['sx'],row[4]*t['sy']]
                actual=rendered[uid]['rect'];checked+=1
                for field,value in zip(('x','y','w','h'),expected):
                    if not math.isfinite(actual[field]) or abs(value-actual[field])>.08:
                        errors.append({'target':target,'state':layout['state'],'node':uid,'field':field,'expected':value,'actual':actual[field]})
    # Fixed components must retain the exact prior transform for local/overlay requests.
    for device in project['devices']:
        layouts={l['state']:l for l in device['layouts']}
        guards={}
        for layout in device['layouts']:
            guard=state_guard(project,states[layout['state']],device)
            representative=guards.setdefault(guard,layout)
            conflict=next((cid for cid,t in layout['transforms'].items() if cid in representative['transforms'] and any(abs(v-representative['transforms'][cid][field])>1e-6 for field,v in t.items())),None)
            if conflict:errors.append({'target':device['profile']['id'],'state':layout['state'],'baseState':representative['state'],'component':conflict,'reason':'identical script state guard has conflicting transforms'})
            request=layout.get('layoutRequest',{});base=layouts.get(request.get('baseState'))
            if base and request.get('kind') in ('local-reflow','overlay'):
                for cid in request.get('fixedComponents',[]):
                    if layout['transforms'].get(cid)!=base['transforms'].get(cid):errors.append({'target':device['profile']['id'],'state':layout['state'],'fixedComponent':cid})
    result={'checkedElementSnapshots':checked,'checkedGlyphSnapshots':glyph_checked,'generatedResourcesChecked':generated_checked,'skippedElementSnapshots':skipped,'zipEntriesChecked':zip_checked,'failures':len(errors),'examples':errors[:30],
            'unadaptedLockscreenVariants':alternate,
            'scope':'XML解析、原资源保留、新增字形资源哈希、采样几何及公共组件固定检查；图片字形按整数像素舍入误差验证。未解析坐标及其他不支持控件跳过。非原生设备验收。'}
    (directory/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def validate_live_project(directory):
    from multires_app.preview.interaction_server import InteractiveSession
    project=json.loads((directory/'project.json').read_text(encoding='utf-8'))
    edges=[e for e in project['graph']['transitions'] if e['from']=='state-000' and e['to']]
    edges.sort(key=lambda e:(e['to']=='state-000',e['action']!='tap'))
    results=[]
    for target in ('tablet-mock-16-10-landscape','dual-fold-mock-inner-portrait'):
        session=InteractiveSession(project,target);initial=copy.deepcopy(session.runtime.state);source_initial=copy.deepcopy(session.source_runtime.state);rejected=[];result=None
        for edge in edges:
            action={'owner':edge['owner'],'kind':edge['action'],'sample':edge['sample'],'gesture':edge['gesture']}
            try:
                response=session.gesture(action);session.back()
                if session.runtime.state!=initial or session.source_runtime.state!=source_initial:raise AssertionError('back did not restore both concrete runtimes')
                result={'target':target,'status':'passed','action':action,'layoutStrategy':response['layout']['layoutRequest']['kind'],
                        'backRestoresRuntime':True,'sourceGestureValid':response['comparison']['sourceOutcome']['valid'],
                        'targetGestureValid':response['comparison']['targetOutcome']['valid'],'renderProcessFrames':len(response['processFrames']),
                        'rejectedBeforeSuccess':len(rejected)};break
            except ValueError as exc:rejected.append(str(exc))
        results.append(result or {'target':target,'status':'no-interaction' if not edges else 'failed','rejected':rejected[:12]})
    report={'results':results,'failures':sum(r['status']=='failed' for r in results),'scope':'每个代表视口从初始状态执行一次可用完整手势，并检查返回恢复。不是全部分支或原生引擎验收。'}
    (directory/'live-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def write_index(root,results):
    (root/'batch-summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=[]
    for r in results:
        slug=r['slug'];status={'validated':'采样检查通过','needs-review':'待复核','failed':'转换失败'}[r['status']];v=r.get('validation',{})
        links=f'<a href="{slug}/scenes/preview.html">预览</a> · <a href="{slug}/scenes/validation.json">验证</a>' if r.get('states') else html.escape(r.get('error',''))
        if r.get('liveValidation'):links+=f' · <a href="{slug}/scenes/live-validation.json">交互验证</a>'
        if (root/slug/'scenes/anchor-layout.json').exists():links+=f' · <a href="{slug}/scenes/anchor-layout.json">锚点与质量选择</a>'
        for variant in r.get('variants',[]):links+=f' · <a href="{variant["slug"]}/scenes/preview.html">UCD 锁屏变体</a>'
        rows.append(f'<tr><td>{html.escape(r["name"])}</td><td>{status}</td><td>{r.get("states",0)}</td><td>{r.get("transitions",0)}</td><td>{v.get("failures","—")}</td><td>{links}</td></tr>')
    page='''<!doctype html><meta charset="utf-8"><title>主题适配批次</title><style>body{font:15px system-ui;background:#121923;color:#e4edf5;padding:30px}table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #39485b;text-align:left}a{color:#80dfcc}</style><h1>主题适配与验证</h1><p>每包生成 22 个设备/方向 XML，导出平板横屏、双折叠竖屏两个代表性 HWT。预览文件为离线回放；连续实时交互使用 multires.py serve。状态探索有上限，候选包需原生设备验收。表内检查覆盖脚本、采样坐标与公共组件固定；构图和素材缺失请查看预览及布局分析。</p><p><a href="media-validation.json">首帧布局与跨时刻验证</a> · <a href="anchor-validation-summary.json">上一轮锚点检查</a> · <a href="anchor-ab-report.json">媒体改造前的算法比较</a></p><table><tr><th>主题</th><th>状态</th><th>场景</th><th>交互转移</th><th>反写/冻结问题</th><th>产物</th></tr>'''+''.join(rows)+'</table>'
    (root/'index.html').write_text(page,encoding='utf-8')
    for r in results:
        if 'states' not in r:continue
        v=r['validation'];coverage=r['coverage']
        content=f'''# {r['name']} 适配结果

[离线预览](preview.html) · [布局聚类](layout-clusters.json) · [验证报告](validation.json) · [项目数据](project.json)

生成 {r['states']} 个采样状态、{r['transitions']} 条完整手势转移、{r['devices']} 份目标 XML。
触发器采样 {coverage['sampledTriggers']} / {coverage['interactiveTriggerCount']}；探索截断：{coverage['truncated']}。
采样几何检查 {v['checkedElementSnapshots']} 个，跳过 {v['skippedElementSnapshots']} 个，发现 {v['failures']} 个问题。
源包 SHA256：{r['sha256']}；源包未改变：{r['sourceUnchanged']}。

[平板横屏 HWT](packages/tablet-mock-16-10-landscape.hwt) · [双折叠展开竖屏 HWT](packages/dual-fold-mock-inner-portrait.hwt)

连续实时交互：在仓库目录执行 `python multires.py serve theme/adaptation/{r['slug']}/scenes/project.json --port 8765`，访问打印的网址。
XML、ZIP和解释器内采样检查不代表原生主题引擎、设备安装或视觉验收。复杂动画、系统控件及未知表达式需要进一步检查。
'''
        (root/r['slug']/'scenes'/'README.md').write_text(content,encoding='utf-8')
        if r.get('variants'):
            with (root/r['slug']/'scenes'/'README.md').open('a',encoding='utf-8') as stream:
                stream.write('\n候选 HWT 同时反写以下锁屏变体：\n\n')
                for variant in r['variants']:stream.write(f'- [{variant["manifest"]}](../../{variant["slug"]}/scenes/preview.html)：{variant["states"]} 个采样状态。\n')


def convert_source(source,root,profiles,previous,resume,relayout):
    slug='wufu' if '五福' in source.name else 'beihai' if source.stem=='北海' else 'theme-'+hashlib.sha256(source.name.encode()).hexdigest()[:10]
    out=root/slug/'scenes';out.mkdir(parents=True,exist_ok=True)
    record={'name':source.name,'slug':slug,'source':str(source.resolve()),'sha256':digest(source)}
    print(json.dumps({'start':source.name,'slug':slug},ensure_ascii=True),flush=True)
    try:
        profile=source_profile(source)
        (out.parent/'source-profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
        with (out/'conversion.log').open('w',encoding='utf-8') as log,contextlib.redirect_stdout(log):
            if resume and previous.get(slug,{}).get('sha256')==record['sha256'] and (out/'report.json').exists():
                if relayout:relayout_project(out/'project.json')
                report=json.loads((out/'report.json').read_text(encoding='utf-8'))
            else:report=create_project(source,profile,profiles,out,max_states=96,max_depth=8)
            with zipfile.ZipFile(source) as archive:
                extra_manifests=[n for n in archive.namelist() if n.lower().endswith('/ucdlockscreen/manifest.xml')]
            variant_paths=[];record['variants']=[]
            for manifest in extra_manifests:
                variant_slug=slug+'-ucd';variant_out=root/variant_slug/'scenes'
                variant_path=variant_out/'project.json'
                if not resume or not variant_path.exists():
                    variant_report=create_project(source,source_profile(source,manifest),profiles,variant_out,max_states=96,max_depth=8,lockscreen_file=manifest)
                else:
                    if relayout:relayout_project(variant_path)
                    variant_report=json.loads((variant_out/'report.json').read_text(encoding='utf-8'))
                variant_paths.append(variant_path.resolve())
                record['variants'].append({'slug':variant_slug,'manifest':manifest,'states':variant_report['states']})
            primary_path=(out/'project.json').resolve()
            primary=json.loads(primary_path.read_text(encoding='utf-8'));primary['variantProjects']=[str(p) for p in variant_paths]
            primary_path.write_text(json.dumps(primary,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
            for path in variant_paths:
                variant=json.loads(path.read_text(encoding='utf-8'));variant['variantProjects']=[str(primary_path)]+[str(p) for p in variant_paths if p!=path]
                path.write_text(json.dumps(variant,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
            packages=[]
            for target in ('tablet-mock-16-10-landscape','dual-fold-mock-inner-portrait'):
                package=out/'packages'/(target+'.hwt');export_package(out/'project.json',target,package);packages.append(package)
            validation=validate(out,packages)
            variant_validations=[validate(path.parent,packages,adapted_projects=[primary_path]+[p for p in variant_paths if p!=path]) for path in variant_paths]
            if variant_validations:
                validation['variantValidations']=variant_validations
                for key in ('checkedElementSnapshots','skippedElementSnapshots','zipEntriesChecked','failures'):
                    validation[key]+=sum(v[key] for v in variant_validations)
                (out/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
            report['adaptedManifests']=[primary['model']['file']]+extra_manifests
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            record['liveValidation']=validate_live_project(out)
            record['liveValidation']['variants']=[validate_live_project(path.parent) for path in variant_paths]
        record.update({k:report[k] for k in ('states','transitions','devices','diagnosticCount','coverage','layoutClustering')})
        record['validation']=validation;record['status']='validated' if validation['failures']==0 and not validation['unadaptedLockscreenVariants'] else 'needs-review'
        if record['liveValidation']['failures'] or any(v['failures'] for v in record['liveValidation']['variants']):record['status']='needs-review'
    except Exception as exc:
        record['status']='failed';record['error']=str(exc)
        (out/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
    record['sourceUnchanged']=digest(source)==record['sha256']
    if not record['sourceUnchanged']:raise RuntimeError('source archive changed')
    print(json.dumps({'finished':source.name,'status':record['status'],'states':record.get('states'),'issues':record.get('validation',{}).get('failures'),'error':record.get('error')},ensure_ascii=True),flush=True)
    return record


def run(theme,profiles,only=None,resume=False,relayout=False,workers=3):
    root=theme/'adaptation';root.mkdir(exist_ok=True)
    sources=sorted(theme.glob('*.hwt'),key=lambda p:(0 if '五福' in p.name else 1 if p.stem=='北海' else 2,p.name))
    if only:sources=[s for s in sources if any(name in s.name for name in only)]
    previous={r['slug']:r for r in json.loads((root/'batch-summary.json').read_text(encoding='utf-8'))} if (root/'batch-summary.json').exists() else {}
    results=[r for r in previous.values() if only and not any(name in r['name'] for name in only)]
    def accept(record):
        results.append(record);results.sort(key=lambda r:(0 if r['slug']=='wufu' else 1 if r['slug']=='beihai' else 2,r['name']))
        write_index(root,results)
    # Complete the original two packages before starting independent new ones.
    initial=[s for s in sources if '五福' in s.name or s.stem=='北海']
    remaining=[s for s in sources if s not in initial]
    for source in initial:accept(convert_source(source,root,profiles,previous,resume,relayout))
    if workers==1:
        for source in remaining:accept(convert_source(source,root,profiles,previous,resume,relayout))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures=[pool.submit(convert_source,source,root,profiles,previous,resume,relayout) for source in remaining]
            for future in as_completed(futures):accept(future.result())
    return results


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--theme',type=Path,default=Path('theme'));parser.add_argument('--only',nargs='*');parser.add_argument('--resume',action='store_true');parser.add_argument('--relayout',action='store_true')
    parser.add_argument('--workers',type=int,choices=range(1,5),default=3)
    args=parser.parse_args();run(args.theme,json.loads(Path('configs/device-profiles.example.json').read_text(encoding='utf-8')),args.only,args.resume,args.relayout,args.workers)


if __name__=='__main__':
    main()
