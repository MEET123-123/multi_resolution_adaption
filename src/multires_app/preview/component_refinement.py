"""Split coarse multi-card containers along authored child groups.

Only owned nodes move between components. Existing nested components, triggers,
visibility expressions, XML hierarchy and cached runtime rows remain intact.
"""
import copy


def refresh_inferred_bindings(project):
    """Upgrade coarse cached ownership using existing source snapshots only."""
    if any(c.get('layout') or c.get('provenance')=='manual' for c in project['components']):return False
    if any(d.get('profile',{}).get('adaptation',{}).get('anchorLayout',{}).get(k)
           for d in [project.get('sourceDevice',{}),*project.get('devices',[])] for k in ('groups','constraints')):return False
    from multires_app.pipeline.scene_pipeline import infer_components
    from multires_app.engine.scene_engine import Runtime
    source=project['sourceDevice'];vp=source['viewport'];model=project['model']
    initial=source['layouts'][0]
    model.setdefault('assetNames',list(project['assets']))
    templates=Runtime(model,vp['width'],vp['height'],initial.get('overrides',{})).snapshot(project['assets'])
    by_id={e['id']:e for e in templates};samples=[]
    for l in source['layouts']:
        current={}
        for r in l['rows']:
            uid=project['nodeOrder'][r[0]]
            e={**by_id[uid],'rect':dict(zip(('x','y','w','h'),r[1:5])),'visible':r[5]>0,'alpha':r[5]}
            current[uid]=e
        samples.append([current.get(e['id'],{**e,'visible':False}) for e in templates])
    from multires_app.interaction.interaction_bindings import analyze_bindings
    new=analyze_bindings(model,samples,{'width':vp['width'],'height':vp['height']},model.get('inputs',{}).get('bindings'))
    old={b['button']:b for b in model.get('layoutBindings',{}).get('bindings',[])}
    if model.get('componentInferenceVersion',0)>=3 and not any(set(b.get('rejectedUnstableAnchors',[]))&set(old.get(b['button'],{}).get('anchors',[])) for b in new['bindings']):
        return False
    components=infer_components(model,project['sourceProfile'],project['assets'],initial.get('overrides',{}),sampled_elements=samples)
    project['components']=components
    project['nodeComponents']={u:c['id'] for c in components for u in c['members']}
    for key in ('_visualBoundsReady','_volatileNames'):project.pop(key,None)
    for d in [source,*project.get('devices',[])]:
        for l in d['layouts']:l['transforms']={}
    from multires_app.interaction.regional_gestures import split_regional_gestures
    from multires_app.layout.composition_sections import split_composition_sections
    from multires_app.layout.background_layers import split_background_layers
    from multires_app.layout.footer_components import split_footer_components
    from multires_app.layout.panel_scaling import infer_panel_scale_families
    split_regional_gestures(project);split_composition_sections(project);split_background_layers(project);split_footer_components(project)
    partition_button_components(project);infer_panel_scale_families(project)
    project['bindingCacheUpgrade']={'policy':'persistent physical correspondence across all co-visible source poses'}
    return True


def refine_components(project):
    result=_refine_authored(project)
    result['buttonPartitions']=partition_button_components(project)
    return result


