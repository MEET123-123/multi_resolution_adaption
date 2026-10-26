"""Kiwi linear model with explicit collision directions and auditable relaxation."""

from __future__ import annotations

import math
import kiwisolver as kiwi
from multires_app.layout.anchor_ir import number, point
from multires_app.pipeline.scene_clusters import overlap


class RequiredConstraintError(ValueError):
    """Hard solver failure; authored rules are never relaxed by scheduling."""

    def __init__(self, message, constraint_id=None, provenance=None):
        super().__init__(message)
        self.constraint_id = constraint_id
        self.provenance = provenance


def anchor_position(anchor, size, parent, source_viewport):
    u, v = point(anchor.get("parent_point", "center"))
    us, vs = point(anchor.get("self_point", anchor.get("parent_point", "center")))
    dx, dy = anchor["offset"]
    if anchor.get("offset_unit", "px") == "normalized":
        dx *= parent["w"]
        dy *= parent["h"]
    return {
        "x": parent["x"] + u * parent["w"] - us * size["w"] + dx,
        "y": parent["y"] + v * parent["h"] - vs * size["h"] + dy,
        **size,
    }


def solve(ir, local, region, desired, frozen=None, constraints=None, min_scale=0.05, max_scale=1.0):
    frozen = frozen or {}
    constraints = constraints or []
    solver = kiwi.Solver()
    records = []
    boxes = {}
    variables = {}
    groups = {g["id"]: g for g in ir["groups"]}

    def add(expression, label, priority="required", weight=1.0, provenance="generator"):
        if priority not in {"required", "strong", "medium", "weak"}:
            raise ValueError("unknown constraint priority: " + str(priority))
        weight = number(weight, "constraint weight", 0.001)
        if weight > 100:
            raise ValueError("constraint weight must be <= 100")
        constraint = (
            expression
            if priority == "required"
            else expression | (getattr(kiwi.strength, priority) * weight)
        )
        try:
            solver.addConstraint(constraint)
        except kiwi.UnsatisfiableConstraint as exc:
            raise RequiredConstraintError(
                "Required constraint conflict: " + label + " [" + str(provenance) + "]",
                label,
                provenance,
            ) from exc
        records.append(
            (
                constraint,
                {"id": label, "priority": priority, "weight": weight, "provenance": provenance},
            )
        )

    for gid in ir["roots"]:
        g = groups[gid]
        geo = local[gid]
        x, y, s = [kiwi.Variable(gid + "." + field) for field in ("x", "y", "scale")]
        variables[gid] = (x, y, s)
        width, height = geo["size"]["w"], geo["size"]["h"]
        boxes[gid] = {"x": x, "y": y, "w": width * s, "h": height * s}
        policy = g.get("size_policy", "uniform_scale")
        if policy not in {"uniform_scale", "fixed", "contain", "cover"}:
            raise ValueError("unsupported group size_policy: " + policy)
        add(s >= max(min_scale, g.get("min_scale", 0)), gid + ":min-scale")
        add(s <= max_scale, gid + ":max-scale")
        if "_required_scale" in g:
            add(s >= g["_required_scale"], gid + ":cover-fill")
        if policy == "fixed" or geo.get("fixed_descendant"):
            add(s == 1, gid + ":fixed-size")
        if "_fixed_resource_scale" in g:
            add(
                s == g["_fixed_resource_scale"],
                gid + ":glyph-resource-scale",
                provenance="one native bitmap glyph family per component and device",
            )
        target = desired[gid]
        # Compare all soft errors in logical pixels. A dimensionless scale
        # residual otherwise makes shrinking artwork much cheaper than moving
        # an anchor by a few pixels, even with identical strengths.
        add(
            width * s == target["w"], gid + ":preferred-width", "strong", g.get("_size_weight", 1.0)
        )
        add(
            height * s == target["h"],
            gid + ":preferred-height",
            "strong",
            g.get("_size_weight", 1.0),
        )
        us, vs = point(g.get("_solve_point", g.get("anchor", {}).get("self_point", "center")))
        add(
            x + us * width * s == target["x"] + us * target["w"],
            gid + ":anchor-x",
            g.get("anchor", {}).get("priority", "strong"),
        )
        add(
            y + vs * height * s == target["y"] + vs * target["h"],
            gid + ":anchor-y",
            g.get("anchor", {}).get("priority", "strong"),
        )
        if not g.get("allow_crop", False) and "_stable_bounds" not in g:
            add(x >= region["x"], gid + ":inside-left")
            add(y >= region["y"], gid + ":inside-top")
            add(x + width * s <= region["x"] + region["w"], gid + ":inside-right")
            add(y + height * s <= region["y"] + region["h"], gid + ":inside-bottom")
        stable = g.get("_stable_bounds", [])
        if stable:
            # A positive uniform affine contains all rectangles exactly iff it
            # contains their union envelope. Four constraints replace 4*N.
            left = min(b["x"] for b in stable)
            top = min(b["y"] for b in stable)
            stable = [
                {
                    "x": left,
                    "y": top,
                    "w": max(b["x"] + b["w"] for b in stable) - left,
                    "h": max(b["y"] + b["h"] for b in stable) - top,
                }
            ]
        for i, b in enumerate(stable):
            label = gid + ":stable-resource:" + str(i)
            epsilon = 0.005 if any(cid in frozen for cid in geo["leaves"]) else 0.0
            add(x + b["x"] * s >= region["x"] - epsilon, label + ":left")
            add(y + b["y"] * s >= region["y"] - epsilon, label + ":top")
            add(x + (b["x"] + b["w"]) * s <= region["x"] + region["w"] + epsilon, label + ":right")
            add(y + (b["y"] + b["h"]) * s <= region["y"] + region["h"] + epsilon, label + ":bottom")
        for i, witness in enumerate(g.get("_waiting_regions", [])):
            b = witness["rect"]
            for edge in witness["edges"]:
                axis = edge["axis"]
                size = "w" if axis == "x" else "h"
                position = x if axis == "x" else y
                margin = edge["gap"] * region[size] / edge["sourceExtent"]
                label = gid + ":waiting:" + str(i) + ":" + axis
                if edge["edge"] == "before":
                    add(position + (b[axis] + b[size]) * s <= region[axis] - margin, label)
                else:
                    add(position + b[axis] * s >= region[axis] + region[size] + margin, label)
        for cid, b in geo["leaves"].items():
            boxes[cid] = {
                "x": x + b["x"] * s,
                "y": y + b["y"] * s,
                "w": b["w"] * s,
                "h": b["h"] * s,
            }

        # Nested group targets are available to authored relational constraints.
        def nested(parent, ox=0.0, oy=0.0):
            for mid, b in local[parent]["frames"].items():
                if mid in groups:
                    boxes[mid] = {
                        "x": x + (ox + b["x"]) * s,
                        "y": y + (oy + b["y"]) * s,
                        "w": b["w"] * s,
                        "h": b["h"] * s,
                    }
                    nested(mid, ox + b["x"], oy + b["y"])

        nested(gid)
    rail_roots = {gid for rail in ir.get("screenDistributions", []) for gid in rail["roots"]}
    for gid in ir["roots"]:
        g = groups[gid]
        b = boxes[gid]
        s = variables[gid][2]
        # Geometry evidence outranks the generic packing preference. Required
        # authored rules, containment and frozen public transforms still win.
        for axis, intent in g.get("screenAttachment", {}).items():
            if gid in rail_roots:
                continue
            size = "w" if axis == "x" else "h"
            from multires_app.layout.screen_relations import attachment_margin

            margin = attachment_margin(intent, s)
            expr = (
                b[axis] - region[axis] - margin
                if intent["edge"] in {"left", "top"}
                else b[axis] + b[size] - region[axis] - region[size] + margin
            )
            if axis == "x" and g.get("_horizontal_bounds"):
                frame = g["_horizontal_bounds"]
                start = b["x"] + frame["x"] * s
                end = start + frame["w"] * s
                expr = (
                    start - region["x"] - margin
                    if intent["edge"] == "left"
                    else end - region["x"] - region["w"] + margin
                )
            if g.get("_corner_bounds"):
                frame = g["_corner_bounds"]
                start = b[axis] + frame[axis] * s
                end = start + frame[size] * s
                expr = (
                    start - region[axis] - margin
                    if intent["edge"] in {"left", "top"}
                    else end - region[axis] - region[size] + margin
                )
            if intent.get("priority") == "required" and not any(
                cid in frozen for cid in local[gid]["leaves"]
            ):
                if g.get("cornerAttachment"):
                    add(
                        expr >= -0.005,
                        gid + ":corner-" + intent["edge"] + ":lower",
                        provenance="independent corner shortcut attachment",
                    )
                    add(
                        expr <= 0.005,
                        gid + ":corner-" + intent["edge"] + ":upper",
                        provenance="independent corner shortcut attachment",
                    )
                else:
                    add(
                        expr == 0,
                        gid + ":edge-" + intent["edge"],
                        provenance="preserve authored horizontal screen attachment",
                    )
            add(
                expr == 0,
                gid + ":screen-" + intent["edge"],
                "strong",
                100,
                "source screen edge attachment",
            )
        for axis in g.get("centerAxes", []):
            if gid in rail_roots and axis not in g.get("centerRequiredAxes", []):
                continue
            size = "w" if axis == "x" else "h"
            visual = g.get("_center_bounds", {"x": 0, "y": 0, **local[gid]["size"]})
            add(
                b[axis] + (visual[axis] + visual[size] / 2) * s == region[axis] + region[size] / 2,
                gid + ":screen-center-" + axis,
                "required" if axis in g.get("centerRequiredAxes", []) else "strong",
                100,
                "source visible content centered, excluding transparent padding",
            )
    leaf_roots = {cid: gid for gid in ir["roots"] for cid in local[gid]["leaves"]}
    elements = {e["id"]: e for e in ir["elements"]}
    # Lock public geometry before introducing the spatial graph. Late locks
    # force large, nearly redundant tableaux through unstable pivot chains.
    for cid, b in frozen.items():
        if cid not in boxes:
            boxes[cid] = {k: kiwi.Variable(cid + ".fixed." + k) for k in ("x", "y", "w", "h")}
        for field in ("x", "y", "w", "h"):
            add(
                boxes[cid][field] == b[field],
                cid + ":public-lock-" + field,
                provenance="frozen common component",
            )

    def add_spatial(
        expression, label, endpoints, priority="required", weight=1.0, provenance="source geometry"
    ):
        try:
            add(expression, label, priority, weight, provenance)
        except RequiredConstraintError as exc:
            peers = [cid for cid in endpoints if cid in frozen]
            if not peers and frozen:
                peers = sorted(frozen)
            if peers and priority == "required":
                raise RequiredConstraintError(
                    str(exc), peers[0] + ":public-lock-spatial", provenance
                ) from exc
            raise

    def visual(cid, rect):
        root = leaf_roots[cid]
        s = variables[root][2]
        origin = elements[cid]["current_bbox"]
        q = boxes[cid]
        return {
            "x": q["x"] + (rect["x"] - origin["x"]) * s,
            "y": q["y"] + (rect["y"] - origin["y"]) * s,
            "w": rect["w"] * s,
            "h": rect["h"] * s,
        }

    alignment_parents = {}

    def line_key(cid, rect, axis, factor):
        root = leaf_roots[cid]
        size = "w" if axis == "x" else "h"
        offset = (
            local[root]["leaves"][cid][axis]
            + rect[axis]
            - elements[cid]["current_bbox"][axis]
            + factor * rect[size]
        )
        return root, axis, round(offset, 6)

    def line_root(key):
        alignment_parents.setdefault(key, key)
        while alignment_parents[key] != key:
            alignment_parents[key] = alignment_parents[alignment_parents[key]]
            key = alignment_parents[key]
        return key

    for relation in ir.get("spatialAlignments", []):
        a, b = relation["first"], relation["last"]
        if a not in leaf_roots or b not in leaf_roots:
            continue
        aa = visual(a, relation["firstRect"])
        bb = visual(b, relation["lastRect"])
        axis = relation["axis"]
        size = "w" if axis == "x" else "h"
        f = relation["factor"]
        delta = aa[axis] + f * aa[size] - bb[axis] - f * bb[size]
        # Atomic interiors cannot be independently moved. Keep their existing
        # affine; an incompatible inner relation is recorded for diagnosis.
        if leaf_roots[a] == leaf_roots[b]:
            relation["scope"] = "container"
            root = leaf_roots[a]
            points = []
            for cid, key in [(a, "firstRect"), (b, "lastRect")]:
                leaf = local[root]["leaves"][cid]
                rect = relation[key]
                origin = elements[cid]["current_bbox"]
                points.append(leaf[axis] + rect[axis] - origin[axis] + f * rect[size])
            if abs(points[0] - points[1]) > 0.01:
                relation["unresolvedLocalOffset"] = abs(points[0] - points[1])
        else:
            relation["scope"] = "shared-line"
            # A near-centered inferred container can have a child line a few
            # pixels from its optical center. Independently centering another
            # group on that child line would require zero/negative scale. Keep
            # the screen-center contracts and retain this inferred line as a
            # strong preference. Authored relations and public locks stay hard.
            ar, br = leaf_roots[a], leaf_roots[b]
            centered = all(axis in groups[root].get("centerRequiredAxes", []) for root in (ar, br))
            if centered and not any(
                cid in frozen for root in (ar, br) for cid in local[root]["leaves"]
            ):
                offsets = []
                for cid, root, rect in (
                    (a, ar, relation["firstRect"]),
                    (b, br, relation["lastRect"]),
                ):
                    frame = groups[root].get(
                        "_center_bounds", {"x": 0, "y": 0, **local[root]["size"]}
                    )
                    line = (
                        local[root]["leaves"][cid][axis]
                        + rect[axis]
                        - elements[cid]["current_bbox"][axis]
                        + f * rect[size]
                    )
                    offsets.append(line - frame[axis] - frame[size] / 2)
                oa, ob = offsets
                incompatible = (abs(oa) <= 0.01) != (abs(ob) <= 0.01) or oa * ob < 0
                if all("_fixed_resource_scale" in groups[root] for root in (ar, br)):
                    incompatible |= (
                        abs(
                            oa * groups[ar]["_fixed_resource_scale"]
                            - ob * groups[br]["_fixed_resource_scale"]
                        )
                        > 0.005
                    )
                if incompatible:
                    relation.update(
                        priority="strong",
                        relaxation="inferred child alignment conflicts with independent optical centering",
                        centerOffsets=offsets,
                    )
                    add(
                        delta == 0,
                        relation["id"] + ":center-preference",
                        "strong",
                        100,
                        relation["provenance"],
                    )
                    continue
            first = line_root(line_key(a, relation["firstRect"], axis, f))
            last = line_root(line_key(b, relation["lastRect"], axis, f))
            if first == last:
                relation["solverRedundant"] = True
                continue
            alignment_parents[last] = first
            # Dense reused systems accumulate floating point cancellation.
            # Bound the line to 0.005 logical px, with an exact-zero preference.
            add_spatial(
                delta >= -0.005,
                relation["id"] + ":lower",
                (a, b),
                provenance=relation["provenance"],
            )
            add_spatial(
                delta <= 0.005, relation["id"] + ":upper", (a, b), provenance=relation["provenance"]
            )
            add(delta == 0, relation["id"] + ":preferred", "strong", 100, relation["provenance"])
    for relation in ir.get("spatialFlows", []):
        a, b = relation["first"], relation["last"]
        if a not in leaf_roots or b not in leaf_roots:
            continue
        aa = visual(a, relation["firstRect"])
        bb = visual(b, relation["lastRect"])
        axis = relation["axis"]
        size = "w" if axis == "x" else "h"
        distance = bb[axis] - aa[axis] - aa[size]
        epsilon = 0.005 if a in frozen and b in frozen else 0.0
        add_spatial(
            distance >= relation["minimumGap"] - epsilon,
            relation["id"] + ":minimum",
            (a, b),
            provenance=relation["provenance"],
        )
        if not relation.get("separationOnly"):
            add(
                distance == relation["preferredGap"],
                relation["id"] + ":preferred",
                "strong",
                relation.get("preferenceWeight", 20),
                relation["provenance"],
            )
    for ri, rail in enumerate(ir.get("screenDistributions", [])):
        ids = rail["roots"]
        n = len(ids)
        s = variables[ids[0]][2]

        def horizontal(gid):
            b = boxes[gid]
            frame = groups[gid].get("_center_bounds")
            if rail.get("horizontalGeometry") == "optical" and frame:
                scale = variables[gid][2]
                return {**b, "x": b["x"] + frame["x"] * scale, "w": frame["w"] * scale}
            return b

        first = horizontal(ids[0])
        last = horizontal(ids[-1])

        def rail_add(expr, label):
            add(expr, "rail:" + str(ri) + ":" + label, "strong", 100, rail["provenance"])

        for i, gid in enumerate(ids):
            b = horizontal(gid)
            vertical = boxes[gid]
            rail_add(variables[gid][2] == s, "scale:" + gid)
            rail_add(
                vertical["y"] + vertical["h"] / 2
                == region["y"]
                + region["h"]
                - rail["targetBottomMargin"]
                - rail["referenceHalfHeight"] * s,
                "bottom:" + gid,
            )
            if rail["mode"] == "equal-cells":
                rail_add(
                    b["x"] + b["w"] / 2 == region["x"] + region["w"] * (i + 0.5) / n, "cell:" + gid
                )
            elif rail["mode"] == "equal-centers":
                rail_add(
                    b["x"] + b["w"] / 2
                    == (first["x"] + first["w"] / 2) * (1 - i / (n - 1))
                    + (last["x"] + last["w"] / 2) * i / (n - 1),
                    "center:" + gid,
                )
            elif i >= 2:
                prev = horizontal(ids[i - 1])
                a = horizontal(ids[0])
                second = horizontal(ids[1])
                rail_add(
                    b["x"] - prev["x"] - prev["w"] == second["x"] - a["x"] - a["w"], "gap:" + gid
                )
        if rail["mode"] != "equal-cells":
            rail_add(first["x"] == region["x"] + rail["targetLeftMargin"], "left")
            rail_add(
                last["x"] + last["w"] == region["x"] + region["w"] - rail["targetRightMargin"],
                "right",
            )

    def edge(box, name):
        if name in {"x", "left"}:
            return box["x"]
        if name in {"y", "top"}:
            return box["y"]
        if name == "right":
            return box["x"] + box["w"]
        if name == "bottom":
            return box["y"] + box["h"]
        if name in {"center_x", "cx"}:
            return box["x"] + box["w"] * 0.5
        if name in {"center_y", "cy"}:
            return box["y"] + box["h"] * 0.5
        if name in {"w", "h"}:
            return box[name]
        raise ValueError("unknown relation edge: " + name)

    all_constraints = ir.get("constraints", []) + constraints
    for i, c in enumerate(all_constraints):
        kind = c.get("type", c.get("kind"))
        targets = c.get("targets", [])
        priority = c.get("priority", "required")
        if (
            not isinstance(targets, list)
            or not targets
            or any(not isinstance(t, str) for t in targets)
        ):
            raise ValueError("constraint targets must be a non-empty ID list")
        if priority not in {"required", "strong", "medium", "weak"}:
            raise ValueError("unknown constraint priority: " + str(priority))
        if kind not in {
            "align",
            "gap",
            "non_overlap",
            "preserve_order",
            "position",
            "size",
            "min_size",
            "max_size",
            "equal_size",
            "containment",
        }:
            raise ValueError("unknown constraint type: " + str(kind))
        expected = (
            2 if kind in {"align", "gap", "non_overlap", "preserve_order", "equal_size"} else 1
        )
        if len(targets) != expected and not (kind == "containment" and len(targets) == 2):
            raise ValueError("invalid target count for " + kind)
        if any(t not in boxes for t in targets):
            # Invisible members are inactive, but misspelled identifiers fail.
            known = {e["id"] for e in ir["elements"]} | set(groups) | set(ir.get("known_ids", []))
            if set(targets) - known:
                raise ValueError("unknown constraint target: " + str(targets))
            continue
        a = boxes[targets[0]]
        b = boxes[targets[1]] if len(targets) > 1 else None
        label = c.get("id", "relation:" + str(i))
        weight = c.get("weight", 1)
        prov = c.get("provenance", "manual relation")

        def emit(expr, suffix=""):
            add(expr, label + suffix, priority, weight, prov)

        if kind == "align":
            if b is None:
                raise ValueError("align needs two targets")
            emit(
                edge(a, c.get("edge", "center_x"))
                == edge(b, c.get("other_edge", c.get("edge", "center_x")))
                + number(c.get("offset", 0), "alignment offset")
            )
        elif kind in {"gap", "non_overlap", "preserve_order"}:
            if b is None:
                raise ValueError(kind + " needs two targets")
            direction = c.get("direction", "right")
            gap = number(c.get("gap", 0), "gap", 0)
            if direction == "right":
                expr = b["x"] - a["x"] - a["w"]
            elif direction == "left":
                expr = a["x"] - b["x"] - b["w"]
            elif direction == "below":
                expr = b["y"] - a["y"] - a["h"]
            elif direction == "above":
                expr = a["y"] - b["y"] - b["h"]
            else:
                raise ValueError("collision direction must be right/left/below/above")
            emit(expr == gap if kind == "gap" else expr >= gap)
        elif kind in {"position", "size", "min_size", "max_size"}:
            fields = ("x", "y") if kind == "position" else ("w", "h")
            for field in fields:
                if field in c:
                    val = number(c[field], kind + "." + field, 0 if field in {"w", "h"} else None)
                    emit(
                        (
                            a[field] >= val
                            if kind == "min_size"
                            else a[field] <= val if kind == "max_size" else a[field] == val
                        ),
                        ":" + field,
                    )
        elif kind == "equal_size":
            if b is None:
                raise ValueError("equal_size needs two targets")
            for field in c.get("axes", ["w", "h"]):
                emit(edge(a, field) == edge(b, field), ":" + field)
        elif kind == "containment":
            parent = b or region
            emit(a["x"] >= parent["x"], ":left")
            emit(a["y"] >= parent["y"], ":top")
            emit(a["x"] + a["w"] <= parent["x"] + parent["w"], ":right")
            emit(a["y"] + a["h"] <= parent["y"] + parent["h"], ":bottom")
        else:
            raise ValueError("unknown constraint type: " + str(kind))
    solver.updateVariables()

    def value(expr):
        return float(expr.value()) if hasattr(expr, "value") else float(expr)

    result = {cid: {k: value(v) for k, v in b.items()} for cid, b in boxes.items()}
    invalid = [
        (cid, b)
        for cid, b in result.items()
        if not all(math.isfinite(v) for v in b.values()) or min(b["w"], b["h"]) <= 0
    ]
    if invalid:
        raise RequiredConstraintError("solver produced an invalid rectangle: " + str(invalid[:2]))

    def residual(c):
        v = c.expression().value()
        return abs(v) if c.op() == "==" else max(0, v) if c.op() == "<=" else max(0, -v)

    violations = [
        {**meta, "expression": str(c), "residual": residual(c)}
        for c, meta in records
        if residual(c) > 1e-6
    ]
    required = [v for v in violations if v["priority"] == "required"]
    if required:
        raise RequiredConstraintError(
            "required residual after solving: "
            + str([(v["id"], v["residual"]) for v in required[:4]]),
            required[0]["id"],
            required[0]["provenance"],
        )
    return result, {
        "backend": "kiwisolver",
        "version": kiwi.__version__,
        "required_ok": True,
        "constraint_count": len(records),
        "soft_violations": violations,
        "relaxation_order": ["weak", "medium", "strong"],
        "collision_policy": "explicit direction or source-disjoint root packing; intentional overlay preserved",
    }


