"""Compile model plans to the existing reference-first Kiwi solver.

This function is also the replacement point for a user-supplied layout solver.
The workflow independently validates geometry, propagation and native writeback.
"""

import math
import kiwisolver as kiwi
from multires_app.preview.target_reflow import solve_reflow


def options(project, target):
    device = next(d for d in project["devices"] if d["profile"]["id"] == target)
    return {
        **project["sourceProfile"].get("adaptation", {}).get("anchorLayout", {}),
        **device["profile"].get("adaptation", {}).get("anchorLayout", {}),
    }


def solve(project, catalog, request, matches, advice, relations, attempt=0):
    """Original global geometry is weak; explicit rules and local identity stay hard."""
    opts = options(project, request["target"])
    if opts.get("groups"):
        raise ValueError("当前求解器不能安全重构显式嵌套容器；请使用自定义求解接口，原布局保留")
    components = {c["id"]: c for c in project["components"]}
    defs = {c["id"]: c for c in request["components"]}
    groups = {g["id"]: g for g in catalog["groups"]}
    owners = {cid: g["id"] for g in catalog["groups"] for cid in g["members"]}
    strengths = {r["id"]: r["strength"] for r in advice.get("relations", [])}
    device = next(d for d in project["devices"] if d["profile"]["id"] == request["target"])
    layout = next(l for l in device["layouts"] if l["state"] == request["state"])
    source = next(l for l in project["sourceDevice"]["layouts"] if l["state"] == request["state"])
    region = request["constraints"]["safeRegion"]
    protected = []
    from multires_app.layout.canvas_bounds import stable_resources

    resources = stable_resources(project, layout, source) if "model" in project else []
    cuts = []
    from multires_app.layout.visual_geometry import visual_rect
    from multires_app.pipeline.scene_clusters import transformed_box, overlap

    def leaves(snapshot):
        values = {}
        for row in snapshot.get("rows", []):
            uid = project["nodeOrder"][row[0]]
            cid = project["nodeComponents"].get(uid)
            if (
                row[5] <= 0
                or cid not in owners
                or project["model"]["nodes"][uid]["tag"] in {"Group", "Button"}
            ):
                continue
            if components[cid].get("role") in {
                "background",
                "gesture-layer",
                "interaction-layer",
                "decoration",
                "popup",
                "overlay",
                "dialog",
            }:
                continue
            b = visual_rect(project, row)
            if b and min(b["w"], b["h"]) > 0:
                values.setdefault(uid, []).append((cid, b))
        return values

    src = leaves(source) if "model" in project else {}
    current = leaves(layout) if src else {}
    leaf_pairs = []
    entries = []
    source_screen = {
        "x": 0,
        "y": 0,
        "w": project["sourceDevice"]["viewport"]["width"],
        "h": project["sourceDevice"]["viewport"]["height"],
    }
    for uid, items in src.items():
        for (cid, b), (_, actual) in zip(items, current.get(uid, [])):
            if overlap(b, source_screen) > 0:
                entries.append((cid, b, actual))
    for index, a in enumerate(entries):
        for b in entries[:index]:
            if a[0] != b[0] and owners[a[0]] != owners[b[0]] and overlap(a[1], b[1]) <= 0.01:
                leaf_pairs.append((a, b))

    def hook(solver, expressions, variables):
        def emit(expression, priority="required"):
            if priority not in {"required", "strong", "medium", "weak"}:
                raise ValueError("显式约束优先级无效")
            solver.addConstraint(
                expression
                if priority == "required"
                else expression | getattr(kiwi.strength, priority)
            )

        def edge(box, name):
            if name in {"left", "x"}:
                return box["x"]
            if name in {"top", "y"}:
                return box["y"]
            if name == "right":
                return box["x"] + box["w"]
            if name == "bottom":
                return box["y"] + box["h"]
            if name in {"center_x", "cx"}:
                return box["x"] + box["w"] / 2
            if name in {"center_y", "cy"}:
                return box["y"] + box["h"] / 2
            if name in {"w", "h"}:
                return box[name]
            raise ValueError("显式约束边无效")

        def frame(cid, b):
            s = variables[owners[cid]][2]
            base = defs[cid]["sourceBBox"]
            e = expressions[cid]
            return {
                "x": e["x"] + (b["x"] - base["x"]) * s,
                "y": e["y"] + (b["y"] - base["y"]) * s,
                "w": b["w"] * s,
                "h": b["h"] * s,
            }

        for resource in resources:
            cid = resource["component"]
            if cid not in expressions:
                continue
            b = frame(cid, resource["rect"])
            epsilon = 0.005
            emit(b["x"] >= region["x"] - epsilon)
            emit(b["y"] >= region["y"] - epsilon)
            emit(b["x"] + b["w"] <= region["x"] + region["w"] + epsilon)
            emit(b["y"] + b["h"] <= region["y"] + region["h"] + epsilon)
        for first, fb, last, lb, axis in cuts:
            a = frame(first, fb)
            b = frame(last, lb)
            size = "w" if axis == "x" else "h"
            emit(a[axis] + a[size] <= b[axis])
        for rule in opts.get("constraints", []):
            ids = rule.get("targets", [])
            if not ids or any(cid not in components for cid in ids):
                raise ValueError("显式关系引用未知组件")
            if any(cid not in expressions for cid in ids):
                continue  # genuinely absent in this scene
            priority = rule.get("priority", "required")
            kind = rule.get("type", rule.get("kind"))
            a = expressions[ids[0]]
            b = expressions[ids[1]] if len(ids) == 2 else None
            if len(ids) > 2:
                raise ValueError("显式关系目标数量无效")
            if kind == "align" and b is not None:
                emit(
                    edge(a, rule.get("edge", "center_x"))
                    == edge(b, rule.get("other_edge", rule.get("edge", "center_x")))
                    + float(rule.get("offset", 0)),
                    priority,
                )
            elif kind in {"gap", "non_overlap", "preserve_order"} and b is not None:
                direction = rule.get("direction", "right")
                gap = float(rule.get("gap", 0))
                delta = {
                    "right": b["x"] - a["x"] - a["w"],
                    "left": a["x"] - b["x"] - b["w"],
                    "below": b["y"] - a["y"] - a["h"],
                    "above": a["y"] - b["y"] - b["h"],
                }.get(direction)
                if delta is None:
                    raise ValueError("显式关系方向无效")
                emit(delta == gap if kind == "gap" else delta >= gap, priority)
            elif kind in {"position", "size", "min_size", "max_size"} and len(ids) == 1:
                for field in ("x", "y") if kind == "position" else ("w", "h"):
                    if field in rule:
                        value = float(rule[field])
                        emit(
                            (
                                a[field] >= value
                                if kind == "min_size"
                                else a[field] <= value if kind == "max_size" else a[field] == value
                            ),
                            priority,
                        )
            elif kind == "equal_size" and b is not None:
                for field in rule.get("axes", ["w", "h"]):
                    emit(edge(a, field) == edge(b, field), priority)
            elif kind == "containment":
                parent = b if b is not None else region
                emit(a["x"] >= parent["x"], priority)
                emit(a["y"] >= parent["y"], priority)
                emit(a["x"] + a["w"] <= parent["x"] + parent["w"], priority)
                emit(a["y"] + a["h"] <= parent["y"] + parent["h"], priority)
            else:
                raise ValueError("不能安全编译该显式约束：" + str(kind))
            protected.append(rule.get("id", kind))
        from multires_app.layout.corner_geometry import component_frame, at_source_corner

        sv = project["sourceDevice"]["viewport"]
        for cid, e in expressions.items():
            c = components[cid]
            g = groups[owners[cid]]
            s = variables[g["id"]][2]
            if c.get("layout", {}):
                if set(c["layout"]) - {"group", "minScale", "maxScale"}:
                    raise ValueError("组件具有显式锚点/布局设置，默认重构器保留原布局：" + cid)
                if "minScale" in c["layout"]:
                    emit(s >= float(c["layout"]["minScale"]))
                if "maxScale" in c["layout"]:
                    emit(s <= float(c["layout"]["maxScale"]))
            corner = c.get("cornerAttachment")
            if corner:
                frame = component_frame(project, source, cid)
                if not at_source_corner(frame, corner, sv, tolerance=1.0):
                    continue
                current = component_frame(project, layout, cid)
                basis = defs[cid]["sourceBBox"]
                margin = corner["sourceMargins"]
                x = e["x"] + (current["x"] - basis["x"]) * s
                y = e["y"] + (current["y"] - basis["y"]) * s
                mx = margin["x"] * s
                my = margin["y"] * s
                emit(
                    x == region["x"] + mx
                    if corner["corner"] == "bottom-left"
                    else x + current["w"] * s == region["x"] + region["w"] - mx
                )
                emit(y + current["h"] * s == region["y"] + region["h"] - my)
                protected.append("corner:" + cid)
        for relation in relations:
            a = relation["first"]
            b = relation["last"]
            if a not in expressions or b not in expressions or owners[a] == owners[b]:
                continue
            axis = relation["axis"]
            size = "w" if axis == "x" else "h"
            ea = expressions[a]
            eb = expressions[b]
            # A source order is a preference, not a reason to crush a new composition.
            emit(eb[axis] - ea[axis] - ea[size] >= 0, strengths.get(relation["id"], "weak"))

    for iteration in range(3):
        rows, report = solve_reflow(catalog, request, matches, attempt, constraint_hook=hook)
        transforms = dict(layout.get("transforms", {}))
        for row in rows:
            cid = row["id"]
            base = defs[cid]["sourceBBox"]
            box = row["bbox"]
            s = box["w"] / base["w"]
            transforms[cid] = {
                "sx": s,
                "sy": s,
                "tx": box["x"] - base["x"] * s,
                "ty": box["y"] - base["y"] * s,
            }
        added = False
        for a, b in leaf_pairs:
            qa = transformed_box(a[2], transforms.get(a[0], {"sx": 1, "sy": 1, "tx": 0, "ty": 0}))
            qb = transformed_box(b[2], transforms.get(b[0], {"sx": 1, "sy": 1, "tx": 0, "ty": 0}))
            if overlap(qa, qb) <= 0.05 * min(qa["w"] * qa["h"], qb["w"] * qb["h"]):
                continue
            choices = []
            for axis, size in [("x", "w"), ("y", "h")]:
                choices.extend(
                    [
                        (qb[axis] - qa[axis] - qa[size], a, b, axis),
                        (qa[axis] - qb[axis] - qb[size], b, a, axis),
                    ]
                )
            choices.sort(key=lambda v: v[0], reverse=True)
            _, first, last, axis = choices[attempt % 2]
            cut = (first[0], first[2], last[0], last[2], axis)
            if cut not in cuts:
                cuts.append(cut)
                added = True
        if not added:
            break
    else:
        raise ValueError("资源级分离迭代达到上限，候选保留为拒绝案例")
    if any(not math.isfinite(v) for row in rows for v in row["bbox"].values()):
        raise ValueError("求解器返回非有限几何")
    report.update(
        protectedRelations=sorted(set(protected)),
        resourceSeparationCuts=len(cuts),
        relationPolicy="explicit/local/corner required; source cross-group order weak",
    )
    return rows, report
