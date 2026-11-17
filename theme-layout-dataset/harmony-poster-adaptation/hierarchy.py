"""Global semantic panels, source-anchored child transforms and paint-order evidence."""
from collections import defaultdict
from itertools import combinations
import math

import torch

from multires_app.learning.geometry_head import geometry


def span(boxes):
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    return [left, top, max(b[0] + b[2] for b in boxes) - left,
            max(b[1] + b[3] for b in boxes) - top]


def overlap(a, b):
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(w, 0) * max(h, 0) / min(a[2] * a[3], b[2] * b[3])


def center(box):
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def family(group):
    roles = group["roles"]
    return next((role for role in ("settings-panel", "shortcut-controls", "datetime",
                                  "header", "footer", "content") if role in roles), "unknown")


def source_graph(item, evidence):
    request_groups = item["sample"]["request"]["groups"]
    annotation = evidence["annotations"]
    raw = annotation["elementLevel"]["elements"]
    native = {g["id"]: g["members"] for g in annotation["compositionLevel"]["groups"]}

    def leaves(gid, ancestors=()):
        if gid in ancestors:
            raise ValueError("Cyclic authored group")
        if gid not in native:
            return {gid}
        return {leaf for node in native[gid] for leaf in leaves(node, (*ancestors, gid))}

    layer_owners = {}
    members = {}
    parents = {}
    for group in request_groups:
        alias = group["id"]
        components = leaves(evidence["groupMap"][alias])
        contained = [e for e in raw if e["component"] in components]
        members[alias] = [e["id"] for e in contained]
        parents[alias] = {c.rsplit("/", 1)[0] for c in components}
        for e in contained:
            if e["id"] in layer_owners:
                raise ValueError("Two roots claim the same visible element")
            layer_owners[e["id"]] = alias
    stack = [{"id": e["id"], "sourceNode": e["source_node_id"],
              "sourceAsset": e.get("asset"), "sourceParent": e["parent"],
              "group": layer_owners.get(e["id"]), "order": i,
              "alpha": e["alpha"], "role": e.get("semantic", {}).get("label", "unknown")}
             for i, e in enumerate(raw)]
    by_index = {entry["id"]: entry["order"] for entry in stack}
    relations = []
    pairs = []
    for i, j in combinations(range(len(request_groups)), 2):
        a, b = request_groups[i], request_groups[j]
        aid, bid = a["id"], b["id"]
        first, second = a["sourceBox"], b["sourceBox"]
        score = overlap(first, second)
        sibling = bool(parents[aid] & parents[bid])
        if score > .12:
            order_a = [by_index[m] for m in members[aid]]
            order_b = [by_index[m] for m in members[bid]]
            if order_a and order_b and max(order_a) < min(order_b):
                relations.append({"behind": aid, "front": bid, "relation": "underlay", "sourceOverlap": round(score, 5)})
            elif order_a and order_b and max(order_b) < min(order_a):
                relations.append({"behind": bid, "front": aid, "relation": "underlay", "sourceOverlap": round(score, 5)})
            else:
                relations.append({"groups": [aid, bid], "relation": "interleaved-layers",
                                  "sourceOverlap": round(score, 5)})
        x, y = center(first)
        u, v = center(second)
        align = min(abs(x - u), abs(y - v)) <= 24
        if align:
            relations.append({"groups": [aid, bid], "relation": "aligned"})
        if sibling:
            relations.append({"groups": [aid, bid], "relation": "authored-siblings"})
        if not item["mask"][i] or not item["mask"][j] or family(a) != family(b) or family(a) == "unknown":
            continue
        if a["edges"] or b["edges"] or a["centerAxes"] != b["centerAxes"]:
            continue
        if not sibling or math.hypot(x - u, y - v) > 360:
            continue
        if family(a) in {"settings-panel", "shortcut-controls"}:
            continue
        # Visual overlap contributes only after checking role and XML ownership.
        similarity = .55 + .18 * align + .17 * (score > .12) + .10 * (a["container"] == b["container"])
        if similarity >= .65:
            pairs.append((similarity, i, j))
    return members, stack, relations, sorted(pairs, reverse=True)


