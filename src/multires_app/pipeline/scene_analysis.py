"""Reviewable analysis artifacts shared by CLI and hosted conversion jobs."""
import json
from collections import Counter

from multires_app.interaction.interaction_ir import compile_interactions
from multires_app.engine.engine_capabilities import capability_report


def attach_analysis(project,out):
    model=project['model'];graph=project['graph'];owners=project['nodeComponents']
    ir=compile_interactions(model,model.get('inputs',{}).get('sourceEngine','unspecified'),graph)
    bindings=model.get('layoutBindings',{'schemaVersion':1,'bindings':[]})
    violations=[];checked=0
    for binding in bindings['bindings']:
        for visual in binding['anchors']:
            checked+=1
            if owners.get(binding['button']) is None or owners.get(binding['button'])!=owners.get(visual):
                violations.append({'button':binding['button'],'visual':visual,'reason':'physical binding crosses components'})
    compatibility={target:capability_report(model,target) for target in ('preserve-source','harmonyos-4','harmonyos-5','harmonyos-6')}
    masked={r['mask'] for r in model.get('compositionBindings',[])};raster_overlays=set()
    source=project.get('sourceDevice',{});vp=source.get('viewport',{})
    for layout in source.get('layouts',[]):
        for row in layout['rows']:
            uid=project['nodeOrder'][row[0]]
            if uid in masked or row[6]<0:continue
            meta=project['assets'][project['assetOrder'][row[6]]].get('alphaGeometry',{})
            if row[3]*row[4]>=vp.get('width',1)*vp.get('height',1)*.8 and meta and meta['opaqueFraction']<.95 and meta['transparentFraction']<.95:
                raster_overlays.add(uid)
    devices={}
    for device in project['devices']:
        layouts=device['layouts'];counts=Counter()
        for layout in layouts:
            for issue in layout.get('adaptationReview',[]):counts[issue.get('reason','unknown')]+=1
        plans=[]
        for l in layouts:
            plan=l.get('adaptationPlan',{})
            if plan.get('policy')=='nine-grid-anchor-kiwi':
                validation=plan.get('validation',{})
                plans.append({'state':l['state'],**{k:plan.get(k) for k in ('policy','route','generator','layoutIRRef','reusedFrom','reviewRequired')},
                              'validation':{'required_ok':validation.get('required_ok'),'backend':validation.get('backend'),
                                            'softViolationCount':len(validation.get('soft_violations',[]))}})
            else:plans.append({'state':l['state'],**plan})
        devices[device['profile']['id']]={'states':len(layouts),'reviewReasons':dict(counts),
                                         'quality':device.get('layoutQuality',{}),'plans':plans}
    analysis={'schemaVersion':1,'kind':'scene-pipeline-analysis','components':len(project['components']),
              'interaction':{'variables':len(ir['variables']),'rules':len(ir['rules']),'dependencyEdges':len(ir['edges']),
                             'symbolicRulesPreserved':True,'sampledStates':len(graph['states']),'coverage':graph['coverage']},
              'compositionBindings':model.get('compositionBindings',[]),
              'rasterOverlayReview':[{'node':uid,'reason':'Full-canvas raster overlay may contain text/arrows or masks tied to source coordinates; artwork review required after reflow.'} for uid in sorted(raster_overlays)],
              'bindings':{'statuses':dict(Counter(b['status'] for b in bindings['bindings'])),
                          'checkedAnchors':checked,'violations':violations},'devices':devices,
              'nativeDeviceTest':False,'artifacts':{'interactions':'interaction-ir.json','bindings':'interaction-bindings.json','compatibility':'engine-compatibility.json'}}
    project['sceneAnalysis']=analysis
    from multires_app.layout.anchor_tools import write_anchor_artifacts
    write_anchor_artifacts(project,out)
    for filename,document in [('interaction-ir.json',ir),('interaction-bindings.json',bindings),('engine-compatibility.json',compatibility),('scene-analysis.json',analysis)]:
        (out/filename).write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
    if violations:raise ValueError('Physical bindings could not be kept in one component; inspect interaction-bindings.json')
    return analysis
