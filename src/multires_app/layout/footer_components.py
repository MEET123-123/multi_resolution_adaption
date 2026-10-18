"""Split only provably independent peers inside an otherwise rigid footer."""
import copy
from multires_app.pipeline.scene_clusters import box_union
from multires_app.layout.screen_relations import footer_rails


def split_footer_components(project):
    nodes=project['model']['nodes'];source=project.get('sourceDevice',{})
    if not source:return []
    samples=[]
    for layout in source['layouts']:
        sample={}
        for r in layout.get('rows',[]):
            if r[5]<=0:continue
            uid=project['nodeOrder'][r[0]];b=dict(zip(('x','y','w','h'),r[1:5]))
            sample[uid]=box_union([sample[uid],b]) if uid in sample else b
        samples.append(sample)
    bindings=project['model'].get('layoutBindings',{}).get('bindings',[])
    profiles=[project.get('sourceProfile',{}),*[d.get('profile',{}) for d in project.get('devices',[])]]
    if any(p.get('adaptation',{}).get('anchorLayout',{}).get('groups') or p.get('adaptation',{}).get('anchorLayout',{}).get('constraints') or
           p.get('adaptation',{}).get('anchorLayout',{}).get('inferScreenRelations') is False for p in profiles):return []
    output=[];changes=[]
    def descendants(uid):
        found={uid}
        for child in nodes[uid]['children']:found.update(descendants(child))
        return found
    for comp in project['components']:
        nested=split_nested_corner_variants(project,comp,samples)
        if nested:
            peers,change=nested;output.extend(peers);changes.append(change);continue
        group_roots=[uid for uid in comp['roots'] if nodes[uid]['tag']=='Group']
        visual_roots=[uid for uid in comp['roots'] if nodes[uid]['tag'] in {'Image','ImageSeries','Video','Group'}]
        direct=(len(visual_roots)>=2 and len({nodes[uid].get('parent') for uid in visual_roots})==1 and
                all(nodes[uid]['tag'] in {'Image','ImageSeries','Video','Group','Button'} for uid in comp['roots']))
        control_section=comp['role'] not in {'background','gesture-layer','interaction-layer','decoration','popup','dialog','overlay'} and len(comp.get('bindingButtons',[]))>=2
        if comp.get('layout') or comp.get('splitFrom') or not(comp['role']=='footer' or control_section) or not(direct or len(group_roots)==1):
            output.append(comp);continue
        root=nodes[group_roots[0]] if group_roots else {'children':comp['roots']}
        children=[uid for uid in root['children'] if nodes[uid]['tag'] in {'Image','ImageSeries','Video','Group'}]
        if len(children)<2 or any(nodes[uid]['tag'] in {'Triggers','PositionAnimation','SizeAnimation','RotationAnimation','ScaleAnimation','AlphaAnimation'} for uid in root['children']):
            output.append(comp);continue
        subtrees={uid:descendants(uid) for uid in children}
        owners={member:uid for uid,subtree in subtrees.items() for member in subtree}
        visuals={uid for uid in comp['members'] if nodes[uid]['tag'] not in {'Group','Button'}}
        if not visuals<=set(owners):output.append(comp);continue
        candidate=None;clusters=None;endpoints=False
        for sample in samples:
            boxes={uid:box_union([sample[mid] for mid in subtree if mid in sample and nodes[mid]['tag']!='Button']) for uid,subtree in subtrees.items()}
            # Hidden light/dark variants with identical authored coordinates
            # stay in the same visual stack. Never invent a hidden location.
            valid=True
            for uid,b in boxes.items():
                if min(b['w'],b['h'])>0:continue
                attrs=nodes[uid].get('attributes',{});keys=('x','y','align','alignV')
                peers=[other for other,q in boxes.items() if min(q['w'],q['h'])>0 and nodes[other]['tag']==nodes[uid]['tag'] and
                       all(nodes[other].get('attributes',{}).get(k)==attrs.get(k) for k in keys)]
                if not peers:valid=False;break
                boxes[uid]=boxes[peers[0]]
            if not valid:continue
            parts=[]
            for uid in sorted(children,key=lambda uid:boxes[uid]['x']):
                b=boxes[uid];matched=None
                for part in parts:
                    q=box_union([boxes[mid] for mid in part]);gap=max(b['x']-q['x']-q['w'],q['x']-b['x']-b['w'],0)
                    if gap<=max(12.,min(b['h'],q['h'])*.35) and abs(b['y']+b['h']/2-q['y']-q['h']/2)<=max(4.,min(b['h'],q['h'])*.2):matched=part;break
                if matched is None:parts.append([uid])
                else:matched.append(uid)
            es=[{'id':str(i),'bbox':box_union([boxes[uid] for uid in part]),'role':'footer'} for i,part in enumerate(parts)]
            rails=footer_rails(es,source['viewport'])
            endpoints=(len(es)==2 and es[0]['bbox']['x']<=source['viewport']['width']*.12 and
                       source['viewport']['width']-es[-1]['bbox']['x']-es[-1]['bbox']['w']<=source['viewport']['width']*.12 and
                       all(e['bbox']['y']>=source['viewport']['height']*.78 and e['bbox']['x']>=0 and
                           e['bbox']['x']+e['bbox']['w']<=source['viewport']['width']+.1 and
                           e['bbox']['y']+e['bbox']['h']<=source['viewport']['height']+.1 and
                           e['bbox']['w']<=source['viewport']['width']*.25 and e['bbox']['h']<=source['viewport']['height']*.15 for e in es))
            if endpoints or (comp['role']=='footer' and len(rails)==1 and len(rails[0]['members'])==len(parts)):
                candidate={part[0]:e['bbox'] for part,e in zip(parts,es)};clusters=parts;break
        if not candidate:output.append(comp);continue
        cluster_owners={uid:part[0] for part in clusters for uid in part}
        owners={member:cluster_owners[uid] for uid,subtree in subtrees.items() for member in subtree}
        buttons={uid for uid in comp['members'] if nodes[uid]['tag']=='Button'};button_owners={};ambiguous=False
        for uid in buttons:
            if uid in owners:button_owners[uid]=owners[uid];continue
            links=[b for b in bindings if b['button']==uid]
            targets={owners[a] for b in links for a in b.get('anchors',[]) if a in owners}
            if len(targets)!=1:ambiguous=True;break
            button_owners[uid]=next(iter(targets))
        if ambiguous:output.append(comp);continue
        for part in clusters:
            uid=part[0]
            bound=[button for button,owner in button_owners.items() if owner==uid]
            members=[mid for mid in comp['members'] if owners.get(mid)==uid or mid in bound]
            output.append({**copy.deepcopy(comp),'id':uid,'name':comp['name']+' / '+nodes[uid].get('attributes',{}).get('name',nodes[uid]['tag']),
                           'roots':[*part,*[button for button in bound if button not in owners]],
                           'members':members,'sourceBounds':candidate[uid],'bindingButtons':bound,
                           'layoutFollowers':[mid for mid in comp.get('layoutFollowers',[]) if mid in members],
                           'allowSpatialStack':nodes[uid]['tag']=='Group','splitFrom':comp['id'],
                           'role':'footer' if endpoints else comp['role'],
                           'semanticRole':'shortcut-controls' if endpoints else comp.get('semanticRole'),
                           'cornerAttachment':{'corner':'bottom-left' if part==clusters[0] else 'bottom-right',
                               'sourceMargins':{'x':candidate[uid]['x'] if part==clusters[0] else source['viewport']['width']-candidate[uid]['x']-candidate[uid]['w'],
                                                'y':source['viewport']['height']-candidate[uid]['y']-candidate[uid]['h']},
                               'provenance':'independent bottom endpoint with its bound hit region'} if endpoints and bound else {},
                           'screenEdgeIntent':{'x':{'edge':'left','margin':candidate[uid]['x']} if part==clusters[0] else
                                               {'edge':'right','margin':source['viewport']['width']-candidate[uid]['x']-candidate[uid]['w']},
                                               'y':{'edge':'bottom','margin':source['viewport']['height']-candidate[uid]['y']-candidate[uid]['h']}} if endpoints else {},
                           'inference':'independent uniformly spaced footer peers; bindings preserved'})
        changes.append({'original':comp['id'],'components':[part[0] for part in clusters],'reason':'independent footer stacks, variants and button bindings retained'})
    if changes:
        project['components']=output
        project['nodeComponents']={uid:c['id'] for c in output for uid in c['members']}
    changes.extend(infer_corner_pairs(project,samples))
    if changes:project.setdefault('screenRelationComponentSplits',[]).extend(changes)
    return changes


