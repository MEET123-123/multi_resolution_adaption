"""Frozen render requests, bounded model feedback and isolated native candidates."""
import copy
import gc
import hashlib
import html
import json
import math
from pathlib import Path
import kiwisolver as kiwi

from .contract import digest,validate
from .templates import catalog as template_catalog,placements
from .providers import MockProvider,OpenAICompatible


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')


def file_digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def prepare(project_path,target,state,out):
    from multires_app.preview.workbench import clone_scene
    from multires_app.preview.target_layout import build_catalog,make_atlas
    from multires_app.learning.render import render
    from multires_app.layout.spatial_flow import layout_flows
    from multires_app.pipeline.scene_clusters import overlap
    path=Path(project_path).resolve();out=Path(out).resolve()
    if out.is_relative_to(path.parent) or path.parent.is_relative_to(out):raise ValueError('请求目录必须与输入 scenes 目录分开')
    out.mkdir(parents=True,exist_ok=True)
    if (out/'request.json').exists():raise ValueError('请求目录已经存在，请指定新的输出目录')
    clone_scene(path.parent,out/'scenes');local=out/'scenes/project.json';p=load(local)
    d=next((d for d in p['devices'] if d['profile']['id']==target),None)
    if d is None:raise ValueError('未知目标设备')
    l=next((l for l in d['layouts'] if l['state']==state),None)
    if l is None:raise ValueError('未知交互场景')
    if d['viewport']['width']<=d['viewport']['height']:raise ValueError('布局重构入口当前针对横屏目标，请选择横屏设备')
    src=next(l for l in p['sourceDevice']['layouts'] if l['state']==state)
    source_render=render(p,p['sourceDevice'],src,local.parent/'assets',out/'source.png')
    before_render=render(p,d,l,local.parent/'assets',out/'before.png')
    c,images=build_catalog(p,target,state,local.parent,layout_mode='reference-first')
    screen={'x':0,'y':0,'w':p['sourceDevice']['viewport']['width'],'h':p['sourceDevice']['viewport']['height']}
    for g in c['groups']:
        if overlap(g['bbox'],screen)<=0:g.update(locked=True,lockReason='原始场景完整位于屏外，继续等待原有交互')
    if not any(not g['locked'] for g in c['groups']):raise ValueError('当前场景没有可定位的可移动组件组，请检查缺失资源或更换场景')
    aliases={cid:g['id'] for g in c['groups'] for cid in g['members']}
    relations=[];seen=set()
    for r in layout_flows(p,d,l):
        key=(r['first'],r['last'],r['axis'])
        if key in seen or r['first'] not in aliases or r['last'] not in aliases or aliases[r['first']]==aliases[r['last']]:continue
        seen.add(key);relations.append({**r,'id':'source-order-'+str(len(relations))})
        if len(relations)>=128:break
    image_paths=['source.png','before.png']
    visible=[g for g in c['groups'] if not g['locked'] and g['id'] in images]
    # Budget for the model: three atlases + source + actual target, never every frame.
    for index in range(0,min(len(visible),27),9):
        name=f'atlas-{index//9}.png';make_atlas(visible[index:index+9],images).save(out/name);image_paths.append(name)
    public_relations=[{'id':r['id'],'first':aliases[r['first']],'last':aliases[r['last']],
                       'axis':r['axis'],'kind':'source-order','strength':'weak'} for r in relations]
    public_relations.extend({'id':name,'kind':name,'strength':'required'} for name in
        ['source-identity-and-visibility','rigid-interaction-bindings','canvas-and-corners','authored-constraints','cross-state-bitmap-scale'])
    request={'schema':'layout-reconstruction-request/1','target':target,'state':state,
        'viewport':d['viewport'],'sourceViewport':p['sourceDevice']['viewport'],'safeRegion':c['constraints']['safeRegion'],
        'templates':template_catalog(),'relations':public_relations,
        'groups':[{'id':g['id'],'members':g['members'],'roles':g['roles'],'names':g['names'],'locked':g['locked'],
                   'sourceBox':g['matchBox'],'footprint':g['bbox'],'nativeScale':g.get('nativeScale'),'lockReason':g.get('lockReason')} for g in c['groups']],
        'rendering':{'source':source_render,'before':before_render},
        'coverage':p.get('graph',{}).get('coverage',{}),'imageBudget':{'atlasGroups':len(visible[:27]),'totalMovable':sum(not g['locked'] for g in c['groups'])},
        'limitations':['离线解释器渲染，不是鸿蒙真机截图','文字宽度沿用解释器估算，未校准原生字体度量','显式复杂容器和跨状态位图尺寸可能限制重构'],
        'scope':'one representative scene; compatible layout propagation; existing script controls only',
        'heuristicEvaluation':True,'training':False}
    request['inputDigest']=digest(request)
    save(out/'request.json',request);save(out/'catalog.json',c);save(out/'relations.json',relations)
    source_file=Path(p['source'])
    save(out/'manifest.json',{'sourceProject':str(path),'sourceProjectSha256':file_digest(path),
        'sourceFile':str(source_file) if source_file.is_file() else None,'sourceFileSha256':file_digest(source_file) if source_file.is_file() else None,
        'projectSha256':file_digest(local),'catalogSha256':file_digest(out/'catalog.json'),
        'relationsSha256':file_digest(out/'relations.json'),
        'images':{name:file_digest(out/name) for name in image_paths}})
    return {'request':str(out/'request.json'),'groups':len(request['groups']),'target':target,'state':state}


