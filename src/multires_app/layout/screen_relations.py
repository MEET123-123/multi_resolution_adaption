"""Conservative, auditable screen attachments and distributed footer rails."""
from __future__ import annotations
import re


def horizontal_attachment(box, viewport, visual=None):
    """A source edge remains an edge, including small authored crop/shadows.

    Full-width backdrops, waiting pages and ambiguous two-edge spans are not
    one-sided controls. The other axis need not fit entirely inside the canvas.
    """
    w,h=viewport['width'],viewport['height'];x,y,bw,bh=(box[k] for k in ('x','y','w','h'))
    if min(bw,bh)<=0 or x+bw<=0 or x>=w or y+bh<=0 or y>=h:return {}
    inset_tolerance=min(12.,w*.01);crop_tolerance=min(96.,w*.05)
    # A drawer/radial menu can be wider than the screen but expose only one
    # side. Its crop is an authored anchor, not unused canvas to pack onscreen.
    cropped=[(edge,distance) for edge,distance,one_sided in
             [('left',x,x<0<x+bw<w),('right',w-x-bw,0<x<w<x+bw)]
             if one_sided and distance < -crop_tolerance and
             (visual is None or (visual['x']<0 if edge=='left' else visual['x']+visual['w']>w))]
    if len(cropped)==1:
        edge,distance=cropped[0]
        return {'edge':edge,'margin':distance,'marginScale':'resource','priority':'required',
                'provenance':'source content crosses one viewport edge; preserve clipped portion'}
    if bw>=w*.96:return {}
    candidates=[(edge,distance) for edge,distance in [('left',x),('right',w-x-bw)]
                if -crop_tolerance<=distance<=inset_tolerance]
    if len(candidates)!=1:return {}
    edge,distance=candidates[0]
    return {'edge':edge,'margin':max(0.,distance),'priority':'required',
            'provenance':'source horizontal inset within one percent / 12 logical pixels, or crop within five percent'}


def attachment_margin(intent,scale):
    """Crop distances scale with artwork; ordinary insets follow the viewport."""
    return intent['margin']*scale if intent.get('marginScale')=='resource' else intent.get('targetMargin',intent['margin']*scale)


def horizontal_attachment_errors(project,device,layout,tolerance=.1):
    """Check the edge contract after reuse, aesthetics, and cache edits."""
    from multires_app.pipeline.scene_clusters import describe_layout,transformed_box
    layouts={l['state']:l for l in device['layouts']};current=layout;seen=set()
    while 'horizontalEdgeAttachments' not in current.get('adaptationPlan',{}) and current['state'] not in seen:
        seen.add(current['state']);base=current.get('adaptationPlan',{}).get('reusedFrom')
        if base not in layouts:break
        current=layouts[base]
    plan=current.get('adaptationPlan',{})
    intents=dict(plan.get('horizontalEdgeAttachments',{}))
    from multires_app.engine.coordinate_space import source_canvas
    from multires_app.layout.visual_geometry import component_frame_bounds
    # Re-derive the contract from source frames as well. Checking only entries
    # written by the solver cannot catch an edge the solver failed to recognize
    # or a cached/exported plan that accidentally dropped its attachment map.
    if source_canvas(project.get('model',{})) is not None:
        options={**project.get('sourceProfile',{}).get('adaptation',{}).get('anchorLayout',{}),
                 **device.get('profile',{}).get('adaptation',{}).get('anchorLayout',{})}
        source=next((l for l in project['sourceDevice']['layouts'] if l['state']==layout['state']),None)
        if source and options.get('inferScreenRelations',True):
            from multires_app.layout.visual_geometry import component_visuals
            visuals=component_visuals(project,source)
            authored={mid for g in options.get('groups',[]) for mid in g.get('members',[])}
            components={c['id']:c for c in project['components']};vp=project['sourceDevice']['viewport']
            extent=plan.get('safeRegion',{}).get('w',device['viewport']['width'])
            for cid,frame in component_frame_bounds(project,source).items():
                c=components[cid]
                if c.get('layout') or c.get('cornerAttachment') or c.get('pointerEffect') or cid in authored or c['role'] in {'background','gesture-layer','interaction-layer'}:continue
                intent=horizontal_attachment(frame,vp,visuals.get(cid))
                if intent:
                    if intent.get('marginScale')!='resource':intent['targetMargin']=intent['margin']*extent/vp['width']
                    intents[cid]=intent
    if not intents:return []
    region=plan.get('safeRegion') or {'x':0,'w':device['viewport']['width']}
    desc=describe_layout(project,layout);errors=[]
    frames=component_frame_bounds(project,layout)
    # A reused affine has one reference pose. Autonomous motion after that
    # pose must not be mistaken for a new crop/edge anchor on every sample.
    reference=layout;visited=set()
    while reference['state'] not in visited:
        visited.add(reference['state']);base=reference.get('adaptationPlan',{}).get('reusedFrom')
        if base not in layouts:break
        reference=layouts[base]
    reference_frames=component_frame_bounds(project,reference)
    for cid,intent in intents.items():
        if cid not in desc or cid not in layout['transforms']:continue
        box=transformed_box(frames.get(cid,desc[cid]['bounds']),layout['transforms'][cid])
        actual=box['x']-region['x'] if intent['edge']=='left' else region['x']+region['w']-box['x']-box['w']
        expected=attachment_margin(intent,layout['transforms'][cid]['sx'])
        if intent.get('marginScale')=='resource' and project.get('sourceDevice'):
            source=next((l for l in project['sourceDevice']['layouts'] if l['state']==reference['state']),None)
            frame=component_frame_bounds(project,source).get(cid) if source else None
            if frame:
                margin=frame['x'] if intent['edge']=='left' else project['sourceDevice']['viewport']['width']-frame['x']-frame['w']
                expected=margin*layout['transforms'][cid]['sx']
                if cid in reference_frames and cid in frames:
                    origin=reference_frames[cid];now=frames[cid]
                    movement=now['x']-origin['x'] if intent['edge']=='left' else origin['x']+origin['w']-now['x']-now['w']
                    expected+=movement*layout['transforms'][cid]['sx']
        if abs(actual-expected)>tolerance:errors.append({'component':cid,'edge':intent['edge'],'expectedMargin':expected,'actualMargin':actual})
    return errors


