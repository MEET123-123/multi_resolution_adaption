"""Layout at playback phase zero; rendering keeps the authored current phase.

Human variables are preserved. ImageSeries is a glyph control, not a timeline.
No decoded video pixels are needed to constrain its intrinsic canvas.
"""
import copy
import posixpath
import re


def index_guard(expression,indices):
    values=sorted(indices);term=f'int({expression})'
    if len(values)==values[-1]-values[0]+1:
        return f'ge({term},{values[0]})*le({term},{values[-1]})'
    return '('+'+'.join(f'eq({term},{i})' for i in values)+')'


def temporal_names(model):
    from multires_app.engine.scene_engine import REF
    names={'time'}
    variables=model.get('variables',{})
    names.update(name for name,node in variables.items() if any(model['nodes'][c]['tag']=='VariableAnimation' for c in node['children']))
    while True:
        expanded=names|{name for name,node in variables.items() if set(REF.findall(node['attributes'].get('expression','')))&names}
        if expanded==names:return names
        names=expanded


def temporal_ids(model):
    from multires_app.engine.scene_engine import REF, VISUAL
    names=temporal_names(model);result=set()
    fields=('srcid','srcExp','x','y','w','h','width','height','size','space','visibility','alpha')
    for uid,node in model['nodes'].items():
        if node['tag'] not in VISUAL or node['tag']=='Group':continue
        animated=node['tag']=='Video';ancestor=uid
        while ancestor:
            n=model['nodes'][ancestor]
            animated |= any(set(REF.findall(n['attributes'].get(k,'')))&names for k in fields)
            animated |= any(model['nodes'][c]['tag'] in {'SourcesAnimation','PositionAnimation','SizeAnimation','AlphaAnimation'} for c in n['children'])
            ancestor=n['parent']
        if animated:result.add(uid)
    return result


def autonomous_position_ids(model):
    """Looping motion uses its reference pose for layout and crop inference.

    Settled human poses and finite entry/exit animations keep their sampled
    geometry. Only an autonomous position timeline can use this shortcut.
    """
    from multires_app.engine.scene_engine import REF, VISUAL
    nodes=model.get('nodes',{});pages=page_animation_names(model) if model.get('variables') else set()
    def looping(node):
        a=node.get('attributes',{})
        return a.get('loop','false' if a.get('repeat')=='1' else 'true').lower()!='false'
    names={'time'}
    names.update(name for name,node in model.get('variables',{}).items() if name not in pages and
                 any(nodes[c]['tag']=='VariableAnimation' and looping(nodes[c]) for c in node['children']))
    human_names=set(pages)
    while True:
        expanded=human_names|{name for name,node in model.get('variables',{}).items() if
                              set(REF.findall(node['attributes'].get('expression','')))&human_names}
        if expanded==human_names:break
        human_names=expanded
    while True:
        expanded=names|{name for name,node in model.get('variables',{}).items() if name not in pages and
                        set(REF.findall(node['attributes'].get('expression','')))&names}
        if expanded==names:break
        names=expanded
    commanded={c['attributes'].get('target','')[:-10] for t in model.get('triggers',[]) for c in t['commands']
               if c['tag']=='Command' and c['attributes'].get('target','').endswith('.animation')}
    result=set()
    for uid,node in nodes.items():
        if node['tag'] not in VISUAL or node['tag']=='Group':continue
        owner=uid;automatic=False;human=False
        while owner:
            n=nodes[owner]
            attrs=n.get('attributes',{})
            refs=set().union(*(set(REF.findall(attrs.get(k,''))) for k in ('x','y')))
            geometry_refs=set().union(*(set(REF.findall(attrs.get(k,''))) for k in ('x','y','w','h','width','height','size','space')))
            human |= bool(geometry_refs&human_names) or attrs.get('name') in commanded
            human |= any(nodes[c]['tag'] in {'PositionAnimation','SizeAnimation'} and not looping(nodes[c]) for c in n.get('children',[]))
            automatic |= bool(refs&names) or any(nodes[c]['tag']=='PositionAnimation' and looping(nodes[c]) for c in n.get('children',[]))
            owner=n.get('parent')
        if automatic and not human:result.add(uid)
    return result


