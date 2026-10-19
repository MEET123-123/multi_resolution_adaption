"""Geometry/structure clustering and incremental layout constraints.

Content (text, asset, alpha) is intentionally separate from layout identity.
Classifications include evidence and are editable policy decisions, not proof
of designer intent.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter, defaultdict


IDENTITY={"sx":1.,"sy":1.,"tx":0.,"ty":0.}


def box_union(rectangles):
    rectangles=[b for b in rectangles if b["w"]>0 and b["h"]>0]
    if not rectangles:return {"x":0.,"y":0.,"w":0.,"h":0.}
    x=min(b["x"] for b in rectangles);y=min(b["y"] for b in rectangles)
    return {"x":x,"y":y,"w":max(b["x"]+b["w"] for b in rectangles)-x,"h":max(b["y"]+b["h"] for b in rectangles)-y}


def overlap(a,b):
    return max(0,min(a["x"]+a["w"],b["x"]+b["w"])-max(a["x"],b["x"]))*max(0,min(a["y"]+a["h"],b["y"]+b["h"])-max(a["y"],b["y"]))


def near_box(a,b,tolerance=.5):return all(abs(a[k]-b[k])<=tolerance for k in ("x","y","w","h"))


def describe_layout(project,layout):
    from multires_app.media.media_layout import layout_rows
    groups=defaultdict(list)
    for row in layout_rows(project,layout):
        uid=project["nodeOrder"][row[0]];cid=project["nodeComponents"].get(uid)
        if cid:groups[cid].append((uid,row,project["model"]["nodes"][uid]))
    descriptors={}
    model=project["model"]
    from multires_app.engine.scene_engine import REF
    if '_volatileNames' not in project:
        names={'time'}
        dependencies={name:set(REF.findall(var['attributes'].get('expression',''))) for name,var in model['variables'].items()}
        names.update(name for name,var in model['variables'].items() if any(model['nodes'][child]['tag']=='VariableAnimation' for child in var['children']))
        while True:
            expanded=names|{name for name,refs in dependencies.items() if refs&names}
            if expanded==names:break
            names=expanded
        project['_volatileNames']=sorted(names)
    volatile_names=set(project['_volatileNames'])
    def volatile(expression):return bool(set(REF.findall(expression))&volatile_names)
    for comp in project["components"]:
        rows=groups.get(comp["id"],[])
        if not rows:continue
        sizing=[(uid,row,node) for uid,row,node in rows if uid not in comp.get('layoutFollowers',[]) and node['tag']!='Button']
        bounds=box_union([dict(zip(("x","y","w","h"),row[1:5])) for _,row,_ in (sizing or rows)])
        geometry=[]
        for uid,row,node in rows:
            box=list(row[1:5])
            if node["tag"]=="Text" and not any(k in node["attributes"] for k in ("w","width")):
                box[2]=0 # elastic text content changes keep the same anchor/font
            quantized=[round(float(v)*2)/2 for v in box]
            if any(model['nodes'][child]['tag']=='PositionAnimation' for child in node['children']):quantized[0]=quantized[1]='animated'
            for i,attr in enumerate(("x","y","w","h")):
                if volatile(node["attributes"].get(attr,"")):quantized[i]="animated"
            parent=node["parent"]
            while parent:
                ancestor=model["nodes"][parent]
                if any(model["nodes"][child]["tag"]=="PositionAnimation" for child in ancestor["children"]):quantized[0]=quantized[1]="animated-parent"
                for i,axis in enumerate(("x","y")):
                    if volatile(ancestor["attributes"].get(axis,"")):quantized[i]="animated-parent"
                parent=ancestor["parent"]
            geometry.append((uid,tuple(quantized)))
        geometry.sort()
        shape=sorted((node["tag"],round((row[1]-bounds["x"])/max(1,bounds["w"]),2),
                      round((row[2]-bounds["y"])/max(1,bounds["h"]),2),
                      round(row[3]/max(1,bounds["w"]),2),round(row[4]/max(1,bounds["h"]),2)) for _,row,node in rows)
        text=" ".join(str(node["attributes"]) for _,_,node in rows).lower()+" "+comp["name"].lower()
        hint=comp.get("role") in {"popup","overlay","dialog"} or any(token in text for token in ("popup","dialog","modal","弹窗","对话框","drawer","sheet"))
        descriptors[comp["id"]]={"id":comp["id"],"role":comp["role"],"bounds":bounds,"geometry":geometry,"shape":shape,
                                  "members":sorted({uid for uid,_,_ in rows}),"modalHint":hint,
                                  "backdropHint":any(("mask" in str(node["attributes"]).lower() or "shade" in str(node["attributes"]).lower()) and row[5]<.95 for _,row,node in rows),
                                  "content":[(uid,row[6],row[7],row[5]) for uid,row,_ in rows],"z":max(row[0] for _,row,_ in rows)}
    from multires_app.layout.visual_geometry import component_visuals
    visuals=component_visuals(project,layout,canonical=True)
    for cid,d in descriptors.items():d['visualBounds']=visuals.get(cid,d['bounds'])
    return descriptors


def same_geometry(a,b):
    # JSON persistence turns tuples into lists; authored anchors must survive it.
    return a["geometry"]==b["geometry"] or json.dumps(a["geometry"],separators=(',',':'))==json.dumps(b["geometry"],separators=(',',':'))


def shape_match(a,b):
    if a["role"]!=b["role"] or len(a["shape"])!=len(b["shape"]):return False
    if not a["shape"]:return False
    for x,y in zip(a["shape"],b["shape"]):
        if x[0]!=y[0] or any(abs(v-w)>.021 for v,w in zip(x[1:],y[1:])):return False
    ba,bb=a["bounds"],b["bounds"]
    aspect_a=ba["w"]/max(1,ba["h"]);aspect_b=bb["w"]/max(1,bb["h"])
    return abs(aspect_a-aspect_b)<=max(.02,aspect_a*.02)


def correspondence(current,base):
    if len(current)!=len(base):return None
    mapping={};used=set()
    for cid,desc in current.items():
        if cid in base and same_geometry(desc,base[cid]):mapping[cid]=cid;used.add(cid)
    for cid,desc in current.items():
        if cid in mapping:continue
        candidates=[key for key,value in base.items() if key not in used and shape_match(desc,value)]
        # Ambiguous repeated shapes require review; do not pair by list order.
        if len(candidates)!=1:return None
        mapping[cid]=candidates[0];used.add(candidates[0])
    return mapping


def classify_layouts(project,device,allow_shape_reuse=True):
    descriptions={layout["state"]:describe_layout(project,layout) for layout in device["layouts"]}
    counts=Counter(cid for desc in descriptions.values() for cid in desc)
    shared=[]
    for cid,count in counts.items():
        versions=[desc[cid] for desc in descriptions.values() if cid in desc]
        if count/max(1,len(descriptions))>=.6 and all(same_geometry(versions[0],v) for v in versions[1:]):shared.append(cid)
    processed=[];requests={};clusters=[];fingerprints={}
    incoming=defaultdict(list)
    for edge in project["graph"]["transitions"]:
        if edge.get("to"):incoming[edge["to"]].append(edge["from"])
    w,h=device["viewport"]["width"],device["viewport"]["height"]
    for layout in device["layouts"]:
        sid=layout["state"];current=descriptions[sid]
        fingerprint=json.dumps([(cid,d["geometry"]) for cid,d in sorted(current.items())],sort_keys=True)
        representative=fingerprints.get(fingerprint)
        reuse_mapping=None
        if representative is not None:reuse_mapping={cid:cid for cid in current}
        elif allow_shape_reuse:
            for previous in processed:
                match=correspondence(current,descriptions[previous])
                if match is not None:
                    representative=previous;reuse_mapping=match;break
        if representative is not None:
            ancestor=requests[representative]
            requests[sid]={"kind":"reuse","baseState":representative,"clusterId":ancestor["clusterId"],"familyId":ancestor["familyId"],
                           "fixedComponents":list(current),"affectedComponents":[],"correspondence":reuse_mapping,
                           "evidence":["same evaluated geometry" if fingerprint in fingerprints else "unique role/shape correspondence"],"reviewRequired":fingerprint not in fingerprints}
            next(c for c in clusters if c["id"]==ancestor["clusterId"])["states"].append(sid)
            processed.append(sid);fingerprints.setdefault(fingerprint,sid);continue
        candidates=[s for s in incoming[sid] if s in requests]
        if not candidates and processed:
            candidates=[max(processed,key=lambda s:len(set(descriptions[s])&set(current)))]
        base_sid=candidates[0] if candidates else None;base=descriptions.get(base_sid,{})
        stable=[cid for cid in current if cid in base and same_geometry(current[cid],base[cid])]
        affected=[cid for cid in current if cid not in stable]
        removed=[cid for cid in base if cid not in current]
        substantive={cid for cid,d in base.items() if d["role"] not in {"background","gesture-layer"}}
        stable_ratio=len(set(stable)&substantive)/max(1,len(substantive))
        new=[cid for cid in affected if cid not in base]
        new_box=box_union([current[cid]["bounds"] for cid in new if not current[cid]["backdropHint"] and current[cid]["role"]!="gesture-layer"])
        compact=new_box["w"]*new_box["h"]<=w*h*.65
        retained=not removed and stable_ratio>=.8
        explicit=any(current[cid]["modalHint"] or current[cid]["backdropHint"] for cid in new)
        geometric=bool(new) and new_box["w"]*new_box["h"]<=w*h*.4 and any(overlap(new_box,base[cid]["bounds"])>new_box["w"]*new_box["h"]*.25 for cid in stable) and min(current[cid]["z"] for cid in new)>max((base[cid]["z"] for cid in stable),default=-1)
        if base_sid and retained and new and compact and (explicit or geometric):
            kind="overlay";evidence=["base geometry retained","compact higher layer added","modal/backdrop hint" if explicit else "overlap + paint order heuristic"]
        elif base_sid and stable and stable_ratio>=.55:
            kind="local-reflow";evidence=["stable components anchored",f"{len(affected)} changed / {len(stable)} retained"]
        else:kind="full-reflow";evidence=["initial layout" if base_sid is None else "structural change exceeds local threshold"]
        cluster_id=f"layout-{len(clusters):03d}";family=requests[base_sid]["familyId"] if base_sid and kind!="full-reflow" else cluster_id
        if kind=="full-reflow":stable=[];affected=list(current)
        request={"kind":kind,"baseState":base_sid,"clusterId":cluster_id,"familyId":family,"fixedComponents":stable,
                 "affectedComponents":affected,"removedComponents":removed,"correspondence":{},"evidence":evidence,
                 "sharedComponents":[cid for cid in stable if cid in shared],"reviewRequired":kind=="overlay" and not explicit}
        requests[sid]=request;clusters.append({"id":cluster_id,"representative":sid,"states":[sid],"kind":kind,"familyId":family})
        processed.append(sid);fingerprints[fingerprint]=sid
    return {"requests":requests,"clusters":clusters,"sharedComponents":shared,"descriptions":descriptions,
            "summary":{"states":len(descriptions),"layoutTemplates":len(clusters),"strategyCounts":dict(Counter(r["kind"] for r in requests.values()))}}


def reuse_transform(current,base,transform):
    if same_geometry(current,base):return dict(transform)
    a=current["bounds"];b=base["bounds"]
    ratio=min(b["w"]/max(.001,a["w"]),b["h"]/max(.001,a["h"]))
    sx=transform["sx"]*ratio;sy=transform["sy"]*ratio
    return {"sx":sx,"sy":sy,"tx":transform["tx"]+(b["x"]+b["w"]/2)*transform["sx"]-(a["x"]+a["w"]/2)*sx,
            "ty":transform["ty"]+(b["y"]+b["h"]/2)*transform["sy"]-(a["y"]+a["h"]/2)*sy}


def transformed_box(bounds,t):
    return {"x":bounds["x"]*t["sx"]+t["tx"],"y":bounds["y"]*t["sy"]+t["ty"],"w":bounds["w"]*t["sx"],"h":bounds["h"]*t["sy"]}


def free_regions(viewport,reserved):
    rectangles=[{"x":0.,"y":0.,"w":viewport["width"],"h":viewport["height"]}]
    for block in reserved:
        result=[]
        for r in rectangles:
            if not overlap(r,block):result.append(r);continue
            x=max(r["x"],block["x"]);y=max(r["y"],block["y"]);right=min(r["x"]+r["w"],block["x"]+block["w"]);bottom=min(r["y"]+r["h"],block["y"]+block["h"])
            result.extend([{"x":r["x"],"y":r["y"],"w":r["w"],"h":y-r["y"]},
                           {"x":r["x"],"y":bottom,"w":r["w"],"h":r["y"]+r["h"]-bottom},
                           {"x":r["x"],"y":y,"w":x-r["x"],"h":bottom-y},
                           {"x":right,"y":y,"w":r["x"]+r["w"]-right,"h":bottom-y}])
        rectangles=[r for r in result if r["w"]>1 and r["h"]>1]
    return rectangles


def fit(bounds,region):
    scale=min(region["w"]/max(1,bounds["w"]),region["h"]/max(1,bounds["h"]))
    return {"sx":scale,"sy":scale,"tx":region["x"]+(region["w"]-bounds["w"]*scale)/2-bounds["x"]*scale,
            "ty":region["y"]+(region["h"]-bounds["h"]*scale)/2-bounds["y"]*scale}


def constrained_reflow(components,context):
    request=context["layoutRequest"];viewport=context["logicalViewport"];w,h=viewport["width"],viewport["height"]
    base=context.get("baseDescriptors",{});base_transforms=context.get("baseTransforms",{})
    output={};new=[]
    for comp in components:
        cid=comp["id"]
        if not comp["visible"]:continue
        if comp["role"]=="gesture-layer":output[cid]=dict(IDENTITY);continue
        if cid in base and cid in base_transforms and request["kind"]!="overlay":
            output[cid]=fit(comp["bounds"],transformed_box(base[cid]["bounds"],base_transforms[cid]))
        else:new.append(comp)
    if new:
        if request["kind"]=="overlay":
            region={"x":w*.09,"y":h*.12,"w":w*.82,"h":h*.76}
            panels=[]
            for comp in new:
                b=comp["bounds"]
                if b["w"]*b["h"]>w*h*.85:
                    output[comp["id"]]={"sx":w/max(1,b["w"]),"sy":h/max(1,b["h"]),"tx":-b["x"]*w/max(1,b["w"]),"ty":-b["y"]*h/max(1,b["h"])}
                else:panels.append(comp)
        else:
            reserved=[transformed_box(base[cid]["bounds"],t) for cid,t in context["fixedTransforms"].items() if cid in base and base[cid]["role"] not in {"background","gesture-layer"}]
            available=free_regions(viewport,reserved)
            if not available:
                # Explicit conflict; scheduler promotes to whole-page layout.
                raise LayoutConflict("局部新增组件没有可用区域")
            region=max(available,key=lambda r:r["w"]*r["h"]);panels=new
        bounds=box_union([comp["bounds"] for comp in panels])
        if panels:
            t=fit(bounds,region)
            for comp in panels:output[comp["id"]]=dict(t)
    return output


class LayoutConflict(ValueError):pass


def schedule_device(project,device,algorithm,preserve_existing=False,_forced_full=None,_resume_prefix=0):
    forced_full=set(_forced_full or [])
    project.get('_stateGuards',{}).pop(device['profile']['id'],None)
    if getattr(algorithm,'name','')!='anchor-theme-layout-v1':
        for key in ('sharedResourceScales','resourceScaleFamilies','nativeGlyphResourceScales'):device.pop(key,None)
    from multires_app.layout.visual_geometry import ensure_visual_bounds,component_visuals
    ensure_visual_bounds(project)
    analysis=classify_layouts(project,device,allow_shape_reuse=getattr(algorithm,'allow_shape_reuse',True));layouts={l["state"]:l for l in device["layouts"]}
    states={s["id"]:s for s in project["graph"]["states"]};comp_map={c["id"]:c for c in project["components"]}
    reference_layouts={l["state"]:l for l in project.get("sourceDevice",{}).get("layouts",[])}
    source_descriptions={sid:describe_layout(project,layout) for sid,layout in reference_layouts.items()}
    source_visuals={sid:component_visuals(project,l) for sid,l in reference_layouts.items()}
    target_visuals={sid:component_visuals(project,l) for sid,l in layouts.items()}
    from multires_app.layout.visual_geometry import component_frame_bounds
    source_frames={sid:component_frame_bounds(project,l) for sid,l in reference_layouts.items()}
    target_frames={sid:component_frame_bounds(project,l) for sid,l in layouts.items()}
    from multires_app.layout.visual_geometry import background_rects
    source_backgrounds={sid:background_rects(project,l) for sid,l in reference_layouts.items()}
    target_backgrounds={sid:background_rects(project,l) for sid,l in layouts.items()}
    from multires_app.layout.canvas_bounds import stable_resources
    stable_by_state={l['state']:stable_resources(project,l,reference_layouts.get(l['state'])) for l in device['layouts']}
    native_families={};native_first={};native_resources={};native_flow_samples={};native_flow_keys={}
    if getattr(algorithm,'name','')=='anchor-theme-layout-v1' and 'controls' in project['model']:
        from multires_app.pipeline.scene_pipeline import state_guard
        project.get('_stateGuards',{}).pop(device['profile']['id'],None)
        for l in device['layouts']:
            guard=state_guard(project,states[l['state']],device);native_families[l['state']]=guard
            native_first.setdefault(guard,l['state'])
            sample=(source_visuals.get(l['state'],{}),target_visuals.get(l['state'],{}))
            signature=tuple(tuple((cid,*(box[k] for k in ('x','y','w','h'))) for cid,box in sorted(boxes.items())) for boxes in sample)
            if signature not in native_flow_keys.setdefault(guard,set()):
                native_flow_keys[guard].add(signature);native_flow_samples.setdefault(guard,[]).append(sample)
            resources=native_resources.setdefault(guard,{})
            for e in stable_by_state[l['state']]:
                resources[(e['component'],e['id'],tuple(e['rect'][k] for k in ('x','y','w','h')))]=e
        # Playback phase cannot select an affine in native scripts. Fit one
        # transform to all sampled stable poses sharing the same human guard.
        stable_by_state={sid:list(native_resources[guard].values()) for sid,guard in native_families.items()}
        # A native guard has one transform even when a playback phase reveals
        # another child. Solve its complete component vocabulary up front.
        family_desc={};family_source={}
        for state_id,guard in native_families.items():
            for store,descriptions in [(family_desc,analysis['descriptions'][state_id]),(family_source,source_descriptions.get(state_id,{}))]:
                for cid,desc in descriptions.items():
                    prior=store.setdefault(guard,{}).get(cid)
                    if prior is None or min(prior['bounds']['w'],prior['bounds']['h'])<=0:store[guard][cid]=desc
        for state_id,guard in native_families.items():
            def complete(current,reference):
                return {cid:current[cid] if cid in current and min(current[cid]['bounds']['w'],current[cid]['bounds']['h'])>0 else desc for cid,desc in reference.items()}
            analysis['descriptions'][state_id]=complete(analysis['descriptions'][state_id],family_desc[guard])
            source_descriptions[state_id]=complete(source_descriptions.get(state_id,{}),family_source.get(guard,{}))
    describe=getattr(algorithm,"describe_adaptation",None)
    corner_refs={};corner_scores={};inactive_corners={}
    corner_components=[c for c in project['components'] if c.get('cornerAttachment')]
    if corner_components and getattr(algorithm,'name','')=='anchor-theme-layout-v1':
        from multires_app.layout.corner_geometry import component_frame,at_source_corner
        for state_id,source_layout in reference_layouts.items():
            guard=native_families.get(state_id,state_id);refs=corner_refs.setdefault(guard,{})
            scores=corner_scores.setdefault(guard,{})
            for c in corner_components:
                if state_id not in layouts:continue
                frame=component_frame(project,source_layout,c['id'])
                if at_source_corner(frame,c['cornerAttachment'],project['sourceDevice']['viewport'],tolerance=1.):
                    a=c['cornerAttachment'];vp=project['sourceDevice']['viewport'];m=a['sourceMargins']
                    mx=frame['x'] if a['corner']=='bottom-left' else vp['width']-frame['x']-frame['w'];my=vp['height']-frame['y']-frame['h']
                    error=max(abs(mx-m['x']),abs(my-m['y']))
                    if error>=scores.get(c['id'],float('inf')):continue
                    target_frame=component_frame(project,layouts[state_id],c['id'])
                    if min(target_frame['w'],target_frame['h'])>0:refs[c['id']]=target_frame;scores[c['id']]=error
        for state_id in layouts:
            refs=corner_refs.get(native_families.get(state_id,state_id),{})
            inactive_corners[state_id]={c['id'] for c in corner_components if c['id'] not in refs}
    if callable(describe):
        device["adaptation"]=describe(project["sourceProfile"],device["profile"],{"model":project["model"],"logicalViewport":device["viewport"]})
    else:device.pop("adaptation",None)
    calls=0;submitted=0;promotions=[];glyph_scales={}
    if getattr(algorithm,'name','')=='anchor-theme-layout-v1':
        from multires_app.media.resource_scaling import glyph_components
        from multires_app.layout.adaptive_layout import safe_region
        from multires_app.layout.anchor_layout import settings
        opts=settings(project['sourceProfile'],device['profile']);region=safe_region(project['sourceProfile'],device['profile'],{'logicalViewport':device['viewport']})
        source_vp=project.get('sourceDevice',{}).get('viewport',device['viewport'])
        from multires_app.layout.visual_scaling import resource_scale
        sizing=resource_scale(source_vp,region,opts,device['profile']);preferred=sizing['scale']
        if not opts.get('groups') and not opts.get('constraints') and not any(c.get('layout') for c in project['components']):
            for cid in glyph_components(project):
                sizes=[d[cid]['bounds'] for d in analysis['descriptions'].values() if cid in d and min(d[cid]['bounds']['w'],d[cid]['bounds']['h'])>0]
                for resources in stable_by_state.values():
                    boxes=[e['rect'] for e in resources if e['component']==cid]
                    if boxes:sizes.append(box_union(boxes))
                if sizes:glyph_scales[cid]=max(float(opts.get('minScale',.03)),min([preferred,*[min(region['w']/b['w'],region['h']/b['h']) for b in sizes]]))
        if preserve_existing:glyph_scales={**glyph_scales,**device.get('nativeGlyphResourceScales',{})}
        from multires_app.layout.panel_scaling import shared_resource_scales
        shared_scales=shared_resource_scales(project,device,preferred,glyph_scales,region)
        glyph_scales={cid:shared_scales.get(cid,s) for cid,s in glyph_scales.items()}
        device['sharedResourceScales']=shared_scales
        device['nativeGlyphResourceScales']=glyph_scales
    for layout_index,layout in enumerate(device["layouts"]):
        sid=layout["state"];request=analysis["requests"][sid];current=analysis["descriptions"][sid]
        if layout_index<_resume_prefix:continue
        if preserve_existing and layout.get("layoutRequest") and layout.get("transforms"):
            continue
        family_base=native_first.get(native_families.get(sid))
        if family_base and family_base!=sid:
            shared=set(current)&set(layouts[family_base].get('transforms',{}))
            request['baseState']=family_base
            request['fixedComponents']=sorted(shared)
            request['affectedComponents']=sorted(set(current)-shared)
            request['kind']='local-reflow' if request['affectedComponents'] else 'reuse'
            request['correspondence']={cid:cid for cid in shared}
            request['evidence'].append('identical native human guard; one affine across playback phases')
        # Canonical first-frame geometry may be identical while a human action
        # brings a waiting panel onscreen. Such resources are not frozen public
        # content; compare settled geometry as well as the layout signature.
        if getattr(algorithm,'name','')=='anchor-theme-layout-v1' and request.get('baseState'):
            def settled_signature(state):
                result={}
                for e in stable_by_state.get(state,[]):
                    result.setdefault(e['component'],set()).add(e['id'])
                return {cid:sorted(rows) for cid,rows in result.items()}
            now=settled_signature(sid);prior=settled_signature(request['baseState'])
            changed={cid for cid in current if now.get(cid,[])!=prior.get(cid,[])}
            changed.update((inactive_corners.get(sid,set())^inactive_corners.get(request['baseState'],set()))&set(current))
            if not (family_base and family_base!=sid):
                # Equal node IDs do not imply an unchanged layout: human
                # controls can move the same visual block to another anchor.
                src_now=source_visuals.get(sid,{});src_before=source_visuals.get(request['baseState'],{})
                changed.update(cid for cid in current if cid in src_now and cid in src_before and not near_box(src_now[cid],src_before[cid]))
            from multires_app.layout.canvas_bounds import violations
            previous_mapping=layouts[request['baseState']].get('transforms',{})
            changed.update(e['component'] for e in violations(stable_by_state[sid],previous_mapping,region))
            if changed:
                if request['kind']=='reuse':
                    request['kind']='local-reflow';request['fixedComponents']=[cid for cid in current if cid not in changed]
                    request['affectedComponents']=sorted(changed)
                else:
                    released=changed & set(request['fixedComponents'])
                    request['fixedComponents']=[cid for cid in request['fixedComponents'] if cid not in released]
                    request['affectedComponents']=sorted(set(request['affectedComponents'])|released)
                request['evidence'].append('settled visible geometry changed; re-layout newly active or moved content')
        missing=set(current)-set(request.get('fixedComponents',[]))-set(request.get('affectedComponents',[]))
        if missing:
            request['affectedComponents']=sorted(set(request.get('affectedComponents',[]))|missing)
            if request['kind']=='reuse':request['kind']='local-reflow'
            request['evidence'].append('include children revealed later under the same native guard before freezing the family')
        if (getattr(algorithm,'name','')=='anchor-theme-layout-v1' and request.get('baseState') and
                not (family_base and family_base!=sid) and
                not opts.get('groups') and not opts.get('constraints') and not any(c.get('layout') for c in project['components'])):
            from multires_app.layout.spatial_flow import layout_flows,insertion_blockers,flow_errors,infer_flows
            prior_mapping=layouts[request['baseState']].get('transforms',{})
            flows=layout_flows(project,device,layout)
            for src,dst in native_flow_samples.get(native_families.get(sid),[]):
                es=[{'id':cid,'bbox':b,'current_visual_bbox':dst[cid],
                     'cornerShortcut':bool(comp_map[cid].get('cornerAttachment')),
                     'special':comp_map[cid]['role'] in {'background','gesture-layer','decoration','interaction-layer'}} for cid,b in src.items() if cid in dst]
                flows.extend(infer_flows(es,source_vp,region,preferred))
            blockers=insertion_blockers(flows,target_visuals.get(sid,{}),prior_mapping,
                                        request['affectedComponents'],request['fixedComponents'],region,sizing['minScale'])
            fixed_ids=set(request['fixedComponents'])
            for failure in flow_errors(flows,prior_mapping):
                if {failure['first'],failure['last']}<=fixed_ids:blockers.update([failure['first'],failure['last']])
            if blockers:
                request['fixedComponents']=[cid for cid in request['fixedComponents'] if cid not in blockers]
                request['affectedComponents']=sorted(set(request['affectedComponents'])|blockers)
                if request['kind']=='reuse':request['kind']='local-reflow'
                request['evidence'].append('new source-ordered content has no canvas interval beside edge-locked peers; include blocking peers in local reflow')
        if native_families.get(sid) in forced_full and family_base==sid:
            request['kind']='full-reflow';request['fixedComponents']=[];request['affectedComponents']=sorted(current)
            request['evidence'].append('complete native family requires shared full reflow to fit all sampled phases')
        # Family-wide requests can mention a page that is now offscreen.
        # Inactive pages must retain native positions, not be solved or frozen
        # using a visible sibling state's descriptor.
        for field in ('fixedComponents','affectedComponents'):
            request[field]=[cid for cid in request[field] if cid in current]
        request['correspondence']={cid:other for cid,other in request.get('correspondence',{}).items() if cid in current}
        base_layout=layouts.get(request["baseState"]);base_desc=analysis["descriptions"].get(request["baseState"],{})
        base_transforms=base_layout.get("transforms",{}) if base_layout else {}
        context={"model":project["model"],"logicalViewport":device["viewport"],"layoutRequest":request,
                 "mediaLayout":layout.get('mediaLayout'),
                 "nodeOrder":project['nodeOrder'],
                 "componentDefinitions":comp_map,
                 "nativeGlyphResourceScales":glyph_scales,
                 "sharedResourceScales":device.get('sharedResourceScales',{}),
                 "sourceStableVisualBounds":source_visuals.get(sid,{}),
                 "stableVisualBounds":target_visuals.get(sid,{}),
                 "sourceFrameBounds":source_frames.get(sid,{}),"frameBounds":target_frames.get(sid,{}),
                 "sourceBackgroundBounds":source_backgrounds.get(sid,{}),"backgroundBounds":target_backgrounds.get(sid,{}),
                 "flowSamples":native_flow_samples.get(native_families.get(sid),[(source_visuals.get(sid,{}),target_visuals.get(sid,{}))]),
                 "stableResources":stable_by_state[sid],
                 "cornerTargetFrameBounds":corner_refs.get(native_families.get(sid,sid),{}),
                 "inactiveCornerComponents":sorted(inactive_corners.get(sid,set())),
                 "anchorIRStore":project.setdefault('anchorIRs',{}),
                 "baseTransforms":base_transforms,"baseDescriptors":base_desc,
                 "sourceComponentDescriptors":source_descriptions.get(sid,{}),
                 "baseSourceComponentDescriptors":source_descriptions.get(request['baseState'],{}),
                 "basePlan":base_layout.get('adaptationPlan',{}) if base_layout else {}}
        if request["kind"]=="reuse":
            mapping={cid:reuse_transform(current[cid],base_desc[source],base_transforms.get(source,IDENTITY)) for cid,source in request["correspondence"].items()}
            if family_base and family_base!=sid:
                mapping={cid:dict(base_transforms.get(cid,IDENTITY)) for cid in current}
            reuse=getattr(algorithm,'reuse_layout',None)
            if callable(reuse):
                components=[{**comp_map[cid],'visible':True,'bounds':desc['bounds']} for cid,desc in current.items()]
                mapping=reuse(components,project['sourceProfile'],device['profile'],context,mapping)
                from multires_app.pipeline.scene_pipeline import validate_transforms
                validate_transforms(mapping,components)
            if 'adaptationPlan' not in context and base_layout and base_layout.get('adaptationPlan'):
                context['adaptationPlan']={**copy.deepcopy(base_layout['adaptationPlan']),'reusedFrom':request['baseState']}
        else:
            fixed={cid:dict(base_transforms.get(cid,IDENTITY)) for cid in request["fixedComponents"]}
            candidates=request["affectedComponents"]
            components=[{**comp_map[cid],"visible":True,"bounds":current[cid]["bounds"],"elements":[]} for cid in candidates]
            elements=[]
            from multires_app.media.media_layout import layout_rows
            for row in layout_rows(project,layout):
                uid=project["nodeOrder"][row[0]];node=project["model"]["nodes"][uid]
                elements.append({"id":uid,"tag":node["tag"],"rect":dict(zip(("x","y","w","h"),row[1:5])),"visible":True,
                                 "component":project['nodeComponents'].get(uid),
                                 "asset":project["assetOrder"][row[6]] if row[6]>=0 else "","text":row[7],"alpha":row[5],"size":row[8]})
            for comp in components:comp["elements"]=[e for e in elements if project["nodeComponents"].get(e["id"])==comp["id"]]
            context={**context,"state":{**states[sid],"controlValues":layout["controlValues"],"overrides":layout["overrides"]},
                     "elements":elements,"graph":project["graph"],"model":project["model"],"assets":project["assets"],
                     "logicalViewport":device["viewport"],"layoutRequest":request,"fixedTransforms":fixed,
                     "baseTransforms":base_transforms,"baseDescriptors":base_desc,"allComponentDescriptors":current,
                     "sourceComponentDescriptors":source_descriptions.get(sid,{})}
            try:
                while True:
                    try:
                        result=algorithm.layout_scene(components,project["sourceProfile"],device["profile"],context);calls+=1;submitted+=len(components)
                        break
                    except ValueError as exc:
                        from multires_app.layout.anchor_solver import RequiredConstraintError
                        label=getattr(exc,'constraint_id','') or ''
                        blocked=label.split(':public-lock-',1)[0] if ':public-lock-' in label else None
                        # Public membership is an inference. For a new human
                        # layout, expand its affected region one blocking peer
                        # at a time; authored constraints stay hard throughout.
                        can_expand=(isinstance(exc,RequiredConstraintError) and blocked in fixed and
                            getattr(algorithm,'name','')=='anchor-theme-layout-v1' and
                            not (family_base and family_base!=sid) and not opts.get('groups') and not opts.get('constraints') and
                            not any(c.get('layout') for c in project['components']) and blocked not in device.get('manualComponentAnchors',{}))
                        guard=native_families.get(sid)
                        if (isinstance(exc,RequiredConstraintError) and blocked in fixed and guard and family_base!=sid and guard not in forced_full and
                                not opts.get('groups') and not opts.get('constraints') and not any(c.get('layout') for c in project['components']) and
                                not device.get('manualComponentAnchors')):
                            # Retry the family as one full layout at its first
                            # state, then reuse that affine in every phase.
                            restart=next(i for i,l in enumerate(device['layouts']) if l['state']==native_first[guard])
                            return schedule_device(project,device,algorithm,False,forced_full|{guard},restart)
                        if not can_expand:raise
                        fixed.pop(blocked);request['fixedComponents']=[cid for cid in request['fixedComponents'] if cid!=blocked]
                        request['affectedComponents']=sorted(set(request['affectedComponents'])|{blocked})
                        request['kind']='local-reflow' if fixed else 'full-reflow'
                        request['evidence'].append('constraint feasibility expands inferred affected region: '+blocked)
                        promotions.append({'state':sid,'reason':'inferred public peer blocks source-order and canvas constraints','affectedComponent':blocked})
                        components=[{**comp_map[cid],'visible':True,'bounds':current[cid]['bounds'],
                                     'elements':[e for e in elements if e['component']==cid]} for cid in request['affectedComponents']]
                        context['fixedTransforms']=fixed
            except LayoutConflict as exc:
                promotions.append({"state":sid,"reason":str(exc)});request["kind"]="full-reflow";request["evidence"].append("local constraints conflict; promoted to full layout")
                request["fixedComponents"]=[];request["affectedComponents"]=list(current);fixed={}
                components=[{**comp_map[cid],"visible":True,"bounds":desc["bounds"],"elements":[e for e in elements if project["nodeComponents"].get(e["id"])==cid]} for cid,desc in current.items()]
                context["fixedTransforms"]={}
                result=algorithm.layout_scene(components,project["sourceProfile"],device["profile"],context);calls+=1;submitted+=len(components)
            if not isinstance(result,dict):raise ValueError("layout_scene 必须返回组件变换字典")
            # A plugin cannot overwrite a fixed public component.
            result={cid:t for cid,t in result.items() if cid not in fixed}
            from multires_app.pipeline.scene_pipeline import validate_transforms
            validate_transforms(result,components)
            mapping={**fixed,**result}
        anchor_warnings=[]
        anchored={cid:dict(anchor['transform']) for cid,anchor in device.get('manualComponentAnchors',{}).items()
                  if cid in current and same_geometry(current[cid],anchor)}
        if anchored:
            from multires_app.preview.layout_refinement import validate_geometry_change
            from multires_app.layout.adaptive_layout import safe_region
            proposed={**mapping,**anchored}
            locked={cid for cid in current if comp_map[cid]['role'] in {'background','gesture-layer'}}
            region=safe_region(project['sourceProfile'],device['profile'],{'logicalViewport':device['viewport']})
            groups={}
            for cid in current:
                group=comp_map[cid].get('layout',{}).get('group')
                if group:groups.setdefault(group,[]).append(cid)
            try:
                validate_geometry_change(current,mapping,proposed,set(anchored),locked,region,sid,groups)
                mapping=proposed
            except ValueError as exc:
                anchor_warnings=[{'kind':'manual-refinement-conflict','severity':'warning','state':sid,'message':str(exc)}]
        layout["transforms"]=mapping;layout["layoutRequest"]=copy.deepcopy(request)
        if 'adaptationPlan' in context:layout['adaptationPlan']=copy.deepcopy(context['adaptationPlan'])
        else:layout.pop('adaptationPlan',None)
        if "adaptation" in device:layout["adaptation"]=copy.deepcopy(device["adaptation"])
        else:layout.pop("adaptation",None)
        review=getattr(algorithm,"review_layout",None)
        if callable(review):
            all_components=[{**comp_map[cid],"bounds":desc["bounds"]} for cid,desc in current.items()]
            layout["adaptationReview"]=review(all_components,mapping,project["sourceProfile"],device["profile"],{"logicalViewport":device["viewport"],"model":project["model"],"sourceComponentDescriptors":source_descriptions.get(sid,{})})
        else:layout.pop("adaptationReview",None)
        if anchor_warnings:layout.setdefault('adaptationReview',[]).extend(anchor_warnings)
    device["layoutClusters"]={k:v for k,v in analysis.items() if k not in {"descriptions","requests"}}
    device["layoutClusters"]["summary"].update(algorithmCalls=calls,submittedComponents=submitted,
                                              naiveComponentRequests=sum(len(d) for d in analysis["descriptions"].values()),
                                              promotions=promotions,strategyCounts=dict(Counter(l["layoutRequest"]["kind"] for l in device["layouts"])))
    if "adaptation" in device:
        device["layoutClusters"]["summary"]["adaptationStrategy"]=device["adaptation"]["strategy"]
        device["layoutClusters"]["summary"]["adaptationReviewCount"]=sum(len(l.get("adaptationReview",[])) for l in device["layouts"])
    finalize=getattr(algorithm,'finalize_device',None)
    if callable(finalize) and not preserve_existing:
        finalize(project,device)
    from multires_app.media.media_layout import retain_waiting_components
    retain_waiting_components(project,device)
    # Native pointer feedback is positioned by live input, not by a sampled
    # scene's last touch. The identity preserves that binding after finalize.
    for c in project['components']:
        if not c.get('pointerEffect'):continue
        for layout in device['layouts']:
            if c['id'] in layout['transforms']:layout['transforms'][c['id']]=dict(IDENTITY)
    return device["layoutClusters"]
