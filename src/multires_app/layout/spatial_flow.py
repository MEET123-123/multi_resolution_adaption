"""Source-disjoint visual blocks retain their order and adaptive axis gaps."""

from multires_app.pipeline.scene_clusters import overlap


def infer_leaf_flows(project, source_layout, layout, source_viewport, region, scale):
    """Separate leaves interleaved inside overlapping component envelopes."""
    from multires_app.layout.visual_geometry import visual_rect, component_visuals

    nodes = project["model"]["nodes"]
    order = project["nodeOrder"]
    owners = project["nodeComponents"]
    definitions = {c["id"]: c for c in project["components"]}
    special = {
        "background",
        "gesture-layer",
        "decoration",
        "interaction-layer",
        "popup",
        "overlay",
        "dialog",
    }

    def rows(l):
        result = {}
        for r in l["rows"]:
            uid = order[r[0]]
            cid = owners.get(uid)
            if (
                r[5] <= 0
                or not cid
                or definitions[cid]["role"] in special
                or nodes[uid]["tag"] == "Group"
            ):
                continue
            b = (
                dict(zip(("x", "y", "w", "h"), r[1:5]))
                if nodes[uid]["tag"] == "Button"
                else visual_rect(project, r)
            )
            if b and min(b["w"], b["h"]) > 0:
                result.setdefault(uid, []).append(b)
        return result

    src = rows(source_layout)
    dst = rows(layout)
    envelopes = component_visuals(project, source_layout)
    entries = []
    result = []
    screen = {"x": 0, "y": 0, "w": source_viewport["width"], "h": source_viewport["height"]}
    for uid, rects in src.items():
        for a, b in zip(rects, dst.get(uid, [])):
            if overlap(a, screen) <= 0:
                continue
            entries.append(
                {
                    "id": owners[uid],
                    "node": uid,
                    "bbox": a,
                    "source_visual_bbox": a,
                    "current_visual_bbox": b,
                }
            )
    for i, a in enumerate(entries):
        for b in entries[:i]:
            if a["id"] == b["id"]:
                continue
            buttons = [nodes[e["node"]]["tag"] == "Button" for e in (a, b)]
            # Hit targets can extend beyond their artwork. Keep disjoint local
            # targets reachable even when reflow brings their visuals close.
            # Mixed visual/hit pairs and authored overlaps carry no separation.
            if any(buttons) and not all(buttons):
                continue
            if not all(buttons) and overlap(envelopes[a["id"]], envelopes[b["id"]]) <= 0:
                continue
            for relation in infer_flows([a, b], source_viewport, region, scale):
                axis = relation["axis"]
                other = "y" if axis == "x" else "x"
                size = "h" if axis == "x" else "w"
                aa = a["bbox"]
                bb = b["bbox"]
                common = max(
                    0, min(aa[other] + aa[size], bb[other] + bb[size]) - max(aa[other], bb[other])
                )
                if common < 0.15 * min(aa[size], bb[size]):
                    continue
                relation.update(
                    id="flow:leaf:" + str(len(result)),
                    firstNode=a["node"] if relation["first"] == a["id"] else b["node"],
                    lastNode=b["node"] if relation["last"] == b["id"] else a["node"],
                    separationOnly=all(buttons),
                    provenance=(
                        "source-disjoint local hit targets"
                        if all(buttons)
                        else "source-disjoint visible leaves within overlapping component envelopes"
                    ),
                )
                result.append(relation)
    return result


