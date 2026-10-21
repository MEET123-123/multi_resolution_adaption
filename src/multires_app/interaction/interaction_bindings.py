"""Separate rigid hit-target attachments from potential interaction effects."""
from __future__ import annotations

import math
from collections import defaultdict

from multires_app.interaction.interaction_ir import dependencies,node_reads
from multires_app.pipeline.scene_clusters import overlap,box_union

DRAWABLE={'Image','Text','Time','Date','DateTime','Number','ImageSeries','Rectangle','Video'}


def analyze_bindings(model,samples,viewport,overrides=None):
    nodes=model['nodes'];reads=node_reads(model);direct,_=dependencies(model);canvas=viewport['width']*viewport['height']
    by_sample=[]
    for elements in samples:
        grouped=defaultdict(list)
        for e in elements:
            if e['visible'] and e['tag']!='Group' and e['rect']['w']>0 and e['rect']['h']>0:grouped[e['id']].append(e['rect'])
        by_sample.append({uid:box_union(rects) for uid,rects in grouped.items()})
    by_owner=defaultdict(list)
    for t in model['triggers']:by_owner[t['owner']].append(t)
    def resolve(value):
        if value in nodes:return value
        found=[n['id'] for n in nodes.values() if n['attributes'].get('name')==value]
        if len(found)!=1:raise ValueError('绑定节点名称不存在或不唯一，请使用完整 ID：'+str(value))
        return found[0]
    explicit={}
    for binding in overrides or []:
        button=resolve(binding['button'])
        if nodes[button]['tag']!='Button':raise ValueError('绑定源必须为 Button')
        visuals=[resolve(v) for v in binding.get('visuals',[])]
        if any(nodes[v]['tag'] not in DRAWABLE for v in visuals):raise ValueError('绑定目标必须为可绘制节点')
        explicit[button]=visuals
    result=[]
    for uid,node in nodes.items():
        if node['tag']!='Button':continue
        writes={c['attributes'].get('name') for t in by_owner[uid] for c in t['commands'] if c['tag']=='VariableCommand'}-{None}
        affected=set(writes)
        while True:
            more=affected|{name for name,refs in direct.items() if refs&affected}
            for owner,triggers in by_owner.items():
                variable=nodes.get(owner,{}).get('attributes',{}).get('name')
                if nodes.get(owner,{}).get('tag')=='Var' and variable in affected:
                    more.update(c['attributes'].get('name') for t in triggers for c in t['commands'] if c['tag']=='VariableCommand')
            more.discard(None)
            if more==affected:break
            affected=more
        effect_nodes=[nid for nid,fields in reads.items() if nodes[nid]['tag'] in DRAWABLE and affected&set().union(*(set(v) for v in fields.values()))]
        base={'button':uid,'writes':sorted(writes),'affectedVariables':sorted(affected),'effectTargets':effect_nodes,
              'effectSemantics':'potential dependency, subject to guards','mode':'rigid','anchors':[],'evidence':[]}
        if uid in explicit:
            result.append({**base,'anchors':explicit[uid],'status':'explicit','confidence':1.,'evidence':['explicit binding configuration']});continue
        if any(uid in s and overlap(s[uid],{'x':0,'y':0,'w':viewport['width'],'h':viewport['height']})>=canvas*.85 for s in by_sample):
            result.append({**base,'status':'global-gesture','confidence':1.});continue
        candidates=defaultdict(list);boxes={}
        for sample in by_sample:
            if uid not in sample:continue
            b=sample[uid];ba=b['w']*b['h']
            for visual,q in sample.items():
                if nodes[visual]['tag'] not in DRAWABLE:continue
                qa=q['w']*q['h']
                if qa>canvas*.65 or not .03<=qa/max(1,ba)<=8:continue
                intersection=overlap(b,q);coverage=intersection/max(1,min(ba,qa))
                if coverage<.65:continue
                proximity=math.hypot(b['x']+b['w']/2-q['x']-q['w']/2,b['y']+b['h']/2-q['y']-q['h']/2)/max(1,math.sqrt(max(ba,qa)))
                if proximity>.65:continue
                same_parent=nodes[visual]['parent']==node['parent'];effect=visual in effect_nodes
                score=.55*coverage+.2*intersection/max(1,ba+qa-intersection)+.15*(1-min(1,proximity))+.05*same_parent+.05*effect
                candidates[visual].append(score);boxes[visual]=q
        rejected=[]
        for visual in list(candidates):
            together=[s for s in by_sample if uid in s and visual in s]
            # A crossing page can coincide once. A rigid attachment must hold
            # throughout co-visibility, including the poses that did not score.
            supported=sum(overlap(s[uid],s[visual])/max(1,min(s[uid]['w']*s[uid]['h'],s[visual]['w']*s[visual]['h']))>=.65 for s in together)
            offsets=[((s[uid]['x']-s[visual]['x'])/max(1,s[uid]['w'],s[visual]['w']),
                      (s[uid]['y']-s[visual]['y'])/max(1,s[uid]['h'],s[visual]['h'])) for s in together]
            drift=max((max(v[a] for v in offsets)-min(v[a] for v in offsets) for a in (0,1)),default=0.)
            if supported<.8*len(together) or drift>.3:
                rejected.append(visual);candidates.pop(visual)
        base['rejectedUnstableAnchors']=rejected
        ranked=sorted(((sum(scores)/len(scores),nid) for nid,scores in candidates.items()),reverse=True)
        if not ranked or ranked[0][0]<.7:
            result.append({**base,'status':'needs-review','confidence':ranked[0][0] if ranked else 0.,'candidates':[{'node':nid,'score':round(score,3)} for score,nid in ranked[:3]]});continue
        score,best=ranked[0];q=boxes[best]
        anchors=[nid for value,nid in ranked if value>=score-.1 and overlap(q,boxes[nid])/max(1,min(q['w']*q['h'],boxes[nid]['w']*boxes[nid]['h']))>=.8]
        ambiguous=[nid for value,nid in ranked[1:] if value>score-.025 and nid not in anchors]
        result.append({**base,'anchors':[] if ambiguous else anchors,'status':'needs-review' if ambiguous else 'inferred',
                       'confidence':round(score,3),'evidence':['co-visible geometry in sampled states','overlap and center proximity','source-parent and dependency evidence'],
                       'candidates':[{'node':nid,'score':round(value,3)} for value,nid in ranked[:5]]})
    return {'schemaVersion':1,'kind':'interaction-bindings','bindings':result,
            'summary':{'buttons':len(result),'bound':sum(bool(b['anchors']) for b in result),
                       'needsReview':sum(b['status']=='needs-review' for b in result),'samples':len(samples)}}