def constraint_rows(project,layout):
    """A looping crop keeps one reference edge, independent of sample time.

    Interior content retains sampled stable bounds for containment. Replacing
    those with an entry frame could erase a settled human size or position.
    """
    model=project['model'];cached=project.get('_autonomousPositionIds')
    if cached is None:
        cached=project['_autonomousPositionIds']=sorted(autonomous_position_ids(model))
    sources=project.get('sourceDevice',{});layouts=sources.get('layouts',[])
    cache_key=(id(layouts),len(layouts))
    crops=project.get('_autonomousCropIndices')
    if not crops or crops[0]!=cache_key:
        candidates={i for i,uid in enumerate(project['nodeOrder']) if uid in cached}
        width=sources.get('viewport',{}).get('width',0);indices=set()
        for source in layouts:
            for row in source.get('mediaLayout',{}).get('rows',[]):
                if row[0] in candidates and (row[1]<0<row[1]+row[3]<width or 0<row[1]<width<row[1]+row[3]):
                    indices.add(row[0])
        project['_autonomousCropIndices']=(cache_key,sorted(indices))
    else:indices=set(crops[1])
    refs={r[0]:r for r in layout.get('mediaLayout',{}).get('rows',[]) if r[0] in indices}
    return [refs.get(r[0],r) for r in layout.get('rows',[])]


def frame_index_names(model):
    """Only asset playback selectors, not clocks that switch whole UI pages."""
    from multires_app.engine.scene_engine import REF
    volatile=temporal_names(model)
    names={name for node in model['nodes'].values() if node['tag'] in {'Image','Video'}
           for field in ('srcid','srcExp') for name in REF.findall(node['attributes'].get(field,'')) if name in volatile}
    while True:
        expanded=names|{ref for name in names if name in model.get('variables',{})
                        for ref in REF.findall(model['variables'][name]['attributes'].get('expression','')) if ref in volatile}
        if expanded==names:return names
        names=expanded


def playback_guard_names(model):
    """Exclude media phases without erasing interaction-controlled UI poses."""
    from multires_app.engine.scene_engine import REF
    volatile=temporal_names(model);preserve=set()
    for node in model['nodes'].values():
        refs=set(REF.findall(node['attributes'].get('visibility','')))&volatile
        if node['tag']!='Group' or not refs:continue
        stack=list(node['children']);has_media=False
        while stack:
            child=model['nodes'][stack.pop()]
            if child['tag'] in {'Image','Video','ImageSeries'}:has_media=True;break
            stack.extend(child['children'])
        if not has_media:preserve.update(refs)
    preserve.update(page_animation_names(model))
    return (volatile-preserve)|(frame_index_names(model)-preserve)


def page_animation_names(model):
    """Compatibility API; classification lives in the interaction layer."""
    from multires_app.interaction.animation_semantics import scene_animation_names
    return scene_animation_names(model)


