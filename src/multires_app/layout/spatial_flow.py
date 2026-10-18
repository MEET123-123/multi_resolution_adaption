"""Source-disjoint visual blocks retain their order and adaptive axis gaps."""
from multires_app.pipeline.scene_clusters import overlap


def infer_flows(elements,source_viewport,region,scale):
    eligible=[];result=[]
    for e in elements:
        b=e.get('source_visual_bbox',e['bbox'])
        if e.get('special') or min(b['w'],b['h'])<=0:continue
        if b['x']+b['w']<=0 or b['y']+b['h']<=0 or b['x']>=source_viewport['width'] or b['y']>=source_viewport['height']:continue
        eligible.append((e,b))
    for i,(a,aa) in enumerate(eligible):
        for b,bb in eligible[:i]:
            if overlap(aa,bb)>1e-6:continue  # authored overlays stay overlays
            choices=[]
            for axis,size,cross,other,extent in [('y','h','x','w','height'),('x','w','y','h','width')]:
                first,last=(a,b) if aa[axis]<=bb[axis] else (b,a)
                ab,zb=(aa,bb) if first is a else (bb,aa)
                gap=zb[axis]-ab[axis]-ab[size]
                if gap<-.01:continue
                cross_overlap=max(0,min(ab[cross]+ab[other],zb[cross]+zb[other])-max(ab[cross],zb[cross]))/max(.001,min(ab[other],zb[other]))
                choices.append((cross_overlap,-gap/source_viewport[extent],axis,size,extent,first,last,max(0,gap)))
            if not choices:continue
            a_corner=bool(a.get('cornerShortcut') or a.get('cornerAttachment')) and aa['y']>=source_viewport['height']*.78
            b_corner=bool(b.get('cornerShortcut') or b.get('cornerAttachment')) and bb['y']>=source_viewport['height']*.78
            zone_relation=False
            if a_corner!=b_corner:
                footer,content=(aa,bb) if a_corner else (bb,aa)
                if content['y']+content['h']<=footer['y']+.01:
                    # A header and a corner icon occupy different vertical
                    # regions even if their horizontal projections miss.
                    # Do not pull the clock sideways to retain an x gap.
                    choices=[c for c in choices if c[2]=='y'] or choices
                    zone_relation=True
            _,_,axis,size,extent,first,last,gap=max(choices,key=lambda c:(c[0],c[1]))
            ratio=region[size]/source_viewport[extent]
            preferred=gap*ratio
            minimum=min(preferred,max(.5,gap*min(ratio,scale)*.25)) if gap>0 else 0.
            result.append({'id':'flow:'+str(len(result)),'first':first['id'],'last':last['id'],'axis':axis,
                'sourceGap':gap,'preferredGap':preferred,'minimumGap':minimum,
                'preferenceWeight':max(.1,100/(1+gap/max(1,source_viewport[extent]*.03))**2),
                'separationOnly':zone_relation,
                'firstRect':first['current_visual_bbox'],'lastRect':last['current_visual_bbox'],
                'provenance':'source visual blocks are disjoint; retain axis order and height/width-relative gap'})
    return result


def flow_errors(relations,transforms,tolerance=.01):
    from multires_app.pipeline.scene_clusters import transformed_box
    errors=[]
    for r in relations:
        if r['first'] not in transforms or r['last'] not in transforms:continue
        a=transformed_box(r['firstRect'],transforms[r['first']]);b=transformed_box(r['lastRect'],transforms[r['last']])
        axis=r['axis'];size='w' if axis=='x' else 'h';gap=b[axis]-a[axis]-a[size]
        if gap<r['minimumGap']-tolerance:errors.append({**r,'actualGap':gap})
    return errors


def insertion_blockers(relations,current_visuals,transforms,affected,fixed,region,min_scale=.03):
    """Classify a local insertion before locking peers at the canvas edge."""
    from multires_app.pipeline.scene_clusters import transformed_box
    released=set();fixed=set(fixed)
    for cid in affected:
        if cid not in current_visuals:continue
        for axis,size in [('x','w'),('y','h')]:
            low=region[axis];high=low+region[size];bounds=[]
            for f in relations:
                if f['axis']!=axis:continue
                if f['last']==cid and f['first'] in fixed and f['first'] in transforms:
                    b=transformed_box(f['firstRect'],transforms[f['first']]);value=b[axis]+b[size]+f['minimumGap']
                    low=max(low,value);bounds.append(f['first'])
                elif f['first']==cid and f['last'] in fixed and f['last'] in transforms:
                    b=transformed_box(f['lastRect'],transforms[f['last']]);value=b[axis]-f['minimumGap']
                    high=min(high,value);bounds.append(f['last'])
            if low+current_visuals[cid][size]*min_scale>high+.01:released.update(bounds)
    return released


def layout_flows(project,device,layout):
    from multires_app.layout.visual_geometry import component_visuals
    from multires_app.layout.adaptive_layout import safe_region
    from multires_app.layout.visual_scaling import resource_scale
    sources={l['state']:l for l in project.get('sourceDevice',{}).get('layouts',[])}
    source=component_visuals(project,sources.get(layout['state'],{}));current=component_visuals(project,layout)
    options={**project['sourceProfile'].get('adaptation',{}).get('anchorLayout',{}),**device['profile'].get('adaptation',{}).get('anchorLayout',{})}
    if not options.get('inferScreenRelations',True) or options.get('groups'):return []
    region=safe_region(project['sourceProfile'],device['profile'],{'logicalViewport':device['viewport']})
    vp=project['sourceDevice']['viewport'];scale=resource_scale(vp,region,options,device['profile'])['scale']
    elements=[{'id':c['id'],'bbox':source[c['id']],'source_visual_bbox':source[c['id']],
               'current_visual_bbox':current[c['id']],'cornerShortcut':bool(c.get('cornerAttachment')),'special':c['role'] in {'background','gesture-layer','decoration','interaction-layer'}}
              for c in project['components'] if c['id'] in source and c['id'] in current]
    return infer_flows(elements,vp,region,scale)