def evaluate(project,target,state,catalog):
    """Transparent geometry proxies; no claim of designer or human preference."""
    from multires_app.pipeline.scene_clusters import box_union,transformed_box,overlap
    from multires_app.layout.stable_review import review_final
    d=next(d for d in project['devices'] if d['profile']['id']==target);l=next(l for l in d['layouts'] if l['state']==state)
    boxes=[];small=[];region=catalog['constraints']['safeRegion'];sv=project['sourceDevice']['viewport']
    contain=min(region['w']/sv['width'],region['h']/sv['height'])
    for g in catalog['groups']:
        if g['locked']:continue
        parts=[];scales=[]
        for cid in g['members']:
            t=l['transforms'].get(cid,{'sx':1,'sy':1,'tx':0,'ty':0});parts.append(transformed_box(g['basisBoxes'][cid],t));scales.append(min(t['sx'],t['sy']))
        b=box_union(parts);boxes.append(b);small.append(max(0,1-min(scales)/max(.001,contain*.65)))
    area=region['w']*region['h'];ink=sum(b['w']*b['h'] for b in boxes)
    left={'x':region['x'],'y':region['y'],'w':region['w']/2,'h':region['h']}
    mass_left=sum(overlap(b,left) for b in boxes);balance=abs(2*mass_left-ink)/max(1,ink)
    issues=review_final(project,d,l);collisions=sum(e['reason']=='new overlap after layout' for e in issues);outside=sum(e['reason']=='component exceeds safe area' for e in issues)
    small=sum(small)/max(1,len(small));coverage=ink/area
    score=100-45*small-15*balance-20*min(1,abs(coverage-.45)) -20*min(1,collisions)-30*min(1,outside)
    return {'score':round(score,4),'smallGroupPenalty':round(small,4),'balanceError':round(balance,4),
            'coverageProxy':round(coverage,4),'newOverlapPairs':collisions,'outsideResources':outside,
            'review':issues,'scope':'geometry proxies only; weights configurable by replacing evaluator callable'}


