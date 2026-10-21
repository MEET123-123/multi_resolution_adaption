"""Review final visible resources, after optimization and waiting-page repair."""
from multires_app.pipeline.scene_clusters import transformed_box,overlap,box_union


def review_final(project,device,layout):
    from multires_app.layout.canvas_bounds import stable_resources,violations
    from multires_app.layout.visual_scaling import resource_scale
    from multires_app.layout.adaptive_layout import safe_region
    from multires_app.layout.anchor_layout import settings
    source=next(l for l in project['sourceDevice']['layouts'] if l['state']==layout['state'])
    region=safe_region(project['sourceProfile'],device['profile'],{'logicalViewport':device['viewport']})
    options=settings(project['sourceProfile'],device['profile'])
    source_vp=project['sourceDevice']['viewport']
    # Readability is relative to a complete fitted source scene, rather than
    # an absolute 30% that incorrectly flags legitimate landscape reductions.
    contain=min(region['w']/source_vp['width'],region['h']/source_vp['height'])
    floor=max(resource_scale(source_vp,region,options,device['profile'])['minScale'],min(.3,.65*contain))
    definitions={c['id']:c for c in project['components']};mapping=layout['transforms'];issues=[]
    resources=stable_resources(project,layout,source)
    for e in violations(resources,mapping,region,tolerance=.5):
        issues.append({'component':e['component'],'node':e['id'],'reason':'component exceeds safe area','target':e['target']})
    active={e['component'] for e in resources}
    for cid in active:
        t=mapping.get(cid)
        minimum=max(floor,float(definitions[cid].get('layout',{}).get('minScale',floor)))
        if t and min(t['sx'],t['sy'])<minimum-1e-6:
            issues.append({'component':cid,'reason':'component below minimum scale','minimum':minimum})
    # Component envelopes may overlap through transparent corners and empty
    # spaces. Check actual source-disjoint leaf footprints, not those envelopes.
    from multires_app.layout.visual_geometry import visual_rect
    def rows(l):
        out={}
        for r in l['rows']:
            uid=project['nodeOrder'][r[0]];cid=project['nodeComponents'].get(uid)
            if r[5]<=0 or cid not in active or project['model']['nodes'][uid]['tag'] in {'Group','Button'}:continue
            b=visual_rect(project,r)
            if b and min(b['w'],b['h'])>0:out.setdefault(uid,[]).append((cid,b))
        return out
    src=rows(source);current=rows(layout);pairs=set();items=[]
    for uid,entries in current.items():
        for i,(cid,b) in enumerate(entries):
            if uid not in src or i>=len(src[uid]) or cid not in mapping:continue
            original=src[uid][i][1]
            sv=project['sourceDevice']['viewport']
            if overlap(original,{'x':0,'y':0,'w':sv['width'],'h':sv['height']})<=0:continue
            items.append((uid,cid,original,transformed_box(b,mapping[cid])))
    for i,(uid,cid,original,q) in enumerate(items):
        if definitions[cid]['role'] in {'background','gesture-layer','decoration','interaction-layer','popup','overlay','dialog'}:continue
        for vid,other,prior,p in items[:i]:
            if cid==other or definitions[other]['role'] in {'background','gesture-layer','decoration','interaction-layer','popup','overlay','dialog'}:continue
            pair=tuple(sorted((cid,other)))
            if pair in pairs or overlap(original,prior)>.01:continue
            area=min(q['w']*q['h'],p['w']*p['h'])
            if area>0 and overlap(q,p)>.05*area:
                pairs.add(pair);issues.append({'component':cid,'other':other,'nodes':[uid,vid],'reason':'new overlap after layout'})
    return issues
