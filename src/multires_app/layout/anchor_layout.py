"""Nine-point anchors, nested containers and Kiwi behind the scene plugin API."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from multires_app.layout.adaptive_layout import AdaptiveThemeLayout, classify_change, safe_region, cover, gesture_transform
from multires_app.layout.anchor_ir import build_ir, local_geometry, number
from multires_app.layout.anchor_solver import solve_directions, anchor_position, collision_directions, RequiredConstraintError
from multires_app.pipeline.scene_clusters import transformed_box, box_union


def settings(source,target):
    return {**source.get('adaptation',{}).get('anchorLayout',{}),**target.get('adaptation',{}).get('anchorLayout',{})}


def pack_roots(groups, local, region, gap, max_scale):
    """Evaluate row/column track counts rather than forcing every theme into two columns."""
    if not groups:return {},{'columns':0,'scale':1}
    groups=sorted(groups,key=lambda g:(g['bbox']['y'],g['bbox']['x'],g['id']))
    best=None
    for cols in range(1,len(groups)+1):
        rows=[groups[i:i+cols] for i in range(0,len(groups),cols)]
        widths=[max(local[g['id']]['size']['w'] for g in groups[j::cols]) for j in range(cols)]
        heights=[max(local[g['id']]['size']['h'] for g in row) for row in rows]
        scale=min(max_scale,(region['w']-gap*(cols-1))/sum(widths),(region['h']-gap*(len(rows)-1))/sum(heights))
        score=(scale,-(cols*len(rows)-len(groups)),-abs(cols-len(rows)))
        if scale>0 and (best is None or score>best[0]):best=(score,cols,rows,widths,heights)
    if best is None:raise RequiredConstraintError('container gaps consume the safe area')
    score,cols,rows,widths,heights=best;scale=score[0];desired={}
    top=region['y']+(region['h']-sum(heights)*scale-gap*(len(rows)-1))/2
    for row,items in enumerate(rows):
        left=region['x']+(region['w']-sum(widths)*scale-gap*(cols-1))/2
        for col,g in enumerate(items):
            size=local[g['id']]['size'];w,h=size['w']*scale,size['h']*scale
            desired[g['id']]={'x':left+(widths[col]*scale-w)/2,'y':top+(heights[row]*scale-h)/2,'w':w,'h':h}
            left+=widths[col]*scale+gap
        top+=heights[row]*scale+gap
    return desired,{'columns':cols,'rows':len(rows),'scale':scale,'gap':gap}


class AnchorThemeLayout(AdaptiveThemeLayout):
    name='anchor-theme-layout-v1'
    allow_shape_reuse=False

    def propose_group_boxes(self, ir, desired, source, target, context):
        """Optional soft group preferences; required solver contracts still apply.

        Override this hook for learned/custom placement. Boxes use target logical
        coordinates. Do not change the IR, source state, or fixed transforms.
        """
        return desired

    def finalize_device(self,project,device):
        from multires_app.layout.layout_quality import finalize_anchor_device
        finalize_anchor_device(project,device)
        from multires_app.layout.aesthetic_optimizer import optimize_device
        optimize_device(project,device)

    def describe_adaptation(self,source,target,context):
        decision=classify_change(source,target)
        return {**decision,'layoutPolicy':'nine-grid-anchor-kiwi','coordinateUnit':'theme-engine-logical',
                'reviewRequired':True,'solver':'kiwisolver','dynamicScope':'stable-frame geometry only'}

    def reuse_layout(self,components,source,target,context,mapping):
        # Exact cluster reuse stays exact. No independent model/solver call.
        base=context.get('basePlan',{})
        context['adaptationPlan']={'policy':'nine-grid-anchor-kiwi','route':'reuse',
                                   'safeRegion':base.get('safeRegion',{'x':0,'y':0,'w':context['logicalViewport']['width'],'h':context['logicalViewport']['height']}),
                                   'spatialFlowPolicy':base.get('spatialFlowPolicy'),
                                   'spatialAlignmentPolicy':base.get('spatialAlignmentPolicy'),
                                   'layoutIRRef':base.get('layoutIRRef'),
                                   'horizontalEdgeAttachments':copy.deepcopy(base.get('horizontalEdgeAttachments',{})),
                                   'validation':{'required_ok':base.get('validation',{}).get('required_ok',True),'inherited':True},
                                   'reusedFrom':context.get('layoutRequest',{}).get('baseState')}
        return mapping

    def layout_scene(self,components,source,target,context):
        options=settings(source,target);vp=context['logicalViewport'];region=safe_region(source,target,context)
        fixed=context.get('fixedTransforms',{});known={c['id']:c for c in components}
        # Include fixed descriptors in the IR so explicit groups can straddle the
        # affected/frozen boundary; required equalities keep those leaves still.
        for cid,t in fixed.items():
            if cid not in known:
                c=context.get('componentDefinitions',{}).get(cid,{'id':cid,'role':context.get('allComponentDescriptors',{}).get(cid,{}).get('role','content')})
                desc=context.get('allComponentDescriptors',{}).get(cid,context.get('baseDescriptors',{}).get(cid))
                if desc:known[cid]={**c,'bounds':desc['bounds'],'visible':True}
        source_vp={'width':float(context.get('model',{}).get('root',{}).get('screenWidth',source['resolutionPx']['width']))}
        source_vp['height']=source_vp['width']*source['resolutionPx']['height']/source['resolutionPx']['width']
        ir=build_ir(list(known.values()),source_vp,context,options)
        ir['known_ids']=list(context.get('componentDefinitions',known))+[g['id'] for g in options.get('groups',[])]
        local=local_geometry(ir);groups={g['id']:g for g in ir['groups']};roots=[groups[i] for i in ir['roots']]
        from multires_app.layout.visual_scaling import resource_scale
        sizing=resource_scale(source_vp,region,options,target)
        min_scale=sizing['minScale'];max_scale=sizing['maxScale'];preferred_scale=sizing['scale']
        scale_policy=sizing['policy'];scale_factor=sizing['factor']
        unit=vp['width']/target['resolutionPx']['width'];gap=number(options.get('gapPx',24),'gapPx',0)*unit
        decision=classify_change(source,target);kind=context.get('layoutRequest',{}).get('kind','full-reflow')
        route='anchor' if decision['strategy']=='preserve' else 'container-solver'
        desired={};packing={};frozen_boxes={cid:transformed_box(known[cid]['bounds'],t) for cid,t in fixed.items() if cid in known and min(known[cid]['bounds']['w'],known[cid]['bounds']['h'])>0}
        placement_policy=options.get('placementPolicy','relationships')
        if placement_policy not in {'relationships','pack'}:raise ValueError('placementPolicy must be relationships or pack')
        if route=='container-solver' and placement_policy=='pack' and kind not in {'local-reflow','overlay'}:
            desired,packing=pack_roots(roots,local,region,gap,max_scale)
        for g in roots:
            gid=g['id'];geo=local[gid];members=set(geo['leaves']);ref=g['bbox']
            g['intentional_overlay']=any(known[c]['role'] in {'popup','overlay','dialog'} for c in members)
            if g['anchor'].get('parent_id','screen')!='screen':
                raise ValueError('root anchor parent_id must be screen; nested anchors belong in their parent container')
            authored=g.get('provenance') in ('manual hierarchy','manual layout.group') or any(known[c].get('layout',{}).get('anchor') for c in members)
            g['_solve_point']=g['anchor'].get('self_point',g['anchor'].get('parent_point','center')) if authored or route=='anchor' else 'center'
            base=context.get('baseTransforms',{})
            if kind in {'local-reflow','overlay'} and any(c in base for c in members):
                cid=next(c for c in sorted(members) if c in base);t=base[cid]
                b=known[cid]['bounds'];leaf=geo['leaves'][cid];scale=t['sx']
                old_ref=context.get('baseSourceComponentDescriptors',{}).get(cid,{}).get('bounds')
                new_ref=context.get('sourceComponentDescriptors',{}).get(cid,{}).get('bounds')
                prior=context.get('baseDescriptors',{}).get(cid,{}).get('bounds',b)
                dx=(new_ref['x']-old_ref['x'])*scale if old_ref and new_ref else 0
                dy=(new_ref['y']-old_ref['y'])*scale if old_ref and new_ref else 0
                desired[gid]={'x':prior['x']*scale+t['tx']+dx-leaf['x']*scale,
                              'y':prior['y']*scale+t['ty']+dy-leaf['y']*scale,
                              'w':geo['size']['w']*scale,'h':geo['size']['h']*scale}
            elif gid not in desired or authored:
                policy=g.get('size_policy','uniform_scale')
                if policy=='fixed':scale=1.
                elif policy=='contain':scale=min(max_scale,region['w']/geo['size']['w'],region['h']/geo['size']['h'])
                elif policy=='cover':
                    scale=max(region['w']/geo['size']['w'],region['h']/geo['size']['h']);g['_required_scale']=scale
                else:scale=preferred_scale
                size={k:v*scale for k,v in geo['size'].items()}
                desired[gid]=anchor_position(g['anchor'],size,region,source_vp)
                if not authored and placement_policy=='relationships':
                    # Preserve the source nine-grid anchor and its relative
                    # offset on each axis; resize resources uniformly.
                    desired[gid]['x']=region['x']+(ref['x']+ref['w']/2)/source_vp['width']*region['w']-size['w']/2
                    desired[gid]['y']=region['y']+(ref['y']+ref['h']/2)/source_vp['height']*region['h']-size['h']/2
                    g['_solve_point']='center';g['_size_weight']=30.
                    # A hidden/offscreen panel must remain outside the screen.
                    # Normalizing its center would pull it into a short screen.
                    for axis,size,extent in [('x','w',source_vp['width']),('y','h',source_vp['height'])]:
                        start=ref[axis];end=start+ref[size]
                        if end<=0:desired[gid][axis]=region[axis]+end*region[size]/extent-desired[gid][size]
                        elif start>=extent:desired[gid][axis]=region[axis]+region[size]+(start-extent)*region[size]/extent
                        elif start<0:desired[gid][axis]=region[axis]+start/ref[size]*desired[gid][size]
                        elif end>extent:desired[gid][axis]=region[axis]+region[size]-(extent-start)/ref[size]*desired[gid][size]
            g['preferredScale']=desired[gid]['w']/geo['size']['w']
            waiting=[]
            for cid,leaf in geo['leaves'].items():
                witness=context.get('waitingRegions',{}).get(cid)
                if witness:
                    origin=known[cid]['bounds'];b=witness['rect']
                    waiting.append({**witness,'rect':{'x':leaf['x']+b['x']-origin['x'],'y':leaf['y']+b['y']-origin['y'],'w':b['w'],'h':b['h']}})
            g['_waiting_regions']=waiting
            visual_parts=[]
            for cid,leaf in geo['leaves'].items():
                if g.get('centerReferenceMembers') and cid not in g['centerReferenceMembers']:continue
                visual=context.get('stableVisualBounds',{}).get(cid,known[cid]['bounds']);origin=known[cid]['bounds']
                visual_parts.append({'x':leaf['x']+visual['x']-origin['x'],'y':leaf['y']+visual['y']-origin['y'],'w':visual['w'],'h':visual['h']})
            g['_center_bounds']=box_union(visual_parts)
            if len(members)==1:
                cid=next(iter(members));element=next((e for e in ir['elements'] if e['id']==cid),None)
                if element and element.get('horizontalAttachment'):
                    frame=element['current_edge_bbox'];origin=known[cid]['bounds'];leaf=geo['leaves'][cid]
                    g['_horizontal_bounds']={'x':leaf['x']+frame['x']-origin['x'],'w':frame['w']}
            if g.get('cornerAttachment'):
                parts=[]
                for cid,leaf in geo['leaves'].items():
                    seen=set();frames=[]
                    for e in context.get('stableResources',[]):
                        if e['component']!=cid or e['id'] in seen:continue
                        node=context.get('model',{}).get('nodes',{}).get(e['id'],{})
                        if node.get('tag') in {'Button','Group'}:continue
                        seen.add(e['id']);frames.append(e['rect'])
                    frame=context.get('cornerTargetFrameBounds',{}).get(cid) or (box_union(frames) if frames else known[cid]['bounds']);origin=known[cid]['bounds']
                    parts.append({'x':leaf['x']+frame['x']-origin['x'],'y':leaf['y']+frame['y']-origin['y'],'w':frame['w'],'h':frame['h']})
                g['_corner_bounds']=box_union(parts)
            locks={**context.get('nativeGlyphResourceScales',{}),**context.get('sharedResourceScales',{})};locked_scales={locks[cid] for cid in members if cid in locks}
            if locked_scales:
                if len(locked_scales)!=1:raise RequiredConstraintError('glyph resource scales disagree inside one group: '+gid)
                scale=next(iter(locked_scales));g['_fixed_resource_scale']=scale
                g['preferredScale']=scale
                desired[gid]['x']+=(desired[gid]['w']-geo['size']['w']*scale)/2
                desired[gid]['y']+=(desired[gid]['h']-geo['size']['h']*scale)/2
                desired[gid]['w']=geo['size']['w']*scale;desired[gid]['h']=geo['size']['h']*scale
            for axis,intent in g.get('screenAttachment',{}).items():
                size='w' if axis=='x' else 'h';extent=source_vp['width' if axis=='x' else 'height']
                if g.get('cornerAttachment'):intent.pop('targetMargin',None)
                elif intent.get('marginScale')!='resource':intent['targetMargin']=intent['margin']*region[size]/extent
            # Screen attachments survive a change from anchor to track packing.
            # Margins follow the corresponding viewport axis, while resources
            # use the shared balanced uniform scale.
            if kind not in {'local-reflow','overlay'}:
                scale=desired[gid]['w']/geo['size']['w']
                for axis,intent in g.get('screenAttachment',{}).items():
                    size='w' if axis=='x' else 'h';origin=region[axis];extent=region[size]
                    from multires_app.layout.screen_relations import attachment_margin
                    margin=attachment_margin(intent,scale)
                    desired[gid][axis]=(origin+margin if intent['edge'] in {'left','top'} else
                                        origin+extent-desired[gid][size]-margin)
                for axis in g.get('centerAxes',[]):
                    size='w' if axis=='x' else 'h'
                    visual=g['_center_bounds']
                    desired[gid][axis]=region[axis]+region[size]/2-(visual[axis]+visual[size]/2)*scale
            # Constrain settled resources, including hit areas and negative
            # child offsets. The first media frame may be an entry pose.
            g['_stable_bounds']=[]
            for e in context.get('stableResources',[]):
                cid=e['component']
                if cid not in members:continue
                b=e['rect'];origin=known[cid]['bounds'];leaf=geo['leaves'][cid]
                g['_stable_bounds'].append({'x':leaf['x']+b['x']-origin['x'],
                    'y':leaf['y']+b['y']-origin['y'],'w':b['w'],'h':b['h']})
            if 'stableResources' in context:
                g['allow_crop']=(not g['_stable_bounds'] or ref['x']<0 or ref['y']<0 or
                    ref['x']+ref['w']>source_vp['width'] or ref['y']+ref['h']>source_vp['height'])
            else:
                g.pop('_stable_bounds',None)
                g['allow_crop']=(ref['x']+ref['w']<=0 or ref['y']+ref['h']<=0 or
                                 ref['x']>=source_vp['width'] or ref['y']>=source_vp['height'] or
                                 g.get('screenAttachment',{}).get('x',{}).get('marginScale')=='resource')
        for rail in ir.get('screenDistributions',[]):
            ids=rail['roots'];n=len(ids)
            rail['targetLeftMargin']=rail['leftMargin']*region['w']/source_vp['width']
            rail['targetRightMargin']=rail['rightMargin']*region['w']/source_vp['width']
            rail['targetBottomMargin']=rail['bottomMargin']*region['h']/source_vp['height']
            rail['referenceHalfHeight']=rail['centerBottomMargin']-rail['bottomMargin']
            # Reflow the rail spacing, preserving each icon's aspect ratio.
            scale=min(desired[gid]['w']/local[gid]['size']['w'] for gid in ids)
            widths=[local[gid]['size']['w']*scale for gid in ids]
            left=region['x']+rail['targetLeftMargin'];right=region['x']+region['w']-rail['targetRightMargin']
            first=left+widths[0]/2;last=right-widths[-1]/2
            gap=(right-left-sum(widths))/(n-1);cursor=left
            for i,gid in enumerate(ids):
                q=desired[gid];q['w']=widths[i];q['h']=local[gid]['size']['h']*scale
                if rail['mode']=='equal-cells':q['x']=region['x']+region['w']*(i+.5)/n-q['w']/2
                elif rail['mode']=='equal-centers':q['x']=first+(last-first)*i/(n-1)-q['w']/2
                else:q['x']=cursor;cursor+=q['w']+gap
                q['y']=region['y']+region['h']-rail['targetBottomMargin']-rail['referenceHalfHeight']*scale-q['h']/2
        # Low-confidence positions remain suggestions; source-disjoint geometry
        # selects collision directions before linear solving.
        from multires_app.layout.spatial_flow import infer_flows
        from multires_app.layout.spatial_alignment import infer_alignments
        ir['spatialFlows']=[]
        ir['spatialAlignments']=[]
        alignment_samples=[]
        if options.get('inferScreenRelations',True) and not options.get('groups'):
            samples=context.get('flowSamples',[(context.get('sourceStableVisualBounds',{}),context.get('stableVisualBounds',{}))])
            for sample_index,(src_visuals,dst_visuals) in enumerate(samples):
                elements=[{**e,'source_visual_bbox':src_visuals.get(e['id'],e['source_visual_bbox']),
                           'current_visual_bbox':dst_visuals.get(e['id'],e['current_visual_bbox'])} for e in ir['elements']
                          if not src_visuals or e['id'] in src_visuals and e['id'] in dst_visuals]
                for flow in infer_flows(elements,source_vp,region,preferred_scale):
                    flow['id']='flow:'+str(sample_index)+':'+flow['id'];ir['spatialFlows'].append(flow)
                for alignment in infer_alignments(elements,source_vp):
                    alignment['id']='alignment:'+str(sample_index)+':'+alignment['id'];ir['spatialAlignments'].append(alignment)
                alignment_samples.append({e['id']:e['source_visual_bbox'] for e in elements})
            # A frame at which two moving objects merely cross is not a design
            # alignment. Only keep lines that persist whenever both are visible.
            def persistent(r):
                axis=r['axis'];size='w' if axis=='x' else 'h';f=r['factor']
                for sample in alignment_samples:
                    a,b=sample.get(r['first']),sample.get(r['last'])
                    if a and b and abs(a[axis]+f*a[size]-b[axis]-f*b[size])>.01:return False
                return True
            ir['spatialAlignments']=[r for r in ir['spatialAlignments'] if persistent(r)]
        if options.get('inferScreenRelations',True) and not options.get('groups'):
            ir['spatialFlows'].extend(context.get('leafFlows',[]))
        desired=self.propose_group_boxes(ir,desired,source,target,context)
        separations=collision_directions(roots,desired,0)
        if options.get('inferScreenRelations',True) and not options.get('groups'):
            # No source-disjoint relation can mean an intentional overlay.
            # An empty set must not revive the legacy bbox collision guesses.
            separations=[]
        separations=[c for c in separations if not all(set(local[g]['leaves'])<=set(frozen_boxes) for g in c['targets'])]
        try:boxes,validation=solve_directions(ir,local,region,desired,frozen_boxes,separations,min_scale,max_scale)
        except RequiredConstraintError as exc:
            raise RequiredConstraintError(str(exc)+'; state='+ir['scene_id']+'; request='+kind,exc.constraint_id,exc.provenance) from exc
        output={}
        for e in ir['elements']:
            cid=e['id'];b=e['current_bbox']
            if cid in fixed:continue
            if e['special']:
                if e.get('pointerEffect'):
                    output[cid]={'sx':1.,'sy':1.,'tx':0.,'ty':0.}
                elif e['role']=='background':
                    backdrop=context.get('backgroundBounds',{}).get(cid,b)
                    # Preserve the authored crop/focal position independently
                    # on each axis (centered x may coexist with top-aligned y).
                    ref=context.get('sourceBackgroundBounds',{}).get(cid,e['bbox'])
                    from multires_app.layout.visual_geometry import background_cover
                    output[cid]=background_cover(backdrop,ref,vp,source_vp)
                elif e['role']=='gesture-layer':output[cid]=gesture_transform(b,vp)
                elif e['role']=='interaction-layer' and not e.get('layout'):
                    # An invisible unbound hit area describes a screen region,
                    # not a bitmap. Uniform resource scaling can make its tall
                    # rectangle cover unrelated buttons on a short canvas.
                    ref=e['bbox'];sx=region['w']/source_vp['width'];sy=region['h']/source_vp['height']
                    output[cid]={'sx':sx,'sy':sy,'tx':region['x'],'ty':region['y']}
                else:
                    s=preferred_scale;ref=e['bbox']
                    visual=e.get('source_visual_bbox',ref);current_visual=e.get('current_visual_bbox',b)
                    def outside(q):
                        return q['x']+q['w']<=0 or q['y']+q['h']<=0 or q['x']>=source_vp['width'] or q['y']>=source_vp['height']
                    # A first-frame reference can still contain the closed
                    # drawer pose after the human has opened it. Place its
                    # settled artwork, keeping the original timeline intact.
                    if outside(ref) and not outside(visual):ref=visual;b=current_visual
                    x=region['x']+(ref['x']+ref['w']/2)/source_vp['width']*region['w']-b['w']*s/2
                    y=region['y']+(ref['y']+ref['h']/2)/source_vp['height']*region['h']-b['h']*s/2
                    from multires_app.layout.screen_relations import scripted_edges
                    centered=[]
                    for axis,intent in scripted_edges(ref,source_vp,e.get('member_ids',[]),context.get('model',{})).items():
                        size='w' if axis=='x' else 'h'
                        margin=intent['margin']*region[size]/source_vp['width' if axis=='x' else 'height']
                        pos=region[axis]+margin if intent['edge'] in {'left','top'} else region[axis]+region[size]-b[size]*s-margin
                        if axis=='x':x=pos
                        else:y=pos
                    visual=e.get('source_visual_bbox',ref);current_visual=e.get('current_visual_bbox',b)
                    for axis,size,extent in [('x','w',source_vp['width']),('y','h',source_vp['height'])]:
                        if axis=='x' and e.get('horizontalAttachment'):continue
                        if abs(visual[axis]+visual[size]/2-extent/2)<=max(2.,extent*.015):
                            centered.append(axis)
                            pos=region[axis]+region[size]/2-(current_visual[axis]+current_visual[size]/2-b[axis])*s
                            if axis=='x':x=pos
                            else:y=pos
                    output[cid]={'sx':s,'sy':s,'tx':x-b['x']*s,'ty':y-b['y']*s}
                    active=[e['rect'] for e in context.get('stableResources',[]) if e['component']==cid]
                    if active:
                        envelope=box_union(active);s=min(s,region['w']/envelope['w'],region['h']/envelope['h'])
                        t=output[cid];tx=x-b['x']*s;ty=y-b['y']*s
                        if 'x' in centered:tx=region['x']+region['w']/2-(current_visual['x']+current_visual['w']/2)*s
                        if 'y' in centered:ty=region['y']+region['h']/2-(current_visual['y']+current_visual['h']/2)*s
                        tx=max(region['x']-envelope['x']*s,min(tx,region['x']+region['w']-(envelope['x']+envelope['w'])*s))
                        ty=max(region['y']-envelope['y']*s,min(ty,region['y']+region['h']-(envelope['y']+envelope['h'])*s))
                        output[cid]={'sx':s,'sy':s,'tx':tx,'ty':ty}
                    if e.get('horizontalAttachment'):
                        intent=e['horizontalAttachment'];t=output[cid]
                        margin=intent['margin']*(t['sx'] if intent.get('marginScale')=='resource' else region['w']/source_vp['width'])
                        frame=e['current_edge_bbox']
                        edge_x=region['x']+margin if intent['edge']=='left' else region['x']+region['w']-margin-frame['w']*t['sx']
                        t['tx']=edge_x-frame['x']*t['sx']
                    from multires_app.layout.offscreen_geometry import retain_offscreen
                    output[cid]=retain_offscreen(visual,current_visual,output[cid],source_vp,region)
                continue
            q=boxes[cid];sx=q['w']/b['w'];sy=q['h']/b['h']
            if abs(sx-sy)>1e-6:raise RequiredConstraintError('atomic aspect ratio changed: '+cid)
            output[cid]={'sx':sx,'sy':sy,'tx':q['x']-b['x']*sx,'ty':q['y']-b['y']*sy}
        # Unresolved/zero-size native glyphs still need an explicit identity
        # transform for exact cache/frozen invariants; they have no solver bbox.
        for c in components:
            if c['id'] not in fixed:output.setdefault(c['id'],dict(context.get('baseTransforms',{}).get(c['id'],{'sx':1.,'sy':1.,'tx':0.,'ty':0.})))
        canonical=copy.deepcopy(ir)
        for e in canonical['elements']:e.pop('current_bbox',None)
        digest=hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:20]
        context.setdefault('anchorIRStore',{})[digest]=canonical
        context['adaptationPlan']={'policy':'nine-grid-anchor-kiwi','route':route,'requestKind':kind,
                                   'safeRegion':region,'packing':packing,'scale':packing.get('scale'),
                                   'resourceScalePolicy':scale_policy,'resourceScaleFactor':scale_factor,'preferredResourceScale':preferred_scale,
                                   'visualSizing':sizing,
                                   'placementPolicy':placement_policy,
                                   'screenDistributions':ir.get('screenDistributions',[]),
                                   'horizontalEdgeAttachments':{e['id']:{**e['horizontalAttachment'],
                                       **({} if e['horizontalAttachment'].get('marginScale')=='resource' else
                                          {'targetMargin':e['horizontalAttachment']['margin']*region['w']/source_vp['width']})}
                                       for e in ir['elements'] if e.get('horizontalAttachment')},
                                   'spatialFlows':ir.get('spatialFlows',[]),
                                   'spatialFlowPolicy':'source-disjoint-adaptive-gaps',
                                   'spatialAlignmentPolicy':'source-visible-shared-lines-v1',
                                   'spatialAlignments':list({(r['first'],r['last'],r['axis'],r['factor']):
                                       {k:r[k] for k in ('first','last','axis','factor','scope','priority','relaxation') if k in r} for r in ir.get('spatialAlignments',[]) if 'scope' in r}.values()),
                                   'layoutIRRef':digest,'validation':validation,
                                   'groups':[{'id':g['id'],'members':g['members'],'container':g['container'],'anchor':g['anchor'],
                                              'sourceBounds':g['bbox'],'targetBounds':boxes[g['id']],'confidence':g['confidence'],
                                              'screenAttachment':g.get('screenAttachment',{}),'centerAxes':g.get('centerAxes',[]),
                                              'cornerAttachment':g.get('cornerAttachment'),
                                              'cornerLocalBounds':g.get('_corner_bounds'),
                                              'horizontalLocalBounds':g.get('_horizontal_bounds'),
                                              'sourceVisualBounds':g.get('sourceVisualBounds'),'centerLocalBounds':g.get('_center_bounds'),
                                              'centerRequiredAxes':g.get('centerRequiredAxes',[]),
                                              'centerReferenceMembers':g.get('centerReferenceMembers'),
                                              'preferredScale':g.get('preferredScale')} for g in ir['groups']],
                                   'frozenIds':sorted(fixed),'reviewRequired':True}
        if 'layoutProposal' in context:
            context['adaptationPlan']['layoutProposal']=copy.deepcopy(context['layoutProposal'])
        return output