def reference(runtime, assets, node_order, asset_order):
    """Compact per-state reference, including a transparent first frame.

    Phase zero is relative to playback. A paused frame is still rendered at its
    paused phase, but cannot change the reference canvas used by the solver.
    """
    from multires_app.engine.scene_engine import Runtime
    ids=temporal_ids(runtime.model)
    if not ids:return None
    rt=Runtime(runtime.model,runtime.system['screen_width'],runtime.system['screen_height'],copy.deepcopy(runtime.state),time_ms=0)
    pages=page_animation_names(runtime.model)
    # A stop or assignment is an authored state, not a playback phase. Only
    # pure resource frame selectors may discard a paused value for layout.
    for name in frame_index_names(runtime.model)-pages:rt.state.pop(name,None)
    for name in pages:
        value=runtime.lookup(name)
        if name not in runtime.state and name in runtime.state.get('__animations',{}):
            node=runtime.model['variables'][name]
            animation=next(runtime.model['nodes'][c] for c in node['children'] if runtime.model['nodes'][c]['tag']=='VariableAnimation')
            frames=[runtime.model['nodes'][c]['attributes'] for c in animation['children'] if runtime.model['nodes'][c]['tag']=='AniFrame']
            if frames:
                destination=max(frames,key=lambda f:float(runtime.value(f.get('time',0))))
                value=runtime.value(destination.get('value',0))
        rt.state[name]=value
    rt.system=copy.deepcopy(runtime.system);rt.system['time']=0
    rt.reference_phase=True
    # Delayed appearance must have a transform even when phase zero is hidden.
    # Probe visibility only at fixed phases, never at the caller's current
    # phase, so the reference remains identical during playback. Human page
    # predicates are evaluated normally at every probe.
    def visible_ids():
        alpha_cache={}
        def alpha(uid):
            if uid in alpha_cache:return alpha_cache[uid]
            node=rt.model['nodes'][uid];value=float(rt.value(node['attributes'].get('alpha',255),0))/255
            for child in node['children']:
                animation=rt.model['nodes'][child]
                if animation['tag']=='AlphaAnimation':value*=float(rt.animation(animation,'a',255))/255
            if node['parent']:value*=alpha(node['parent'])
            alpha_cache[uid]=value;return value
        return {e['id'] for e in rt.logical_snapshot() if rt.model['nodes'][e['id']]['tag']=='Button' or alpha(e['id'])>0}
    possible=visible_ids()
    rt.reference_phase=False
    for playback in rt.state.get('__animations',{}).values():playback['started']=0
    for phase in (100,200,700,1600,2400):
        rt.time_ms=phase;rt.system['time']=phase;rt.cache.clear()
        possible.update(visible_ids())
    rt.time_ms=0;rt.system['time']=0;rt.reference_phase=True;rt.cache.clear()
    ni={uid:i for i,uid in enumerate(node_order)};ai={uid:i for i,uid in enumerate(asset_order)}
    # A translated page waiting outside the canvas is intentionally absent.
    # Keep its authored render rows, but never pull it into the active layout.
    inactive=set()
    if pages:
        from multires_app.engine.scene_engine import REF
        # Waiting/offscreen is an authored source-space property. Responsive
        # y expressions can temporarily move normal content above a short
        # target canvas before layout; that does not make it an inactive page.
        source_viewport=runtime.model.get('layoutSourceViewport',{
            'width':runtime.system['screen_width'],'height':runtime.system['screen_height']})
        source_rt=Runtime(runtime.model,source_viewport['width'],source_viewport['height'],copy.deepcopy(rt.state),time_ms=0)
        source_rt.system=copy.deepcopy(rt.system)
        source_rt.system.update(screen_width=source_viewport['width'],screen_height=source_viewport['height'])
        # Autonomous entry effects can themselves have a transparent or
        # offscreen first frame. Probe their fixed playback phases while
        # keeping the human-controlled pose pinned. They must not turn a
        # normal component into an interaction-waiting component.
        controlled_ids=set()
        for uid in ids:
            owner=uid
            while owner:
                node=runtime.model['nodes'][owner]
                if any(set(REF.findall(node['attributes'].get(a,'')))&pages
                       for a in ('x','y','w','h','width','height','alpha','visibility')):
                    controlled_ids.add(uid);break
                owner=node['parent']
        actual=[];source_rt.reference_phase=False
        for phase in (0,100,200,700,1600,2400):
            source_rt.time_ms=phase;source_rt.system['time']=phase;source_rt.cache.clear()
            actual.extend(source_rt.snapshot(assets,only_ids=controlled_ids))
        candidates={}
        for e in actual:
            if e['tag']=='Group' or e['id'] not in ni:continue
            if e['id'] in controlled_ids:candidates.setdefault(e['id'],[]).append(e)
        w=source_viewport['width'];h=source_viewport['height']
        for uid,elements in candidates.items():
            boxes=[e['rect'] for e in elements if e['visible'] and e['rect']['w']>0 and e['rect']['h']>0]
            outside=not boxes or all(b['x']>=w or b['x']+b['w']<=0 or b['y']>=h or b['y']+b['h']<=0 for b in boxes)
            if outside:inactive.add(ni[uid])
    rows=[];warnings=[]
    for e in rt.snapshot(assets,only_ids=ids):
        if e['id'] not in ids or e['id'] not in ni or e['id'] not in possible:continue
        b=dict(e['rect']);meta=assets.get(e['asset'],{});a=rt.model['nodes'][e['id']]['attributes']
        # Intro animations often start at scale zero. Reserve the first
        # asset's intrinsic canvas, rather than submitting an empty group.
        for dim,axis,align,center,end in [('w','x','align','center','right'),('h','y','alignV','center','bottom')]:
            natural=meta.get('width' if dim=='w' else 'height',0)
            if b[dim]<=0 and natural>0:
                factor=.5 if a.get(align)==center else 1 if a.get(align)==end else 0
                b[axis]-=natural*factor;b[dim]=natural
                warnings.append({'node':e['id'],'reason':'zero-first-frame-size-use-intrinsic-canvas','axis':dim})
        rows.append([ni[e['id']],*[round(b[k],3) for k in ('x','y','w','h')],1.,ai.get(e['asset'],-1),e['text'],e['size']])
        if b['w']<=0 or b['h']<=0:warnings.append({'node':e['id'],'reason':'first-frame-canvas-unresolved'})
    result={'policy':'first-frame-affine','phaseMs':0,'nodeIndices':sorted(ni[uid] for uid in ids if uid in ni),'rows':rows,'warnings':warnings}
    if pages:
        result['pageAnimationVariables']=sorted(pages)
        result['sceneAnimationVariables']=sorted(pages)
    if inactive:result['inactiveSceneNodeIndices']=sorted(inactive)
    return result


