"""Source optical centering, independently measurable after solving/export.

Only visible, contained source artwork establishes an intent. A moving sample
cannot create a permanent screen anchor. Explicit designer rules remain owners.
"""

import math

from multires_app.pipeline.scene_clusters import box_union, transformed_box


def centered_axes(box, viewport, fraction=0.015):
    if not box or not all(math.isfinite(box.get(k, float("nan"))) for k in ("x", "y", "w", "h")):
        return []
    if min(box["w"], box["h"]) <= 0:
        return []
    return [
        axis
        for axis, size, extent in [("x", "w", viewport["width"]), ("y", "h", viewport["height"])]
        if -0.1 <= box[axis]
        and box[axis] + box[size] <= extent + 0.1
        and abs(box[axis] + box[size] / 2 - extent / 2) <= max(2.0, extent * fraction)
    ]


def center_contracts(project, device, layout):
    """Re-derive source relations; never trust the solver's centerAxes flags.

    Groups describe membership only. Source atomic groups also participate, so
    dropping an adaptation plan does not turn missing intent into a pass.
    """
    from multires_app.layout.visual_geometry import component_visuals

    source = next(
        (
            s
            for s in project.get("sourceDevice", {}).get("layouts", [])
            if s["state"] == layout["state"]
        ),
        None,
    )
    if source is None:
        raise ValueError("matching source state missing")
    sv = project["sourceDevice"]["viewport"]
    originals = component_visuals(project, source)
    current = component_visuals(project, layout)
    components = {c["id"]: c for c in project["components"]}
    options = {
        **project.get("sourceProfile", {}).get("adaptation", {}).get("anchorLayout", {}),
        **device.get("profile", {}).get("adaptation", {}).get("anchorLayout", {}),
    }
    if not options.get("inferScreenRelations", True):
        return []
    authored = {m for g in options.get("groups", []) for m in g.get("members", [])}
    plan = layout.get("adaptationPlan", {})
    layouts = {s["state"]: s for s in device["layouts"]}
    seen = set()
    while not plan.get("groups") and plan.get("reusedFrom") in layouts:
        sid = plan["reusedFrom"]
        if sid in seen:
            raise ValueError("cyclic reused layout")
        seen.add(sid)
        plan = layouts[sid].get("adaptationPlan", {})
    groups = {g["id"]: g for g in plan.get("groups", [])}

    def leaves(gid, stack=()):
        if gid in stack:
            raise ValueError("cyclic center group")
        if gid in components:
            return [gid]
        return sorted(
            {
                c
                for mid in groups.get(gid, {}).get("centerReferenceMembers")
                or groups.get(gid, {}).get("members", [])
                for c in leaves(mid, (*stack, gid))
            }
        )

    nested = {mid for g in groups.values() for mid in g.get("members", []) if mid in groups}
    candidates = [(gid, leaves(gid), g) for gid, g in groups.items() if gid not in nested]
    covered = {mid for _, ids, _ in candidates for mid in ids}
    candidates.extend((cid, [cid], {}) for cid in originals if cid not in covered)
    result = []
    for gid, ids, g in candidates:
        if not ids or g.get("provenance") in {"manual hierarchy", "manual layout.group"}:
            continue
        if any(
            components[c].get("layout")
            or c in authored
            or components[c].get("cornerAttachment")
            or c in device.get("manualComponentAnchors", {})
            or components[c].get("pointerEffect")
            or components[c].get("role") in {"background", "gesture-layer", "interaction-layer"}
            for c in ids
        ):
            continue
        if any(c not in originals for c in ids):
            continue
        original = box_union([originals[c] for c in ids])
        axes = centered_axes(original, sv)
        if not axes:
            continue
        missing = [c for c in ids if c not in current or c not in layout.get("transforms", {})]
        target = (
            None
            if missing
            else box_union([transformed_box(current[c], layout["transforms"][c]) for c in ids])
        )
        region = plan.get("safeRegion") or {
            "x": 0,
            "y": 0,
            "w": device["viewport"]["width"],
            "h": device["viewport"]["height"],
        }
        for axis in axes:
            size = "w" if axis == "x" else "h"
            expected = region[axis] + region[size] / 2
            actual = target[axis] + target[size] / 2 if target else None
            result.append(
                {
                    "group": gid,
                    "members": ids,
                    "axis": axis,
                    "sourceBounds": original,
                    "targetBounds": target,
                    "expected": expected,
                    "actual": actual,
                    "error": abs(actual - expected) if actual is not None else None,
                    "extent": region[size],
                    "missingMembers": missing,
                }
            )
    return result
