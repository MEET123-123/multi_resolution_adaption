"""Scene/component layout API, project generation and state-aware XML export."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree as ET

from multires_app.engine.scene_engine import Runtime, VISUAL, load_model, explore, extract_assets, replay_state


class SceneLayoutAlgorithm(Protocol):
    name: str

    def layout_scene(self, components, source_profile, target_profile, context):
        """Return {component_id:{sx,sy,tx,ty}} in target logical coordinates.

        components: atomic visual stacks plus their buttons, bounds and roles.
        context: state, elements, graph, model, assets, logicalViewport,
        layoutRequest, fixedTransforms, baseTransforms, baseDescriptors,
        allComponentDescriptors. Only affected components are submitted for
        local/overlay requests; reuse makes no algorithm call.
        Translate top-level roots once; scale descendants in the same component.
        """
        ...


def union(rects):
    rects=[r for r in rects if r["w"]>0 and r["h"]>0]
    if not rects:return {"x":0,"y":0,"w":0,"h":0}
    x=min(r["x"] for r in rects);y=min(r["y"] for r in rects)
    return {"x":x,"y":y,"w":max(r["x"]+r["w"] for r in rects)-x,
            "h":max(r["y"]+r["h"] for r in rects)-y}


def source_canvas_profile(model, profile):
    """Normalize physical source pixels to the manifest's coordinate system."""
    result=copy.deepcopy(profile);r=profile['resolutionPx']
    width=float(model['root'].get('screenWidth',r['width']))
    if not all(math.isfinite(float(v)) and float(v)>0 for v in (width,r['width'],r['height'])):
        raise ValueError('源画布尺寸必须为有限正数')
    result['resolutionPx']={'width':width,'height':width*r['height']/r['width']}
    return result


def infer_components(model, source_profile, assets, initial_state, overrides=None, graph=None):
    resolution=source_canvas_profile(model,source_profile)["resolutionPx"]
    elements=Runtime(model,resolution["width"],resolution["height"],initial_state).snapshot(assets)
    nodes=model["nodes"]
    boundaries={e["component"] for e in elements if nodes[e["component"]]["tag"] in VISUAL}
    def animated_ancestor(uid):
        parent=nodes[uid]["parent"]
        while parent:
            if any(nodes[c]["tag"]=="PositionAnimation" for c in nodes[parent]["children"]):return True
            parent=nodes[parent]["parent"]
        return False
    # Independently visible nested groups are natural panel/modal boundaries.
    # Animated ancestors remain atomic until their transform semantics are known.
    for node in nodes.values():
        if node["tag"]=="Group" and "visibility" in node["attributes"] and not animated_ancestor(node["id"]):boundaries.add(node["id"])
    from multires_app.layout.aesthetic_layout import datetime_roots
    clock_roots=datetime_roots(model)
    boundaries.update(clock_roots)
    from multires_app.interaction.interaction_bindings import analyze_bindings,structural_boundaries,attach_components
    samples=[elements]
    if graph:
        from multires_app.interaction.interaction_semantics import replay_human_state
        cache={}
        samples=[replay_human_state(model,resolution['width'],resolution['height'],s,assets,cache=cache).snapshot(assets) for s in graph['states']]
        elements=samples[0]
    bindings=analyze_bindings(model,samples,resolution,model.get('inputs',{}).get('bindings'))
    model['layoutBindings']=bindings
    boundaries.update(structural_boundaries(model,elements,resolution,clock_roots))
    boundaries.update(b['button'] for b in bindings['bindings'] if b['anchors'] and not animated_ancestor(b['button']))
    for e in elements:
        if e["tag"]=="Button" and e["rect"]["w"]*e["rect"]["h"]>=resolution["width"]*resolution["height"]*.85 and not animated_ancestor(e["id"]):boundaries.add(e["id"])
    def boundary(uid,roots):
        while uid:
            if uid in roots:return uid
            uid=nodes[uid]["parent"]
        return None
    grouped=defaultdict(list)
    for e in elements:
        owner=boundary(e["id"],boundaries)
        if owner:grouped[owner].append(e)
    components=[]
    for top,items in grouped.items():
        root=model["nodes"][top];bounds=union([e["rect"] for e in items if e["visible"]])
        if not bounds["w"]:bounds=union([e["rect"] for e in items])
        background=root["tag"]=="Image" and bounds["w"]>=resolution["width"]*.8 and bounds["h"]>=resolution["height"]*.8
        center=bounds["y"]+bounds["h"]/2
        from multires_app.pipeline.scene_clusters import overlap
        global_gesture=root["tag"]=="Button" and overlap(bounds,{'x':0,'y':0,'w':resolution['width'],'h':resolution['height']})>=resolution["width"]*resolution["height"]*.85
        role="gesture-layer" if global_gesture else "background" if background else "header" if center<resolution["height"]*.30 else "footer" if center>resolution["height"]*.82 else "content"
        panel_surface=any(e['parent']==top and e['tag']=='Image' and e['rect']['w']*e['rect']['h']>=bounds['w']*bounds['h']*.7 for e in items)
        if root['tag']=='Group' and 'visibility' in root['attributes'] and not any(e['visible'] for e in items) and panel_surface and sum(e['tag']=='Button' for e in items)>=2 and bounds['w']*bounds['h']>=resolution['width']*resolution['height']*.2:
            role='popup'
        leaves=[e for e in items if e['tag'] not in {'Group','Button'}]
        if role!='background' and leaves and not any(e['tag']=='Button' for e in items):
            animated_decor=all(e['tag']=='Image' and any(nodes[c]['tag']=='PositionAnimation' for c in nodes[e['id']]['children']) for e in leaves)
            from multires_app.pipeline.scene_clusters import overlap
            clipped=overlap(bounds,{'x':0,'y':0,'w':resolution['width'],'h':resolution['height']})/max(1,bounds['w']*bounds['h'])<.6
            if animated_decor or (root['tag']=='Image' and clipped):role='decoration'
        components.append({"id":top,"name":root["attributes"].get("name",root["tag"]+" "+str(len(components)+1)),
                           "roots":[top],"members":list(dict.fromkeys(e["id"] for e in items)),"role":role,"sourceBounds":bounds,
                           "inference":"independent visibility boundary; role inferred from bounds","reviewRequired":True})
        if top in clock_roots:components[-1]['semanticRole']='datetime'
    if overrides:
        if not isinstance(overrides,list):raise ValueError("组件配置必须为 components 数组")
        existing={c["id"]:c for c in components}
        for override in overrides:
            if override["id"] in existing:existing[override["id"]].update(override)
            else:components.append(override)
        # Nearest explicit/inferred root owns descendants, allowing nested panels.
        for override in overrides:
            roots=set(override.get("roots",existing.get(override["id"],{}).get("roots",[])))
            if roots:
                for c in components:
                    if c["id"]!=override["id"]:
                        c["roots"]=[r for r in c["roots"] if r not in roots]
        components=[c for c in components if c["roots"]]
    components=attach_components(components,bindings,nodes)
    from multires_app.layout.composition_layers import find_compositions,attach_compositions
    compositions=find_compositions(elements,assets,resolution)
    model['compositionBindings']=compositions
    components=attach_compositions(components,compositions,nodes)
    root_owners={root:c["id"] for c in components for root in c["roots"]}
    for c in components:c["members"]=[]
    component_index={c["id"]:c for c in components}
    for node in nodes.values():
        if node["tag"] not in VISUAL:continue
        owner=boundary(node["id"],root_owners)
        if owner:component_index[root_owners[owner]]["members"].append(node["id"])
    components=[c for c in components if any(nodes[uid]['tag']!='Group' for uid in c['members'])]
    for c in components:
        c['bindingButtons']=[b['button'] for b in bindings['bindings'] if b['button'] in c['members']]
        drawable=[uid for uid in c['members'] if nodes[uid]['tag'] not in {'Group','Button'}]
        if not drawable and c['role']!='gesture-layer':c['role']='interaction-layer'
        c['allowSpatialStack']=all(nodes[r]['tag']=='Group' for r in c['roots'] if nodes[r]['tag']!='Button')
        if drawable and any(nodes[r]['tag']=='Group' for r in c['roots']):
            for binding in bindings['bindings']:
                if binding['button'] in c['members'] and binding['status']=='needs-review':
                    binding.update(status='source-group',component=c['id'],evidence=['existing source group retained; exact image anchor not inferred'])
    ids=set();members=set()
    for c in components:
        if c["id"] in ids:raise ValueError("重复组件 id: "+c["id"])
        ids.add(c["id"])
        for root in c["roots"]:
            if root not in model["nodes"]:raise ValueError("组件节点不存在: "+root)
            if animated_ancestor(root):raise ValueError("含位置动画的祖先暂不能跨组件拆分："+root)
        for member in c["members"]:
            if member in members:raise ValueError("节点属于多个组件: "+member)
            members.add(member)
    return components


