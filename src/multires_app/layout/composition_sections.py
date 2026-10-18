"""Separate distant vertical sections without changing authored event trees."""
import copy
from multires_app.pipeline.scene_clusters import box_union,overlap


def split_composition_sections(project):
    from multires_app.layout.clock_semantics import promote_clock_components
    promote_clock_components(project)
    nodes=project['model']['nodes'];source=project.get('sourceDevice',{})
    if not source:return []
    profiles=[project.get('sourceProfile',{}),*[d.get('profile',{}) for d in project.get('devices',[])]]
    if any(p.get('adaptation',{}).get('anchorLayout',{}).get('groups') or p.get('adaptation',{}).get('anchorLayout',{}).get('constraints') for p in profiles):return []
    from multires_app.media.media_layout import layout_rows
    samples=[]
    for layout in source['layouts']:
        sample={}
        for r in layout.get('rows',[]):
            if r[5]<=0:continue
            sample[project['nodeOrder'][r[0]]]=dict(zip(('x','y','w','h'),r[1:5]))
        samples.append(sample)
    w,h=source['viewport']['width'],source['viewport']['height'];output=[];changes=[]
    def descendants(uid):
        result={uid}
        for child in nodes[uid]['children']:result.update(descendants(child))
        return result
    for comp in project['components']:
        roots=[uid for uid in comp['roots'] if nodes[uid]['tag']=='Group']
        if comp.get('layout') or comp.get('sectionSplit') or len(roots)!=1 or comp['role'] in {'background','gesture-layer','interaction-layer','decoration','popup','dialog','overlay'}:
            output.append(comp);continue
        ancestor=roots[0];animated=False
        while ancestor:
            if any(nodes[child]['tag'] in {'PositionAnimation','SizeAnimation','ScaleAnimation','RotationAnimation'} for child in nodes[ancestor]['children']):animated=True;break
            ancestor=nodes[ancestor].get('parent')
        if animated:output.append(comp);continue
        children=[uid for uid in nodes[roots[0]]['children'] if nodes[uid]['tag'] in {'Group','Image','ImageNumber','ImageSeries','Text','DateTime','Video'}]
        trees={uid:descendants(uid)&set(comp['members']) for uid in children}
        trees={uid:members for uid,members in trees.items() if any(nodes[mid]['tag'] not in {'Group','Button'} for mid in members)}
        if len(trees)<2:output.append(comp);continue
        # Pick an observed pose with the most visible branches. Hidden variants
        # are assigned only when their authored coordinates match a visible peer.
        best=None
        for sample in samples:
            boxes={uid:box_union([sample[mid] for mid in members if mid in sample and nodes[mid]['tag'] not in {'Group','Button'}]) for uid,members in trees.items()}
            live=[b for b in boxes.values() if min(b['w'],b['h'])>0]
            score=(len(live),-sum(max(0,b['x']+b['w']-w)+max(0,b['y']+b['h']-h)+max(0,-b['x'])+max(0,-b['y']) for b in live),-box_union(live)['h'])
            if best is None or score>best[0]:best=(score,boxes,sample)
        _,boxes,sample=best
        visible=[uid for uid,b in boxes.items() if min(b['w'],b['h'])>0]
        backdrops=[uid for uid in visible if nodes[uid]['tag'] in {'Image','Video'} and boxes[uid]['w']>=w*.9 and boxes[uid]['h']>=h*.9]
        foreground=[uid for uid in visible if uid not in backdrops]
        # Multiple artwork layers form a single composition, not a dialog scrim.
        if backdrops and any(boxes[uid]['h']>h*.65 for uid in foreground):
            if not comp.get('bindingButtons') and not any(nodes[mid]['tag']=='Button' for mid in comp['members']):
                output.append({**comp,'role':'background','backgroundComposition':True});changes.append({'original':comp['id'],'backgroundComposition':True});continue
            backdrops=[];foreground=visible
        clusters=[]
        for uid in sorted(foreground,key=lambda u:boxes[u]['y']):
            b=boxes[uid]
            if not clusters or b['y']-max(boxes[mid]['y']+boxes[mid]['h'] for mid in clusters[-1])>max(80,h*.06):clusters.append([uid])
            else:clusters[-1].append(uid)
        clusters=[[uid] for uid in backdrops]+clusters
        if len(clusters)<2:output.append(comp);continue
        owner={mid:i for i,part in enumerate(clusters) for uid in part for mid in trees[uid]}
        valid=True
        for uid in trees:
            if uid in visible:continue
            attrs=nodes[uid]['attributes'];matches=[v for v in visible if nodes[v]['tag']==nodes[uid]['tag'] and all(nodes[v]['attributes'].get(k)==attrs.get(k) for k in ('x','y','align','alignV'))]
            if not matches:valid=False;break
            i=owner[matches[0]];clusters[i].append(uid)
            for mid in trees[uid]:owner[mid]=i
        if not valid:output.append(comp);continue
        buttons=[uid for uid in comp['members'] if nodes[uid]['tag']=='Button' and uid not in owner]
        for uid in buttons:
            b=sample.get(uid)
            if not b:valid=False;break
            scores=[0. if part[0] in backdrops else overlap(b,box_union([boxes[v] for v in part if v in visible]))/max(1,b['w']*b['h']) for part in clusters]
            ranked=sorted(range(len(scores)),key=lambda i:scores[i],reverse=True)
            if scores[ranked[0]]<.5 or len(ranked)>1 and abs(scores[ranked[0]]-scores[ranked[1]])<.1:valid=False;break
            owner[uid]=ranked[0]
        if not valid:output.append(comp);continue
        for i,part in enumerate(clusters):
            members=[uid for uid in comp['members'] if owner.get(uid)==i]
            bound=[uid for uid in buttons if owner[uid]==i]
            output.append({**copy.deepcopy(comp),'id':part[0],'name':comp['name']+' / section',
                'roots':part+bound,'members':members,'role':'background' if part[0] in backdrops else comp['role'],
                'sourceBounds':box_union([boxes[uid] for uid in part if uid in visible]),'sectionSplit':comp['id'],
                'layoutFollowers':[uid for uid in comp.get('layoutFollowers',[]) if uid in members],
                'bindingButtons':[uid for uid in members if nodes[uid]['tag']=='Button'],
                'inference':'observed distant vertical sections; original ancestor animations and triggers retained'})
        changes.append({'original':comp['id'],'sections':[part[0] for part in clusters]})
    if changes:
        project['components']=output;project['nodeComponents']={uid:c['id'] for c in output for uid in c['members']}
        project.setdefault('compositionSectionSplits',[]).extend(changes)
    return changes