def independent_flow(groups, item):
    return (len(groups) >= 3 and all(family(g) == "content" and
            g["container"] in {"row", "column"} and g["id"] not in item.get("dynamicGroups", {})
            for g in groups) and not any(overlap(a["sourceBox"], b["sourceBox"]) > .02
                                        for a, b in combinations(groups, 2)))


def build(item, evidence, limit=12):
    groups = item["sample"]["request"]["groups"]
    members, stack, relations, pairs = source_graph(item, evidence)
    if len(stack) < 60 and len(groups) <= limit:
        return None
    identity = list(range(len(groups)))
    clusters = {i: [i] for i, enabled in enumerate(item["mask"]) if enabled}
    overlaps = defaultdict(set)
    for relation in relations:
        if relation["relation"] == "underlay":
            overlaps[relation["behind"]].add(relation["front"])
            overlaps[relation["front"]].add(relation["behind"])
        elif relation["relation"] == "interleaved-layers":
            a, b = relation["groups"]
            overlaps[a].add(b)
            overlaps[b].add(a)
    spans = {}
    for i in list(clusters):
        group = groups[i]
        box = group["sourceBox"]
        if family(group) == "content" and max(box[2:]) >= 740 and len(overlaps[group["id"]]) >= 3:
            spans[group["id"]] = sorted(overlaps[group["id"]])
            del clusters[i]
    for _, i, j in pairs:
        a, b = identity[i], identity[j]
        if a == b or a not in clusters or b not in clusters or len(clusters[a]) + len(clusters[b]) > 6:
            continue
        for child in clusters[b]:
            identity[child] = a
        clusters[a].extend(clusters.pop(b))
    active = sorted(clusters.values(), key=lambda c: (
        max(family(groups[i]) in {"datetime", "header", "settings-panel"} for i in c),
        sum(len(members[groups[i]["id"]]) for i in c), -min(c)), reverse=True)[:limit]
    panels, macro_groups, dynamic = [], [], {}
    for number, indexes in enumerate(active):
        selected = [groups[i] for i in sorted(indexes)]
        label = selected[0]["id"] if len(selected) == 1 else f"macro{number}"
        box = span([g["sourceBox"] for g in selected])
        flow = independent_flow(selected, item)
        summarized = {
            **selected[0], "id": label, "sourceBox": box,
            "memberCount": sum(g["memberCount"] for g in selected),
            "container": "row" if flow else "rigid" if len(selected) > 1 else selected[0]["container"],
        }
        slots = [item.get("dynamicGroups", {}).get(g["id"]) for g in selected]
        slots = [slot for slot in slots if slot]
        if slots:
            extents = list(zip(*(s["sourceInkOrGlyphExtent"] for s in slots)))
            advances = [s["maxTextAdvance"] for s in slots if s["maxTextAdvance"] is not None]
            dynamic[label] = {"roles": sorted({r for s in slots for r in s["roles"]}),
                              "sourceInkOrGlyphExtent": [max(axis) for axis in extents],
                              "maxTextAdvance": max(advances, default=None),
                              "digitLayers": sum(s["digitLayers"] for s in slots)}
        primary = max(selected, key=lambda g: len(members[g["id"]]))
        panels.append({"id": label, "children": [g["id"] for g in selected],
                       "policy": "ordered-flow" if flow else "preserve-and-scale",
                       "anchor": {"group": primary["id"], "point": primary["anchor"]},
                       "sourceBox": box})
        macro_groups.append(summarized)
    consumed = {g for panel in panels for g in panel["children"]}
    detached = [g["id"] for g in groups if g["id"] not in consumed]
    request = {**item["sample"]["request"], "groups": macro_groups}
    numbers = [geometry(request, g) for g in macro_groups]
    proxy = None
    if numbers:
        proxy = {"sample": {**item["sample"], "request": request}, "gids": [g["id"] for g in macro_groups],
                 "features": torch.tensor([v[0] for v in numbers]), "constants": torch.tensor([v[1] for v in numbers]),
                 "mask": torch.ones(len(numbers), dtype=torch.bool), "dynamicGroups": dynamic}
    document = {"role": "hierarchical-composition", "panels": panels, "originalGroups": members,
                "authoredTree": annotation_tree(evidence), "visualRelations": relations,
                "layerStack": stack, "heldGroups": detached, "multiAnchorGroups": spans,
                "inputElements": len(stack)}
    return proxy, document