class SemanticReflow:
    """Example policy: portrait bands, landscape two columns; atomic stacks."""
    name="semantic-component-reflow-v1"

    def layout_scene(self, components, source_profile, target_profile, context):
        if context.get("layoutRequest",{}).get("kind") in {"local-reflow","overlay"}:
            from multires_app.pipeline.scene_clusters import constrained_reflow
            return constrained_reflow(components,context)
        w,h=context["logicalViewport"]["width"],context["logicalViewport"]["height"]
        margin=min(w,h)*.035;gap=margin
        landscape=w/h>1.15
        if landscape:
            left=(w-3*margin)*.40;right=w-left-3*margin
            regions={"header":{"x":margin,"y":margin,"w":left,"h":h*.57-margin},
                     "footer":{"x":margin,"y":h*.59,"w":left,"h":h*.41-margin},
                     "content":{"x":left+2*margin,"y":margin,"w":right,"h":h-2*margin}}
        else:
            regions={"header":{"x":margin,"y":margin,"w":w-2*margin,"h":h*.25-margin},
                     "content":{"x":margin,"y":h*.27,"w":w-2*margin,"h":h*.52},
                     "footer":{"x":margin,"y":h*.81,"w":w-2*margin,"h":h*.19-margin}}
        output={}
        for c in components:
            if c["role"]=="gesture-layer":output[c["id"]]={"sx":1.,"sy":1.,"tx":0.,"ty":0.}
        for role in ("header","content","footer","background"):
            lane=[c for c in components if (c["role"]==role or role=="content" and c["role"] in {"popup","overlay","dialog","decoration","interaction-layer"}) and c["visible"]]
            bounds=union([c["bounds"] for c in lane])
            if role=="background":
                # Full-canvas source imagery retains its cover composition.
                for c in lane:
                    b=c["bounds"];s=max(w/max(1,b["w"]),h/max(1,b["h"]))
                    output[c["id"]]={"sx":s,"sy":s,"tx":(w-b["w"]*s)/2-b["x"]*s,"ty":(h-b["h"]*s)/2-b["y"]*s}
                continue
            if not bounds["w"] or not bounds["h"]:continue
            region=regions[role];s=min(region["w"]/bounds["w"],region["h"]/bounds["h"])
            transform={"sx":s,"sy":s,"tx":region["x"]+(region["w"]-bounds["w"]*s)/2-bounds["x"]*s,
                       "ty":region["y"]+(region["h"]-bounds["h"]*s)/2-bounds["y"]*s}
            for c in lane:output[c["id"]]=dict(transform)
        return output


