"""Infer atomic visual/interaction groups from reachable runtime states."""

from collections import defaultdict
from multires_app.engine.scene_engine import Runtime, VISUAL
from multires_app.layout.geometry import union
from multires_app.pipeline.profiles import source_canvas_profile


def infer_components(
    model, source_profile, assets, initial_state, overrides=None, graph=None, sampled_elements=None
):
    resolution = source_canvas_profile(model, source_profile)["resolutionPx"]
    elements = Runtime(model, resolution["width"], resolution["height"], initial_state).snapshot(
        assets
    )
    nodes = model["nodes"]
    boundaries = {e["component"] for e in elements if nodes[e["component"]]["tag"] in VISUAL}

    def animated_ancestor(uid):
        parent = nodes[uid]["parent"]
        while parent:
            if any(nodes[c]["tag"] == "PositionAnimation" for c in nodes[parent]["children"]):
                return True
            parent = nodes[parent]["parent"]
        return False

    # Independently visible nested groups are natural panel/modal boundaries.
    # Animated ancestors remain atomic until their transform semantics are known.
    for node in nodes.values():
        if (
            node["tag"] == "Group"
            and "visibility" in node["attributes"]
            and not animated_ancestor(node["id"])
        ):
            boundaries.add(node["id"])
    from multires_app.layout.aesthetic_layout import datetime_roots

    clock_roots = datetime_roots(model)
    boundaries.update(clock_roots)
    from multires_app.interaction.interaction_bindings import (
        analyze_bindings,
        structural_boundaries,
        attach_components,
    )

    samples = [elements]
    if sampled_elements is not None:
        samples = sampled_elements
        elements = samples[0]
    elif graph:
        from multires_app.interaction.interaction_semantics import replay_human_state

        cache = {}
        samples = [
            replay_human_state(
                model, resolution["width"], resolution["height"], s, assets, cache=cache
            ).snapshot(assets)
            for s in graph["states"]
        ]
        elements = samples[0]
    bindings = analyze_bindings(model, samples, resolution, model.get("inputs", {}).get("bindings"))
    model["layoutBindings"] = bindings
    # A settings page can be invisible at startup. Its organizational groups
    # still need the same structural partition when a real journey opens it.
    for sample in samples:
        boundaries.update(structural_boundaries(model, sample, resolution, clock_roots))
    boundaries.update(
        b["button"]
        for b in bindings["bindings"]
        if b["anchors"] and not animated_ancestor(b["button"])
    )
    for e in elements:
        if (
            e["tag"] == "Button"
            and e["rect"]["w"] * e["rect"]["h"] >= resolution["width"] * resolution["height"] * 0.85
            and not animated_ancestor(e["id"])
        ):
            boundaries.add(e["id"])

    def boundary(uid, roots):
        while uid:
            if uid in roots:
                return uid
            uid = nodes[uid]["parent"]
        return None

    grouped = defaultdict(list)
    for e in elements:
        owner = boundary(e["id"], boundaries)
        if owner:
            grouped[owner].append(e)
    components = []
    for top, items in grouped.items():
        root = model["nodes"][top]
        bounds = union([e["rect"] for e in items if e["visible"]])
        if not bounds["w"]:
            bounds = union([e["rect"] for e in items])
        background = (
            root["tag"] == "Image"
            and bounds["w"] >= resolution["width"] * 0.8
            and bounds["h"] >= resolution["height"] * 0.8
        )
        center = bounds["y"] + bounds["h"] / 2
        from multires_app.pipeline.scene_clusters import overlap

        global_gesture = (
            root["tag"] == "Button"
            and overlap(
                bounds, {"x": 0, "y": 0, "w": resolution["width"], "h": resolution["height"]}
            )
            >= resolution["width"] * resolution["height"] * 0.85
        )
        role = (
            "gesture-layer"
            if global_gesture
            else (
                "background"
                if background
                else (
                    "header"
                    if center < resolution["height"] * 0.30
                    else "footer" if center > resolution["height"] * 0.82 else "content"
                )
            )
        )
        panel_surface = any(
            e["parent"] == top
            and e["tag"] == "Image"
            and e["rect"]["w"] * e["rect"]["h"] >= bounds["w"] * bounds["h"] * 0.7
            for e in items
        )
        if (
            root["tag"] == "Group"
            and "visibility" in root["attributes"]
            and not any(e["visible"] for e in items)
            and panel_surface
            and sum(e["tag"] == "Button" for e in items) >= 2
            and bounds["w"] * bounds["h"] >= resolution["width"] * resolution["height"] * 0.2
        ):
            role = "popup"
        leaves = [e for e in items if e["tag"] not in {"Group", "Button"}]
        if role != "background" and leaves and not any(e["tag"] == "Button" for e in items):
            animated_decor = all(
                e["tag"] == "Image"
                and any(nodes[c]["tag"] == "PositionAnimation" for c in nodes[e["id"]]["children"])
                for e in leaves
            )
            from multires_app.pipeline.scene_clusters import overlap

            clipped = (
                overlap(
                    bounds, {"x": 0, "y": 0, "w": resolution["width"], "h": resolution["height"]}
                )
                / max(1, bounds["w"] * bounds["h"])
                < 0.6
            )
            if animated_decor or (root["tag"] == "Image" and clipped):
                role = "decoration"
        components.append(
            {
                "id": top,
                "name": root["attributes"].get(
                    "name", root["tag"] + " " + str(len(components) + 1)
                ),
                "roots": [top],
                "members": list(dict.fromkeys(e["id"] for e in items)),
                "role": role,
                "sourceBounds": bounds,
                "inference": "independent visibility boundary; role inferred from bounds",
                "reviewRequired": True,
            }
        )
        if top in clock_roots:
            components[-1]["semanticRole"] = "datetime"
    if overrides:
        if not isinstance(overrides, list):
            raise ValueError("组件配置必须为 components 数组")
        existing = {c["id"]: c for c in components}
        for override in overrides:
            if override["id"] in existing:
                existing[override["id"]].update(override)
            else:
                components.append(override)
        # Nearest explicit/inferred root owns descendants, allowing nested panels.
        for override in overrides:
            roots = set(override.get("roots", existing.get(override["id"], {}).get("roots", [])))
            if roots:
                for c in components:
                    if c["id"] != override["id"]:
                        c["roots"] = [r for r in c["roots"] if r not in roots]
        components = [c for c in components if c["roots"]]
    components = attach_components(components, bindings, nodes)
    from multires_app.layout.composition_layers import find_compositions, attach_compositions

    compositions = find_compositions(elements, assets, resolution)
    model["compositionBindings"] = compositions
    components = attach_compositions(components, compositions, nodes)
    # Shrinking a parent must not pull its waiting page onto the viewport.
    # Preserve each observed sliding subtree as an independent layout root.
    from multires_app.media.media_layout import page_animation_names
    from multires_app.engine.scene_engine import REF

    pages = page_animation_names(model)
    for uid, node in nodes.items():
        if node["tag"] != "Group" or animated_ancestor(uid):
            continue
        if not any(
            set(REF.findall(node["attributes"].get(axis, ""))) & pages for axis in ("x", "y")
        ):
            continue
        poses = []
        for sample in samples:
            visible = [
                e["rect"]
                for e in sample
                if e["id"].startswith(uid + "/")
                and e["visible"]
                and e["tag"] not in {"Button", "Group"}
            ]
            if not visible:
                continue
            b = union(visible)
            poses.append(
                (
                    b,
                    b["x"] >= resolution["width"]
                    or b["x"] + b["w"] <= 0
                    or b["y"] >= resolution["height"]
                    or b["y"] + b["h"] <= 0,
                )
            )
        if not any(outside for _, outside in poses) or not any(not outside for _, outside in poses):
            continue
        for c in components:
            c["roots"] = [
                root for root in c["roots"] if root != uid and not root.startswith(uid + "/")
            ]
        components = [c for c in components if c["roots"]]
        page = {
            "id": uid,
            "name": "Authored sliding page",
            "roots": [uid],
            "members": [],
            "role": "content",
            "sourceBounds": next(b for b, outside in poses if not outside),
            "slidingPage": True,
            "inference": "observed source page enters and leaves viewport; preserve subtree",
        }
        existing = next((c for c in components if c["id"] == uid), None)
        if existing:
            page = {**existing, **page, "roots": list(dict.fromkeys([uid, *existing["roots"]]))}
            components.remove(existing)
        components.append(page)
    # Introducing a page boundary can replace an attached visual root while
    # leaving its external hit target in the previous component. Reconcile
    # physical attachments against the final roots before assigning members.
    components = attach_components(components, bindings, nodes)
    root_owners = {root: c["id"] for c in components for root in c["roots"]}
    for c in components:
        c["members"] = []
    component_index = {c["id"]: c for c in components}
    for node in nodes.values():
        if node["tag"] not in VISUAL:
            continue
        owner = boundary(node["id"], root_owners)
        if owner:
            component_index[root_owners[owner]]["members"].append(node["id"])
    components = [
        c for c in components if any(nodes[uid]["tag"] != "Group" for uid in c["members"])
    ]
    from multires_app.interaction.animation_semantics import pointer_effect_ids

    pointer_ids = pointer_effect_ids(model)
    for c in components:
        c["bindingButtons"] = [
            b["button"] for b in bindings["bindings"] if b["button"] in c["members"]
        ]
        drawable = [uid for uid in c["members"] if nodes[uid]["tag"] not in {"Group", "Button"}]
        if (
            drawable
            and set(drawable) <= pointer_ids
            and not c["bindingButtons"]
            and not c.get("layout")
        ):
            c["role"] = "decoration"
            c["pointerEffect"] = True
            c["inference"] = (
                "native pointer feedback follows current touch coordinates on both axes"
            )
        if not drawable and c["role"] != "gesture-layer":
            c["role"] = "interaction-layer"
        c["allowSpatialStack"] = all(
            nodes[r]["tag"] == "Group" for r in c["roots"] if nodes[r]["tag"] != "Button"
        )
        if drawable and any(nodes[r]["tag"] == "Group" for r in c["roots"]):
            for binding in bindings["bindings"]:
                if binding["button"] in c["members"] and binding["status"] == "needs-review":
                    binding.update(
                        status="source-group",
                        component=c["id"],
                        evidence=[
                            "existing source group retained; exact image anchor not inferred"
                        ],
                    )
    model["componentInferenceVersion"] = 3
    ids = set()
    members = set()
    for c in components:
        if c["id"] in ids:
            raise ValueError("重复组件 id: " + c["id"])
        ids.add(c["id"])
        for root in c["roots"]:
            if root not in model["nodes"]:
                raise ValueError("组件节点不存在: " + root)
            if animated_ancestor(root):
                raise ValueError("含位置动画的祖先暂不能跨组件拆分：" + root)
        for member in c["members"]:
            if member in members:
                raise ValueError("节点属于多个组件: " + member)
            members.add(member)
    return components
