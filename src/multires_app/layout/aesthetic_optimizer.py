"""Bounded coordinate search over coherent groups, guarded by original intent."""

import copy
import hashlib
import json
import math
import kiwisolver as kiwi
from collections import Counter
from pathlib import Path
from urllib.parse import unquote
from multires_app.layout.canvas_bounds import stable_resources, violations, intersects
from multires_app.pipeline.scene_clusters import box_union, describe_layout, transformed_box
from multires_app.layout.layout_aesthetics import (
    evaluate,
    normalized,
    reference_relations,
    DEFAULT_WEIGHTS,
    mean,
    area,
    intersection,
)


class Evaluator:
    def __init__(self, project, device, weights=None):
        self.project = project
        self.device = device
        self.weights = weights
        self.vp = device["viewport"]
        self.svp = project["sourceDevice"]["viewport"]
        self.roles = {c["id"]: c["role"] for c in project["components"]}
        self.sources = {l["state"]: l for l in project["sourceDevice"]["layouts"]}
        self.samples = []
        self.by_state = {}
        self.resources = {}
        seen = {}
        self.background_cache = {}
        from multires_app.layout.visual_geometry import with_stable_visuals

        self.descriptions = {
            l["state"]: with_stable_visuals(project, l, describe_layout(project, l))
            for l in device["layouts"]
        }
        self.layouts = {l["state"]: l for l in device["layouts"]}
        for l in device["layouts"]:
            src = self.sources.get(l["state"], {})
            active = stable_resources(project, l, src)
            self.resources[l["state"]] = active
            sample = {
                "layout": l,
                "resources": active,
                "raw": self.items(l),
                "reference": self.items(src, source=True),
            }
            sample["raw"] = {
                cid: e for cid, e in sample["raw"].items() if cid in sample["reference"]
            }
            sample["reference"] = {
                cid: e for cid, e in sample["reference"].items() if cid in sample["raw"]
            }
            sample["relations"] = reference_relations(sample["reference"])
            sample["plan"] = self.plan(l)
            sample["region"] = sample["plan"].get(
                "safeRegion", {"x": 0, "y": 0, "w": self.vp["width"], "h": self.vp["height"]}
            )
            sizing = sample["plan"].get("visualSizing", {})
            sample["densityRatio"] = sizing.get("relativeAreaRatio", 1.0)
            sample["background"] = self.background(l)
            key = hashlib.sha256(
                json.dumps(
                    [
                        sample["raw"],
                        sample["reference"],
                        l["transforms"],
                        sample["background"][0] if sample["background"] else None,
                    ],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            if key in seen:
                sample = seen[key]
            else:
                seen[key] = sample
                self.samples.append(sample)
            self.by_state[l["state"]] = sample
        self.baseline = {id(s): self.score(s) for s in self.samples}
        self.anchor_baseline = {l["state"]: self.anchor_errors(l) for l in device["layouts"]}
        from multires_app.layout.spatial_flow import layout_flows

        self.flows = {
            l["state"]: (
                layout_flows(project, device, l)
                if l.get("adaptationPlan", {}).get("spatialFlowPolicy")
                else []
            )
            for l in device["layouts"]
        }
        from multires_app.layout.spatial_alignment import layout_alignments

        self.alignments = {
            l["state"]: layout_alignments(project, device, l, include_container=True)
            for l in device["layouts"]
        }
        for l in device["layouts"]:
            for f in self.flows[l["state"]]:
                if f["first"] not in l["transforms"] or f["last"] not in l["transforms"]:
                    continue
                a = transformed_box(f["firstRect"], l["transforms"][f["first"]])
                b = transformed_box(f["lastRect"], l["transforms"][f["last"]])
                axis = f["axis"]
                size = "w" if axis == "x" else "h"
                f["minimumGap"] = max(
                    f["minimumGap"], min(f["preferredGap"], b[axis] - a[axis] - a[size])
                )
        self.original_transforms = {
            l["state"]: copy.deepcopy(l["transforms"]) for l in device["layouts"]
        }
        self.hit_pairs = {}
        for l in device["layouts"]:
            buttons = [
                {**e, "originalBox": transformed_box(e["rect"], l["transforms"][e["component"]])}
                for e in self.resources[l["state"]]
                if project["model"]["nodes"][e["id"]]["tag"] == "Button"
                and e["component"] in l["transforms"]
            ]
            self.hit_pairs[l["state"]] = [
                (a, b)
                for i, a in enumerate(buttons)
                for b in buttons[:i]
                if intersection(a["originalBox"], b["originalBox"]) < 1e-6
            ]

    def plan(self, layout):
        seen = set()
        while (
            not layout.get("adaptationPlan", {}).get("groups")
            and layout.get("adaptationPlan", {}).get("reusedFrom")
            and layout["state"] not in seen
        ):
            seen.add(layout["state"])
            layout = self.layouts[layout["adaptationPlan"]["reusedFrom"]]
        return layout.get("adaptationPlan", {})

    def items(self, layout, source=False):
        groups = {}
        vp = self.svp if source else self.vp
        inactive = set(layout.get("mediaLayout", {}).get("inactiveSceneNodeIndices", []))
        density = (self.project["sourceProfile"] if source else self.device["profile"])[
            "resolutionPx"
        ]["width"] / vp["width"]
        for row in layout.get("rows", []):
            if row[0] in inactive:
                continue
            uid = self.project["nodeOrder"][row[0]]
            cid = self.project["nodeComponents"].get(uid)
            tag = self.project["model"]["nodes"][uid]["tag"]
            b = dict(zip(("x", "y", "w", "h"), row[1:5]))
            if (
                self.roles.get(cid)
                not in {"header", "footer", "content", "popup", "overlay", "dialog"}
                or tag in {"Group", "Button"}
                or row[5] <= 0
                or min(b["w"], b["h"]) <= 0
                or not all(math.isfinite(v) for v in b.values())
            ):
                continue
            entry = groups.setdefault(cid, {"boxes": [], "fonts": [], "alpha": []})
            entry["boxes"].append(b)
            entry["alpha"].append(row[5])
            if tag == "Text" and row[8] > 0:
                entry["fonts"].append(row[8] * density)
            elif tag in {"ImageSeries", "Time", "Date", "Number"}:
                entry["fonts"].append(b["h"] * density)
        result = {}
        for cid, g in groups.items():
            b = box_union(g["boxes"])
            if source and (
                b["x"] < 0
                or b["y"] < 0
                or b["x"] + b["w"] > vp["width"] + 0.01
                or b["y"] + b["h"] > vp["height"] + 0.01
            ):
                continue
            result[cid] = {
                "rect": b,
                "box": normalized(b, vp),
                "role": self.roles[cid],
                "alpha": mean(g["alpha"]),
                "fontPx": min(g["fonts"]) if g["fonts"] else 0.0,
            }
        return result

    def background(self, layout):
        folder = self.project.get("_projectFolder")
        if not folder:
            return None
        candidates = []
        for row in layout.get("rows", []):
            uid = self.project["nodeOrder"][row[0]]
            cid = self.project["nodeComponents"].get(uid)
            if self.roles.get(cid) != "background" or row[6] < 0 or row[5] <= 0:
                continue
            asset = self.project["assetOrder"][row[6]]
            meta = self.project["assets"].get(asset, {})
            if meta.get("kind") != "image":
                continue
            b = dict(zip(("x", "y", "w", "h"), row[1:5]))
            t = layout["transforms"].get(cid, {"sx": 1, "sy": 1, "tx": 0, "ty": 0})
            candidates.append((area(b), asset, meta, transformed_box(b, t)))
        if not candidates:
            return None
        _, asset, meta, b = max(candidates, key=lambda e: e[0])
        key = json.dumps([asset, b], sort_keys=True)
        if key in self.background_cache:
            return self.background_cache[key]
        root = Path(folder).resolve()
        path = (root / unquote(meta["url"].split("?")[0])).resolve()
        if not path.is_relative_to(root):
            return None
        try:
            import numpy as np
            from PIL import Image

            with Image.open(path) as image:
                image = image.convert("L")
                iw, ih = image.size
                data = image.transform(
                    (128, 128),
                    Image.Transform.AFFINE,
                    (
                        self.vp["width"] / 128 * iw / b["w"],
                        0,
                        -b["x"] * iw / b["w"],
                        0,
                        self.vp["height"] / 128 * ih / b["h"],
                        -b["y"] * ih / b["h"],
                    ),
                    Image.Resampling.BILINEAR,
                )
                p = np.pad(np.asarray(data, dtype=float) / 255.0, 1, mode="edge")
                gx = (
                    -p[:-2, :-2]
                    + p[:-2, 2:]
                    - 2 * p[1:-1, :-2]
                    + 2 * p[1:-1, 2:]
                    - p[2:, :-2]
                    + p[2:, 2:]
                ) / 8
                gy = (
                    -p[:-2, :-2]
                    - 2 * p[:-2, 1:-1]
                    - p[:-2, 2:]
                    + p[2:, :-2]
                    + 2 * p[2:, 1:-1]
                    + p[2:, 2:]
                ) / 8
                result = (key, np.minimum(1.0, np.sqrt((gx * gx + gy * gy) / 2) * 4))
        except (OSError, ValueError, ZeroDivisionError):
            result = None
        self.background_cache[key] = result
        return result

    def score(self, sample):
        items = {}
        mapping = sample["layout"]["transforms"]
        for cid, e in sample["raw"].items():
            t = mapping.get(cid, {"sx": 1, "sy": 1, "tx": 0, "ty": 0})
            b = transformed_box(e["rect"], t)
            item = {**e, "box": normalized(b, self.vp), "fontPx": e["fontPx"] * t["sy"]}
            if sample["background"] and e["fontPx"] > 0:
                q = item["box"]
                x1 = max(0, min(127, int(q["x"] * 128)))
                y1 = max(0, min(127, int(q["y"] * 128)))
                x2 = max(x1 + 1, min(128, math.ceil((q["x"] + q["w"]) * 128)))
                y2 = max(y1 + 1, min(128, math.ceil((q["y"] + q["h"]) * 128)))
                item["backgroundComplexity"] = float(sample["background"][1][y1:y2, x1:x2].mean())
            items[cid] = item
        return evaluate(
            items, sample["reference"], sample["relations"], sample["densityRatio"], self.weights
        )

    def anchor_errors(self, layout):
        from multires_app.layout.layout_quality import screen_relation_errors

        return screen_relation_errors(
            self.descriptions[layout["state"]], layout["transforms"], self.plan(layout)
        )

    def scores(self):
        return {id(s): self.score(s) for s in self.samples}

    def average(self, scores):
        return mean([r["score"] for r in scores.values() if r["score"] is not None])

    def guard(self, members, scores):
        for l in self.device["layouts"]:
            sample = self.by_state[l["state"]]
            affected = [e for e in self.resources[l["state"]] if e["component"] in members]
            # A duplicate representative has the same initial transforms and
            # receives the same group operation, so its envelope check is exact.
            if violations(affected, l["transforms"], sample["region"]):
                return "canvas"
            from multires_app.layout.spatial_flow import flow_errors

            if flow_errors(self.flows[l["state"]], l["transforms"]):
                return "source-axis-gap"
            from multires_app.layout.spatial_alignment import alignment_errors

            if alignment_errors(self.alignments[l["state"]], l["transforms"]):
                return "source-alignment"
            moved = {}
            for a, b in self.hit_pairs[l["state"]]:
                if not ({a["component"], b["component"]} & members):
                    continue

                def placed(e):
                    if e["component"] not in members:
                        return e["originalBox"]
                    if e["id"] not in moved:
                        moved[e["id"]] = transformed_box(e["rect"], l["transforms"][e["component"]])
                    return moved[e["id"]]

                if intersection(placed(a), placed(b)) > 1e-5:
                    return "hit-region-overlap"
            errors = self.anchor_errors(l)
            if any(
                a > max(1.0, b + 0.25) for a, b in zip(errors, self.anchor_baseline[l["state"]])
            ):
                return "anchor"
            for cid in members & set(l["transforms"]):
                t = l["transforms"][cid]
                old = self.original_transforms[l["state"]][cid]
                if not 0.94 - 1e-8 <= t["sx"] / old["sx"] <= 1.06 + 1e-8:
                    return "change-budget"
        for s in self.samples:
            r = scores[id(s)]
            old = self.baseline[id(s)]
            if r["score"] is None:
                continue
            if r["score"] < old["score"] - 0.25:
                return "state-score-floor"
            for key in ("overlap", "order", "readability"):
                if r["losses"][key] > old["losses"][key] + 1e-7:
                    return key
            for cid, e in s["raw"].items():
                if cid not in members:
                    continue
                q = normalized(transformed_box(e["rect"], s["layout"]["transforms"][cid]), self.vp)
                ref = s["reference"][cid]["box"]
                oldq = normalized(
                    transformed_box(e["rect"], self.original_transforms[s["layout"]["state"]][cid]),
                    self.vp,
                )
                ratio = area(q) / area(ref)
                oldratio = area(oldq) / area(ref)
                axes = max(q["w"] / ref["w"], q["h"] / ref["h"])
                oldaxes = max(oldq["w"] / ref["w"], oldq["h"] / ref["h"])
                if ratio > max(1.25, oldratio) + 1e-6 or axes > max(1.6, oldaxes) + 1e-6:
                    return "occupancy"
        return None


def coherent_groups(project, device):
    parent = {c["id"]: c["id"] for c in project["components"]}
    lock_axes = {}
    locked = set(device.get("manualComponentAnchors", {}))

    def find(cid):
        while parent[cid] != cid:
            parent[cid] = parent[parent[cid]]
            cid = parent[cid]
        return cid

    def union(ids):
        ids = [i for i in ids if i in parent]
        for i in ids[1:]:
            parent[find(i)] = find(ids[0])

    for l in device["layouts"]:
        plan = l.get("adaptationPlan", {})
        groups = {g["id"]: g for g in plan.get("groups", [])}

        def leaves(gid, seen=None):
            if gid in parent:
                return {gid}
            if gid not in groups or gid in (seen or set()):
                return set()
            return set().union(
                *(leaves(m, (seen or set()) | {gid}) for m in groups[gid]["members"])
            )

        for gid, g in groups.items():
            ids = leaves(gid)
            union(sorted(ids))
            axes = set(g.get("screenAttachment", {})) | set(g.get("centerAxes", []))
            for cid in ids:
                lock_axes.setdefault(cid, set()).update(axes)
        for rail in plan.get("screenDistributions", []):
            ids = set().union(*(leaves(gid) for gid in rail["roots"]))
            union(sorted(ids))
            for cid in ids:
                lock_axes.setdefault(cid, set()).update(("x", "y"))
    groups = {}
    for c in project["components"]:
        groups.setdefault(find(c["id"]), set()).add(c["id"])
    eligible = {
        c["id"]
        for c in project["components"]
        if c["role"] in {"header", "footer", "content", "popup", "overlay", "dialog"}
    }
    result = []
    for ids in groups.values():
        if ids & locked or not ids <= eligible:
            continue
        axes = set().union(*(lock_axes.get(cid, set()) for cid in ids))
        result.append((ids, axes))
    return sorted(result, key=lambda g: sorted(g[0]))


def project_candidate(evaluator, members, dx, dy, k, glyphs):
    """Project the proposed shared affine onto a Kiwi feasible region."""
    import kiwisolver as kiwi

    solver = kiwi.Solver()
    scale = kiwi.Variable("scale")
    shift_x = kiwi.Variable("dx")
    shift_y = kiwi.Variable("dy")
    vp = evaluator.vp
    cx = vp["width"] / 2
    cy = vp["height"] / 2
    solver.addConstraint((scale * max(vp.values()) == k * max(vp.values())) | kiwi.strength.strong)
    solver.addConstraint((shift_x == dx) | kiwi.strength.strong)
    solver.addConstraint((shift_y == dy) | kiwi.strength.strong)
    if members & glyphs:
        solver.addConstraint(scale == 1)
    seen = set()
    for l in evaluator.device["layouts"]:
        region = evaluator.by_state[l["state"]]["region"]
        envelopes = {}
        for e in evaluator.resources[l["state"]]:
            if e["component"] in members:
                envelopes.setdefault(e["component"], []).append(e["rect"])
        for cid, boxes in envelopes.items():
            if cid not in l["transforms"]:
                continue
            q = transformed_box(box_union(boxes), l["transforms"][cid])
            key = tuple(q.values()) + tuple(region.values())
            if key in seen:
                continue
            seen.add(key)
            solver.addConstraint((q["x"] - cx) * scale + cx + shift_x >= region["x"])
            solver.addConstraint((q["y"] - cy) * scale + cy + shift_y >= region["y"])
            solver.addConstraint(
                (q["x"] + q["w"] - cx) * scale + cx + shift_x <= region["x"] + region["w"]
            )
            solver.addConstraint(
                (q["y"] + q["h"] - cy) * scale + cy + shift_y <= region["y"] + region["h"]
            )
        for cid in members & set(l["transforms"]):
            ratio = (
                l["transforms"][cid]["sx"] / evaluator.original_transforms[l["state"]][cid]["sx"]
            )
            solver.addConstraint(scale >= 0.94 / ratio)
            solver.addConstraint(scale <= 1.06 / ratio)
        for r in evaluator.alignments.get(l["state"], []):
            if not {r["first"], r["last"]} & members:
                continue
            if r["first"] not in l["transforms"] or r["last"] not in l["transforms"]:
                continue
            axis = r["axis"]
            size = "w" if axis == "x" else "h"
            origin = cx if axis == "x" else cy
            shift = shift_x if axis == "x" else shift_y
            points = []
            for cid, key in [(r["first"], "firstRect"), (r["last"], "lastRect")]:
                b = transformed_box(r[key], l["transforms"][cid])
                p = b[axis] + r["factor"] * b[size]
                points.append((p - origin) * scale + origin + shift if cid in members else p)
            solver.addConstraint(points[0] == points[1])
        # Preserve inferred edge/center positions while allowing scale about
        # the relevant anchor instead of the physical screen center.
        plan = evaluator.plan(l)
        groups = {g["id"]: g for g in plan.get("groups", [])}
        desc = evaluator.descriptions[l["state"]]
        mapping = l["transforms"]

        def resolve(gid):
            if gid in desc and gid in mapping:
                return {gid}, transformed_box(desc[gid]["bounds"], mapping[gid])
            if gid not in groups:
                return set(), None
            parts = [resolve(c) for c in groups[gid]["members"]]
            parts = [p for p in parts if p[1]]
            return (
                (set().union(*(p[0] for p in parts)), box_union([p[1] for p in parts]))
                if parts
                else (set(), None)
            )

        for gid, g in groups.items():
            ids, b = resolve(gid)
            if not b or not ids <= members:
                continue
            positions = {
                axis: (
                    b[axis]
                    if intent["edge"] in {"left", "top"}
                    else b[axis] + b["w" if axis == "x" else "h"]
                )
                for axis, intent in g.get("screenAttachment", {}).items()
            }
            if "x" in positions and g.get("horizontalLocalBounds"):
                frame = g["horizontalLocalBounds"]
                s = mapping[next(iter(ids))]["sx"]
                intent = g["screenAttachment"]["x"]
                positions["x"] = (
                    b["x"] + (frame["x"] + (0 if intent["edge"] == "left" else frame["w"])) * s
                )
            if g.get("cornerLocalBounds"):
                frame = g["cornerLocalBounds"]
                s = mapping[next(iter(ids))]["sx"]
                positions = {
                    axis: b[axis]
                    + (
                        frame[axis]
                        + (
                            0
                            if intent["edge"] in {"left", "top"}
                            else frame["w" if axis == "x" else "h"]
                        )
                    )
                    * s
                    for axis, intent in g.get("screenAttachment", {}).items()
                }
            center_ids = g.get("centerReferenceMembers") or ids
            visual = box_union(
                [
                    transformed_box(
                        desc[cid].get(
                            "stableVisualBounds", desc[cid].get("visualBounds", desc[cid]["bounds"])
                        ),
                        mapping[cid],
                    )
                    for cid in center_ids
                    if cid in desc and cid in mapping
                ]
            )
            positions.update(
                {
                    axis: visual[axis] + visual["w" if axis == "x" else "h"] / 2
                    for axis in g.get("centerAxes", [])
                }
            )
            for axis, p in positions.items():
                origin = cx if axis == "x" else cy
                shift = shift_x if axis == "x" else shift_y
                solver.addConstraint((p - origin) * scale + origin + shift == p)
        # Do not bring authored waiting artwork into view.
        source_rows = {r[0]: r for r in evaluator.sources.get(l["state"], {}).get("rows", [])}
        for row in l["rows"]:
            uid = evaluator.project["nodeOrder"][row[0]]
            cid = evaluator.project["nodeComponents"].get(uid)
            src = source_rows.get(row[0])
            if cid not in members or cid not in mapping or not src:
                continue
            original = dict(zip(("x", "y", "w", "h"), src[1:5]))
            if intersects(original, evaluator.svp):
                continue
            b = transformed_box(dict(zip(("x", "y", "w", "h"), row[1:5])), mapping[cid])
            if b["x"] + b["w"] <= 0:
                solver.addConstraint((b["x"] + b["w"] - cx) * scale + cx + shift_x <= 0)
            elif b["x"] >= vp["width"]:
                solver.addConstraint((b["x"] - cx) * scale + cx + shift_x >= vp["width"])
            if b["y"] + b["h"] <= 0:
                solver.addConstraint((b["y"] + b["h"] - cy) * scale + cy + shift_y <= 0)
            elif b["y"] >= vp["height"]:
                solver.addConstraint((b["y"] - cy) * scale + cy + shift_y >= vp["height"])
    solver.updateVariables()
    return shift_x.value(), shift_y.value(), scale.value()


def optimize_device(project, device, options=None):
    options = {
        **project["sourceProfile"].get("adaptation", {}).get("aesthetics", {}),
        **device["profile"].get("adaptation", {}).get("aesthetics", {}),
        **(options or {}),
    }
    evaluator = Evaluator(project, device, options.get("weights"))
    before = evaluator.scores()
    current = before
    baseline = evaluator.average(before)
    accepted = []
    rejected = Counter()
    tested = 0
    anchors = {
        **project["sourceProfile"].get("adaptation", {}).get("anchorLayout", {}),
        **device["profile"].get("adaptation", {}).get("anchorLayout", {}),
    }
    authored = bool(
        anchors.get("groups")
        or anchors.get("constraints")
        or any(c.get("layout") for c in project["components"])
    )
    enabled = options.get("enabled", True) and not authored
    budget = int(options.get("candidateBudget", 48))
    rounds = int(options.get("rounds", 2))
    minimum = float(options.get("minGain", 0.01))
    if not 0 <= budget <= 512 or not 0 <= rounds <= 8 or minimum < 0 or not math.isfinite(minimum):
        raise ValueError("invalid aesthetic search budget")
    from multires_app.media.resource_scaling import glyph_components

    glyphs = glyph_components(project) | set(device.get("sharedResourceScales", {}))
    vp = device["viewport"]
    for round_index in range(rounds if enabled else 0):
        improved = False
        step = 0.008 / (2**round_index)
        for members, locked_axes in coherent_groups(project, device):
            candidates = []
            if "x" not in locked_axes:
                candidates.extend([(step * vp["width"], 0, 1), (-step * vp["width"], 0, 1)])
            if "y" not in locked_axes:
                candidates.extend([(0, step * vp["height"], 1), (0, -step * vp["height"], 1)])
            if not members & glyphs:
                candidates.extend(
                    [(0, 0, 1 + 0.03 / (2**round_index)), (0, 0, 1 - 0.03 / (2**round_index))]
                )
            best = None
            start = evaluator.average(current)
            snapshot = [
                (l, cid, dict(l["transforms"][cid]))
                for l in device["layouts"]
                for cid in members
                if cid in l["transforms"]
            ]

            def restore():
                for l, cid, t in snapshot:
                    l["transforms"][cid] = dict(t)

            for dx, dy, k in candidates:
                if tested >= budget:
                    break
                tested += 1
                try:
                    dx, dy, k = project_candidate(evaluator, members, dx, dy, k, glyphs)
                except (ValueError, kiwi.UnsatisfiableConstraint):
                    rejected["projection-conflict"] += 1
                    continue
                for l, cid, t in snapshot:
                    l["transforms"][cid] = {
                        "sx": t["sx"] * k,
                        "sy": t["sy"] * k,
                        "tx": t["tx"] * k + (1 - k) * vp["width"] / 2 + dx,
                        "ty": t["ty"] * k + (1 - k) * vp["height"] / 2 + dy,
                    }
                scores = evaluator.scores()
                failure = evaluator.guard(members, scores)
                score = evaluator.average(scores)
                if failure:
                    rejected[failure] += 1
                elif score > start + minimum and (best is None or score > best[0]):
                    best = (
                        score,
                        dx,
                        dy,
                        k,
                        scores,
                        [(l, cid, dict(l["transforms"][cid])) for l, cid, _ in snapshot],
                    )
                restore()
            if best:
                for l, cid, t in best[5]:
                    l["transforms"][cid] = t
                current = best[4]
                improved = True
                accepted.append(
                    {
                        "round": round_index + 1,
                        "components": sorted(members),
                        "dx": best[1],
                        "dy": best[2],
                        "scale": best[3],
                        "beforeScore": round(start, 6),
                        "afterScore": round(best[0], 6),
                    }
                )
            if tested >= budget:
                break
        if not improved or tested >= budget:
            break
    validation_error = None
    try:
        from multires_app.layout.anchor_validation import validate_export

        validate_export(project, device)
        from multires_app.pipeline.scene_pipeline import compiled_transform

        project.get("_stateGuards", {}).pop(device["profile"]["id"], None)
        for cid in {c for l in device["layouts"] for c in l["transforms"]}:
            for field in ("sx", "sy", "tx", "ty"):
                compiled_transform(project, device, cid, field)
    except ValueError as exc:
        validation_error = str(exc)
        for l in device["layouts"]:
            l["transforms"] = copy.deepcopy(evaluator.original_transforms[l["state"]])
        accepted = []
        current = before
    for l in device["layouts"]:
        sample = evaluator.by_state[l["state"]]
        a = before[id(sample)]
        b = current[id(sample)]
        l["aestheticEvaluation"] = {"before": a, "after": b}
        changed = {
            cid: t
            for cid, t in evaluator.original_transforms[l["state"]].items()
            if t != l["transforms"].get(cid)
        }
        if changed:
            l["aestheticBeforeTransforms"] = changed
        else:
            l.pop("aestheticBeforeTransforms", None)
        plan = l.get("adaptationPlan", {})
        groups = {g["id"]: g for g in plan.get("groups", [])}
        desc = evaluator.descriptions[l["state"]]
        boxes = {
            cid: transformed_box(d["bounds"], l["transforms"][cid])
            for cid, d in desc.items()
            if cid in l["transforms"]
        }

        def group_box(gid):
            if gid in boxes:
                return boxes[gid]
            if gid not in groups:
                return None
            parts = [group_box(c) for c in groups[gid]["members"]]
            parts = [b for b in parts if b]
            if parts:
                boxes[gid] = box_union(parts)
                return boxes[gid]

        for gid, g in groups.items():
            b = group_box(gid)
            if b:
                g["targetBounds"] = b
        if changed:
            plan.setdefault("validation", {}).update(
                postOptimizationRequiredGuard=True,
                softResidualsScope="pre-aesthetic solve; current transforms checked separately",
            )
    result = {
        "method": "reference-aware-geometric-proxy-v1",
        "humanCalibrated": False,
        "enabled": enabled,
        "beforeScore": round(baseline, 6),
        "afterScore": round(evaluator.average(current), 6),
        "geometryRepresentatives": len(evaluator.samples),
        "states": len(device["layouts"]),
        "evaluatedComponents": sum(r["components"] for r in before.values()),
        "candidateBudget": budget,
        "candidatesTested": tested,
        "acceptedSteps": accepted,
        "rejected": dict(rejected),
        "validationError": validation_error,
        "reason": (
            "authored hierarchy/constraints: evaluation only"
            if authored
            else (
                "best feasible candidate retained"
                if accepted
                else "no admissible improvement found within budget"
            )
        ),
        "weights": {**DEFAULT_WEIGHTS, **options.get("weights", {})},
        "scope": "all cached stable states, deduplicated geometry; bbox/size/Sobel proxies, no semantic saliency, contrast or human preference model",
    }
    device["aestheticEvaluation"] = result
    return result