def inactive_indices(project,layout):
    """A sampled visible source pose outranks an old bounded phase probe."""
    inactive=set(layout.get('mediaLayout',{}).get('inactiveSceneNodeIndices',[]))
    source=project.get('sourceDevice',{});layouts=source.get('layouts',[]);vp=source.get('viewport',{})
    if not inactive or not layouts or not vp:return inactive
    key=(id(layouts),len(layouts));cached=project.get('_sourceActiveIndices')
    if cached is None or tuple(cached[:2])!=key:
        active={}
        for l in layouts:
            active[l.get('state')]=sorted({r[0] for r in l['rows'] if r[5]>0 and min(r[3:5])>0 and
                r[1]<vp['width'] and r[2]<vp['height'] and r[1]+r[3]>0 and r[2]+r[4]>0})
        cached=(key[0],key[1],active);project['_sourceActiveIndices']=cached
    return inactive-set(cached[2].get(layout.get('state'),[]))


def layout_rows(project, layout):
    """Only solver input is replaced. Preview rows remain current-frame data."""
    ref=layout.get('mediaLayout')
    if not ref:return layout['rows']
    indices=set(ref.get('nodeIndices',[]))
    if 'nodeIds' in ref:
        ids=set(ref['nodeIds']);indices.update(i for i,uid in enumerate(project['nodeOrder']) if uid in ids)
    current={r[0]:r for r in layout['rows']}
    # A temporal expression does not necessarily describe a media frame. Intro
    # easing on a static weather card must use its observed settled geometry;
    # freezing just some descendants at t=0 creates an enormous false frame.
    # True videos and frame sequences keep their canonical first-frame
    # reference. Looping crop inference is handled by constraint_rows instead.
    # Human page/visibility predicates remain unchanged.
    canonical=project.get('_canonicalMediaLayoutIds')
    if canonical is None:
        from multires_app.engine.scene_engine import REF
        model=project.get('model',{'nodes':{},'variables':{}});names=temporal_names(model)
        canonical=set()
        if not model['nodes']:canonical.update(project['nodeOrder'])
        for uid,node in model['nodes'].items():
            a=node['attributes']
            if (node['tag']=='Video' or any(model['nodes'][c]['tag']=='SourcesAnimation' for c in node['children']) or
                    any(set(REF.findall(a.get(k,'')))&names for k in ('srcid','srcExp'))):canonical.add(uid)
        project['_canonicalMediaLayoutIds']=sorted(canonical)
    canonical=set(canonical);references=[];replaced=set()
    for r in ref['rows']:
        if project['nodeOrder'][r[0]] not in canonical:continue
        references.append(current[r[0]] if min(r[3:5])<=0 and r[0] in current and min(current[r[0]][3:5])>0 else r)
        replaced.add(r[0])
    # Keep every occurrence of non-media UI (including bitmap clock glyphs),
    # even if a finite phase probe never observed its later appearance.
    inactive=inactive_indices(project,layout)
    return sorted([r for r in [*[r for r in layout['rows'] if r[0] not in replaced],*references] if r[0] not in inactive],key=lambda r:r[0])