def split_nested_corner_variants(project,comp,samples):
    """Separate positional endpoints through style wrappers, keeping XML intact.

    Parent translations/entry animations remain on the original ancestors.
    Each leaf and hit area gets its own world-coordinate affine on writeback.
    """
    nodes=project['model']['nodes'];vp=project['sourceDevice']['viewport'];w,h=vp['width'],vp['height']
    if comp['role']!='footer' or comp.get('layout') or comp.get('splitFrom') or len(comp['roots'])!=1:return None
    root=nodes[comp['roots'][0]]
    if root['tag']!='Group' or not any(nodes[c]['tag']=='Group' for c in root['children']):return None
    # Translations are safe to retain on ancestors; size/rotation change the
    # local frame and need a more general transform composition model.
    if any(nodes[u]['tag'] in {'SizeAnimation','ScaleAnimation','RotationAnimation'} for u in nodes if u.startswith(root['id']+'/')):return None
    visuals=[u for u in comp['members'] if nodes[u]['tag'] not in {'Group','Button'}]
    if not visuals or any(nodes[u]['tag'] not in {'Image','ImageSeries','Video'} for u in visuals):return None
    buttons=[u for u in comp['members'] if nodes[u]['tag']=='Button']
    if len(buttons)<2:return None
    boxes={}
    for sample in samples:
        for u in [*visuals,*buttons]:
            if u in sample and min(sample[u]['w'],sample[u]['h'])>0:boxes.setdefault(u,sample[u])
    missing=set(visuals+buttons)-set(boxes)
    if missing:
        from multires_app.engine.scene_engine import Runtime
        first=project['sourceDevice']['layouts'][0]
        rt=Runtime(project['model'],w,h,first.get('overrides',{}),time_ms=first.get('timeMs',2400))
        for e in rt.snapshot(project.get('assets',{}),only_ids=missing):
            if e['id'] in missing and min(e['rect']['w'],e['rect']['h'])>0:boxes[e['id']]=e['rect']
    if missing-set(boxes):return None
    sides={};frames={}
    for u in buttons:
        b=boxes[u];mx=min(b['x'],w-b['x']-b['w'])
        if not(0<b['w']<=w*.25 and 0<b['h']<=h*.15 and b['y']>=h*.78 and mx>=0 and mx<=w*.12 and b['y']+b['h']<=h+.1):return None
        side='left' if b['x']+b['w']/2<w/2 else 'right';sides[u]=side
        prior=frames.get(side)
        if prior and any(abs(b[k]-prior[k])>.5 for k in ('x','y','w','h')):return None
        frames[side]=b
    if set(frames)!={'left','right'} or abs(frames['left']['y']-frames['right']['y'])>.5:return None
    for u in visuals:
        b=boxes[u];matches=[side for side,q in frames.items() if b['x']>=q['x']-4 and b['y']>=q['y']-4 and
                           b['x']+b['w']<=q['x']+q['w']+4 and b['y']+b['h']<=q['y']+q['h']+4]
        if len(matches)!=1:return None
        sides[u]=matches[0]
    peers=[]
    for side in ('left','right'):
        members=[u for u in comp['members'] if sides.get(u)==side];bound=[u for u in buttons if sides[u]==side]
        uid=next(u for u in visuals if sides[u]==side);b=frames[side];mx=b['x'] if side=='left' else w-b['x']-b['w'];my=h-b['y']-b['h']
        peers.append({**copy.deepcopy(comp),'id':uid,'name':comp['name']+' / bottom-'+side,'roots':members,'members':members,
                      'sourceBounds':b,'bindingButtons':bound,'layoutFollowers':[],'allowSpatialStack':True,
                      'cornerReferenceNodes':bound,
                      'splitFrom':comp['id'],'semanticRole':'shortcut-controls',
                      'cornerAttachment':{'corner':'bottom-'+side,'sourceMargins':{'x':mx,'y':my},'provenance':'nested style variants share positional endpoints; ancestor entry motion retained'},
                      'screenEdgeIntent':{'x':{'edge':side,'margin':mx},'y':{'edge':'bottom','margin':my}}})
    return peers,{'original':comp['id'],'components':[c['id'] for c in peers],'reason':'nested shortcut variants split by proven world-space icon/hit rectangles; parent translation retained'}


