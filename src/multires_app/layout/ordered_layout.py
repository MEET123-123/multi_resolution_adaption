"""Source-ordered, two-column packing with one scale for every content block."""
from __future__ import annotations

import math

from multires_app.pipeline.scene_clusters import LayoutConflict, overlap, transformed_box


def reference_components(components, references):
    result=[]
    for component in components:
        ref=references.get(component['id'],{}).get('bounds',component.get('sourceBounds',component['bounds']))
        # Source anchors determine order and relative positions. Current extents
        # still determine fit: responsive expressions may change intrinsic size.
        bounds={**ref,'w':component['bounds']['w'],'h':component['bounds']['h']}
        result.append({**component,'bounds':bounds,'currentBounds':component['bounds']})
    return result


def ordered_blocks(blocks):
    def key(block):
        hints=[c.get('layout',{}).get('order') for c in block['components'] if c.get('layout',{}).get('order') is not None]
        if any(not isinstance(n,(int,float)) or not math.isfinite(n) for n in hints):
            raise ValueError('layout.order must be finite')
        b=block['bounds']
        return (0,min(hints),b['y'],b['x']) if hints else (1,b['y'],b['x'],min(c['id'] for c in block['components']))
    return sorted(blocks,key=key)


def two_column_plan(blocks, region, column_gap, row_gap, flow='row-major', max_scale=1.):
    """Rows keep input order; different block sizes never get different scales."""
    blocks=ordered_blocks(blocks)
    if not blocks:return {'policy':'ordered-two-columns','blocks':[],'scale':1.,'columns':2,'flow':flow}
    columns=2 if len(blocks)>1 else 1
    cw=(region['w']-column_gap*(columns-1))/columns
    if cw<=0:raise LayoutConflict('column gap consumes the safe width')
    max_width=max(max(1,b['bounds']['w']) for b in blocks)
    scale=min(max_scale,cw/max_width)
    if flow=='row-major':
        rows=[blocks[i:i+columns] for i in range(0,len(blocks),columns)]
        heights=[max(max(1,b['bounds']['h']) for b in row) for row in rows]
        usable=region['h']-row_gap*(len(rows)-1)
        if usable<=0:raise LayoutConflict('row gaps consume the safe height; reduce rowGapPx/gapRatio')
        scale=min(scale,usable/sum(heights))
        y=region['y']+(region['h']-(sum(heights)*scale+row_gap*(len(rows)-1)))/2
        placements=[]
        for row_index,(row,height) in enumerate(zip(rows,heights)):
            for column,block in enumerate(row):
                frame={'x':region['x']+column*(cw+column_gap),'y':y,'w':cw,'h':height*scale}
                placements.append((block,row_index,column,frame))
            y+=height*scale+row_gap
    elif flow=='column-major':
        split=math.ceil(len(blocks)/columns)
        lanes=[blocks[i*split:(i+1)*split] for i in range(columns)]
        for lane in lanes:
            usable=region['h']-row_gap*(len(lane)-1)
            if usable<=0:raise LayoutConflict('row gaps consume the safe height; reduce rowGapPx/gapRatio')
            scale=min(scale,usable/sum(max(1,b['bounds']['h']) for b in lane))
        placements=[]
        for column,lane in enumerate(lanes):
            height=sum(max(1,b['bounds']['h']) for b in lane)*scale+row_gap*(len(lane)-1)
            y=region['y']+(region['h']-height)/2
            for row_index,block in enumerate(lane):
                frame={'x':region['x']+column*(cw+column_gap),'y':y,'w':cw,'h':block['bounds']['h']*scale}
                placements.append((block,row_index,column,frame));y+=frame['h']+row_gap
    else:raise ValueError('adaptation.flow must be row-major or column-major')
    if not math.isfinite(scale) or scale<=0:raise LayoutConflict('no room for ordered columns')
    plan={'policy':'ordered-two-columns','flow':flow,'columns':columns,'scale':scale,'region':dict(region),
          'columnGap':column_gap,'rowGap':row_gap,'blocks':[]}
    for block,row,column,frame in placements:
        b=block['bounds']
        x=frame['x']+(frame['w']-b['w']*scale)/2;y=frame['y']+(frame['h']-b['h']*scale)/2
        plan['blocks'].append({'members':[c['id'] for c in block['components']], 'row':row,'column':column,
                               'sourceBounds':dict(b),'frame':frame,'targetBounds':{'x':x,'y':y,'w':b['w']*scale,'h':b['h']*scale}})
    return plan


def apply_plan(blocks, plan):
    members={c['id']:c for block in blocks for c in block['components']}
    output={};scale=plan['scale']
    for block in plan['blocks']:
        source=block['sourceBounds'];destination=block['targetBounds']
        for cid in block['members']:
            comp=members[cid];ref=comp['bounds'];current=comp.get('currentBounds',ref)
            output[cid]={'sx':scale,'sy':scale,
                         'tx':destination['x']-source['x']*scale+(ref['x']-current['x'])*scale,
                         'ty':destination['y']-source['y']*scale+(ref['y']-current['y'])*scale}
    return output


def local_from_source(components, context, region):
    """Continue the preceding layout, carrying authored source-coordinate deltas.

    New independent content requires repacking. Existing content keeps its
    scale; it is never stretched to fill a predecessor's bounding rectangle.
    """
    previous=context.get('baseDescriptors',{});transforms=context.get('baseTransforms',{})
    source=context.get('sourceComponentDescriptors',{});source_before=context.get('baseSourceComponentDescriptors',{})
    output={};placed=[]
    for cid,transform in context.get('fixedTransforms',{}).items():
        desc=context.get('allComponentDescriptors',{}).get(cid)
        if desc and desc['role'] not in {'background','gesture-layer'}:
            placed.append((cid,transformed_box(desc['bounds'],transform)))
    for comp in components:
        cid=comp['id']
        if cid not in previous or cid not in transforms:
            raise LayoutConflict('新增独立组件需要按原顺序重新排列两列')
        base=transforms[cid];old=previous[cid]['bounds'];current=comp['bounds']
        old_ref=source_before.get(cid,{}).get('bounds',old)
        ref=source.get(cid,{}).get('bounds',comp.get('sourceBounds',current))
        transform={**base,'tx':base['tx']+((ref['x']-old_ref['x'])-(current['x']-old['x']))*base['sx'],
                   'ty':base['ty']+((ref['y']-old_ref['y'])-(current['y']-old['y']))*base['sy']}
        box=transformed_box(current,transform)
        if box['x']<region['x']-.5 or box['y']<region['y']-.5 or box['x']+box['w']>region['x']+region['w']+.5 or box['y']+box['h']>region['y']+region['h']+.5:
            raise LayoutConflict('原布局槽位容纳不了变化后的组件，需要重新排列两列')
        for other,other_box in placed:
            area=min(box['w']*box['h'],other_box['w']*other_box['h'])
            other_ref=source.get(other,{}).get('bounds')
            if area>0 and overlap(box,other_box)/area>.05 and (not other_ref or overlap(ref,other_ref)==0):
                raise LayoutConflict('局部调整产生新的组件重叠，需要重新排列两列')
        output[cid]=transform
        placed.append((cid,box))
    return output
