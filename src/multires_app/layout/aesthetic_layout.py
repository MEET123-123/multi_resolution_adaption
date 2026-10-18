"""Deterministic whitespace placement: hard geometry constraints + soft scores.

Scores are design heuristics, not a trained or universal measure of beauty.
"""
from __future__ import annotations

import math
import re

from multires_app.pipeline.scene_clusters import box_union, free_regions, overlap, transformed_box

TIME_TAGS={'Time','Date','DateTime'}
TIME_INPUTS={'hour','hour12','hour24','minute','second','date','month','year','day_of_week'}


def datetime_roots(model):
    """Find compact clock/date subtrees, including authored bitmap digit clocks."""
    nodes=model['nodes'];variables=model['variables']
    dependencies={name:set(re.findall(r'#([\w]+)',var['attributes'].get('expression',''))) for name,var in variables.items()}
    timed=set(TIME_INPUTS)
    while True:
        expanded=timed|{name for name,refs in dependencies.items() if refs&timed}
        if expanded==timed:break
        timed=expanded
    def timed_node(node):
        a=node['attributes']
        if node['tag'] in TIME_TAGS:
            return node['tag']!='DateTime' or bool(re.search('[yMdHhms]',a.get('format','MM/dd EEEE')))
        if node['tag'] not in {'Image','ImageSeries','Text','Number'}:return False
        expressions=' '.join(a.get(k,'') for k in ('srcExp','srcid','string','textExp'))
        refs=set(re.findall(r'#([\w]+)',expressions))
        return bool(refs&timed)
    summaries={}
    def summarize(uid):
        if uid in summaries:return summaries[uid]
        node=nodes[uid];clock=int(timed_node(node));visual=int(node['tag'] in {'Image','ImageSeries','Text','Number',*TIME_TAGS})
        for child in node['children']:
            c,v=summarize(child);clock+=c;visual+=v
        summaries[uid]=(clock,visual)
        return clock,visual
    for uid in nodes:summarize(uid)
    def animated_parent(uid):
        parent=nodes[uid]['parent']
        while parent:
            if any(nodes[c]['tag']=='PositionAnimation' for c in nodes[parent]['children']):return True
            parent=nodes[parent]['parent']
        return False
    candidates=set()
    for uid,node in nodes.items():
        c,v=summaries[uid]
        direct=sum(timed_node(nodes[child]) for child in node['children'])
        if node['tag']=='Group' and c>=2 and direct and c/max(1,v)>=.5 and not animated_parent(uid):candidates.add(uid)
    # Keep the outer compact group so digits, separators, date and hit areas stay together.
    roots=set()
    for uid in candidates:
        parent=nodes[uid]['parent'];nested=False
        while parent:
            if parent in candidates:nested=True;break
            parent=nodes[parent]['parent']
        if not nested:roots.add(uid)
    for uid,node in nodes.items():
        if node['tag'] not in TIME_TAGS or animated_parent(uid):continue
        parent=node['parent'];owned=False
        while parent:
            if parent in roots:owned=True;break
            parent=nodes[parent]['parent']
        # An isolated weekday label in a weather card belongs to that card.
        if not owned and (node['tag']=='Time' or node['parent'] is None or nodes[node['parent']]['tag']=='Lockscreen'):
            roots.add(uid)
    return roots


def is_datetime(component):
    return component.get('layout',{}).get('semantic',component.get('semanticRole')) in {'time','date','datetime'}


def options_for(source,target):
    raw={**source.get('adaptation',{}).get('aesthetics',{}),**target.get('adaptation',{}).get('aesthetics',{})}
    result={'enabled':True,'minWhitespaceRatio':.06,'clearancePx':24.,'minGain':.03,
            'weights':{'preference':3.,'whitespace':1.,'alignment':1.,'balance':1.,'stability':.5},**raw}
    if not isinstance(result['enabled'],bool):raise ValueError('aesthetics.enabled must be boolean')
    for key in ('minWhitespaceRatio','clearancePx','minGain'):
        result[key]=float(result[key])
        if not math.isfinite(result[key]) or result[key]<0:raise ValueError('invalid aesthetics.'+key)
    if result['minWhitespaceRatio']>1:raise ValueError('minWhitespaceRatio must be <= 1')
    weights={**{'preference':3.,'whitespace':1.,'alignment':1.,'balance':1.,'stability':.5},**result['weights']}
    for key,value in weights.items():
        if key not in {'preference','whitespace','alignment','balance','stability'} or not math.isfinite(float(value)) or float(value)<0:
            raise ValueError('invalid aesthetics weight: '+str(key))
    result['weights']={k:float(v) for k,v in weights.items()}
    return result