def collision_directions(groups, desired, gap=0):
    """Choose a discrete direction outside Kiwi; only source-disjoint root pairs."""
    result = []
    for i, a in enumerate(groups):
        for b in groups[:i]:
            if (
                a.get("allow_crop")
                or b.get("allow_crop")
                or a.get("intentional_overlay")
                or b.get("intentional_overlay")
            ):
                continue
            if overlap(a["bbox"], b["bbox"]) > 1e-6:
                continue
            aa, bb = desired[a["id"]], desired[b["id"]]
            dx = (bb["x"] + bb["w"] / 2) - (aa["x"] + aa["w"] / 2)
            dy = (bb["y"] + bb["h"] / 2) - (aa["y"] + aa["h"] / 2)
            horizontal = abs(dx) / max(1, (aa["w"] + bb["w"]) / 2) >= abs(dy) / max(
                1, (aa["h"] + bb["h"]) / 2
            )
            direction = (
                ("right" if dx >= 0 else "left")
                if horizontal
                else ("below" if dy >= 0 else "above")
            )
            result.append(
                {
                    "id": "separation:" + str(len(result)),
                    "type": "non_overlap",
                    "targets": [a["id"], b["id"]],
                    "direction": direction,
                    "gap": gap,
                    "priority": "required",
                    "provenance": "source-disjoint groups, selected container direction",
                }
            )
    return result