def partition_button_components(project):
    """Partition independent controls by their observed physical hit footprints.

    This covers flat XML groups without child Group nodes (clock/date/switch,
    or left/right shortcuts). Every visual leaf must be explained by a local
    button; ambiguous overlapping buttons merge instead of inventing identities.
    The original XML tree, visibility expressions and handlers are preserved.
    """
    from multires_app.pipeline.scene_clusters import box_union,overlap
    nodes=project['model']['nodes'];source=project.get('sourceDevice',{});vp=source.get('viewport',{})
    if not vp:return []
    snapshots=[]
    for l in source.get('layouts',[]):
        by_uid={}
        for r in l['rows']:
            if r[5]>0 and min(r[3:5])>0:by_uid.setdefault(project['nodeOrder'][r[0]],[]).append(dict(zip(('x','y','w','h'),r[1:5])))
        snapshots.append({u:box_union(rs) for u,rs in by_uid.items()})
    output=[];partitions=[];replacements={}
    for comp in project['components']:
        root=comp['id'];members=comp['members'];owned=set(members)
        buttons=[u for u in members if nodes[u]['tag']=='Button']
        visuals=[u for u in members if nodes[u]['tag'] not in {'Button','Group'}]
        if (nodes.get(root,{}).get('tag')!='Group' or comp['role'] not in {'header','content','footer'} or comp.get('layout') or
                len(buttons)<2 or len(visuals)<2 or any(nodes[u]['tag'] in {'Video','MediaControl'} for u in visuals)):
            output.append(comp);continue
        parents={b:b for b in buttons}
        def find(b):
            while parents[b]!=b:parents[b]=parents[parents[b]];b=parents[b]
            return b
        candidates={u:set() for u in visuals}
        for sample in snapshots:
            for button in buttons:
                b=sample.get(button)
                if not b or b['w']*b['h']>vp['width']*vp['height']*.5:continue
                for u in visuals:
                    v=sample.get(u)
                    if not v or overlap(b,v)/max(1,v['w']*v['h'])<.7:continue
                    cx,cy=v['x']+v['w']/2,v['y']+v['h']/2
                    if b['x']<=cx<=b['x']+b['w'] and b['y']<=cy<=b['y']+b['h']:candidates[u].add(button)
        if any(not bs for bs in candidates.values()):output.append(comp);continue
        for bs in candidates.values():
            bs=sorted(bs)
            for b in bs[1:]:parents[find(b)]=find(bs[0])
        clusters={}
        for u,bs in candidates.items():clusters.setdefault(find(next(iter(bs))),set()).add(u)
        for b in buttons:
            if find(b) in clusters:clusters[find(b)].add(b)
        clusters={key:ms for key,ms in clusters.items() if any(nodes[u]['tag']!='Button' for u in ms)}
        # Unexplained controls or explicit cross-control bindings are not split.
        assigned=set().union(*clusters.values()) if clusters else set()
        explicit=any(b.get('status')=='explicit' and b['button'] in owned and
            any(a not in owned for a in b.get('anchors',[])) for b in project['model'].get('layoutBindings',{}).get('bindings',[]))
        if len(clusters)<2 or explicit or any(nodes[u]['tag']!='Group' for u in owned-assigned):output.append(comp);continue
        children=[]
        for cid,ms in sorted(clusters.items()):
            # Keep all descendants needed by a visual wrapper on the same affine.
            roots=[u for u in ms if not any(u.startswith(v+'/') for v in ms)]
            new={**copy.deepcopy(comp),'id':cid,'name':comp['name']+' / control '+str(len(children)+1),
                'members':[u for u in members if u in ms],'roots':roots,'refinedFrom':root,
                'inference':'co-visible button footprint contains its complete visual leaves'}
            new.pop('bindingButtons',None);new.pop('layoutFollowers',None)
            output.append(new);children.append(cid)
            for u in ms:project['nodeComponents'][u]=cid
        for u in owned-assigned:project['nodeComponents'].pop(u,None)
        replacements[root]=children;partitions.append({'parent':root,'children':children,'basis':'source hit footprints; all visual leaves accounted for'})
    if not replacements:return []
    project['components']=output
    for d in [source,*project.get('devices',[])]:
        for l in d.get('layouts',[]):
            transforms=l.get('transforms',{})
            for old,children in replacements.items():
                prior=transforms.pop(old,{'sx':1,'sy':1,'tx':0,'ty':0})
                for cid in children:transforms[cid]=dict(prior)
            l['adaptationPlan']={'policy':'component-refinement-preserved-transforms','baselineTransforms':copy.deepcopy(transforms),'previousPlan':l.get('adaptationPlan',{})}
            l['layoutRequest']={**l.get('layoutRequest',{}),'fixedComponents':[]}
    project.setdefault('buttonComponentPartitions',[]).extend(partitions)
    return partitions