def retain_waiting_components(project,device):
    """Keep dormant artwork outside the same edge of any target canvas.

    Identity alone is insufficient: a pose below a short source screen can
    become visible on a taller target. Waiting transforms are separate from
    active solver input and are selected by the normal human state guards.
    """
    from collections import defaultdict
    from multires_app.pipeline.scene_pipeline import state_guard
    for layout in device['layouts']:
        for cid in layout.pop('waitingComponents',[]):layout['transforms'].pop(cid,None)
    if not any(inactive_indices(project,l) for l in device['layouts']):return
    project.get('_stateGuards',{}).pop(device['profile']['id'],None)
    sources={l['state']:l for l in project.get('sourceDevice',{}).get('layouts',[])}
    source_vp=project.get('sourceDevice',{}).get('viewport',device['viewport'])
    states={s['id']:s for s in project['graph']['states']};families={};guards={};active_families={}
    for layout in device['layouts']:
        guard=state_guard(project,states[layout['state']],device);guards[layout['state']]=guard
        active={project['nodeComponents'].get(project['nodeOrder'][r[0]]) for r in layout_rows(project,layout)}
        for cid in active:
            if cid in layout['transforms']:active_families.setdefault((guard,cid),layout['transforms'][cid])
    for layout in device['layouts']:
        inactive=inactive_indices(project,layout)
        if not inactive:continue
        active={project['nodeComponents'].get(project['nodeOrder'][r[0]]) for r in layout_rows(project,layout)}
        source_rows=defaultdict(list);current_rows=defaultdict(list)
        for rows,store in ((sources.get(layout['state'],{}).get('rows',[]),source_rows),(layout['rows'],current_rows)):
            for row in rows:
                if row[0] in inactive and min(row[3:5])>0:
                    cid=project['nodeComponents'].get(project['nodeOrder'][row[0]])
                    if cid is not None:store[cid].append(row)
        guard=guards[layout['state']]
        for cid,rows in source_rows.items():
            # A family can already have an active pose for this component.
            # Its solved affine must also be used by the other media phases.
            if cid in active or not current_rows[cid]:continue
            if (guard,cid) in active_families:
                layout['transforms'][cid]=dict(active_families[guard,cid]);continue
            family=families.setdefault((guard,cid),{'layouts':[],'x':[-float('inf'),float('inf')],
                                                  'y':[-float('inf'),float('inf')]})
            family['layouts'].append(layout)
            for axis,start,size in (('x',1,3),('y',2,4)):
                extent='width' if axis=='x' else 'height'
                lo=min(r[start] for r in rows);hi=max(r[start]+r[size] for r in rows)
                current_lo=min(r[start] for r in current_rows[cid]);current_hi=max(r[start]+r[size] for r in current_rows[cid])
                if hi<=0:family[axis][1]=min(family[axis][1],hi-current_hi)
                elif lo>=source_vp[extent]:
                    family[axis][0]=max(family[axis][0],device['viewport'][extent]+lo-source_vp[extent]-current_lo)
    for (_,cid),family in families.items():
        transform={'sx':1.,'sy':1.,'tx':0.,'ty':0.}
        for axis in ('x','y'):
            low,high=family[axis]
            if low>high:raise ValueError('incompatible waiting edges for one human state: '+cid)
            transform['t'+axis]=max(low,min(0.,high))
        for layout in family['layouts']:
            layout['transforms'][cid]=dict(transform)
            layout.setdefault('waitingComponents',[]).append(cid)


