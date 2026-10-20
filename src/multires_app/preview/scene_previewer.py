"""Source-phone and adapted-device comparison with concrete gesture timelines."""
import json
from pathlib import Path


def preview_layout(layout):
    """Full solver evidence stays in the downloadable anchor analysis."""
    result=dict(layout)
    if 'layoutRequest' in result:result['layoutRequest']={k:v for k,v in result['layoutRequest'].items() if k not in {'correspondence','sharedComponents'}}
    if 'adaptationPlan' in result:
        plan=dict(result['adaptationPlan']);plan.pop('spatialFlows',None)
        if 'spatialAlignments' in plan:plan['spatialAlignmentCount']=len(plan.pop('spatialAlignments'))
        validation=dict(plan.get('validation',{}));issues=validation.get('soft_violations',[])
        validation['softViolationCount']=validation.get('softViolationCount',len(issues));validation['soft_violations']=issues[:12]
        if len(issues)>12:validation['fullDiagnostics']='anchor-layout.json'
        plan['validation']=validation;result['adaptationPlan']=plan
    return result


def scene_preview_html(project):
    if 'sourceDevice' not in project:
        from multires_app.pipeline.source_comparison import build_source_device
        project['sourceDevice']=build_source_device(project)
    nodes=project['model']['nodes']
    devices=[{**d,'layouts':[preview_layout(l) for l in d['layouts']]} for d in project['devices']]
    payload={'devices':devices,'sourceDevice':project['sourceDevice'],
             'nodes':[nodes[uid] for uid in project['nodeOrder']],'components':project['components'],
             'nodeComponents':project['nodeComponents'],'assets':[{**project['assets'][uid],'path':uid} for uid in project['assetOrder']],
             'states':[{k:v for k,v in s.items() if k in {'id','label','depth','controlValues'}} for s in project['graph']['states']],
             'transitions':project['graph']['transitions'],'coverage':project['graph']['coverage'],'diagnostics':project['diagnostics'],
             'limitations':project['limitations'],'humanActions':project['graph'].get('semantics',{}).get('actions',[]),
             'pointerPolicy':project['model'].get('inputs',{}).get('interaction',{}).get('hitPolicy','specific-controls-before-global-gestures'),
             'bindings':project['model'].get('layoutBindings',{}).get('bindings',[]),
             'sceneAnalysis':project.get('sceneAnalysis',{})}
    data=json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
    script='const DATA='+data+';\n'+Path(__file__).with_name('scene_viewer.js').read_text(encoding='utf-8')+'\n'+Path(__file__).with_name('layout_refinement_ui.js').read_text(encoding='utf-8')+'\n'+Path(__file__).with_name('anchor_layout_ui.js').read_text(encoding='utf-8')
    page=Path(__file__).with_name('scene_viewer.html').read_text(encoding='utf-8').replace('__SCRIPT__',script)
    return page


def build_scene_preview(project,out:Path):
    out.write_text(scene_preview_html(project),encoding='utf-8')