def structural_boundaries(model,elements,viewport,clock_roots):
    """Split large organizational groups, retain small composites and animations."""
    nodes=model['nodes'];by_id={e['id']:e for e in elements};output=set()
    for uid,node in nodes.items():
        if node['tag']!='Group' or uid in clock_roots:continue
        ancestor=uid;animated=False
        while ancestor:
            if any(nodes[c]['tag']=='PositionAnimation' for c in nodes[ancestor]['children']):animated=True;break
            ancestor=nodes[ancestor]['parent']
        if animated:continue
        descendants=[e for e in elements if e['visible'] and e['tag'] in DRAWABLE and e['id'].startswith(uid+'/')]
        bounds=box_union(e['rect'] for e in descendants)
        if bounds['h']<viewport['height']*.42 and bounds['w']<viewport['width']*1.25:continue
        # A containing card/panel surface is evidence of a deliberate composite.
        if any(e['parent']==uid and e['rect']['w']*e['rect']['h']>=bounds['w']*bounds['h']*.7 for e in descendants):continue
        children=[c for c in node['children'] if nodes[c]['tag'] in DRAWABLE|{'Group'}]
        if len(children)<2:continue
        for child in children:output.add(child)
    return output


def attach_components(components,bindings,nodes):
    """Union physical attachments only; effects on distant panels never merge."""
    parents={c['id']:c['id'] for c in components};root_owner={r:c['id'] for c in components for r in c['roots']}
    def owner(uid):
        while uid:
            if uid in root_owner:return root_owner[uid]
            uid=nodes[uid]['parent']
    def find(cid):
        while parents[cid]!=cid:cid=parents[cid]
        return cid
    for binding in bindings['bindings']:
        button=owner(binding['button']);anchors=[owner(a) for a in binding['anchors']]
        anchors=[a for a in anchors if a]
        if not button or not anchors:continue
        preferred=find(anchors[0])
        for cid in [button,*anchors]:parents[find(cid)]=preferred
    groups=defaultdict(list)
    for c in components:groups[find(c['id'])].append(c)
    output=[]
    for cid,items in groups.items():
        representative=next(c for c in items if c['id']==cid)
        output.append({**representative,'roots':list(dict.fromkeys(r for c in items for r in c['roots'])),
                       'members':list(dict.fromkeys(m for c in items for m in c['members'])),
                       'sourceBounds':box_union(c['sourceBounds'] for c in items)})
    return output