def edge_intent(box, viewport):
    w,h=viewport['width'],viewport['height']
    result={}
    horizontal=horizontal_attachment(box,viewport)
    if horizontal:result['x']=horizontal
    if min(box['w'],box['h'])<=0 or box['x']<-.1 or box['y']<-.1 or box['x']+box['w']>w+.1 or box['y']+box['h']>h+.1:return result
    for axis,size,extent,start,end in [('x','w',w,'left','right'),('y','h',h,'top','bottom')]:
        if axis in result:continue
        if box[size]>extent*.6:continue
        margins=[(start,box[axis]),(end,extent-box[axis]-box[size])]
        name,distance=min(margins,key=lambda item:item[1])
        if distance<=min(96.,extent*.05):result[axis]={'edge':name,'margin':max(0.,distance),'provenance':'source margin within five percent of screen edge'}
    return result


def screen_aliases(model):
    aliases={'screen_width':'x','screen_height':'y'}
    for _ in range(8):
        before=len(aliases)
        for name,var in model.get('variables',{}).items():
            expression=var.get('attributes',{}).get('expression','').strip().strip('() ')
            match=re.fullmatch(r'#([A-Za-z_]\w*)',expression)
            if match and match[1] in aliases:aliases[name]=aliases[match[1]]
        if len(aliases)==before:break
    return aliases


def scripted_edges(box, viewport, members, model, aliases=None):
    result=edge_intent(box,viewport);aliases=screen_aliases(model) if aliases is None else aliases
    for uid in members:
        attrs=model.get('nodes',{}).get(uid,{}).get('attributes',{})
        for axis,size,extent,end in [('x','w',viewport['width'],'right'),('y','h',viewport['height'],'bottom')]:
            expression=attrs.get(axis,'').strip().lstrip('(')
            match=re.match(r'#([A-Za-z_]\w*)\s*\)?\s*-',expression)
            margin=extent-box[axis]-box[size]
            if match and aliases.get(match[1])==axis and box[size]<extent*.6 and -.1<=margin<=extent*.2 and box[axis]>=0:
                if result.get(axis,{}).get('priority')!='required':
                    result[axis]={'edge':end,'margin':max(0.,margin),'provenance':'script position subtracts from screen dimension (including direct aliases)'}
    return result


def footer_rails(elements, viewport):
    """Require >=3 peers, a wide source span, alignment and uniform spacing.

    Deliberately excludes authored layouts, overlays and partial/offscreen rows.
    Far-apart buttons need not be spatial neighbours to form a rail.
    """
    w,h=viewport['width'],viewport['height'];candidates=[]
    for e in elements:
        b=e['bbox']
        if e.get('special') or e.get('layout') or e['role'] in {'popup','dialog','overlay'}:continue
        if b['y']<h*.78 or b['x']<0 or b['x']+b['w']>w or b['y']+b['h']>h+.1:continue
        if b['w']>w*.25 or b['h']>h*.15:continue
        candidates.append(e)
    rails=[];used=set()
    for seed in sorted(candidates,key=lambda e:(e['bbox']['y'],e['id'])):
        if seed['id'] in used:continue
        b=seed['bbox'];cy=b['y']+b['h']/2
        row=[e for e in candidates if e['id'] not in used and e['role']==seed['role'] and
             abs(e['bbox']['y']+e['bbox']['h']/2-cy)<=max(3.,min(b['h'],e['bbox']['h'])*.12)]
        row.sort(key=lambda e:e['bbox']['x'])
        if len(row)<3:continue
        bs=[e['bbox'] for e in row];centers=[b['x']+b['w']/2 for b in bs]
        gaps=[r['x']-l['x']-l['w'] for l,r in zip(bs,bs[1:])]
        steps=[r-l for l,r in zip(centers,centers[1:])]
        if min(gaps)<0 or centers[-1]-centers[0]<w*.5:continue
        uniform=lambda values:max(values)-min(values)<=max(3.,sum(values)/len(values)*.06)
        if uniform(steps):mode='equal-centers'
        elif uniform(gaps):mode='equal-gaps'
        else:continue
        equal_cells=mode=='equal-centers' and max(abs(c/w-(i+.5)/len(row)) for i,c in enumerate(centers))<=.035
        optical=[e.get('source_visual_bbox',e['bbox']) for e in row]
        rails.append({'members':[e['id'] for e in row],'mode':'equal-cells' if equal_cells else mode,
                      'horizontalGeometry':'optical',
                      'leftMargin':optical[0]['x'],'rightMargin':w-optical[-1]['x']-optical[-1]['w'],
                      'bottomMargin':h-max(b['y']+b['h'] for b in bs),
                      'centerBottomMargin':h-sum(b['y']+b['h']/2 for b in bs)/len(bs),
                      'provenance':'aligned footer peers with uniform source spacing','confidence':.92})
        used.update(e['id'] for e in row)
    return rails