def compact_references(project):
    """Avoid repeating long XML node paths in every device/state reference."""
    indices={uid:i for i,uid in enumerate(project['nodeOrder'])}
    for device in [*project['devices'],project.get('sourceDevice',{})]:
        for layout in device.get('layouts',[]):
            ref=layout.get('mediaLayout')
            if ref and 'nodeIds' in ref:
                ref['nodeIndices']=sorted(indices[uid] for uid in ref.pop('nodeIds') if uid in indices)


def refresh_references(project):
    """Upgrade cached projects without altering their sampled render rows."""
    from multires_app.engine.scene_engine import Runtime
    model=project['model'];model['assetNames']=list(project['assets'])
    source_ids={uid for uid,n in model['nodes'].items() if any(model['nodes'][c]['tag']=='SourcesAnimation' for c in n['children'])}
    for device in [*project['devices'],project.get('sourceDevice',{})]:
        if not device:continue
        vp=device['viewport'];replay_cache={}
        for layout in device['layouts']:
            rt=Runtime(model,vp['width'],vp['height'],layout.get('overrides',{}),time_ms=layout.get('timeMs',2400))
            ref=reference(rt,project['assets'],project['nodeOrder'],project['assetOrder'])
            if ref:layout['mediaLayout']=ref
            else:layout.pop('mediaLayout',None)
            if source_ids:
                # Old caches had no SourcesAnimation renderer. Refresh only
                # these rows; unrelated cached controls and geometry stay intact.
                from multires_app.pipeline.source_comparison import packed_snapshot
                from multires_app.interaction.interaction_semantics import replay_human_state
                state=next(s for s in project['graph']['states'] if s['id']==layout['state'])
                replay=replay_human_state(model,vp['width'],vp['height'],state,project['assets'],verify_hit=False,cache=replay_cache)
                evaluated=packed_snapshot(project,replay,layout['state'])
                layout['rows']=sorted([r for r in layout['rows'] if project['nodeOrder'][r[0]] not in source_ids]+
                                      [r for r in evaluated['rows'] if project['nodeOrder'][r[0]] in source_ids],key=lambda r:r[0])
    refresh_media_metadata(project)


def refresh_media_metadata(project):
    """Preserve script play/pause in cached target rows without re-layout."""
    from multires_app.engine.scene_engine import Runtime
    model=project['model']
    source_times={l['state']:l.get('timeMs',2400) for l in project.get('sourceDevice',{}).get('layouts',[])}
    for device in [*project['devices'],project.get('sourceDevice',{})]:
        if not device:continue
        vp=device['viewport']
        for layout in device['layouts']:
            runtime=None
            for row in layout['rows']:
                uid=project['nodeOrder'][row[0]];node=model['nodes'][uid]
                if node['tag']!='Video':continue
                if 'timeMs' not in layout:layout['timeMs']=source_times.get(layout.get('state'),2400)
                if runtime is None:runtime=Runtime(model,vp['width'],vp['height'],layout.get('overrides',{}),time_ms=layout.get('timeMs',2400))
                a=node['attributes']
                while len(row)<11:row.append(None)
                row[9]=project['assetOrder'][row[6]] if row[6]>=0 else a.get('src','')
                row[10]={'playing':bool(runtime.value(a.get('play',1),1,uid+':play')),'looping':a.get('looping','true')!='false'}