def run(request_path,*,provider=None,config=None,template=None,rounds=2,advice=None,solver=None,evaluator=None,compile_package=True,progress=None):
    from multires_app.preview.layout_refinement import export_request,validate_and_apply
    from multires_app.learning.render import render
    from .solver import solve
    request_path=Path(request_path).resolve();root=request_path.parent;req=load(request_path);manifest=load(root/'manifest.json')
    def immutable():
        checks=[(manifest['sourceProject'],manifest['sourceProjectSha256']),(root/'scenes/project.json',manifest['projectSha256']),
                (root/'catalog.json',manifest['catalogSha256']),(root/'relations.json',manifest['relationsSha256'])]
        checks.extend((root/name,value) for name,value in manifest['images'].items())
        if manifest.get('sourceFile'):checks.append((manifest['sourceFile'],manifest['sourceFileSha256']))
        if any(file_digest(path)!=value for path,value in checks):raise ValueError('输入或图片已改变，请重新准备请求')
        if digest({k:v for k,v in req.items() if k!='inputDigest'})!=req.get('inputDigest'):raise ValueError('请求摘要无效')
    immutable()
    if type(rounds) is not int or not 1<=rounds<=3:raise ValueError('反馈轮次必须为 1..3')
    if (root/'result.json').exists():raise ValueError('该请求已运行，重新 prepare 后再试')
    p=load(root/'scenes/project.json');p['_referenceAssetRoot']=str(root/'scenes')
    catalog=load(root/'catalog.json');relations=load(root/'relations.json');raw_request=export_request(p,req['target'],req['state'])
    if raw_request['baseDigest']!=catalog['baseDigest']:raise ValueError('组件目录已过期')
    from multires_app.preview.target_layout import _validate_catalog
    _validate_catalog(catalog,raw_request)
    solver=solver or solve;evaluator=evaluator or evaluate
    provider=provider or (OpenAICompatible(config) if config else MockProvider(template))
    baseline=evaluator(p,req['target'],req['state'],catalog);feedback=[];records=[];seen=set();errors=[]
    selected=None;best_score=baseline['score'];images=[(name,root/name) for name in manifest['images']];selection='geometry-proxy';review_reason=None
    def signal(message):
        if progress:progress(message)
    for turn in range(1,rounds+1):
        signal(f'模型建议与渲染反馈：第 {turn}/{rounds} 轮')
        try:
            reviewable=[r['candidate'] for r in feedback if r['status']=='feasible']
            feedback_ids={r['candidate'] for r in feedback}
            actual=[(r['id']+' 实际求解渲染（'+r['status']+'）',root/r['image']) for r in records if r['id'] in feedback_ids and r.get('image')]
            response=validate(advice if advice is not None else provider(req,images if not feedback else [*images[:2],*actual],feedback),req,reviewable)
        except Exception as exc:
            errors.append({'round':turn,'stage':'provider','error':str(exc)[:500]});break
        save(root/f'advice-{turn}.json',response)
        if 'preferredCandidate' in response:
            selected=None if response['preferredCandidate']=='baseline' else response['preferredCandidate']
            selection='model-visual-review';review_reason=response['reviewReason']
        for index,candidate in enumerate(response['candidates']):
            key=digest(candidate)
            if key in seen:continue
            seen.add(key);name=f'candidate-{turn}-{index}';record={'id':name,'round':turn,'template':candidate['template'],'rationale':candidate['rationale']}
            matches=placements(req,candidate);alternatives=[]
            for attempt in range(2):
                updated=None
                try:
                    rows,projection=solver(p,catalog,raw_request,matches,candidate,relations,attempt)
                    proposal={**raw_request['responseTemplate'],'origin':'layout-reconstructor','applyScope':'matching-components',
                              'propagationPolicy':'compatible-layout','components':rows}
                    updated,propagation=validate_and_apply(p,proposal)
                    from multires_app.layout.corner_geometry import corner_errors
                    d=next(d for d in updated['devices'] if d['profile']['id']==req['target'])
                    if any(corner_errors(updated,d,l) for l in d['layouts']):raise ValueError('重构改变了稳定场景的角落快捷键关系')
                    metrics=evaluator(updated,req['target'],req['state'],catalog)
                    if not math.isfinite(metrics['score']):raise ValueError('评价器返回非有限分数')
                    if metrics['outsideResources']>baseline['outsideResources'] or metrics['newOverlapPairs']>baseline['newOverlapPairs']:
                        record['diagnosticMetrics']=metrics
                        diagnostic_device=next(d for d in updated['devices'] if d['profile']['id']==req['target'])
                        diagnostic_layout=next(l for l in diagnostic_device['layouts'] if l['state']==req['state'])
                        record['image']=name+'-rejected.png'
                        render(updated,diagnostic_device,diagnostic_layout,root/'scenes/assets',root/record['image'])
                        raise ValueError('重构新增出界或遮挡，保留原布局；诊断图仅用于反馈，不用于脚本导出')
                    alternatives.append((metrics['score'],updated,projection,metrics,propagation,proposal))
                except (ValueError,kiwi.UnsatisfiableConstraint) as exc:record.setdefault('rejectedAttempts',[]).append(str(exc)[:500])
            if not alternatives:
                record['status']='rejected';records.append(record);continue
            _,updated,projection,metrics,propagation,proposal=max(alternatives,key=lambda a:a[0]);del alternatives
            d=next(d for d in updated['devices'] if d['profile']['id']==req['target']);l=next(l for l in d['layouts'] if l['state']==req['state'])
            image=root/(name+'.png');rendering=render(updated,d,l,root/'scenes/assets',image)
            project_file=root/(name+'.json');save(project_file,{k:v for k,v in updated.items() if not k.startswith('_')})
            record.update(status='feasible',metrics=metrics,projection=projection,propagation=propagation,rendering=rendering,
                          image=image.name,project=project_file.name)
            save(root/(name+'-proposal.json'),proposal)
            if selection!='model-visual-review' and metrics['score']>best_score+.01:
                best_score=metrics['score'];selected=name
            records.append(record);del updated,d,l;gc.collect()
        feedback=[{'candidate':r['id'],'status':r['status'],'metrics':r.get('metrics',r.get('diagnosticMetrics')),
                   'rejections':r.get('rejectedAttempts'),'projection':r.get('projection')} for r in records[-3:]]
        if response.get('stop') or advice is not None:break
    immutable();result={'schema':'layout-reconstruction-result/1','provider':getattr(provider,'name','custom-callable'),
        'inputDigest':req['inputDigest'],'target':req['target'],'state':req['state'],'selected':selected,'baseline':baseline,
        'selectionPolicy':selection,'visualReviewReason':review_reason,
        'candidates':records,'errors':errors,'sourceUnchanged':True,'nativeDeviceTest':False,'training':False,
        'scope':req['scope'],'limitations':req.get('limitations',[]),'status':'unchanged','directory':str(root),'heuristicEvaluation':True}
    if selected:
        result['status']='candidate'
        if compile_package:
            signal('写回隔离脚本并回放原始交互路径')
            result.update(compile_selected(root,req,records,baseline,selected=selected,progress=progress))
    save(root/'result.json',result);report(root,req,result)
    return result


