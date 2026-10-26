"""Application workflows: analyse a theme, create a project, or relayout its cache.

Reading order: create_project -> create_model_project -> schedule_device.
Component inference, layout plugins and native export have separate modules.
"""

import copy
import json
import zipfile
from pathlib import Path
from multires_app.engine.scene_engine import Runtime, VISUAL, extract_assets, load_model
from multires_app.export.scene_export import export_script
from multires_app.layout.geometry import union
from multires_app.layout.scene_api import load_scene_algorithm
from multires_app.pipeline.components import infer_components
from multires_app.pipeline.profiles import source_canvas_profile, target_profiles


def create_project(
    source,
    source_profile,
    profiles,
    out,
    algorithm_spec="anchor",
    max_states=48,
    max_depth=4,
    component_file=None,
    inputs_file=None,
    lockscreen_file=None,
):
    from multires_app.preview.scene_previewer import build_scene_preview

    if max_states < 1 or max_depth < 0:
        raise ValueError("状态上限需大于 0，深度不能为负")
    archive_validation = None
    if source.is_file() and (
        source.suffix.lower() in {".zip", ".hwt"} or zipfile.is_zipfile(source)
    ):
        from multires_app.engine.archive_safety import validate_archive

        archive_validation = validate_archive(source)
    out.mkdir(parents=True, exist_ok=True)
    model = load_model(source, lockscreen_file)
    model["layoutSourceViewport"] = source_canvas_profile(model, source_profile)["resolutionPx"]
    if inputs_file:
        model["inputs"] = json.loads(inputs_file.read_text(encoding="utf-8"))
    if component_file:
        configuration = json.loads(component_file.read_text(encoding="utf-8"))
        if "bindings" in configuration:
            model.setdefault("inputs", {})["bindings"] = configuration["bindings"]
    assets = extract_assets(source, model, out)
    return create_model_project(
        model,
        assets,
        source_profile,
        profiles,
        out,
        algorithm_spec,
        max_states,
        max_depth,
        component_file,
        archive_validation,
    )