def intrinsic_expression(model, assets, attributes, axis, node=None):
    """Natural canvas size for *every* indexed frame, including unsampled ones.

    A constant first-frame size here would stretch later unequal-size frames.
    Support indexed assets with a static prefix; other dynamic paths retain the
    existing sampled-state export and are explicitly reported by the exporter.
    """
    from multires_app.engine.scene_engine import resolve_asset
    if node:
        animation=next((model['nodes'][c] for c in node['children'] if model['nodes'][c]['tag']=='SourcesAnimation'),None)
        if animation:
            frames=[model['nodes'][c]['attributes'] for c in animation['children'] if model['nodes'][c]['tag']=='Source']
            sizes=[assets.get(resolve_asset({**model,'assetNames':list(assets)},f.get('src','')),{}).get(axis,0) for f in frames]
            if sizes and len(set(sizes))==1:return str(sizes[0])
            if frames:
                duration=frames[-1].get('time','0');a=animation['attributes'];delay=a.get('delay','0')
                phase=f'max(0,#time-({delay}))'
                phase=f'({phase})%({duration})' if a.get('loop','false' if a.get('repeat')=='1' else 'true')!='false' else f'min({phase},({duration}))'
                args=[]
                for f,size in zip(frames,sizes):args.extend([f'le({phase},({f.get("time","0")}))',str(size)])
                return 'ifelse('+','.join(args+[str(sizes[-1])])+')'
    src=attributes.get('src','');index=attributes.get('srcid');expression=attributes.get('srcExp')
    if expression:
        # Common string sequence: 'frames/pic_' + int(#frame) + '.png'.
        prefix=re.match(r'''^\s*(['"])(.*?)\1\s*\+''',expression)
        suffix=re.search(r'''\+\s*(['"])(\.[A-Za-z0-9]+)\1\s*$''',expression)
        if not prefix or not suffix or index:return None
        temp={**model,'assetNames':list(assets)}
        stem=resolve_asset(temp,prefix[2]);ext=suffix[2]
        matches={p:m[axis] for p,m in assets.items() if p.startswith(stem) and p.endswith(ext) and p[len(stem):-len(ext)].lstrip('-').isdigit()}
        if not matches:return None
        if len(set(matches.values()))==1:
            middle=expression[prefix.end():suffix.start()].strip()
            indices=[int(p[len(stem):-len(ext)]) for p in matches]
            guard=f'eq(({middle}),int({middle}))*({index_guard(middle,indices)})'
            return f'ifelse({guard},{next(iter(matches.values()))},0)'
        args=[]
        for path,size in sorted(matches.items()):
            relative=posixpath.relpath(path,posixpath.dirname(model['file']))
            args.extend([f'eq(({expression}),{relative!r})+eq(({expression}),{path!r})',str(size)])
        return 'ifelse('+','.join(args+['0'])+')'
    if not index:return None
    base,ext=posixpath.splitext(resolve_asset({**model,'assetNames':list(assets)},src))
    pattern=re.compile(re.escape(base)+r'_(-?\d+)'+re.escape(ext)+r'$')
    dimensions={}
    for path,meta in assets.items():
        match=pattern.fullmatch(path)
        if match:dimensions[int(match[1])]=meta[axis]
    if not dimensions:return None
    values=set(dimensions.values())
    # The existing reference interpreter cannot parse leading-digit variable
    # names (e.g. #12xs). Do not nest that unresolved expression into w/h and
    # turn an otherwise valid, uniformly sized native sequence into zero size.
    if re.search(r'[#@]\d',index):return str(next(iter(values))) if len(values)==1 else None
    if len(values)==1:return f'ifelse({index_guard(index,dimensions)},{next(iter(values))},0)'
    args=[]
    for frame,size in sorted(dimensions.items()):args.extend([f'eq(int({index}),{frame})',str(size)])
    return 'ifelse('+','.join(args+['0'])+')'
