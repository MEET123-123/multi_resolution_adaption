"""Offline export/import of reviewable layout JSON. No model API is called."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import uuid
from pathlib import Path

from multires_app.layout.adaptive_layout import safe_region
from multires_app.pipeline.scene_clusters import describe_layout, transformed_box, overlap, same_geometry


def geometry_digest(target,state,viewport,components):
    # Match JavaScript explicitly at half-micro-unit boundaries (Python format
    # otherwise uses ties-to-even while JS toFixed can choose the other side).
    def fixed(value):return f'{math.floor(value*1_000_000+.5)/1_000_000:.6f}'
    lines=['layout-refinement-v1',target,state,fixed(viewport['width'])+','+fixed(viewport['height'])]
    for c in sorted(components,key=lambda c:c['id']):
        b=c['bbox'];lines.append(c['id']+'\t'+','.join(fixed(b[k]) for k in ('x','y','w','h')))
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()


def export_request(project,target,state):
    device=next((d for d in project['devices'] if d['profile']['id']==target),None)
    if device is None:raise ValueError('未知目标设备：'+str(target))
    layout=next((l for l in device['layouts'] if l['state']==state),None)
    if layout is None:raise ValueError('未知交互状态：'+str(state))
    descriptions=describe_layout(project,layout);defs={c['id']:c for c in project['components']}
    components=[]
    for cid,desc in descriptions.items():
        c=defs[cid];t=layout['transforms'].get(cid,{'sx':1,'sy':1,'tx':0,'ty':0})
        components.append({'id':cid,'name':c['name'],'role':c['role'],'semanticRole':c.get('semanticRole'),
                           'group':c.get('layout',{}).get('group'),'locked':c['role'] in {'background','gesture-layer'},
                           'bbox':transformed_box(desc['bounds'],t),'sourceBBox':desc['bounds']})
    region=safe_region(project['sourceProfile'],device['profile'],{'logicalViewport':device['viewport']})
    return {'schemaVersion':1,'kind':'layout-refinement-request','target':target,'state':state,
            'viewport':device['viewport'],'resolutionPx':device['profile']['resolutionPx'],'coordinateUnit':'theme-engine-logical',
            'baseDigest':geometry_digest(target,state,device['viewport'],components),'components':components,
            'constraints':{'safeRegion':region,'preserveAspectRatio':True,'lockedRoles':['background','gesture-layer'],
                           'moveWholeComponents':True,'changeGeometryOnly':True},
            'instructions':'只返回 layout-refinement-proposal JSON，保留 schemaVersion/target/state/baseDigest；components 仅包含需修改项的 id 和 bbox{x,y,w,h}。只调整几何，保持等比、同组关系、热区和安全区；不要输出代码、资源路径或命令。优先左侧/上方留白中的时间日期、对齐与适当间距。',
            'responseTemplate':{'schemaVersion':1,'kind':'layout-refinement-proposal','target':target,'state':state,
                                'baseDigest':geometry_digest(target,state,device['viewport'],components),'applyScope':'matching-components','components':[]}}


def validate_geometry_change(descriptions,before,after,changed,locked,region,state,groups=None):
    """Check every affected scene, including components absent in the edited scene."""
    old_boxes={cid:transformed_box(desc['bounds'],before.get(cid,{'sx':1,'sy':1,'tx':0,'ty':0})) for cid,desc in descriptions.items()}
    new_boxes={cid:transformed_box(desc['bounds'],after.get(cid,{'sx':1,'sy':1,'tx':0,'ty':0})) for cid,desc in descriptions.items()}
    for cid in changed:
        if cid not in new_boxes:continue
        b=new_boxes[cid]
        if b['x']<region['x']-.5 or b['y']<region['y']-.5 or b['x']+b['w']>region['x']+region['w']+.5 or b['y']+b['h']>region['y']+region['h']+.5:
            raise ValueError('状态 '+state+' 中 bbox 超出安全区：'+cid)
    ids=list(new_boxes)
    for i,cid in enumerate(ids):
        if cid in locked:continue
        a=new_boxes[cid]
        for other in ids[:i]:
            if other in locked or not ({cid,other}&changed):continue
            b=new_boxes[other];area=min(a['w']*a['h'],b['w']*b['h'])
            if area>0 and overlap(a,b)/area>.05 and overlap(old_boxes[cid],old_boxes[other])==0:
                raise ValueError('状态 '+state+' 中建议产生新的组件重叠：'+cid+' / '+other)
    for name,members in (groups or {}).items():
        visible=[cid for cid in members if cid in descriptions and cid in after]
        if not (set(visible)&changed):continue
        matrices=[after[cid] for cid in visible]
        if any(any(abs(t[k]-matrices[0][k])>1e-4 for k in t) for t in matrices[1:]):
            raise ValueError('状态 '+state+' 中同组图层无法保持共同变换：'+str(name))


def validate_and_apply(project,proposal):
    if not isinstance(proposal,dict):raise ValueError('布局建议必须为 JSON 对象')
    if proposal.get('schemaVersion')!=1 or proposal.get('kind')!='layout-refinement-proposal':raise ValueError('布局建议 schema/kind 无效')
    target,state=proposal.get('target'),proposal.get('state')
    scope=proposal.get('applyScope','matching-components')
    if scope not in {'state','matching-components'}:raise ValueError('applyScope 必须为 state 或 matching-components')
    request=export_request(project,target,state)
    if proposal.get('baseDigest')!=request['baseDigest']:raise ValueError('布局已变化，请重新导出请求或重新匹配参考图')
    if not isinstance(proposal.get('components'),list) or len(proposal['components'])>512:raise ValueError('components 必须为不超过 512 项的数组')
    current={c['id']:c for c in request['components']};seen=set();changes={};region=request['constraints']['safeRegion']
    for item in proposal['components']:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str):raise ValueError('组件必须包含字符串 id')
        cid=item['id']
        if cid in seen:raise ValueError('重复组件：'+cid)
        seen.add(cid)
        if cid not in current:raise ValueError('未知组件：'+cid)
        comp=current[cid]
        if comp['locked']:raise ValueError('背景/全屏手势层已锁定：'+cid)
        box=item.get('bbox',{})
        if not isinstance(box,dict) or set(box)!={'x','y','w','h'} or not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in box.values()):
            raise ValueError('bbox 必须包含有限数值 x/y/w/h')
        if min(box['w'],box['h'])<=0:raise ValueError('bbox 尺寸必须为正数')
        if box['x']<region['x']-.5 or box['y']<region['y']-.5 or box['x']+box['w']>region['x']+region['w']+.5 or box['y']+box['h']>region['y']+region['h']+.5:
            raise ValueError('bbox 超出安全区：'+cid)
        original=comp['sourceBBox']
        if min(original['w'],original['h'])<=0:raise ValueError('源组件尺寸为空：'+cid)
        sx,sy=box['w']/original['w'],box['h']/original['h']
        if abs(sx-sy)>max(1e-5,max(sx,sy)*.005):raise ValueError('bbox 改变了组件宽高比：'+cid)
        scale=min(sx,sy)
        actual={**box,'w':original['w']*scale,'h':original['h']*scale}
        changes[cid]={'bbox':actual,'transform':{'sx':scale,'sy':scale,'tx':box['x']-original['x']*scale,'ty':box['y']-original['y']*scale}}
    # Explicit authored groups must use one common affine transform.
    groups={}
    for c in current.values():
        if c.get('group'):groups.setdefault(c['group'],[]).append(c['id'])
    for name,members in groups.items():
        if not (set(members)&seen):continue
        if not set(members)<=seen:raise ValueError('同组组件必须一起修改：'+str(name))
        matrices=[changes[cid]['transform'] for cid in members]
        if any(any(abs(t[k]-matrices[0][k])>1e-4 for k in t) for t in matrices[1:]):raise ValueError('同组图层必须共用等比变换：'+str(name))
    warnings=[]
    result=copy.deepcopy(project)
    device=next(d for d in result['devices'] if d['profile']['id']==target)
    layout=next(l for l in device['layouts'] if l['state']==state)
    for cid,change in changes.items():layout['transforms'][cid]=change['transform']
    report={'target':target,'state':state,'baseDigest':request['baseDigest'],'changedComponents':len(changes),
            'warnings':warnings,'origin':proposal.get('origin','offline-json'),'nativeDeviceTest':False}
    if proposal.get('origin')=='expected-image-matcher':
        report['matching']=copy.deepcopy(proposal.get('matching',{}))
        report['reference']=copy.deepcopy(proposal.get('reference',{}))
        report['artifacts']=copy.deepcopy(proposal.get('artifacts',{}))
    layout['manualRefinement']=report
    report['applyScope']=scope
    if scope=='matching-components':
        original_device=next(d for d in project['devices'] if d['profile']['id']==target)
        original_layout=next(l for l in original_device['layouts'] if l['state']==state)
        baseline=describe_layout(project,original_layout)
        anchors=device.setdefault('manualComponentAnchors',{})
        for cid,change in changes.items():anchors[cid]={'geometry':baseline[cid]['geometry'],'bounds':baseline[cid]['bounds'],'transform':change['transform']}
        for other_layout in device['layouts']:
            desc=describe_layout(result,other_layout)
            for cid,change in changes.items():
                if cid in desc and same_geometry(desc[cid],baseline[cid]):other_layout['transforms'][cid]=dict(change['transform'])
    original_device=next(d for d in project['devices'] if d['profile']['id']==target)
    originals={l['state']:l for l in original_device['layouts']}
    propagated=0
    all_groups={}
    for c in project['components']:
        if c.get('layout',{}).get('group'):all_groups.setdefault(c['layout']['group'],[]).append(c['id'])
    for candidate in device['layouts']:
        before=originals[candidate['state']]
        changed={cid for cid,t in candidate['transforms'].items() if t!=before['transforms'].get(cid)}
        if not changed:continue
        desc=describe_layout(result,candidate)
        validate_geometry_change(desc,before['transforms'],candidate['transforms'],changed,{cid for cid,d in desc.items() if d['role'] in {'background','gesture-layer'}},region,candidate['state'],all_groups)
        propagated+=1
    report['affectedStates']=propagated
    prior=layout.get('adaptationPlan',{})
    layout['adaptationPlan']={'policy':'reviewed-layout-json','refinement':report,
                              'previousPolicy':prior.get('policy'),
                              'anchorPlan':copy.deepcopy(prior.get('anchorPlan',prior))}
    return result,report


def write_back(project_path,proposal):
    """Stage compiler outputs first, then keep a recoverable source JSON backup."""
    from multires_app.pipeline.scene_pipeline import export_script
    from multires_app.preview.scene_previewer import build_scene_preview
    path=Path(project_path);old=path.read_bytes();project=json.loads(old)
    updated,report=validate_and_apply(project,proposal)
    token=uuid.uuid4().hex[:10];directory=path.parent
    staged_script=directory/('refinement-'+token+'.xml');staged_preview=directory/('refinement-'+token+'.html')
    staged_project=directory/('refinement-'+token+'.json')
    destinations={};committed=[]
    resource_root=(directory/'resource-assets').resolve()
    existing_resources={p.resolve() for p in resource_root.rglob('*') if p.is_file()} if resource_root.exists() else set()
    import tempfile
    analysis_stage=tempfile.TemporaryDirectory(dir=directory,prefix='analysis-stage-')
    try:
        export_script(updated,proposal['target'],staged_script)
        from multires_app.pipeline.scene_analysis import attach_analysis
        attach_analysis(updated,Path(analysis_stage.name))
        build_scene_preview(updated,staged_preview)
        staged_project.write_text(json.dumps({k:v for k,v in updated.items() if not k.startswith('_')},ensure_ascii=False,separators=(',',':')),encoding='utf-8')
        backup=directory/(path.stem+'.layout-backup-'+token+'.json');backup.write_bytes(old)
        scripts=directory/'scripts';scripts.mkdir(exist_ok=True)
        writes=[(staged_project,path),(staged_script,scripts/(proposal['target']+'.xml')),
                (staged_script.with_suffix('.patch.json'),scripts/(proposal['target']+'.patch.json')),
                (staged_script.with_suffix('.resources.json'),scripts/(proposal['target']+'.resources.json')),(staged_preview,directory/'preview.html')]
        writes.extend((p,directory/p.name) for p in Path(analysis_stage.name).iterdir())
        for _,destination in writes:destinations[destination]=destination.read_bytes() if destination.exists() else None
        for temporary,destination in writes:
            temporary.replace(destination);committed.append(destination)
        report['backup']=str(backup.resolve())
        (directory/'layout-refinement-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception:
        for destination in reversed(committed):
            if destinations[destination] is None:destination.unlink(missing_ok=True)
            else:destination.write_bytes(destinations[destination])
        if resource_root.exists():
            for resource in resource_root.rglob('*'):
                resolved=resource.resolve()
                if resource.is_file() and resolved.is_relative_to(resource_root) and resolved not in existing_resources:resource.unlink()
        raise
    finally:
        analysis_stage.cleanup()
        for temporary in (staged_script,staged_script.with_suffix('.patch.json'),staged_script.with_suffix('.resources.json'),staged_preview,staged_project):
            if temporary.exists():temporary.unlink()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    export=sub.add_parser('export');export.add_argument('project',type=Path);export.add_argument('--target',required=True);export.add_argument('--state',default='state-000');export.add_argument('--out',type=Path,required=True)
    apply=sub.add_parser('apply');apply.add_argument('project',type=Path);apply.add_argument('--proposal',type=Path,required=True)
    match=sub.add_parser('match');match.add_argument('project',type=Path);match.add_argument('--target',required=True);match.add_argument('--state',default='state-000');match.add_argument('--image',type=Path,required=True);match.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='apply':result=write_back(args.project,json.loads(args.proposal.read_text(encoding='utf-8')))
    else:
        project=json.loads(args.project.read_text(encoding='utf-8'))
        if args.command=='export':result=export_request(project,args.target,args.state)
        else:
            from multires_app.preview.reference_matching import match_reference
            result=match_reference(project,args.target,args.state,args.image.read_bytes(),args.project.parent)
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        result={'output':str(args.out.resolve()),'kind':result['kind']}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