def inside(box,region):
    return box['x']>=region['x']-1e-6 and box['y']>=region['y']-1e-6 and box['x']+box['w']<=region['x']+region['w']+1e-6 and box['y']+box['h']<=region['y']+region['h']+1e-6


def padding(box,amount):
    return {'x':box['x']-amount,'y':box['y']-amount,'w':box['w']+2*amount,'h':box['h']+2*amount}


def whitespace_regions(region,blockers,width,height):
    """Sweep a clock-width strip; avoid fragmentation from many small decorations."""
    right=region['x']+region['w'];bottom=region['y']+region['h']
    xs={region['x'],right-width,region['x']+region['w']/3-width/2}
    xs.update(b['x']+b['w'] for b in blockers)
    xs.update(b['x']-width for b in blockers)
    xs=sorted(x for x in xs if x>=region['x'] and x+width<=right)
    if len(xs)>72:xs=[xs[round(i*(len(xs)-1)/71)] for i in range(72)]
    results={}
    for x in xs:
        intervals=sorted((max(region['y'],b['y']),min(bottom,b['y']+b['h'])) for b in blockers
                         if b['x']<x+width and b['x']+b['w']>x and b['y']<bottom and b['y']+b['h']>region['y'])
        cursor=region['y'];gaps=[]
        for top,end in intervals:
            if top-cursor>=height:gaps.append((cursor,top))
            cursor=max(cursor,end)
        if bottom-cursor>=height:gaps.append((cursor,bottom))
        for top,end in gaps:
            edge=min([right]+[b['x'] for b in blockers if b['x']>=x+width and b['y']<end and b['y']+b['h']>top])
            box={'x':x,'y':top,'w':edge-x,'h':end-top}
            results[tuple(round(box[k],6) for k in ('x','y','w','h'))]=box
    return list(results.values())