def infer_flows(elements, source_viewport, region, scale):
    eligible = []
    result = []
    for e in elements:
        b = e.get("source_visual_bbox", e["bbox"])
        if e.get("special") or min(b["w"], b["h"]) <= 0:
            continue
        if (
            b["x"] + b["w"] <= 0
            or b["y"] + b["h"] <= 0
            or b["x"] >= source_viewport["width"]
            or b["y"] >= source_viewport["height"]
        ):
            continue
        eligible.append((e, b))
    for i, (a, aa) in enumerate(eligible):
        for b, bb in eligible[:i]:
            if overlap(aa, bb) > 1e-6:
                continue  # authored overlays stay overlays
            choices = []
            for axis, size, cross, other, extent in [
                ("y", "h", "x", "w", "height"),
                ("x", "w", "y", "h", "width"),
            ]:
                first, last = (a, b) if aa[axis] <= bb[axis] else (b, a)
                ab, zb = (aa, bb) if first is a else (bb, aa)
                gap = zb[axis] - ab[axis] - ab[size]
                if gap < -0.01:
                    continue
                cross_overlap = max(
                    0, min(ab[cross] + ab[other], zb[cross] + zb[other]) - max(ab[cross], zb[cross])
                ) / max(0.001, min(ab[other], zb[other]))
                choices.append(
                    (
                        cross_overlap,
                        -gap / source_viewport[extent],
                        axis,
                        size,
                        extent,
                        first,
                        last,
                        max(0, gap),
                    )
                )
            if not choices:
                continue
            a_corner = (
                bool(a.get("cornerShortcut") or a.get("cornerAttachment"))
                and aa["y"] >= source_viewport["height"] * 0.78
            )
            b_corner = (
                bool(b.get("cornerShortcut") or b.get("cornerAttachment"))
                and bb["y"] >= source_viewport["height"] * 0.78
            )
            zone_relation = False
            if a_corner != b_corner:
                footer, content = (aa, bb) if a_corner else (bb, aa)
                if content["y"] + content["h"] <= footer["y"] + 0.01:
                    # A header and a corner icon occupy different vertical
                    # regions even if their horizontal projections miss.
                    # Do not pull the clock sideways to retain an x gap.
                    choices = [c for c in choices if c[2] == "y"] or choices
                    zone_relation = True
            _, _, axis, size, extent, first, last, gap = max(choices, key=lambda c: (c[0], c[1]))
            ratio = region[size] / source_viewport[extent]
            preferred = gap * ratio
            minimum = min(preferred, max(0.5, gap * min(ratio, scale) * 0.25)) if gap > 0 else 0.0
            result.append(
                {
                    "id": "flow:" + str(len(result)),
                    "first": first["id"],
                    "last": last["id"],
                    "axis": axis,
                    "sourceGap": gap,
                    "preferredGap": preferred,
                    "minimumGap": minimum,
                    "preferenceWeight": max(
                        0.1, 100 / (1 + gap / max(1, source_viewport[extent] * 0.03)) ** 2
                    ),
                    "separationOnly": zone_relation,
                    "firstRect": first["current_visual_bbox"],
                    "lastRect": last["current_visual_bbox"],
                    "provenance": "source visual blocks are disjoint; retain axis order and height/width-relative gap",
                }
            )
    return result


def flow_errors(relations, transforms, tolerance=0.01):
    from multires_app.pipeline.scene_clusters import transformed_box

    errors = []
    for r in relations:
        if r["first"] not in transforms or r["last"] not in transforms:
            continue
        a = transformed_box(r["firstRect"], transforms[r["first"]])
        b = transformed_box(r["lastRect"], transforms[r["last"]])
        axis = r["axis"]
        size = "w" if axis == "x" else "h"
        gap = b[axis] - a[axis] - a[size]
        if gap < r["minimumGap"] - tolerance:
            errors.append({**r, "actualGap": gap})
    return errors


def insertion_blockers(
    relations, current_visuals, transforms, affected, fixed, region, min_scale=0.03
):
    """Classify a local insertion before locking peers at the canvas edge."""
    from multires_app.pipeline.scene_clusters import transformed_box

    released = set()
    fixed = set(fixed)
    for cid in affected:
        if cid not in current_visuals:
            continue
        for axis, size in [("x", "w"), ("y", "h")]:
            low = region[axis]
            high = low + region[size]
            bounds = []
            for f in relations:
                if f["axis"] != axis:
                    continue
                if f["last"] == cid and f["first"] in fixed and f["first"] in transforms:
                    b = transformed_box(f["firstRect"], transforms[f["first"]])
                    value = b[axis] + b[size] + f["minimumGap"]
                    low = max(low, value)
                    bounds.append(f["first"])
                elif f["first"] == cid and f["last"] in fixed and f["last"] in transforms:
                    b = transformed_box(f["lastRect"], transforms[f["last"]])
                    value = b[axis] - f["minimumGap"]
                    high = min(high, value)
                    bounds.append(f["last"])
            if low + current_visuals[cid][size] * min_scale > high + 0.01:
                released.update(bounds)
    return released


def layout_flows(project, device, layout):
    from multires_app.layout.visual_geometry import component_visuals
    from multires_app.layout.adaptive_layout import safe_region
    from multires_app.layout.visual_scaling import resource_scale

    sources = {l["state"]: l for l in project.get("sourceDevice", {}).get("layouts", [])}
    source = component_visuals(project, sources.get(layout["state"], {}))
    current = component_visuals(project, layout)
    options = {
        **project["sourceProfile"].get("adaptation", {}).get("anchorLayout", {}),
        **device["profile"].get("adaptation", {}).get("anchorLayout", {}),
    }
    if not options.get("inferScreenRelations", True) or options.get("groups"):
        return []
    region = safe_region(
        project["sourceProfile"], device["profile"], {"logicalViewport": device["viewport"]}
    )
    vp = project["sourceDevice"]["viewport"]
    scale = resource_scale(vp, region, options, device["profile"])["scale"]
    elements = [
        {
            "id": c["id"],
            "bbox": source[c["id"]],
            "source_visual_bbox": source[c["id"]],
            "current_visual_bbox": current[c["id"]],
            "cornerShortcut": bool(c.get("cornerAttachment")),
            "special": c["role"]
            in {"background", "gesture-layer", "decoration", "interaction-layer"},
        }
        for c in project["components"]
        if c["id"] in source and c["id"] in current
    ]
    return infer_flows(elements, vp, region, scale) + infer_leaf_flows(
        project, sources.get(layout["state"], layout), layout, vp, region, scale
    )
