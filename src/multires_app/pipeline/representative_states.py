"""Bounded evidence sampling over recorded, executable human journeys."""


def select_states(project,device,limit=6):
    """Cover visible components, native guards and action types, initial first."""
    from multires_app.pipeline.scene_pipeline import state_guard
    if limit<1:raise ValueError('audit state limit must be positive')
    states={s['id']:s for s in project['graph']['states']};features={}
    for layout in device['layouts']:
        sid=layout['state'];s=states[sid]
        f={('guard',state_guard(project,s,device)),('kind',layout.get('layoutRequest',{}).get('kind','unknown'))}
        f.update(('component',project['nodeComponents'].get(project['nodeOrder'][r[0]])) for r in layout['rows'] if r[5]>0 and min(r[3:5])>0)
        for step in s.get('trace',[]):
            a=step.get('humanAction',{});f.add(('action',a.get('kind'),a.get('gesture')))
        features[sid]=f
    selected=[device['layouts'][0]['state']];covered=set(features[selected[0]])
    while len(selected)<min(limit,len(features)):
        remaining=[s for s in features if s not in selected]
        best=max(remaining,key=lambda sid:(sum(3 if f[0]=='component' else 1 for f in features[sid]-covered),len(states[sid].get('trace',[])),-remaining.index(sid)))
        if not features[best]-covered and len(selected)>1:break
        selected.append(best);covered.update(features[best])
    return {'states':selected,'availableStates':len(features),'selectedStates':len(selected),
        'coveredFeatures':len(covered),'totalFeatures':len(set().union(*features.values())),
        'policy':'initial + greedy visible-component/native-guard/human-action coverage',
        'exhaustive':len(selected)==len(features)}