def place_datetime(components,mapping,source,target,context,region):
    settings=options_for(source,target)
    if not settings['enabled'] or target['resolutionPx']['width']<=target['resolutionPx']['height']:
        return mapping,{'status':'disabled-or-portrait'}
    selected=[c for c in components if is_datetime(c) and c['id'] in mapping and c.get('visible',True)]
    if not selected:return mapping,{'status':'no-independent-datetime-component'}
    selected_ids={c['id'] for c in selected}
    # A manual mixed art/clock group is indivisible. Do not detach its clock.
    for block in context.get('adaptationPlan',{}).get('blocks',[]):
        members=set(block['members'])
        if members&selected_ids and members-selected_ids:
            return mapping,{'status':'datetime-is-part-of-mixed-atomic-group'}
    original=box_union(transformed_box(c['bounds'],mapping[c['id']]) for c in selected)
    if original['w']<=0 or original['h']<=0:return mapping,{'status':'empty-datetime-bounds'}
    comp_map={c['id']:c for c in components};occupied=[]
    for element in context.get('elements',[]):
        cid=element.get('component');comp=comp_map.get(cid)
        if not comp or cid in selected_ids or comp['role'] in {'background','gesture-layer','decoration','interaction-layer'} or element.get('alpha',1)<.1:continue
        if element['id'] in comp.get('layoutFollowers',[]) or element['tag']=='Button':continue
        occupied.append(transformed_box(element['rect'],mapping.get(cid,{'sx':1,'sy':1,'tx':0,'ty':0})))
    if not occupied:
        occupied=[transformed_box(c['bounds'],mapping[c['id']]) for c in components
                  if c['id'] in mapping and c['id'] not in selected_ids and c['role'] not in {'background','gesture-layer','decoration','interaction-layer'} and c.get('visible',True)]
    clearance=settings['clearancePx']*context['logicalViewport']['width']/target['resolutionPx']['width']
    blockers=[padding(b,clearance) for b in occupied]
    free=free_regions({'width':region['w'],'height':region['h']},
                      [{**b,'x':b['x']-region['x'],'y':b['y']-region['y']} for b in blockers])
    free=[{**b,'x':b['x']+region['x'],'y':b['y']+region['y']} for b in free]
    free+=whitespace_regions(region,blockers,original['w'],original['h'])
    total=region['w']*region['h'];diagonal=math.hypot(region['w'],region['h'])
    occupied_area=sum(max(0,b['w']*b['h']) for b in occupied)
    ink_x=sum((b['x']+b['w']/2)*b['w']*b['h'] for b in occupied)
    ink_y=sum((b['y']+b['h']/2)*b['w']*b['h'] for b in occupied)
    previous=context.get('basePlan',{}).get('datetimePlacement',{}).get('targetBounds',original)
    def score(box,free_area):
        if not inside(box,region) or any(overlap(box,b)>1e-6 for b in blockers):return None
        cx,cy=box['x']+box['w']/2,box['y']+box['h']/2
        xn=(cx-region['x'])/region['w'];yn=(cy-region['y'])/region['h']
        preference=.65*(1-xn)+.35*(1-yn)
        lines_x=[region['x'],region['x']+region['w']/3]+[b['x'] for b in occupied]+[b['x']+b['w'] for b in occupied]
        lines_y=[region['y'],region['y']+region['h']/3]+[b['y'] for b in occupied]
        alignment=1-min(1,(min(abs(box['x']-x) for x in lines_x)/region['w']+min(abs(box['y']-y) for y in lines_y)/region['h']))
        area=box['w']*box['h'];mass=max(1,occupied_area+area)
        balance=1-min(1,math.hypot((ink_x+cx*area)/mass-(region['x']+region['w']/2),
                                  (ink_y+cy*area)/mass-(region['y']+region['h']/2))/max(1,diagonal/2))
        stability=1-min(1,math.hypot(box['x']-previous['x'],box['y']-previous['y'])/max(1,diagonal))
        terms={'preference':preference,'whitespace':min(1,free_area/max(1,total)),
               'alignment':alignment,'balance':balance,'stability':stability}
        return {'score':sum(settings['weights'][k]*v for k,v in terms.items()),'terms':terms}
    old_space=max([r['w']*r['h'] for r in free if inside(original,r)]+[0])
    baseline=score(original,old_space);candidates=[]
    for r in free:
        if r['w']<original['w'] or r['h']<original['h'] or r['w']*r['h']/total<settings['minWhitespaceRatio']:continue
        for fx,fy in ((0,0),(.5,0),(0,.5),(.5,.5),(1,0),(0,1)):
            box={**original,'x':r['x']+(r['w']-original['w'])*fx,'y':r['y']+(r['h']-original['h'])*fy}
            left=(box['x']+box['w']/2)<region['x']+region['w']*.5
            top=(box['y']+box['h']/2)<region['y']+region['h']*.34
            if not (left or top):continue
            scored=score(box,r['w']*r['h'])
            if scored:candidates.append({**scored,'bounds':box,'freeRegion':r})
    if not candidates:return mapping,{'status':'kept-no-qualified-whitespace','candidateCount':0,'originalBounds':original}
    best=max(candidates,key=lambda c:(c['score'],-c['bounds']['y'],-c['bounds']['x']))
    if baseline and best['score']<baseline['score']+settings['minGain']:
        return mapping,{'status':'kept-stable','candidateCount':len(candidates),'targetBounds':original,'score':baseline}
    dx=best['bounds']['x']-original['x'];dy=best['bounds']['y']-original['y']
    result={cid:dict(t) for cid,t in mapping.items()}
    for cid in selected_ids:result[cid]['tx']+=dx;result[cid]['ty']+=dy
    return result,{'status':'moved-to-whitespace','members':sorted(selected_ids),'candidateCount':len(candidates),
                   'originalBounds':original,'targetBounds':best['bounds'],'freeRegion':best['freeRegion'],
                   'score':best['score'],'terms':best['terms'],'clearance':clearance}