def solve_directions(
    ir, local, region, desired, frozen, separations, min_scale, max_scale, budget=48
):
    """Bounded discrete search outside Cassowary. No required constraint is dropped."""
    queue = [copy_constraints(separations)]
    seen = set()
    attempts = 0
    last = None
    while queue and attempts < budget:
        candidate = queue.pop()
        key = tuple(c["direction"] for c in candidate)
        if key in seen:
            continue
        seen.add(key)
        attempts += 1
        try:
            boxes, report = solve(
                ir, local, region, desired, frozen, candidate, min_scale, max_scale
            )
            report["directionSearch"] = {
                "attempts": attempts,
                "budget": budget,
                "chosen": candidate,
            }
            return boxes, report
        except RequiredConstraintError as exc:
            last = exc
            index = next((i for i, c in enumerate(candidate) if c["id"] == exc.constraint_id), None)
            if index is None:
                raise
            for direction in ["above", "below", "left", "right"]:
                if direction == candidate[index]["direction"]:
                    continue
                alternative = copy_constraints(candidate)
                alternative[index]["direction"] = direction
                queue.append(alternative)
    raise RequiredConstraintError(
        "No collision direction satisfies required constraints within "
        + str(attempts)
        + " attempts: "
        + str(last)
    )


def copy_constraints(items):
    return [dict(c) for c in items]