def compile_selected(root,req,records,baseline,selected=None,progress=None):
    from multires_app.preview.workbench import clone_scene
    from multires_app.pipeline.scene_pipeline import export_package
    from multires_app.preview.reference_workflow import audit_package_states
    from multires_app.pipeline.representative_states import select_states
    from multires_app.preview.scene_previewer import build_scene_preview
    eligible=sorted([r for r in records if r['id']==selected or r.get('metrics',{}).get('score',-math.inf)>baseline['score']+.01],key=lambda r:(r['id']==selected,r['metrics']['score']),reverse=True)
    for row in eligible:
        folder=root/(row['id']+'-compiled');clone_scene(root/'scenes',folder/'scenes')
        path=folder/'scenes/project.json';p=load(root/row['project']);save(path,p)
        try:
            package=folder/'adapted.hwt';export_package(path,req['target'],package)
            d=next(d for d in p['devices'] if d['profile']['id']==req['target']);sampling=select_states(p,d,6)
            states=list(dict.fromkeys([req['state'],*sampling['states']]))
            audits=audit_package_states(path,package,req['target'],states);save(folder/'native-audits.json',audits)
            if not all(a['geometryPassed'] for a in audits):raise ValueError('原生脚本回读/交互路径失败')
            build_scene_preview(p,folder/'scenes/preview.html')
            row['nativeReadbackPassed']=True
            complete=(row['rendering']['status']=='simulated' and req['rendering']['source']['status']=='simulated' and req['rendering']['before']['status']=='simulated'
                      and req['imageBudget']['atlasGroups']==req['imageBudget']['totalMovable'])
            return {'selected':row['id'],'status':'compiled' if complete and not row['metrics'].get('review') else 'needs-review','package':str(package),
                    'project':str(path),'readbackPassed':True,'readbackCoverage':sampling,'viewport':d['viewport'],
                    'renderingComplete':complete,'report':str(root/'report.html')}
        except (ValueError,OSError) as exc:
            row.update(nativeReadbackPassed=False,compileError=str(exc)[:700])
    return {'selected':None,'status':'rejected','readbackPassed':False,'error':'候选未通过脚本导出与回读，保留输入项目'}


def report(root,req,result):
    page='<meta charset="utf-8"><title>横屏布局重构</title><style>body{font:15px system-ui;margin:24px}img{max-width:44%;max-height:440px;background:#ddd}pre{white-space:pre-wrap}td,th{padding:10px;border-bottom:1px solid #ccc;text-align:left}</style>'
    page+='<h1>构图建议 → 约束求解 → 实际渲染 → 反馈</h1><p>'+html.escape(result['status'])+' · '+html.escape(result['provider'])+'</p>'
    page+='<p>原始场景 / 重构前目标</p><img src="source.png"><img src="before.png">'
    if result.get('selected'):page+='<h2>选中候选的实际渲染</h2><img src="'+html.escape(result['selected'])+'.png">'
    for row in result['candidates']:
        if row.get('image'):
            page+='<h3>'+html.escape(row['id']+' · '+row['template']+' · '+row['status'])+'</h3><img src="'+html.escape(row['image'])+'">'
    page+='<p>评价分数是几何启发式，不代表人工审美验收。脚本回读基于本地解释器；未做鸿蒙真机验证。未渲染项与受约束的位图尺寸仍需复核。</p>'
    if result.get('package'):page+='<p><a href="'+Path(result['package']).relative_to(root).as_posix()+'">下载 HWT</a> · <a href="'+Path(result['project']).with_name('preview.html').relative_to(root).as_posix()+'">场景预览</a></p>'
    page+='<pre>'+html.escape(json.dumps(result,ensure_ascii=False,indent=2))+'</pre>'
    (root/'report.html').write_text(page,encoding='utf8')