def _refine_authored(project):
    nodes=project['model']['nodes'];created=[];removed={};result=[]
    for c in project['components']:
        root=c['id'];node=nodes.get(root)
        if not node or node['tag']!='Group' or c['role'] in {'background','gesture-layer','dialog','overlay','popup'} or c.get('layout',{}).get('group'):
            result.append(c);continue
        owned=set(c['members']);branches=[]
        for uid in node['children']:
            if nodes[uid]['tag']!='Group' or uid not in owned:continue
            members=[m for m in c['members'] if m==uid or m.startswith(uid+'/')]
            if sum(nodes[m]['tag'] in {'Image','Text','Time','ImageNumber','Video'} for m in members)>=2:branches.append((uid,members))
        if len(branches)<2:result.append(c);continue
        assigned={m for _,ms in branches for m in ms}
        leftovers=[m for m in c['members'] if m not in assigned and m!=root]
        # An authored visual directly on the parent may span all child cards.
        # Retain its atomic container until an explicit segmentation can prove it.
        if any(nodes[m]['tag'] in {'Image','Text','Time','ImageNumber','Video','Button'} for m in leftovers):
            result.append(c);continue
        branch_of={m:uid for uid,ms in branches for m in ms};corrections=[];blocked=False
        for binding in project['model'].get('layoutBindings',{}).get('bindings',[]):
            owner=branch_of.get(binding['button']);anchors=binding.get('anchors',[])
            if not owner:continue
            local=[a for a in anchors if branch_of.get(a)==owner]
            remote=[a for a in anchors if branch_of.get(a) not in {None,owner}]
            if not remote:continue
            if binding.get('status')=='inferred' and local:
                corrections.append((binding,local,remote))
            else:blocked=True;break
        if blocked:result.append(c);continue
        for binding,local,remote in corrections:
            binding['anchors']=[a for a in binding['anchors'] if a not in remote]
            binding.setdefault('rejectedInferredAnchors',[]).extend(remote)
            binding.setdefault('evidence',[]).append('prefer authored button subtree when local physical anchors exist; sibling proximity is insufficient')
        removed[root]=[]
        for uid,members in branches:
            new={**copy.deepcopy(c),'id':uid,'name':c['name']+' / '+uid.rsplit('/',1)[-1],
                 'roots':[uid],'members':members,'refinedFrom':root}
            new.pop('layoutFollowers',None);new.pop('layout',None)
            result.append(new);removed[root].append(uid);created.append(uid)
            for m in members:project['nodeComponents'][m]=uid
        for m in [root,*leftovers]:project['nodeComponents'].pop(m,None)
    if not removed:return {'splitContainers':0,'created':[]}
    project['components']=result
    for device in [project.get('sourceDevice',{}),*project.get('devices',[])]:
        for layout in device.get('layouts',[]):
            transforms=layout.get('transforms',{})
            for old,children in removed.items():
                previous=transforms.pop(old,{'sx':1,'sy':1,'tx':0,'ty':0})
                for cid in children:transforms[cid]=dict(previous)
            for key in ('aestheticBeforeTransforms',):
                if key in layout:
                    for old,children in removed.items():
                        prior=layout[key].pop(old,None)
                        if prior:
                            for cid in children:layout[key][cid]=dict(prior)
            # These plans refer to the old component IDs; runtime geometry is
            # unchanged, but the reference solver creates a fresh checked plan.
            layout['adaptationPlan']={'policy':'component-refinement-preserved-transforms',
                'baselineTransforms':copy.deepcopy(transforms),'previousPlan':layout.get('adaptationPlan',{})}
            layout['layoutRequest']={**layout.get('layoutRequest',{}),'fixedComponents':[]}
        for key in ('manualComponentAnchors','sharedResourceScales'):
            for old in removed:device.get(key,{}).pop(old,None)
    project['componentRefinement']={'policy':'authored-child-group-partition','removed':removed}
    return {'splitContainers':len(removed),'created':created}