def annotation_tree(evidence):
    return [{"id": g["id"], "children": g["members"], "container": g.get("container")}
            for g in evidence["annotations"]["compositionLevel"]["groups"]]


def apply_prior(box, wanted, panel_id):
    if wanted is None:
        return box
    if (not isinstance(wanted, list) or len(wanted) != 4 or
            any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in wanted) or
            min(wanted) < 0 or min(wanted[2:]) <= 0 or
            wanted[0] + wanted[2] > 1.000001 or wanted[1] + wanted[3] > 1.000001):
        raise ValueError(f"Invalid normalized composition reference for {panel_id}")
    x, y, w, h = box
    u, v = center(wanted)
    tx, ty = center(box)
    return [x + .35 * (u - tx), y + .35 * (v - ty), w, h]


def child_boxes(panel, target, boxes, viewport, safe):
    x, y, w, h = panel["sourceBox"]
    tx, ty, tw, th = target
    if panel["policy"] == "ordered-flow":
        wide = tw * safe["w"] >= th * safe["h"]
        children = sorted(panel["children"], key=lambda g: (boxes[g][1], boxes[g][0]))
        n = len(children)
        cell_w, cell_h = (tw / n, th) if wide else (tw, th / n)
        result = {}
        for i, child in enumerate(children):
            W, H = boxes[child][2:]
            aspect = W * viewport["width"] / (H * viewport["height"])
            gw = min(.9 * cell_w, .9 * cell_h * safe["h"] * aspect / safe["w"])
            gh = gw * safe["w"] / (aspect * safe["h"])
            result[child] = [tx + (i * cell_w if wide else 0) + (cell_w - gw) / 2,
                             ty + (0 if wide else i * cell_h) + (cell_h - gh) / 2, gw, gh]
        return result
    px, py = viewport["width"] / (1000 * safe["w"]), viewport["height"] / (1000 * safe["h"])
    scale = min(tw / (w * px), th / (h * py))
    ox = tx + (tw - w * px * scale) / 2
    oy = ty + (th - h * py * scale) / 2
    return {child: [ox + (boxes[child][0] - x) * px * scale,
                    oy + (boxes[child][1] - y) * py * scale,
                    boxes[child][2] * px * scale, boxes[child][3] * py * scale]
            for child in panel["children"]}


def expand(item, hierarchy, coarse, baseline, prior):
    groups = item["sample"]["request"]["groups"]
    original = {g["id"]: g["sourceBox"] for g in groups}
    request = item["sample"]["request"]
    opening = {g: b for g, b in zip(item["gids"], baseline.tolist())}
    final = dict(opening)
    operations = []
    for panel, target in zip(hierarchy["panels"], coarse.tolist()):
        target = apply_prior(target, prior.get(panel["id"]), panel["id"])
        children = child_boxes(panel, target, original, request["sourceViewport"], request["safeRegion"])
        final.update(children)
        operations.append({"op": "flow_group" if panel["policy"] == "ordered-flow" else "move_group",
                           "id": panel["id"], "targetBox": target, "children": list(children)})
    return torch.tensor([final[g] for g in item["gids"]]), operations