def load_scene_algorithm(spec):
    if spec in {"anchor", "auto", "anchor-theme-layout-v1"}:
        from multires_app.layout.anchor_layout import AnchorThemeLayout
        return AnchorThemeLayout()
    if spec in {"adaptive", "adaptive-theme-layout-v1"}:
        from multires_app.layout.adaptive_layout import AdaptiveThemeLayout
        return AdaptiveThemeLayout()
    if spec in {"reflow",SemanticReflow.name}:return SemanticReflow()
    # New plugins need layout_scene, without requiring the old layout method.
    if ":" not in spec:raise ValueError("场景算法使用 anchor、adaptive、reflow 或 file.py:Class")
    module_name,cls=spec.rsplit(":",1)
    if module_name.endswith(".py"):
        import importlib.util
        spec_obj=importlib.util.spec_from_file_location("scene_layout_plugin",Path(module_name).resolve())
        if not spec_obj or not spec_obj.loader:raise ValueError("无法加载场景算法")
        module=importlib.util.module_from_spec(spec_obj);spec_obj.loader.exec_module(module)
    else:
        import importlib
        from multires_app.compat import resolve_module
        module=importlib.import_module(resolve_module(module_name))
    algorithm=getattr(module,cls)()
    if not callable(getattr(algorithm,"layout_scene",None)):raise ValueError("场景算法必须实现 layout_scene")
    return algorithm


def target_profiles(doc):
    for base in doc["profiles"]:
        r=base.get("resolutionPx")
        if not r or not r.get("width") or not r.get("height"):continue
        original=base.get("baseOrientation","landscape" if r["width"]>r["height"] else "portrait")
        for orientation in base.get("orientations",["portrait","landscape"]):
            p=copy.deepcopy(base);p["id"]=base["id"]+"-"+orientation;p["orientation"]=orientation
            if orientation!=original:p["resolutionPx"]={"width":r["height"],"height":r["width"]}
            yield p


def validate_transforms(mapping, components):
    if not isinstance(mapping,dict):raise ValueError("layout_scene 必须返回组件变换字典")
    known={c["id"] for c in components}
    for cid,t in mapping.items():
        if cid not in known:raise ValueError("算法返回未知组件: "+cid)
        if set(t)!={"sx","sy","tx","ty"}:raise ValueError("组件变换需要 sx/sy/tx/ty")
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in t.values()) or min(t["sx"],t["sy"])<=0:
            raise ValueError("组件变换必须为有限数且缩放大于 0")


