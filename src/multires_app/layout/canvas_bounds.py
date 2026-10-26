"""Stable visible resource bounds; authored offscreen waiting panels stay inactive."""

import math


def intersects(b, viewport):
    return (
        min(b["w"], b["h"]) > 0
        and b["x"] < viewport["width"]
        and b["y"] < viewport["height"]
        and b["x"] + b["w"] > 0
        and b["y"] + b["h"] > 0
    )


def stable_resources(project, layout, source_layout):
    from multires_app.media.media_layout import constraint_rows, inactive_indices

    viewport = project.get("sourceDevice", {}).get("viewport", {})
    if not viewport:
        return []
    inactive = inactive_indices(project, layout)
    active = {}
    for row in constraint_rows(project, source_layout or {}):
        b = dict(zip(("x", "y", "w", "h"), row[1:5]))
        if row[5] > 0 and intersects(b, viewport):
            # Constrain only the portion visible on the authored canvas. Making
            # the full frame fit would reveal icons on the hidden half of a menu.
            active.setdefault(row[0], []).append(b)
        else:
            active.setdefault(row[0], []).append(None)
    result = []
    occurrences = {}
    roles = {c["id"]: c["role"] for c in project["components"]}
    pointer_effects = {c["id"] for c in project["components"] if c.get("pointerEffect")}
    for row in constraint_rows(project, layout):
        uid = project["nodeOrder"][row[0]]
        cid = project["nodeComponents"].get(uid)
        b = dict(zip(("x", "y", "w", "h"), row[1:5]))
        index = occurrences.get(row[0], 0)
        occurrences[row[0]] = index + 1
        if (
            row[0] in inactive
            or row[0] not in active
            or row[5] <= 0
            or cid in pointer_effects
            or roles.get(cid) in {"background", "gesture-layer"}
            or project["model"]["nodes"][uid]["tag"] == "Group"
            or min(b["w"], b["h"]) <= 0
            or not all(math.isfinite(v) for v in b.values())
        ):
            continue
        source = active[row[0]][min(index, len(active[row[0]]) - 1)]
        if source is None:
            continue
        clipped = dict(b)
        for axis, size, extent in [("x", "w", "width"), ("y", "h", "height")]:
            start = max(0.0, source[axis])
            end = min(viewport[extent], source[axis] + source[size])
            clipped[axis] = b[axis] + (start - source[axis]) * b[size] / source[size]
            clipped[size] = (end - start) * b[size] / source[size]
        result.append({"id": uid, "component": cid, "rect": clipped})
    return result


def violations(resources, transforms, region, tolerance=0.01):
    result = []
    for e in resources:
        t = transforms.get(e["component"], {"sx": 1, "sy": 1, "tx": 0, "ty": 0})
        b = e["rect"]
        q = {
            "x": b["x"] * t["sx"] + t["tx"],
            "y": b["y"] * t["sy"] + t["ty"],
            "w": b["w"] * t["sx"],
            "h": b["h"] * t["sy"],
        }
        if (
            q["x"] < region["x"] - tolerance
            or q["y"] < region["y"] - tolerance
            or q["x"] + q["w"] > region["x"] + region["w"] + tolerance
            or q["y"] + q["h"] > region["y"] + region["h"] + tolerance
        ):
            result.append({**e, "target": q})
    return result


def waiting_regions(project, layout, source_layout):
    """Full offscreen components, including invisible interaction footprints."""
    from multires_app.engine.coordinate_space import source_canvas
    from multires_app.pipeline.scene_clusters import box_union

    if source_canvas(project["model"]) is None:
        return {}

    def envelopes(l):
        groups = {}
        for r in l["rows"]:
            u = project["nodeOrder"][r[0]]
            c = project["nodeComponents"].get(u)
            if (
                c
                and r[5] > 0
                and min(r[3:5]) > 0
                and project["model"]["nodes"][u]["tag"] != "Group"
            ):
                groups.setdefault(c, []).append(dict(zip(("x", "y", "w", "h"), r[1:5])))
        return {c: box_union(bs) for c, bs in groups.items()}

    source = envelopes(source_layout)
    current = envelopes(layout)
    result = {}
    vp = project["sourceDevice"]["viewport"]
    for cid, b in source.items():
        if cid not in current:
            continue
        edges = []
        for axis, size, extent in [("x", "w", "width"), ("y", "h", "height")]:
            if b[axis] + b[size] <= 0:
                edges.append(
                    {
                        "axis": axis,
                        "edge": "before",
                        "gap": -b[axis] - b[size],
                        "sourceExtent": vp[extent],
                    }
                )
            elif b[axis] >= vp[extent]:
                edges.append(
                    {
                        "axis": axis,
                        "edge": "after",
                        "gap": b[axis] - vp[extent],
                        "sourceExtent": vp[extent],
                    }
                )
        if edges:
            result[cid] = {"rect": current[cid], "edges": edges}
    return result
