"""Reviewable component/group IR. Geometry alone never proves designer intent."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from multires_app.pipeline.scene_clusters import box_union, overlap

POINTS = {
    f"{v}_{h}": (x, y)
    for v, y in [("top", 0.0), ("center", 0.5), ("bottom", 1.0)]
    for h, x in [("left", 0.0), ("center", 0.5), ("right", 1.0)]
}
POINTS["center"] = (0.5, 0.5)
ALIASES = {
    "top": "top_center",
    "bottom": "bottom_center",
    "left": "center_left",
    "right": "center_right",
}


def point(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        result = tuple(float(x) for x in value)
        if all(math.isfinite(x) and 0 <= x <= 1 for x in result):
            return result
    if isinstance(value, str) and ALIASES.get(value, value) in POINTS:
        return POINTS[ALIASES.get(value, value)]
    raise ValueError("anchor point must be a nine-grid name or normalized [u, v]")


def number(value, name, minimum=None):
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(
            name + " must be finite" + (" and >= " + str(minimum) if minimum is not None else "")
        )
    return result


def infer_anchor(bounds, viewport, paired_targets=None):
    """Single-frame prior, explicitly lower confidence than paired supervision."""
    candidates = []
    for name, (u, v) in POINTS.items():
        if name == "center":
            continue
        dx = bounds["x"] + u * bounds["w"] - u * viewport["width"]
        dy = bounds["y"] + v * bounds["h"] - v * viewport["height"]
        error = abs(dx) / viewport["width"] + abs(dy) / viewport["height"]
        if paired_targets:
            errors = []
            for pair in paired_targets:
                target = pair["viewport"]
                expected = pair["bbox"]
                width = number(target["width"], "paired viewport width", 1)
                height = number(target["height"], "paired viewport height", 1)
                px = u * width - u * expected["w"] + dx
                py = v * height - v * expected["h"] + dy
                errors.append(
                    math.hypot(px - expected["x"], py - expected["y"]) / math.hypot(width, height)
                )
            error = sum(errors) / len(errors)
        candidates.append((error, name, dx, dy))
    candidates.sort()
    best = candidates[0]
    margin = candidates[1][0] - best[0]
    return {
        "parent_id": "screen",
        "parent_point": best[1],
        "self_point": best[1],
        "offset": [best[2], best[3]],
        "offset_unit": "px",
        "confidence": min(0.82, 0.5 + margin),
        "provenance": "paired designer target" if paired_targets else "single-frame geometry prior",
        "candidates": [{"point": n, "score": round(e, 5)} for e, n, _, _ in candidates],
        "review_required": margin < 0.06,
    }


def relation(a, b, viewport):
    aa, bb = a["bbox"], b["bbox"]
    area = min(aa["w"] * aa["h"], bb["w"] * bb["h"])
    ratio = overlap(aa, bb) / max(1, area)
    if ratio >= 0.85 and a.get("allow_stack") and b.get("allow_stack"):
        return {
            "type": "overlay",
            "score": 0.91,
            "evidence": ["source overlap", "atomic source groups"],
        }
    for axis, other, kind in [("x", "y", "row"), ("y", "x", "column")]:
        size, cross = ("w", "h") if axis == "x" else ("h", "w")
        first, last = sorted([aa, bb], key=lambda r: r[axis])
        gap = last[axis] - first[axis] - first[size]
        alignment = min(
            abs(aa[other] + k * aa[cross] - bb[other] - k * bb[cross]) for k in (0, 0.5, 1)
        ) / max(1, min(aa[cross], bb[cross]))
        similar = min(aa[cross], bb[cross]) / max(1, max(aa[cross], bb[cross]))
        nearby = (
            gap <= min(viewport["width"], viewport["height"]) * 0.06
            and gap <= max(aa[size], bb[size]) * 0.8
        )
        if (
            gap >= -1e-6
            and nearby
            and alignment < 0.12
            and similar > 0.7
            and a["role"] == b["role"]
        ):
            return {
                "type": kind,
                "score": 0.86,
                "evidence": ["nearby", "aligned", "similar cross size", "same role"],
            }
    return None


def sparse_edges(elements, viewport):
    """Spatial bins plus bounded neighbour search; a full-screen image is excluded."""
    cell = max(1, min(viewport["width"], viewport["height"]) / 6)
    bins = defaultdict(list)
    pairs = set()
    result = []
    for element in sorted(elements, key=lambda e: e["id"]):
        b = element["bbox"]
        left, right = math.floor(b["x"] / cell), math.floor((b["x"] + b["w"]) / cell)
        top, bottom = math.floor(b["y"] / cell), math.floor((b["y"] + b["h"]) / cell)
        # Extremely large/offscreen artwork remains a free rigid component.
        if (right - left + 3) * (bottom - top + 3) > 256:
            continue
        for x in range(left - 1, right + 2):
            for y in range(top - 1, bottom + 2):
                for prior in bins[(x, y)]:
                    key = tuple(sorted((prior["id"], element["id"])))
                    if key in pairs:
                        continue
                    pairs.add(key)
                    edge = relation(prior, element, viewport)
                    if edge:
                        result.append(
                            {"targets": list(key), **edge, "provenance": "geometry candidate"}
                        )
        for x in range(left, right + 1):
            for y in range(top, bottom + 1):
                bins[(x, y)].append(element)
    return result


def container_type(value):
    value = value.get("type", "rigid") if isinstance(value, dict) else value
    value = {"horizontal": "row", "vertical": "column"}.get(str(value).lower(), str(value).lower())
    if value not in {"rigid", "row", "column", "overlay", "grid"}:
        raise ValueError("unknown container: " + value)
    return value


def build_ir(components, viewport, context, options):
    refs = context.get("sourceComponentDescriptors", {})
    nodes = context.get("model", {}).get("nodes", {})
    z_indices = {uid: i for i, uid in enumerate(nodes)}
    elements = []
    special = {"background", "gesture-layer", "decoration", "interaction-layer"}
    for c in sorted(components, key=lambda c: c["id"]):
        if not c.get("visible", True) or min(c["bounds"]["w"], c["bounds"]["h"]) <= 0:
            continue
        b = dict(refs.get(c["id"], {}).get("bounds", c.get("sourceBounds", c["bounds"])))
        elements.append(
            {
                "id": c["id"],
                "bbox": b,
                "current_bbox": dict(c["bounds"]),
                "role": c["role"],
                "source_visual_bbox": context.get("sourceStableVisualBounds", {}).get(
                    c["id"], refs.get(c["id"], {}).get("visualBounds", b)
                ),
                "current_visual_bbox": context.get("stableVisualBounds", {}).get(
                    c["id"], refs.get(c["id"], {}).get("visualBounds", c["bounds"])
                ),
                "source_edge_bbox": context.get("sourceFrameBounds", {}).get(c["id"], b),
                "current_edge_bbox": context.get("frameBounds", {}).get(c["id"], c["bounds"]),
                "coordinate_space": "source_screen",
                "layout": c.get("layout", {}),
                "source_ref": c.get("roots", [c["id"]]),
                "z_order": min((z_indices[n] for n in c.get("roots", []) if n in nodes), default=0),
                "asset_refs": [e.get("asset", "") for e in c.get("elements", [])],
                "member_ids": c.get("members", []),
                "interaction_bindings": c.get("bindingButtons", []),
                "screenEdgeIntent": (
                    {}
                    if c.get("cornerAttachment")
                    and c["id"] in context.get("inactiveCornerComponents", [])
                    else c.get("screenEdgeIntent", {})
                ),
                "cornerAttachment": (
                    {}
                    if c["id"] in context.get("inactiveCornerComponents", [])
                    else c.get("cornerAttachment", {})
                ),
                "cornerShortcut": bool(c.get("cornerAttachment")),
                "inferredSection": c.get("sectionSplit"),
                "visible_alpha_bbox": None,
                "bbox_provenance": "source same-state script geometry; alpha contour not inferred",
                "asset_types": sorted(
                    {
                        context.get("assets", {}).get(e.get("asset"), {}).get("kind", "unknown")
                        for e in c.get("elements", [])
                    }
                ),
                "allow_stack": c.get("allowSpatialStack", False),
                "canonical_group_id": c["id"],
                "atomic": True,
                "special": c["role"] in special,
                "pointerEffect": c.get("pointerEffect", False),
            }
        )
    by_id = {e["id"]: e for e in elements}
    ordinary = [e for e in elements if not e["special"]]
    ungrouped = (
        set(options.get("ungroupedIds", []))
        | set(context.get("nativeGlyphResourceScales", {}))
        | set(context.get("sharedResourceScales", {}))
        | {e["id"] for e in ordinary if e.get("cornerAttachment")}
    )
    from multires_app.layout.screen_relations import footer_rails, horizontal_attachment

    authored_members = {mid for g in options.get("groups", []) for mid in g.get("members", [])}
    if options.get("inferScreenRelations", True):
        for e in elements:
            if (
                not e["layout"]
                and not e.get("pointerEffect")
                and e["id"] not in authored_members
                and e["role"] not in {"background", "gesture-layer", "interaction-layer"}
            ):
                e["horizontalAttachment"] = horizontal_attachment(
                    e["source_edge_bbox"], viewport, e["source_visual_bbox"]
                )
        # Edge atoms keep their own roots instead of inheriting an interior
        # container's centered origin. Their internal image/button binding stays.
        ungrouped.update(e["id"] for e in ordinary if e.get("horizontalAttachment"))
    rails = footer_rails(ordinary, viewport) if options.get("inferScreenRelations", True) else []
    rail_ids = {cid for rail in rails for cid in rail["members"]}
    edges = sparse_edges(
        [
            e
            for e in ordinary
            if e["id"] not in context.get("fixedTransforms", {})
            and e["id"] not in ungrouped
            and e["id"] not in rail_ids
        ],
        viewport,
    )
    groups = []
    assigned = set()
    explicit = defaultdict(list)
    for e in ordinary:
        if e["layout"].get("group") and e["id"] not in ungrouped:
            explicit[str(e["layout"]["group"])].append(e["id"])
    for name, ids in sorted(explicit.items()):
        hint = by_id[ids[0]]["layout"]
        groups.append(
            {
                "id": "group:" + name,
                "members": sorted(ids),
                "container": hint.get("container", "rigid"),
                "confidence": 1.0,
                "provenance": "manual layout.group",
                "anchor": hint.get("anchor"),
                "size_policy": hint.get("sizePolicy", "uniform_scale"),
            }
        )
        assigned.update(ids)
    # Wide bitmap callouts immediately above an interactive footer are a
    # shared composition: their arrow tips must keep pointing at its buttons.
    for footer in ordinary:
        fb = footer["bbox"]
        if (
            footer["role"] != "footer"
            or len(footer["interaction_bindings"]) < 2
            or fb["w"] < viewport["width"] * 0.65
        ):
            continue
        for hint in ordinary:
            hb = hint["bbox"]
            gap = fb["y"] - hb["y"] - hb["h"]
            if (
                hint["id"] in assigned
                or footer["id"] in assigned
                or not hint.get("inferredSection")
                or hint["interaction_bindings"]
            ):
                continue
            if (
                hb["w"] < viewport["width"] * 0.65
                or hb["h"] > viewport["height"] * 0.18
                or not 0 <= gap <= viewport["height"] * 0.08
            ):
                continue
            if (
                min(hb["x"] + hb["w"], fb["x"] + fb["w"]) - max(hb["x"], fb["x"])
                < min(hb["w"], fb["w"]) * 0.85
            ):
                continue
            ids = [footer["id"], hint["id"]]
            groups.append(
                {
                    "id": "attachment:" + footer["id"],
                    "members": ids,
                    "container": "rigid",
                    "confidence": 0.9,
                    "centerReferenceMembers": [footer["id"]],
                    "provenance": "nearby wide callout follows interactive footer; arrow offset retained",
                }
            )
            assigned.update(ids)
    # Complete-link validation avoids A-B-C proximity chains merging a page.
    edge_map = {tuple(e["targets"]): e for e in edges}
    for edge in sorted(edges, key=lambda e: (-e["score"], e["targets"])):
        a, b = edge["targets"]
        if a in assigned or b in assigned:
            continue
        ids = [a, b]
        for e in ordinary:
            cid = e["id"]
            if cid in assigned or cid in ids or cid in rail_ids:
                continue
            if edge["type"] in {"row", "column"}:
                axis = "x" if edge["type"] == "row" else "y"
                candidate = sorted(ids + [cid], key=lambda i: by_id[i]["bbox"][axis])
                valid = all(
                    (pair := edge_map.get(tuple(sorted((left, right)))))
                    and pair["type"] == edge["type"]
                    for left, right in zip(candidate, candidate[1:])
                )
                cross = "y" if axis == "x" else "x"
                size = "h" if axis == "x" else "w"
                centers = [by_id[i]["bbox"][cross] + by_id[i]["bbox"][size] / 2 for i in candidate]
                valid = (
                    valid
                    and max(centers) - min(centers)
                    < min(by_id[i]["bbox"][size] for i in candidate) * 0.12
                )
            else:
                valid = all(
                    (pair := edge_map.get(tuple(sorted((cid, other)))))
                    and pair["type"] == edge["type"]
                    for other in ids
                )
            if valid:
                ids.append(cid)
        # Spread across original ordered boxes is still checked by every pair.
        gid = "auto:" + hashlib.sha1("|".join(sorted(ids)).encode()).hexdigest()[:12]
        groups.append(
            {
                "id": gid,
                "members": ids,
                "container": {"type": edge["type"], "preserveSourceAlignment": True},
                "confidence": edge["score"],
                "provenance": edge["evidence"],
            }
        )
        assigned.update(ids)
    for e in ordinary:
        if e["id"] not in assigned:
            groups.append(
                {
                    "id": "atomic:" + e["id"],
                    "members": [e["id"]],
                    "container": "rigid",
                    "confidence": 0.6,
                    "provenance": "atomic source component",
                    "anchor": e["layout"].get("anchor"),
                    "size_policy": e["layout"].get("sizePolicy", "uniform_scale"),
                }
            )
    # Explicit hierarchy replaces automatic ownership of its leaves.
    custom = options.get("groups", [])
    if custom:
        # Validate the complete authored hierarchy, then activate only members
        # visible in this reachable scene. Hidden variants are not spelling errors.
        configured = {g["id"]: g for g in custom}
        if len(configured) != len(custom):
            raise ValueError("duplicate group id")
        all_known = set(context.get("componentDefinitions", by_id)) | set(by_id)
        owners_full = {}
        active = {}
        checking = set()

        def activate(gid):
            if gid in checking:
                raise ValueError("cyclic group hierarchy: " + gid)
            if gid in active:
                return active[gid]
            checking.add(gid)
            g = configured[gid]
            members = []
            for mid in g.get("members", []):
                if mid in owners_full and owners_full[mid] != gid:
                    raise ValueError("member has multiple parents: " + mid)
                owners_full[mid] = gid
                if mid in configured:
                    if activate(mid):
                        members.append(mid)
                elif mid in by_id:
                    members.append(mid)
                elif mid not in all_known:
                    raise ValueError("unknown group member: " + mid)
            checking.remove(gid)
            active[gid] = members
            return members

        for gid in configured:
            activate(gid)
        custom = [{**g, "members": active[g["id"]]} for g in custom if active[g["id"]]]
        custom_ids = {g["id"] for g in custom}
        if len(custom_ids) != len(custom):
            raise ValueError("duplicate group id")
        member_ids = {m for g in custom for m in g["members"]}
        referenced_leaves = member_ids - custom_ids
        unknown = referenced_leaves - set(by_id)
        if unknown:
            raise ValueError("unknown group members: " + str(sorted(unknown)))
        if any(by_id[i]["special"] for i in referenced_leaves):
            raise ValueError(
                "background/gesture/decorative layers cannot enter a content container"
            )
        groups = [g for g in groups if not (set(g["members"]) & referenced_leaves)]
        # Keep non-overridden siblings as free atoms.
        covered = {m for g in groups for m in g["members"]} | referenced_leaves
        for e in ordinary:
            if e["id"] not in covered:
                groups.append(
                    {
                        "id": "atomic:" + e["id"],
                        "members": [e["id"]],
                        "container": "rigid",
                        "confidence": 0.6,
                    }
                )
        groups += [{**g, "confidence": 1.0, "provenance": "manual hierarchy"} for g in custom]
    index = {g["id"]: g for g in groups}
    if set(index) & set(by_id):
        raise ValueError("group and component ids must be distinct")
    visiting = set()
    owners = {}
    resolved = set()

    def visit(gid):
        if gid in visiting:
            raise ValueError("cyclic group hierarchy: " + gid)
        if gid in resolved:
            return index[gid]["bbox"]
        visiting.add(gid)
        g = index[gid]
        boxes = []
        if not g.get("members"):
            raise ValueError("empty group: " + gid)
        for mid in g["members"]:
            if mid in owners:
                raise ValueError("member has multiple parents: " + mid)
            owners[mid] = gid
            if mid not in index and mid not in by_id:
                raise ValueError("unknown member: " + mid)
            boxes.append(visit(mid) if mid in index else by_id[mid]["bbox"])
        g["bbox"] = box_union(boxes)
        g["container"] = {
            "type": container_type(g.get("container", "rigid")),
            **(g.get("container") if isinstance(g.get("container"), dict) else {}),
        }
        g["container"]["type"] = container_type(g["container"])
        g.setdefault("size_policy", "uniform_scale")
        g["min_scale"] = max(
            [
                number(by_id[m]["layout"].get("minScale", 0), "layout.minScale", 0)
                for m in g["members"]
                if m in by_id
            ]
            + [index[m].get("min_scale", 0) for m in g["members"] if m in index]
            + [number(g.get("min_scale", 0), "group.min_scale", 0)]
        )
        anchor = g.get("anchor")
        automatic_anchor = anchor in (None, "auto", "original")
        explicit_parent = isinstance(anchor, dict) and "parent_id" in anchor
        if automatic_anchor:
            anchor = infer_anchor(g["bbox"], viewport, options.get("pairedTargets", {}).get(gid))
        elif isinstance(anchor, str):
            anchor = {"parent_point": anchor, "self_point": anchor}
        if not isinstance(anchor, dict):
            raise ValueError("anchor must be an object or point name")
        anchor = {**anchor}
        u, v = point(anchor.get("parent_point", "center"))
        us, vs = point(anchor.get("self_point", anchor.get("parent_point", "center")))
        anchor.setdefault("parent_id", "screen")
        anchor.setdefault("offset_unit", "px")
        anchor["_offset_inferred"] = "offset" not in anchor or automatic_anchor
        anchor["_explicit_parent"] = explicit_parent
        anchor.setdefault(
            "offset",
            [
                g["bbox"]["x"] + us * g["bbox"]["w"] - u * viewport["width"],
                g["bbox"]["y"] + vs * g["bbox"]["h"] - v * viewport["height"],
            ],
        )
        if anchor["_offset_inferred"] and anchor["offset_unit"] == "normalized":
            anchor["offset"] = [
                anchor["offset"][0] / viewport["width"],
                anchor["offset"][1] / viewport["height"],
            ]
        if len(anchor["offset"]) != 2:
            raise ValueError("anchor offset requires [dx, dy]")
        anchor["offset"] = [number(n, "anchor offset") for n in anchor["offset"]]
        if anchor["offset_unit"] not in {"px", "normalized"}:
            raise ValueError("offset_unit must be px or normalized")
        g["anchor"] = anchor
        g["coordinate_space"] = "source_screen"
        visiting.remove(gid)
        resolved.add(gid)
        return g["bbox"]

    roots = [g["id"] for g in groups if not any(g["id"] in p["members"] for p in groups)]
    for gid in roots:
        visit(gid)
    for gid in index:
        if gid not in resolved:
            visit(gid)  # catches isolated cycles
    for g in groups:
        parent = owners.get(g["id"])
        if parent:
            a = g["anchor"]
            if a.pop("_explicit_parent", False) and a["parent_id"] != parent:
                raise ValueError("nested anchor parent must match group hierarchy: " + g["id"])
            a["parent_id"] = parent
            if a.pop("_offset_inferred", False):
                u, v = point(a.get("parent_point", "center"))
                us, vs = point(a.get("self_point", a.get("parent_point", "center")))
                p = index[parent]["bbox"]
                b = g["bbox"]
                a["offset"] = [
                    b["x"] - p["x"] + us * b["w"] - u * p["w"],
                    b["y"] - p["y"] + vs * b["h"] - v * p["h"],
                ]
                if a["offset_unit"] == "normalized":
                    a["offset"] = [
                        a["offset"][0] / max(0.001, p["w"]),
                        a["offset"][1] / max(0.001, p["h"]),
                    ]
        else:
            g["anchor"].pop("_offset_inferred", None)
            g["anchor"].pop("_explicit_parent", None)
    from multires_app.layout.screen_relations import scripted_edges, screen_aliases

    aliases = screen_aliases(context.get("model", {}))

    def visible_group(gid):
        return box_union(
            [
                visible_group(mid) if mid in index else by_id[mid]["source_visual_bbox"]
                for mid in index[gid].get("centerReferenceMembers", index[gid]["members"])
            ]
        )

    for g in groups:
        authored = g.get("provenance") in ("manual hierarchy", "manual layout.group") or any(
            by_id[m].get("layout") for m in g["members"] if m in by_id
        )
        if g["id"] in roots and not authored and options.get("inferScreenRelations", True):
            members = [
                uid for mid in g["members"] if mid in by_id for uid in by_id[mid]["member_ids"]
            ]
            g["screenAttachment"] = scripted_edges(
                g["bbox"], viewport, members, context.get("model", {}), aliases
            )
            # A canonical animation frame cannot invent a hard screen edge.
            if g["screenAttachment"].get("x", {}).get("priority") == "required":
                g["screenAttachment"].pop("x")
            if len(g["members"]) == 1 and by_id.get(g["members"][0], {}).get(
                "horizontalAttachment"
            ):
                g["screenAttachment"]["x"] = dict(by_id[g["members"][0]]["horizontalAttachment"])
            if len(g["members"]) == 1:
                g["screenAttachment"].update(
                    by_id.get(g["members"][0], {}).get("screenEdgeIntent", {})
                )
            corner = (
                by_id.get(g["members"][0], {}).get("cornerAttachment")
                if len(g["members"]) == 1
                else None
            )
            if corner:
                g["cornerAttachment"] = corner
                g["screenAttachment"] = {
                    "x": {
                        "edge": "left" if corner["corner"] == "bottom-left" else "right",
                        "margin": corner["sourceMargins"]["x"],
                        "priority": "required",
                    },
                    "y": {
                        "edge": "bottom",
                        "margin": corner["sourceMargins"]["y"],
                        "priority": "required",
                    },
                }
            b = visible_group(g["id"])
            g["sourceVisualBounds"] = b
            g["centerAxes"] = [
                axis
                for axis, size, extent in [
                    ("x", "w", viewport["width"]),
                    ("y", "h", viewport["height"]),
                ]
                if 0 <= b[axis]
                and b[axis] + b[size] <= extent + 0.1
                and abs(b[axis] + b[size] / 2 - extent / 2) <= max(2.0, extent * 0.015)
            ]

            # Optical centering often differs by a few pixels due to glyphs or
            # alpha padding. Preserve that design intent as a constraint, not
            # only when a floating point center is exactly equal to the canvas.
            def leaf_ids(gid):
                return [
                    cid
                    for mid in index[gid].get("centerReferenceMembers", index[gid]["members"])
                    for cid in (leaf_ids(mid) if mid in index else [mid])
                ]

            ids = leaf_ids(g["id"])
            from multires_app.layout.center_intent import centered_axes

            for samples in context.get("flowSamples", []):
                source_sample = samples[0]
                if all(cid in source_sample for cid in ids):
                    stable = box_union([source_sample[cid] for cid in ids])
                    g["centerAxes"] = [
                        axis for axis in g["centerAxes"] if axis in centered_axes(stable, viewport)
                    ]
            g["centerRequiredAxes"] = [
                axis
                for axis in g["centerAxes"]
                if not any(mid in context.get("fixedTransforms", {}) for mid in ids)
            ]
            if corner:
                g["centerAxes"] = []
                g["centerRequiredAxes"] = []
            g["centerAxes"] = [
                axis
                for axis in g["centerAxes"]
                if g["screenAttachment"].get(axis, {}).get("priority") != "required"
            ]
            g["centerRequiredAxes"] = [
                axis for axis in g["centerRequiredAxes"] if axis in g["centerAxes"]
            ]
            for axis in g["centerAxes"]:
                g["screenAttachment"].pop(axis, None)
    # Only independent atoms are eligible; custom grouping remains authoritative.
    atom_roots = {
        g["members"][0]: g["id"]
        for g in groups
        if g["id"] in roots
        and len(g["members"]) == 1
        and g["members"][0] in by_id
        and g.get("provenance") != "manual hierarchy"
    }
    rails = [
        {**rail, "roots": [atom_roots[cid] for cid in rail["members"]]}
        for rail in rails
        if all(cid in atom_roots for cid in rail["members"])
    ]
    return {
        "schema_version": "1.0",
        "scene_id": context.get("state", {}).get("id", "unknown"),
        "source_viewport": dict(viewport),
        "coordinate_unit": "theme-engine-logical",
        "elements": elements,
        "groups": groups,
        "roots": roots,
        "relations": edges,
        "constraints": options.get("constraints", []),
        "screenDistributions": rails,
        "z_order_policy": "preserve source script order",
        "dynamic_validation": "stable-frame geometry only",
    }


def local_geometry(ir):
    """Nested container frames in unscaled local coordinates; atomic interiors stay rigid."""
    elements = {e["id"]: e for e in ir["elements"]}
    groups = {g["id"]: g for g in ir["groups"]}
    output = {}

    def make(gid):
        g = groups[gid]
        kind = g["container"]["type"]
        b = g["bbox"]
        children = []
        for mid in g["members"]:
            if mid in groups:
                child = make(mid)
                size = child["size"]
                leaves = child["leaves"]
                ref = groups[mid]["bbox"]
            else:
                e = elements[mid]
                ref = e["bbox"]
                size = {k: max(0.001, e["current_bbox"][k]) for k in ("w", "h")}
                leaves = {mid: {"x": 0.0, "y": 0.0, **size}}
            visual_ref = ref
            visual_local = {"x": 0.0, "y": 0.0, **size}
            if mid not in groups:
                visual_ref = e.get("source_visual_bbox", ref)
                v = e.get("current_visual_bbox", e["current_bbox"])
                visual_local = {
                    **v,
                    "x": v["x"] - e["current_bbox"]["x"],
                    "y": v["y"] - e["current_bbox"]["y"],
                }
            children.append(
                {
                    "id": mid,
                    "ref": ref,
                    "size": size,
                    "leaves": leaves,
                    "visualRef": visual_ref,
                    "visualLocal": visual_local,
                }
            )
        if kind in {"row", "column", "grid"}:
            axis = "y" if kind == "column" else "x"
            children.sort(key=lambda c: (c["ref"][axis], c["ref"]["y"], c["id"]))
        cfg = g["container"]
        padding = cfg.get("padding", 0)
        if isinstance(padding, (int, float)):
            padding = [padding] * 4
        if not isinstance(padding, (list, tuple)) or len(padding) != 4:
            raise ValueError("padding must be a number or [left,top,right,bottom]")
        pl, pt, pr, pb = [number(v, "padding", 0) for v in padding]
        positions = []
        gap_values = []
        if kind in {"row", "column"}:
            axis, size = ("x", "w") if kind == "row" else ("y", "h")
            original = [
                max(0, r["ref"][axis] - l["ref"][axis] - l["ref"][size])
                for l, r in zip(children, children[1:])
            ]
            raw = cfg.get("gaps", cfg.get("gap", original))
            gap_values = (
                [number(v, "gap", 0) for v in raw]
                if isinstance(raw, (list, tuple))
                else [number(raw, "gap", 0)] * max(0, len(children) - 1)
            )
            if len(gap_values) != max(0, len(children) - 1):
                raise ValueError("one custom gap is required per adjacent pair")
            cursor = pl if kind == "row" else pt
            cross = max(c["size"]["h" if kind == "row" else "w"] for c in children)
            align = cfg.get("align", "center")
            if cfg.get("preserveSourceAlignment") and "align" not in cfg:
                cross_axis, cross_size = ("y", "h") if kind == "row" else ("x", "w")
                choices = []
                for mode, f in [("center", 0.5), ("start", 0.0), ("end", 1.0)]:
                    values = [
                        c["visualRef"][cross_axis] + f * c["visualRef"][cross_size]
                        for c in children
                    ]
                    choices.append((max(values) - min(values), mode))
                spread, mode = min(choices, key=lambda q: q[0])
                align = mode if spread <= 0.01 else "source"
            if (
                align not in {"start", "center", "end", "source"}
                or align == "source"
                and not cfg.get("preserveSourceAlignment")
            ):
                raise ValueError("align must be start, center or end")
            factor = {"start": 0, "center": 0.5, "end": 1, "source": 0}[align]
            optical = (
                cfg.get("preserveSourceAlignment") and "align" not in cfg and align != "source"
            )
            if optical:
                line = max(
                    c["visualLocal"][cross_axis] + factor * c["visualLocal"][cross_size]
                    for c in children
                )
            for i, c in enumerate(children):
                other = (cross - c["size"]["h" if kind == "row" else "w"]) * factor
                if optical:
                    other = (
                        line - c["visualLocal"][cross_axis] - factor * c["visualLocal"][cross_size]
                    )
                if align == "source":
                    other = c["ref"][cross_axis] - min(q["ref"][cross_axis] for q in children)
                positions.append((cursor, pt + other) if kind == "row" else (pl + other, cursor))
                cursor += c["size"][size] + (gap_values[i] if i < len(gap_values) else 0)
        elif kind == "grid":
            cols = int(number(cfg.get("columns", 2), "columns", 1))
            gap = number(cfg.get("gap", 0), "gap", 0)
            cw = max(c["size"]["w"] for c in children)
            ch = max(c["size"]["h"] for c in children)
            positions = [
                (pl + i % cols * (cw + gap), pt + i // cols * (ch + gap))
                for i in range(len(children))
            ]
        else:
            for c in children:
                if c["id"] in groups:
                    a = groups[c["id"]]["anchor"]
                    u, v = point(a.get("parent_point", "center"))
                    us, vs = point(a.get("self_point", a.get("parent_point", "center")))
                    dx, dy = a["offset"]
                    if a.get("offset_unit") == "normalized":
                        dx *= b["w"]
                        dy *= b["h"]
                    positions.append(
                        (
                            pl + u * b["w"] - us * c["size"]["w"] + dx,
                            pt + v * b["h"] - vs * c["size"]["h"] + dy,
                        )
                    )
                else:
                    positions.append((pl + c["ref"]["x"] - b["x"], pt + c["ref"]["y"] - b["y"]))
        leaves = {}
        frames = {}
        for c, (x, y) in zip(children, positions):
            frames[c["id"]] = {"x": x, "y": y, **c["size"]}
            for cid, r in c["leaves"].items():
                leaves[cid] = {**r, "x": x + r["x"], "y": y + r["y"]}
        width = max(x + c["size"]["w"] for c, (x, y) in zip(children, positions)) + pr
        height = max(y + c["size"]["h"] for c, (x, y) in zip(children, positions)) + pb
        fixed_descendant = any(
            groups[c["id"]].get("size_policy") == "fixed" or output[c["id"]].get("fixed_descendant")
            for c in children
            if c["id"] in groups
        )
        result = {
            "size": {"w": width, "h": height},
            "leaves": leaves,
            "frames": frames,
            "gaps": gap_values,
            "fixed_descendant": fixed_descendant,
        }
        output[gid] = result
        return result

    for gid in ir["roots"]:
        make(gid)
    return output
