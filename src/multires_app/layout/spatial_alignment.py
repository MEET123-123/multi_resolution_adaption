"""Alignment evidence uses resolved canvas coordinates, never local XML x/y."""
from multires_app.pipeline.scene_clusters import transformed_box


def infer_alignments(elements, viewport, tolerance=.01):
    eligible=[]; result=[]
    for e in elements:
        b=e.get('source_visual_bbox',e['bbox'])
        if e.get('special') or min(b['w'],b['h'])<=0:continue
        if b['x']<0 or b['y']<0 or b['x']+b['w']>viewport['width']+.01 or b['y']+b['h']>viewport['height']+.01:continue
        eligible.append((e,b))
    for i,(a,aa) in enumerate(eligible):
        for b,bb in eligible[:i]:
            for axis,size in [('x','w'),('y','h')]:
                # Opposite corner shortcuts belong to a screen edge frame.
                # A panel above them can coincidentally share an x edge in
                # the source; that is not a common alignment container.
                ac=bool(a.get('cornerShortcut') or a.get('cornerAttachment'))
                bc=bool(b.get('cornerShortcut') or b.get('cornerAttachment'))
                if axis=='x' and ac!=bc:
                    footer,content=(aa,bb) if ac else (bb,aa)
                    if content['y']+content['h']<=footer['y']+.01:continue
                # One line per pair/axis avoids accidentally requiring equal
                # sizes when the source happens to have identical rectangles.
                candidates=[(abs(aa[axis]+f*aa[size]-bb[axis]-f*bb[size]),rank,f)
                            for rank,f in enumerate((.5,0.,1.))]
                delta,_,factor=min(candidates)
                if delta>tolerance:continue
                result.append({'id':'alignment:'+str(len(result)),
                    'first':a['id'],'last':b['id'],'axis':axis,'factor':factor,
                    'firstRect':a['current_visual_bbox'],'lastRect':b['current_visual_bbox'],
                    'sourceResidual':delta,
                    'provenance':'source resolved visible edges/centers share a canvas coordinate'})
    return result


def alignment_errors(relations, transforms, tolerance=.1):
    errors=[]
    for r in relations:
        if r['first'] not in transforms or r['last'] not in transforms:continue
        a=transformed_box(r['firstRect'],transforms[r['first']]);b=transformed_box(r['lastRect'],transforms[r['last']])
        axis=r['axis'];size='w' if axis=='x' else 'h';factor=r['factor']
        delta=abs(a[axis]+factor*a[size]-b[axis]-factor*b[size])
        if delta>tolerance:errors.append({**r,'actualResidual':delta})
    return errors


def layout_alignments(project,device,layout,include_container=False):
    """Read the active contract, including exact native-family reuse."""
    layouts={l['state']:l for l in device['layouts']};plan=layout.get('adaptationPlan',{});seen=set()
    while plan.get('reusedFrom') and plan['reusedFrom'] not in seen:
        seen.add(plan['reusedFrom']);base=layouts.get(plan['reusedFrom'])
        if not base:break
        plan=base.get('adaptationPlan',{})
    contract=plan.get('spatialAlignments',[])
    if not contract:return []
    from multires_app.layout.visual_geometry import component_visuals
    source=next((l for l in project.get('sourceDevice',{}).get('layouts',[]) if l['state']==layout['state']),None)
    if source is None:return []
    src=component_visuals(project,source);dst=component_visuals(project,layout)
    result=[];seen=set();vp=project['sourceDevice']['viewport']
    for r in contract:
        if r.get('scope')!='shared-line' and not include_container:continue
        a,b=r['first'],r['last'];axis=r['axis'];size='w' if axis=='x' else 'h';f=r['factor'];key=(a,b,axis,f)
        if key in seen or a not in src or b not in src or a not in dst or b not in dst:continue
        seen.add(key)
        if any(min(q['w'],q['h'])<=0 or q['x']<0 or q['y']<0 or q['x']+q['w']>vp['width']+.01 or q['y']+q['h']>vp['height']+.01 for q in (src[a],src[b])):continue
        delta=abs(src[a][axis]+f*src[a][size]-src[b][axis]-f*src[b][size])
        if delta>.01:continue
        result.append({**r,'firstRect':dst[a],'lastRect':dst[b],'sourceResidual':delta})
    return result