def infer_corner_pairs(project,samples):
    """Already separate icons still need explicit opposite-corner intent."""
    nodes=project['model']['nodes'];vp=project['sourceDevice']['viewport'];w,h=vp['width'],vp['height'];changes=[]
    for sample in samples:
        candidates=[]
        for c in project['components']:
            if c.get('layout') or c.get('cornerAttachment') or not c.get('bindingButtons') or c['role'] in {'background','gesture-layer','interaction-layer','decoration','popup','dialog','overlay'}:continue
            boxes=[sample[uid] for uid in c['members'] if uid in sample and nodes[uid]['tag'] not in {'Group','Button'} and uid not in c.get('layoutFollowers',[])]
            b=box_union(boxes)
            if not boxes or not(0<b['w']<=w*.25 and 0<b['h']<=h*.15 and b['y']>=h*.78 and b['x']>=0 and b['x']+b['w']<=w+.1 and b['y']+b['h']<=h+.1):continue
            left,right=b['x'],w-b['x']-b['w']
            if min(left,right)>w*.12:continue
            parents={nodes[uid].get('parent') for uid in c['roots'] if nodes[uid]['tag']!='Button'}
            if len(parents)!=1:continue
            candidates.append((c,b,next(iter(parents)),'left' if left<right else 'right'))
        used=set()
        for c,b,parent,side in candidates:
            if c['id'] in used:continue
            row=[q for q in candidates if q[2]==parent and abs(q[1]['y']+q[1]['h']/2-b['y']-b['h']/2)<=max(4.,min(q[1]['h'],b['h'])*.2)]
            if len(row)!=2 or {q[3] for q in row}!={'left','right'}:continue
            for peer,box,_,edge in row:
                mx=box['x'] if edge=='left' else w-box['x']-box['w'];my=h-box['y']-box['h']
                peer['cornerAttachment']={'corner':'bottom-'+edge,'sourceMargins':{'x':mx,'y':my},'provenance':'co-visible bound shortcut peers at opposite bottom corners'}
                peer['screenEdgeIntent']={'x':{'edge':edge,'margin':mx},'y':{'edge':'bottom','margin':my}}
                peer['role']='footer';peer['semanticRole']='shortcut-controls';used.add(peer['id'])
            changes.append({'cornerPeers':[q[0]['id'] for q in row],'reason':'independent shortcut peers retain opposite bottom corners'})
    return changes