def create_project(source, source_profile, profiles, out, algorithm_spec="anchor", max_states=48, max_depth=4, component_file=None, inputs_file=None, lockscreen_file=None):
    from multires_app.preview.scene_previewer import build_scene_preview
    if max_states<1 or max_depth<0:raise ValueError("状态上限需大于 0，深度不能为负")
    archive_validation=None
    if source.is_file() and (source.suffix.lower() in {'.zip','.hwt'} or zipfile.is_zipfile(source)):
        from multires_app.engine.archive_safety import validate_archive
        archive_validation=validate_archive(source)
    out.mkdir(parents=True,exist_ok=True)
    model=load_model(source,lockscreen_file)
    if inputs_file:model["inputs"]=json.loads(inputs_file.read_text(encoding="utf-8"))
    if component_file:
        configuration=json.loads(component_file.read_text(encoding='utf-8'))
        if 'bindings' in configuration:model.setdefault('inputs',{})['bindings']=configuration['bindings']
    assets=extract_assets(source,model,out)
    from multires_app.layout.composition_layers import inspect_alpha
    inspect_alpha(assets,out)
    from multires_app.interaction.interaction_semantics import explore_human
    from multires_app.pipeline.scene_clusters import schedule_device
    graph=explore_human(model,source_canvas_profile(model,source_profile),assets,max_states,max_depth)
    overrides=json.loads(component_file.read_text(encoding="utf-8")).get("components") if component_file else None
    components=infer_components(model,source_profile,assets,graph["states"][0]["overrides"],overrides,graph)
    algorithm=load_scene_algorithm(algorithm_spec)
    node_order=[n["id"] for n in model["nodes"].values() if n["tag"] in VISUAL]
    node_index={uid:i for i,uid in enumerate(node_order)}
    comp_by_node={uid:c["id"] for c in components for uid in c["members"]}
    asset_keys=list(assets);asset_index={key:i for i,key in enumerate(asset_keys)}
    # Reference geometry must come from the same state on the source canvas.
    # Otherwise a responsive #screen_height expression gets adjusted twice.
    from multires_app.pipeline.source_comparison import build_source_device
    reference_project={"model":model,"graph":graph,"components":components,"nodeOrder":node_order,
                       "nodeComponents":comp_by_node,"assets":assets,"assetOrder":asset_keys,"sourceProfile":source_profile}
    source_device=build_source_device(reference_project)
    from multires_app.layout.footer_components import split_footer_components
    reference_project['sourceDevice']=source_device
    reference_project['devices']=[{'profile':profile} for profile in target_profiles(profiles)]
    from multires_app.layout.composition_sections import split_composition_sections
    section_splits=split_composition_sections(reference_project) if not component_file else []
    from multires_app.layout.background_layers import split_background_layers
    background_splits=split_background_layers(reference_project) if not component_file else []
    footer_splits=split_footer_components(reference_project) if not component_file else []
    from multires_app.layout.panel_scaling import infer_panel_scale_families
    scale_families=infer_panel_scale_families(reference_project) if not component_file else []
    components=reference_project['components'];comp_by_node=reference_project['nodeComponents']
    devices=[];diagnostics=dict(graph["diagnostics"]);anchor_store={}
    for profile in target_profiles(profiles):
        r=profile["resolutionPx"];logical_width=float(model["root"].get("screenWidth",source_profile["resolutionPx"]["width"]))
        viewport={"width":logical_width,"height":logical_width*r["height"]/r["width"]}
        layouts=[];replay_cache={}
        for state in graph["states"]:
            from multires_app.interaction.interaction_semantics import replay_human_state
            rt=replay_human_state(model,viewport["width"],viewport["height"],state,assets,verify_hit=False,cache=replay_cache)
            elements=rt.snapshot(assets,visible_only=True);diagnostics.update(rt.issues)
            visible=[e for e in elements if e["visible"]]
            input_components=[]
            for comp in components:
                present=[e for e in visible if e["id"] in comp["members"] and e["tag"]!="Group"]
                input_components.append({**comp,"visible":bool(present),"bounds":union([e["rect"] for e in present]),"elements":present})
            controls={name:rt.lookup(name) for name in set(model["controls"])|set(graph['semantics'].get('guardVariables',[]))}
            context={"state":{**state,"overrides":rt.state,"controlValues":controls},"elements":elements,"graph":graph,"model":model,"assets":assets,"logicalViewport":viewport}
            # Packed per-device snapshot: metadata lives once in model/nodes.
            rows=[]
            for e in visible:
                b=e["rect"]
                if e["tag"]=="Group":continue
                rows.append([node_index[e["id"]],*[round(b[k],3) for k in ("x","y","w","h")],round(e["alpha"],4),
                             asset_index.get(e["asset"],-1),e["text"],e["size"],e['asset'],e.get('media')])
            from multires_app.media.media_layout import reference
            layout={"state":state["id"],"transforms":{},"rows":rows,"controlValues":controls,"overrides":rt.state,'timeMs':rt.time_ms}
            ref=reference(rt,assets,node_order,asset_keys)
            if ref:layout['mediaLayout']=ref
            layouts.append(layout)
        device={"profile":profile,"viewport":viewport,"layouts":layouts}
        schedule_device({"model":model,"graph":graph,"components":components,"nodeOrder":node_order,"nodeComponents":comp_by_node,
                         "assets":assets,"assetOrder":asset_keys,"sourceProfile":source_profile,"sourceDevice":source_device,"anchorIRs":anchor_store,
                         "source":str(source.resolve()),"devices":[device],"resourceScaleFamilies":scale_families},device,algorithm)
        devices.append(device)
        print(json.dumps({"profile":profile["id"],"states":len(layouts)},ensure_ascii=True),flush=True)
    model.pop("assetNames",None)
    project={"formatVersion":3,"source":str(source.resolve()),"sourceProfile":source_profile,"algorithm":getattr(algorithm,"name",algorithm_spec),"algorithmSpec":algorithm_spec,
             "model":model,"components":components,"graph":graph,"devices":devices,"nodeOrder":node_order,
             "nodeComponents":comp_by_node,"assetOrder":asset_keys,"assets":assets,"diagnostics":diagnostics,
             "limitations":["此解释器覆盖部分旧版锁屏 XML，非鸿蒙原生引擎。", "动画按时间采样，动画控制命令与系统应用动作列入诊断。",
                             "场景图受状态数/深度及触摸采样限制，未探索分支保留为 bounded。", "组件与角色由脚本层级、空间关系推断，可用 components 配置覆盖。"]}
    project['sourceDevice']=source_device
    project['screenRelationComponentSplits']=footer_splits
    project['compositionSectionSplits']=section_splits
    project['backgroundLayerSplits']=background_splits
    project['resourceScaleFamilies']=scale_families
    project['anchorIRs']=anchor_store
    project['archiveValidation']=archive_validation
    from multires_app.pipeline.layout_storage import compact_layout_storage
    compact_layout_storage(project)
    from multires_app.pipeline.scene_analysis import attach_analysis
    attach_analysis(project,out)
    from multires_app.pipeline.layout_storage import externalize_anchor_evidence
    externalize_anchor_evidence(project)
    model.pop('assetNames',None)
    (out/"project.json").write_text(json.dumps(project,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    (out/"components.json").write_text(json.dumps({"components":components},ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"scene-graph.json").write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"layout-clusters.json").write_text(json.dumps({d['profile']['id']:d['layoutClusters'] for d in devices},ensure_ascii=False,indent=2),encoding="utf-8")
    build_scene_preview(project,out/"preview.html")
    manifests=out/"scripts";manifests.mkdir(exist_ok=True)
    exports=[]
    for device in devices:
        path=manifests/(device["profile"]["id"]+".xml")
        result=export_script(project,device["profile"]["id"],path)
        exports.append(result)
    report={"source":project["source"],"lockscreenFile":model["file"],"algorithm":project["algorithm"],
            "states":len(graph["states"]),"transitions":len(graph["transitions"]),"devices":len(devices),
            "components":len(components),"coverage":graph["coverage"],"diagnosticCount":len(diagnostics),"exports":exports,
            "layoutClustering":{d["profile"]["id"]:d["layoutClusters"]["summary"] for d in devices},
            "adaptation":{d["profile"]["id"]:d.get("adaptation") for d in devices},
            "limitations":project["limitations"],"archiveValidation":archive_validation}
    (out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def number(value):return format(float(value),".8g")


def relayout_project(project_path, algorithm_spec="anchor", component_file=None, anchor_config=None):
    """Replace layout policy using cached device-state evaluations and assets."""
    from multires_app.preview.scene_previewer import build_scene_preview
    project=json.loads(project_path.read_text(encoding="utf-8"));model=project["model"];out=project_path.parent
    project['anchorIRs']={}
    if anchor_config:
        configuration=json.loads(Path(anchor_config).read_text(encoding='utf-8'))
        for device in project['devices']:device['profile'].setdefault('adaptation',{})['anchorLayout']=copy.deepcopy(configuration)
    from multires_app.layout.composition_layers import inspect_alpha
    inspect_alpha(project['assets'],out)
    model["assetNames"]=list(project["assets"])
    overrides=json.loads(component_file.read_text(encoding="utf-8")).get("components") if component_file else None
    if component_file:
        configuration=json.loads(component_file.read_text(encoding='utf-8'))
        if 'bindings' in configuration:model.setdefault('inputs',{})['bindings']=configuration['bindings']
    components=infer_components(model,project["sourceProfile"],project["assets"],project["graph"]["states"][0]["overrides"],overrides,project['graph'])
    old_order=project["nodeOrder"];order=[uid for uid,n in model['nodes'].items() if n['tag'] in VISUAL]
    missing_nodes=set(order)-set(old_order)
    new_index={uid:i for i,uid in enumerate(order)}
    by_node={uid:c["id"] for c in components for uid in c["members"]}
    algorithm=load_scene_algorithm(algorithm_spec);states={s["id"]:s for s in project["graph"]["states"]}
    for device in project["devices"]:
        for layout in device["layouts"]:
            if missing_nodes:
                # Upgrade stale caches when renderer support adds a visual tag.
                runtime=Runtime(model,device['viewport']['width'],device['viewport']['height'],layout['overrides'],time_ms=layout.get('timeMs',0))
                assets_index={uid:i for i,uid in enumerate(project['assetOrder'])}
                for e in runtime.snapshot(project['assets'],visible_only=True):
                    if e['id'] not in missing_nodes or not e['visible'] or e['tag']=='Group':continue
                    # Temporarily append the ID to the old index so the normal
                    # remapping below preserves source drawing order.
                    if e['id'] not in old_order:old_order.append(e['id'])
                    b=e['rect'];layout['rows'].append([old_order.index(e['id']),*[round(b[k],3) for k in ('x','y','w','h')],round(e['alpha'],4),assets_index.get(e['asset'],-1),e['text'],e['size'],e['asset']])
            rows=[];elements=[]
            for row in layout["rows"]:
                uid=old_order[row[0]]
                if uid not in new_index:continue
                row=[new_index[uid]]+row[1:];rows.append(row);node=model["nodes"][uid]
                elements.append({"id":uid,"tag":node["tag"],"component":by_node.get(uid),"parent":node["parent"],
                                 "rect":dict(zip(("x","y","w","h"),row[1:5])),"alpha":row[5],"visible":True,
                                 "asset":project["assetOrder"][row[6]] if row[6]>=0 else "","text":row[7],"size":row[8]})
            evaluated=[]
            for comp in components:
                present=[e for e in elements if e["id"] in comp["members"]]
                evaluated.append({**comp,"visible":bool(present),"bounds":union([e["rect"] for e in present]),"elements":present})
            context={"state":{**states[layout["state"]],"overrides":layout["overrides"],"controlValues":layout["controlValues"]},
                     "elements":elements,"graph":project["graph"],"model":model,"assets":project["assets"],"logicalViewport":device["viewport"]}
            layout["transforms"]={};layout["rows"]=sorted(rows,key=lambda r:r[0])
    model.pop("assetNames",None)
    project["nodeOrder"]=order;project["nodeComponents"]=by_node;project["components"]=components
    project["algorithm"]=getattr(algorithm,"name",algorithm_spec)
    project["algorithmSpec"]=algorithm_spec
    # Cached rows remain reusable, but ownership changes require a fresh source map.
    from multires_app.pipeline.source_comparison import build_source_device
    project['sourceDevice']=build_source_device(project)
    from multires_app.media.media_layout import refresh_references
    refresh_references(project)
    from multires_app.layout.footer_components import split_footer_components
    if not component_file:split_footer_components(project)
    from multires_app.layout.panel_scaling import infer_panel_scale_families
    if not component_file:infer_panel_scale_families(project)
    from multires_app.pipeline.scene_clusters import schedule_device
    for device in project["devices"]:schedule_device(project,device,algorithm)
    for state in project["graph"]["states"]:state["visibleElements"]=[uid for uid in state["visibleElements"] if uid in new_index]
    project["diagnostics"]={key:value for key,value in project["diagnostics"].items() if not ("/CountDownTime[" in key and key.endswith(":render"))}
    project["graph"]["diagnostics"]={key:value for key,value in project["graph"]["diagnostics"].items() if not ("/CountDownTime[" in key and key.endswith(":render"))}
    from multires_app.pipeline.scene_analysis import attach_analysis
    attach_analysis(project,out)
    project_path.write_text(json.dumps(project,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    (out/"components.json").write_text(json.dumps({"components":components},ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"scene-graph.json").write_text(json.dumps(project["graph"],ensure_ascii=False,indent=2),encoding="utf-8")
    (out/"layout-clusters.json").write_text(json.dumps({d['profile']['id']:d['layoutClusters'] for d in project['devices']},ensure_ascii=False,indent=2),encoding="utf-8")
    build_scene_preview(project,out/"preview.html")
    report=json.loads((out/"report.json").read_text(encoding="utf-8"))
    report.update(algorithm=project["algorithm"],components=len(components),diagnosticCount=len(project["diagnostics"]))
    report["layoutClustering"]={d["profile"]["id"]:d["layoutClusters"]["summary"] for d in project["devices"]}
    report["adaptation"]={d["profile"]["id"]:d.get("adaptation") for d in project["devices"]}
    report["exports"]=[export_script(project,d["profile"]["id"],out/"scripts"/(d["profile"]["id"]+".xml")) for d in project["devices"]]
    (out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return {"project":str(project_path.resolve()),"algorithm":project["algorithm"],"devices":len(project["devices"])}


def state_guard(project, state, device):
    cache=project.setdefault("_stateGuards",{})
    cache_key=device["profile"]["id"]
    if cache_key in cache:return cache[cache_key][state["id"]]
    states=[{"id":l["state"],"controlValues":l["controlValues"]} for l in device["layouts"]]
    # Use all changing controls, so unknown/unsampled states fall through.
    relevant=project["graph"].get("semantics",{}).get("behaviorControls",project["model"]["controls"])
    relevant=list(dict.fromkeys(relevant+project['graph'].get('semantics',{}).get('guardVariables',[])))
    from multires_app.media.media_layout import playback_guard_names
    # Playback phase may select content, but must never select a new affine.
    # Human page/style controls remain in these guards.
    phase_names=playback_guard_names(project['model'])
    relevant=[name for name in relevant if name not in phase_names]
    varying=[name for name in relevant if len({json.dumps(s["controlValues"].get(name),sort_keys=True) for s in states})>1]
    guards={}
    for entry in states:
        terms=[]
        for name in varying:
            value=entry["controlValues"].get(name,0)
            if isinstance(value,(int,float)) and math.isfinite(value):literal=number(value)
            elif isinstance(value,str) and "'" not in value:literal="'"+value+"'"
            else:continue
            terms.append(f"lt(abs(#{name}-({literal})),0.00001)" if isinstance(value,float) and not value.is_integer() else f"eq(#{name},{literal})")
        expression="*".join(terms) or "1"
        name="mr_state_"+hashlib.sha1(expression.encode()).hexdigest()[:12]
        project.setdefault("_stateGuardExpressions",{})[name]=expression
        guards[entry["id"]]="#"+name
    cache[cache_key]=guards
    return guards[state["id"]]


def compiled_transform(project, device, cid, field):
    states={s["id"]:s for s in project["graph"]["states"]}
    fallback={"sx":1,"sy":1,"tx":0,"ty":0}[field]
    variants=defaultdict(list);guard_values={}
    for layout in device["layouts"]:
        if cid not in layout['transforms']:
            inactive=layout.get('mediaLayout',{}).get('inactiveSceneNodeIndices',[])
            if not any(project['nodeComponents'].get(project['nodeOrder'][i])==cid for i in inactive):continue
            value=number(fallback)
        else:value=number(layout['transforms'][cid][field])
        guard=state_guard(project,states[layout["state"]],device)
        if guard in guard_values and guard_values[guard]!=value:
            raise ValueError('同一原生状态条件存在不同布局：'+cid+' / '+field+'；请使用 matching-components 或增加可区分的交互变量')
        guard_values[guard]=value;variants[value].append(guard)
    if not variants:return number(fallback)
    if len(variants)==1:return next(iter(variants))
    args=[]
    for value,guards in variants.items():
        args.extend(["("+")+(".join(sorted(set(guards)))+")",value])
    return "ifelse("+",".join(args+[number(fallback)])+")"


def export_script(project, target, out):
    model=project["model"];source=Path(project["source"])
    if source.is_file():
        with zipfile.ZipFile(source) as z:data=z.read(model["file"])
    else:data=(source/model["file"]).read_bytes()
    parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root=ET.fromstring(data,parser=parser)
    original_nodes=[(n,dict(n.attrib),list(n)) for n in root.iter()]
    # Stable file+positional path remains unique even when names repeat.
    xml_nodes={}
    def visit(node,path):
        xml_nodes[model["file"]+"::"+path]=node;counts=defaultdict(int)
        for child in node:
            if not isinstance(child.tag,str):continue
            counts[child.tag]+=1;visit(child,path+f"/{child.tag}[{counts[child.tag]}]")
    visit(root,"/"+root.tag+"[1]")
    device=next((d for d in project["devices"] if d["profile"]["id"]==target),None)
    if not device:raise ValueError("未知目标设备: "+target)
    from multires_app.layout.anchor_validation import validate_export
    anchor_validation=validate_export(project,device)
    project.get('_stateGuards',{}).pop(target,None)
    changed=[];transforms={}
    macros={}
    def macro(expression):
        try:float(expression);return expression
        except ValueError:pass
        if expression not in macros:
            name="mr_layout_"+hashlib.sha1(expression.encode()).hexdigest()[:12]
            if name in model["variables"]:raise ValueError("布局变量与源变量冲突")
            macros[expression]=name
        return "#"+macros[expression]
    row_sizes=defaultdict(dict)
    for layout in device["layouts"]:
        for row in layout["rows"]:
            if row[6]>=0:
                uid=project["nodeOrder"][row[0]]
                meta=project["assets"][project["assetOrder"][row[6]]]
                row_sizes[uid][layout["state"]]=(meta["width"],meta["height"])
    state_index={s["id"]:s for s in project["graph"]["states"]}
    intrinsic_warnings=[]
    for comp in project["components"]:
        transforms[comp["id"]]={field:macro(compiled_transform(project,device,comp["id"],field)) for field in ("sx","sy","tx","ty")}
    roots={r:c["id"] for c in project["components"] for r in c["roots"]}
    def affine(raw,s,offset="0"):
        if s=="1" and offset=="0":return raw
        return f"({s})*({raw})"+(f"+({offset})" if offset!="0" else "")
    def boundary_offset(uid,axis,transform):
        parent=model["nodes"][uid]["parent"]
        parent_cid=project["nodeComponents"].get(parent)
        if parent_cid==project["nodeComponents"].get(uid):return "0"
        coordinates=[];adapted_coordinates=[];ancestor=parent
        while ancestor and model["nodes"][ancestor]["tag"]!="Lockscreen":
            if model["nodes"][ancestor]["tag"]=="Group":
                coordinates.append("("+model["nodes"][ancestor]["attributes"].get(axis,"0")+")")
                adapted_coordinates.append('('+xml_nodes[ancestor].get(axis,'0')+')')
            ancestor=model["nodes"][ancestor]["parent"]
        scale,shift="s"+axis,"t"+axis
        if not coordinates:return transform[shift]
        # Sum the actual adapted ancestor positions, including unowned wrapper
        # Groups between component roots. Immediate-parent lookup misses these.
        return f"({transform[scale]})*({'+'.join(coordinates)})+({transform[shift]})-({'+'.join(adapted_coordinates)})"
    for uid,node in xml_nodes.items():
        cid=project["nodeComponents"].get(uid)
        if not cid:continue
        transform=transforms[cid];is_root=uid in roots
        for attr,scale,offset in (("x","sx","tx"),("y","sy","ty"),("w","sx",None),("width","sx",None),("h","sy",None),("height","sy",None),("size","sy",None),("space","sx",None)):
            raw=node.get(attr)
            if raw is None and attr in {"x","y"} and is_root:raw="0"
            if raw is None and attr in {'w','h'} and node.tag=='Video' and node.get('isBackground','false').lower()=='true' and node.get('width' if attr=='w' else 'height') is None:
                raw='#screen_width' if attr=='w' else '#screen_height'
            if raw is None and attr in {"w","h"} and node.tag in {"Image","Video"} and node.get("width" if attr=="w" else "height") is None:
                # Preserve natural image size under component scale as well.
                from multires_app.engine.scene_engine import resolve_asset
                temp={**model,"assetNames":list(project["assets"])}
                asset=project["assets"].get(resolve_asset(temp,node.get("src","")))
                from multires_app.media.media_layout import intrinsic_expression
                indexed_size=intrinsic_expression(model,project['assets'],node.attrib,'width' if attr=='w' else 'height',model['nodes'][uid])
                if indexed_size is not None:
                    raw=indexed_size
                    if attr=='w' and re.search(r'[#@]\d',node.get('srcid','')):
                        intrinsic_warnings.append(uid+': leading-digit variable in srcid is not interpreted; uniform native frame canvas preserved')
                elif asset and not node.get("srcExp") and not node.get("srcid"):
                    raw=str(asset["width" if attr=="w" else "height"])
                elif row_sizes.get(uid):
                    dimensions=defaultdict(list)
                    for sid,pair in row_sizes[uid].items():
                        dimensions[str(pair[0 if attr=="w" else 1])].append(state_guard(project,state_index[sid],device))
                    if len(dimensions)==1:raw=next(iter(dimensions))
                    else:
                        args=[]
                        for size,guards in dimensions.items():args.extend(["("+")+(".join(sorted(set(guards)))+")",size])
                        raw="ifelse("+",".join(args+[next(iter(dimensions))])+")"
                        intrinsic_warnings.append(uid+": dynamic intrinsic dimensions use sampled states")
                else:intrinsic_warnings.append(uid+": unresolved intrinsic image dimensions")
            if raw is None and attr=="size" and node.tag in {'Text','Time','Date','DateTime','Number'}:raw="42"
            if raw is None:continue
            value=affine(raw,transform[scale],boundary_offset(uid,attr,transform) if offset else "0")
            if value!=node.get(attr):node.set(attr,value);changed.append({"node":uid,"attribute":attr,"old":raw,"new":value})
        for animation in node:
            if animation.tag in {"SourcesAnimation","PositionAnimation","SizeAnimation"}:
                for frame in animation:
                    for attr in ("x","y","w","h"):
                        if attr in frame.attrib:frame.set(attr,affine(frame.get(attr),transform["sx" if attr in {"x","w"} else "sy"]))
    # Splitting positional peers leaves their style/animation wrappers in the
    # original XML. A shared parent translation must use the same scale as
    # every leaf below it; otherwise its entry distance remains unscaled.
    for uid,node in xml_nodes.items():
        if node.tag!='Group' or uid in project['nodeComponents']:continue
        animations=[a for a in node if a.tag=='PositionAnimation']
        if not animations:continue
        descendants=[u for u,n in model['nodes'].items() if u.startswith(uid+'/') and n['tag'] in VISUAL and n['tag']!='Group']
        owners={project['nodeComponents'].get(u) for u in descendants}
        if not owners or None in owners:continue
        for axis in ('x','y'):
            scales={transforms[cid]['s'+axis] for cid in owners}
            if len(scales)!=1:
                raise ValueError('split animation wrapper has incompatible child scales: '+uid+' / '+axis)
            scale=next(iter(scales))
            for animation in animations:
                for frame in animation:
                    if axis in frame.attrib:frame.set(axis,affine(frame.get(axis),scale))
    # Capture the chosen component transform before an event mutates its state;
    # remap gesture expressions back to the original component coordinates.
    capture_count=0;capture_defined=set()
    touch_owners={t["owner"] for t in model["triggers"] if any(re.search(r"#touch_(?:begin_)?[xy]\b",str(c["attributes"])) for c in t["commands"])}
    down_owners={t["owner"] for t in model["triggers"] if t["action"]=="down"}
    for trigger in model["triggers"]:
        cid=project["nodeComponents"].get(trigger["owner"])
        if not cid:continue
        xml_trigger=xml_nodes[trigger["id"]]
        if trigger["owner"] not in touch_owners:continue
        prefix="mr_"+hashlib.sha1(trigger["owner"].encode()).hexdigest()[:10]+"_"
        if any(name.startswith(prefix) for name in model["variables"]):raise ValueError("生成变量与源脚本冲突")
        if prefix not in capture_defined:
            for field in ("sx","sy","tx","ty"):
                root.insert(0,ET.Element("Var",{"name":prefix+field,"expression":"1" if field in {"sx","sy"} else "0"}))
            capture_defined.add(prefix)
        capture=trigger["action"]=="down" or trigger["owner"] not in down_owners
        for child in xml_trigger.iter():
            for attr in ("expression","condition"):
                if attr in child.attrib:
                    def remap(m):
                        axis=m[1][-1]
                        shift=transforms[cid]["t"+axis] if child is xml_trigger and capture else "#"+prefix+"t"+axis
                        scale=transforms[cid]["s"+axis] if child is xml_trigger and capture else "#"+prefix+"s"+axis
                        return f"((#{m[1]}-({shift}))/({scale}))"
                    child.set(attr,re.sub(r"#(touch_(?:begin_)?[xy])\b",remap,child.get(attr)))
        if capture:
            for i,field in enumerate(("sx","sy","tx","ty")):
                xml_trigger.insert(i,ET.Element("VariableCommand",{"name":prefix+field,"expression":transforms[cid][field]}))
        capture_count+=1
    for expression,name in macros.items():root.insert(0,ET.Element("Var",{"name":name,"expression":expression}))
    used_guards=set(re.findall(r"#(mr_state_[0-9a-f]+)",ET.tostring(root,encoding="unicode")))
    for name,expression in project.get("_stateGuardExpressions",{}).items():
        if name not in used_guards:continue
        if name in model["variables"]:raise ValueError("场景变量与源变量冲突")
        root.insert(0,ET.Element("Var",{"name":name,"expression":expression}))
    out.parent.mkdir(parents=True,exist_ok=True)
    from multires_app.export.layout_writeback import patch_script
    from multires_app.media.resource_scaling import generate_glyph_resources
    resources=generate_glyph_resources(project,device,out)
    approved_remaps={}
    for uid,src in resources['remaps'].items():
        if uid in xml_nodes:xml_nodes[uid].set('src',src);approved_remaps[id(xml_nodes[uid])]=src
    intrinsic_warnings.extend(resources['warnings'])
    patched,writeback=patch_script(data,original_nodes,root,approved_remaps)
    out.write_bytes(patched)
    out.with_suffix('.patch.json').write_text(json.dumps(writeback,ensure_ascii=False,indent=2),encoding='utf-8')
    ET.parse(out)
    return {"target":target,"script":str(out.resolve()),"changedAttributes":len(changed),"touchTriggers":capture_count,
            "intrinsicSizingWarnings":sorted(set(intrinsic_warnings)),
            "resourceScaling":{'generatedFiles':len(resources['files']),'remappedNodes':len(resources['remaps']),'warnings':resources['warnings']},
            "writebackValidation":{k:v for k,v in writeback.items() if k!='changes'},
            "anchorValidation":anchor_validation,
            "note":"保留原命令/条件/动画，组件变换按状态编译为表达式；需主题引擎实测。"}


def export_package(project_path, target, out):
    project=json.loads(project_path.read_text(encoding="utf-8"));source=Path(project["source"])
    if not zipfile.is_zipfile(source):raise ValueError("重打包需要原始 ZIP/HWT")
    if out.resolve()==source.resolve():raise ValueError("输出不得覆盖源主题包")
    script=project_path.parent/"scripts"/(target+".xml")
    result=export_script(project,target,script)
    replacements={project['model']['file']:script.read_bytes()}
    from multires_app.media.resource_scaling import manifest_resources
    generated=manifest_resources(script)['files']
    for extra_path in project.get('variantProjects',[]):
        extra_path=Path(extra_path);extra=json.loads(extra_path.read_text(encoding='utf-8'))
        if Path(extra['source']).resolve()!=source.resolve():raise ValueError('锁屏变体必须属于同一个源 HWT')
        extra_script=extra_path.parent/'scripts'/(target+'.xml');export_script(extra,target,extra_script)
        replacements[extra['model']['file']]=extra_script.read_bytes()
        generated.update(manifest_resources(extra_script)['files'])
    out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(source) as original,zipfile.ZipFile(out,"w") as output:
        for info in original.infolist():
            output.writestr(info,replacements.get(info.filename,original.read(info)))
        for name,meta in sorted(generated.items()):
            if name in original.namelist():raise ValueError('generated resource collides with original: '+name)
            data=Path(meta['file']).read_bytes()
            if hashlib.sha256(data).hexdigest()!=meta['sha256']:raise ValueError('generated resource digest mismatch')
            output.writestr(name,data)
    result["package"]=str(out.resolve())
    result['adaptedManifests']=list(replacements)
    return result