def create_model_project(
    model,
    assets,
    source_profile,
    profiles,
    out,
    algorithm_spec="anchor",
    max_states=48,
    max_depth=4,
    component_file=None,
    archive_validation=None,
):
    """Shared build pipeline for archive intake and validated local asset caches.

    Callers reusing extracted resources must verify the source and resource
    cache before entry. Interaction analysis and layout are always rebuilt.
    """
    from multires_app.preview.scene_previewer import build_scene_preview

    if max_states < 1 or max_depth < 0:
        raise ValueError("状态上限需大于 0，深度不能为负")
    source = Path(model["source"])
    out.mkdir(parents=True, exist_ok=True)
    model["layoutSourceViewport"] = source_canvas_profile(model, source_profile)["resolutionPx"]
    from multires_app.layout.composition_layers import inspect_alpha

    inspect_alpha(assets, out)
    from multires_app.interaction.interaction_semantics import explore_human
    from multires_app.pipeline.scene_clusters import schedule_device

    graph = explore_human(
        model, source_canvas_profile(model, source_profile), assets, max_states, max_depth
    )
    overrides = (
        json.loads(component_file.read_text(encoding="utf-8")).get("components")
        if component_file
        else None
    )
    components = infer_components(
        model, source_profile, assets, graph["states"][0]["overrides"], overrides, graph
    )
    algorithm = load_scene_algorithm(algorithm_spec)
    node_order = [n["id"] for n in model["nodes"].values() if n["tag"] in VISUAL]
    node_index = {uid: i for i, uid in enumerate(node_order)}
    comp_by_node = {uid: c["id"] for c in components for uid in c["members"]}
    asset_keys = list(assets)
    asset_index = {key: i for i, key in enumerate(asset_keys)}
    # Reference geometry must come from the same state on the source canvas.
    # Otherwise a responsive #screen_height expression gets adjusted twice.
    from multires_app.pipeline.source_comparison import build_source_device

    reference_project = {
        "model": model,
        "graph": graph,
        "components": components,
        "nodeOrder": node_order,
        "nodeComponents": comp_by_node,
        "assets": assets,
        "assetOrder": asset_keys,
        "sourceProfile": source_profile,
    }
    source_device = build_source_device(reference_project)
    from multires_app.layout.footer_components import split_footer_components

    reference_project["sourceDevice"] = source_device
    from multires_app.interaction.regional_gestures import split_regional_gestures

    split_regional_gestures(reference_project)
    reference_project["devices"] = [{"profile": profile} for profile in target_profiles(profiles)]
    from multires_app.layout.composition_sections import split_composition_sections

    section_splits = split_composition_sections(reference_project) if not component_file else []
    from multires_app.layout.background_layers import split_background_layers

    background_splits = split_background_layers(reference_project) if not component_file else []
    footer_splits = split_footer_components(reference_project) if not component_file else []
    from multires_app.preview.component_refinement import partition_button_components

    button_partitions = partition_button_components(reference_project) if not component_file else []
    from multires_app.layout.panel_scaling import infer_panel_scale_families

    scale_families = infer_panel_scale_families(reference_project) if not component_file else []
    components = reference_project["components"]
    comp_by_node = reference_project["nodeComponents"]
    devices = []
    diagnostics = dict(graph["diagnostics"])
    anchor_store = {}
    for profile in target_profiles(profiles):
        r = profile["resolutionPx"]
        logical_width = float(
            model["root"].get("screenWidth", source_profile["resolutionPx"]["width"])
        )
        viewport = {"width": logical_width, "height": logical_width * r["height"] / r["width"]}
        layouts = []
        replay_cache = {}
        for state in graph["states"]:
            from multires_app.interaction.interaction_semantics import replay_human_state

            rt = replay_human_state(
                model,
                viewport["width"],
                viewport["height"],
                state,
                assets,
                verify_hit=False,
                cache=replay_cache,
            )
            elements = rt.snapshot(assets, visible_only=True)
            diagnostics.update(rt.issues)
            visible = [e for e in elements if e["visible"]]
            input_components = []
            for comp in components:
                present = [e for e in visible if e["id"] in comp["members"] and e["tag"] != "Group"]
                input_components.append(
                    {
                        **comp,
                        "visible": bool(present),
                        "bounds": union([e["rect"] for e in present]),
                        "elements": present,
                    }
                )
            controls = {
                name: rt.lookup(name)
                for name in set(model["controls"])
                | set(graph["semantics"].get("guardVariables", []))
            }
            context = {
                "state": {**state, "overrides": rt.state, "controlValues": controls},
                "elements": elements,
                "graph": graph,
                "model": model,
                "assets": assets,
                "logicalViewport": viewport,
            }
            # Packed per-device snapshot: metadata lives once in model/nodes.
            rows = []
            for e in visible:
                b = e["rect"]
                if e["tag"] == "Group":
                    continue
                rows.append(
                    [
                        node_index[e["id"]],
                        *[round(b[k], 3) for k in ("x", "y", "w", "h")],
                        round(e["alpha"], 4),
                        asset_index.get(e["asset"], -1),
                        e["text"],
                        e["size"],
                        e["asset"],
                        e.get("media"),
                    ]
                )
            from multires_app.media.media_layout import reference

            layout = {
                "state": state["id"],
                "transforms": {},
                "rows": rows,
                "controlValues": controls,
                "overrides": rt.state,
                "timeMs": rt.time_ms,
            }
            ref = reference(rt, assets, node_order, asset_keys)
            if ref:
                layout["mediaLayout"] = ref
            layouts.append(layout)
        device = {"profile": profile, "viewport": viewport, "layouts": layouts}
        schedule_device(
            {
                "model": model,
                "graph": graph,
                "components": components,
                "nodeOrder": node_order,
                "nodeComponents": comp_by_node,
                "assets": assets,
                "assetOrder": asset_keys,
                "sourceProfile": source_profile,
                "sourceDevice": source_device,
                "anchorIRs": anchor_store,
                "source": str(source.resolve()),
                "devices": [device],
                "resourceScaleFamilies": scale_families,
            },
            device,
            algorithm,
        )
        devices.append(device)
        print(
            json.dumps({"profile": profile["id"], "states": len(layouts)}, ensure_ascii=True),
            flush=True,
        )
    model.pop("assetNames", None)
    project = {
        "formatVersion": 3,
        "source": str(source.resolve()),
        "sourceProfile": source_profile,
        "algorithm": getattr(algorithm, "name", algorithm_spec),
        "algorithmSpec": algorithm_spec,
        "model": model,
        "components": components,
        "graph": graph,
        "devices": devices,
        "nodeOrder": node_order,
        "nodeComponents": comp_by_node,
        "assetOrder": asset_keys,
        "assets": assets,
        "diagnostics": diagnostics,
        "limitations": [
            "此解释器覆盖部分旧版锁屏 XML，非鸿蒙原生引擎。",
            "动画按时间采样，动画控制命令与系统应用动作列入诊断。",
            "场景图受状态数/深度及触摸采样限制，未探索分支保留为 bounded。",
            "组件与角色由脚本层级、空间关系推断，可用 components 配置覆盖。",
        ],
    }
    project["sourceDevice"] = source_device
    from multires_app.interaction.animation_semantics import STATE_POLICY

    project["animationStatePolicy"] = dict(STATE_POLICY)
    project["screenRelationComponentSplits"] = footer_splits
    project["compositionSectionSplits"] = section_splits
    project["backgroundLayerSplits"] = background_splits
    project["resourceScaleFamilies"] = scale_families
    project["buttonComponentPartitions"] = button_partitions
    project["anchorIRs"] = anchor_store
    project["archiveValidation"] = archive_validation
    from multires_app.pipeline.layout_storage import compact_layout_storage

    compact_layout_storage(project)
    from multires_app.pipeline.scene_analysis import attach_analysis

    attach_analysis(project, out)
    from multires_app.pipeline.layout_storage import externalize_anchor_evidence

    externalize_anchor_evidence(project)
    model.pop("assetNames", None)
    (out / "project.json").write_text(
        json.dumps(project, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (out / "components.json").write_text(
        json.dumps({"components": components}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "scene-graph.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "layout-clusters.json").write_text(
        json.dumps(
            {d["profile"]["id"]: d["layoutClusters"] for d in devices}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    build_scene_preview(project, out / "preview.html")
    manifests = out / "scripts"
    manifests.mkdir(exist_ok=True)
    exports = []
    for device in devices:
        path = manifests / (device["profile"]["id"] + ".xml")
        result = export_script(project, device["profile"]["id"], path)
        exports.append(result)
    report = {
        "source": project["source"],
        "lockscreenFile": model["file"],
        "algorithm": project["algorithm"],
        "states": len(graph["states"]),
        "transitions": len(graph["transitions"]),
        "devices": len(devices),
        "components": len(components),
        "coverage": graph["coverage"],
        "diagnosticCount": len(diagnostics),
        "exports": exports,
        "layoutClustering": {d["profile"]["id"]: d["layoutClusters"]["summary"] for d in devices},
        "adaptation": {d["profile"]["id"]: d.get("adaptation") for d in devices},
        "limitations": project["limitations"],
        "archiveValidation": archive_validation,
    }
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def relayout_project(
    project_path, algorithm_spec="anchor", component_file=None, anchor_config=None
):
    """Replace layout policy using cached device-state evaluations and assets."""
    from multires_app.preview.scene_previewer import build_scene_preview

    project = json.loads(project_path.read_text(encoding="utf-8"))
    model = project["model"]
    out = project_path.parent
    project["anchorIRs"] = {}
    if anchor_config:
        configuration = json.loads(Path(anchor_config).read_text(encoding="utf-8"))
        for device in project["devices"]:
            device["profile"].setdefault("adaptation", {})["anchorLayout"] = copy.deepcopy(
                configuration
            )
    from multires_app.layout.composition_layers import inspect_alpha

    inspect_alpha(project["assets"], out)
    model["assetNames"] = list(project["assets"])
    overrides = (
        json.loads(component_file.read_text(encoding="utf-8")).get("components")
        if component_file
        else None
    )
    if component_file:
        configuration = json.loads(component_file.read_text(encoding="utf-8"))
        if "bindings" in configuration:
            model.setdefault("inputs", {})["bindings"] = configuration["bindings"]
    components = infer_components(
        model,
        project["sourceProfile"],
        project["assets"],
        project["graph"]["states"][0]["overrides"],
        overrides,
        project["graph"],
    )
    old_order = project["nodeOrder"]
    order = [uid for uid, n in model["nodes"].items() if n["tag"] in VISUAL]
    missing_nodes = set(order) - set(old_order)
    new_index = {uid: i for i, uid in enumerate(order)}
    by_node = {uid: c["id"] for c in components for uid in c["members"]}
    algorithm = load_scene_algorithm(algorithm_spec)
    states = {s["id"]: s for s in project["graph"]["states"]}
    for device in project["devices"]:
        for layout in device["layouts"]:
            if missing_nodes:
                # Upgrade stale caches when renderer support adds a visual tag.
                runtime = Runtime(
                    model,
                    device["viewport"]["width"],
                    device["viewport"]["height"],
                    layout["overrides"],
                    time_ms=layout.get("timeMs", 0),
                )
                assets_index = {uid: i for i, uid in enumerate(project["assetOrder"])}
                for e in runtime.snapshot(project["assets"], visible_only=True):
                    if e["id"] not in missing_nodes or not e["visible"] or e["tag"] == "Group":
                        continue
                    # Temporarily append the ID to the old index so the normal
                    # remapping below preserves source drawing order.
                    if e["id"] not in old_order:
                        old_order.append(e["id"])
                    b = e["rect"]
                    layout["rows"].append(
                        [
                            old_order.index(e["id"]),
                            *[round(b[k], 3) for k in ("x", "y", "w", "h")],
                            round(e["alpha"], 4),
                            assets_index.get(e["asset"], -1),
                            e["text"],
                            e["size"],
                            e["asset"],
                        ]
                    )
            rows = []
            elements = []
            for row in layout["rows"]:
                uid = old_order[row[0]]
                if uid not in new_index:
                    continue
                row = [new_index[uid]] + row[1:]
                rows.append(row)
                node = model["nodes"][uid]
                elements.append(
                    {
                        "id": uid,
                        "tag": node["tag"],
                        "component": by_node.get(uid),
                        "parent": node["parent"],
                        "rect": dict(zip(("x", "y", "w", "h"), row[1:5])),
                        "alpha": row[5],
                        "visible": True,
                        "asset": project["assetOrder"][row[6]] if row[6] >= 0 else "",
                        "text": row[7],
                        "size": row[8],
                    }
                )
            evaluated = []
            for comp in components:
                present = [e for e in elements if e["id"] in comp["members"]]
                evaluated.append(
                    {
                        **comp,
                        "visible": bool(present),
                        "bounds": union([e["rect"] for e in present]),
                        "elements": present,
                    }
                )
            context = {
                "state": {
                    **states[layout["state"]],
                    "overrides": layout["overrides"],
                    "controlValues": layout["controlValues"],
                },
                "elements": elements,
                "graph": project["graph"],
                "model": model,
                "assets": project["assets"],
                "logicalViewport": device["viewport"],
            }
            layout["transforms"] = {}
            layout["rows"] = sorted(rows, key=lambda r: r[0])
    model.pop("assetNames", None)
    project["nodeOrder"] = order
    project["nodeComponents"] = by_node
    project["components"] = components
    project["algorithm"] = getattr(algorithm, "name", algorithm_spec)
    project["algorithmSpec"] = algorithm_spec
    # Cached rows remain reusable, but ownership changes require a fresh source map.
    from multires_app.pipeline.source_comparison import build_source_device

    project["sourceDevice"] = build_source_device(project)
    from multires_app.interaction.regional_gestures import split_regional_gestures

    split_regional_gestures(project)
    from multires_app.media.media_layout import refresh_references

    refresh_references(project)
    from multires_app.layout.footer_components import split_footer_components

    if not component_file:
        split_footer_components(project)
    if not component_file:
        from multires_app.preview.component_refinement import partition_button_components

        partition_button_components(project)
    from multires_app.layout.panel_scaling import infer_panel_scale_families

    if not component_file:
        infer_panel_scale_families(project)
    from multires_app.pipeline.scene_clusters import schedule_device

    for device in project["devices"]:
        schedule_device(project, device, algorithm)
    for state in project["graph"]["states"]:
        state["visibleElements"] = [uid for uid in state["visibleElements"] if uid in new_index]
    project["diagnostics"] = {
        key: value
        for key, value in project["diagnostics"].items()
        if not ("/CountDownTime[" in key and key.endswith(":render"))
    }
    project["graph"]["diagnostics"] = {
        key: value
        for key, value in project["graph"]["diagnostics"].items()
        if not ("/CountDownTime[" in key and key.endswith(":render"))
    }
    from multires_app.pipeline.scene_analysis import attach_analysis

    attach_analysis(project, out)
    project_path.write_text(
        json.dumps(project, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (out / "components.json").write_text(
        json.dumps({"components": components}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "scene-graph.json").write_text(
        json.dumps(project["graph"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "layout-clusters.json").write_text(
        json.dumps(
            {d["profile"]["id"]: d["layoutClusters"] for d in project["devices"]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    build_scene_preview(project, out / "preview.html")
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    report.update(
        algorithm=project["algorithm"],
        components=len(components),
        diagnosticCount=len(project["diagnostics"]),
    )
    report["layoutClustering"] = {
        d["profile"]["id"]: d["layoutClusters"]["summary"] for d in project["devices"]
    }
    report["adaptation"] = {d["profile"]["id"]: d.get("adaptation") for d in project["devices"]}
    report["exports"] = [
        export_script(project, d["profile"]["id"], out / "scripts" / (d["profile"]["id"] + ".xml"))
        for d in project["devices"]
    ]
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "project": str(project_path.resolve()),
        "algorithm": project["algorithm"],
        "devices": len(project["devices"]),
    }
