"""Reference-first grouping and placement. Original screen relations are soft priors.

The matching basis is the untransformed script pose, so a poor earlier adaptation
cannot become the template for the next one. Group identity and hit bindings stay
rigid. No theme names, resource names, or target coordinates are special-cased.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import kiwisolver as kiwi

from multires_app.pipeline.scene_clusters import box_union, describe_layout, overlap


def build_reflow_catalog(project, request, layout, asset_root):
    from multires_app.evaluation.report import elements
    from multires_app.media.media_layout import layout_rows
    from multires_app.preview.paint_visibility import raster_snapshot, read_bitmap
    from multires_app.preview.target_layout import SCHEMA

    current = {c["id"]: c for c in request["components"]}
    if len(current) > 512:
        raise ValueError("单场景最多支持 512 个组件")
    basis = {**layout, "rows": layout_rows(project, layout), "transforms": {}}
    desc = describe_layout(project, basis)
    rendered = elements(project, basis)
    parents = {cid: cid for cid in current}

    def find(cid):
        while parents[cid] != cid:
            parents[cid] = parents[parents[cid]]
            cid = parents[cid]
        return cid

    def merge(ids):
        ids = sorted(set(ids) & current.keys())
        for cid in ids[1:]:
            parents[find(cid)] = find(ids[0])

    authored = {}
    for c in current.values():
        if c.get("group"):
            authored.setdefault(c["group"], []).append(c["id"])
    for ids in authored.values():
        merge(ids)
    # Nested visibility boundaries (e.g. countdown text) belong to their card.
    # Screen-wide inferred flow containers are deliberately not merged here.
    for cid, c in current.items():
        if c["locked"]:
            continue
        ancestors = [
            other
            for other in current
            if cid.startswith(other + "/") and not current[other]["locked"]
        ]
        if ancestors:
            merge([cid, max(ancestors, key=len)])
    bindings = project["model"].get("layoutBindings", {}).get("bindings", [])
    for binding in bindings:
        cid = project["nodeComponents"].get(binding.get("button"))
        if (
            cid not in current
            or current[cid]["locked"]
            or current[cid]["role"] != "interaction-layer"
        ):
            continue
        candidates = {project["nodeComponents"].get(uid) for uid in binding.get("anchors", [])}
        candidates = {
            x for x in candidates if x in current and x != cid and not current[x]["locked"]
        }
        if len(candidates) == 1:
            anchor = next(iter(candidates))
            a = current[cid]["sourceBBox"]
            b = current[anchor]["sourceBBox"]
            if a["w"] * a["h"] <= b["w"] * b["h"] * 1.5:
                merge([cid, anchor])
    clusters = {}
    for cid in current:
        clusters.setdefault(find(cid), []).append(cid)
    followers = {uid for c in project["components"] for uid in c.get("layoutFollowers", [])}
    assets = Path(asset_root).resolve() / "assets"
    groups = []
    templates = {}
    from multires_app.media.resource_scaling import glyph_nodes

    device = next(d for d in project["devices"] if d["profile"]["id"] == request["target"])
    native_glyph_scales = {}
    for uid in glyph_nodes(project):
        cid = project["nodeComponents"].get(uid)
        others = [
            l
            for l in device["layouts"]
            if l["state"] != request["state"]
            and any(project["nodeOrder"][r[0]] == uid for r in l["rows"])
        ]
        scales = {round(l["transforms"].get(cid, {"sx": 1})["sx"], 9) for l in others}
        if scales:
            native_glyph_scales.setdefault(cid, set()).update(scales)
    for index, ids in enumerate(sorted(sorted(ids) for ids in clusters.values())):
        alias = "g" + str(index)
        members = [current[cid] for cid in ids]
        selected = [
            copy.deepcopy(e)
            for e in rendered
            if project["nodeComponents"].get(e["id"]) in ids
            and e["tag"] != "Button"
            and e["id"] not in followers
            and min(e["rect"]["w"], e["rect"]["h"]) > 0
        ]
        # Optical crop excludes transparent bitmap padding and screen-sized mask
        # followers. The followers still receive the same affine at writeback.
        visual = box_union([desc[cid]["visualBounds"] for cid in ids])
        footprint = box_union(
            [c["sourceBBox"] for c in members]
            + [
                e["rect"]
                for e in rendered
                if e["tag"] == "Button" and project["nodeComponents"].get(e["id"]) in ids
            ]
        )
        g = {
            "id": alias,
            "members": ids,
            "names": [c["name"] for c in members],
            "roles": sorted({c.get("semanticRole") or c["role"] for c in members}),
            "bbox": footprint,
            "matchBox": visual,
            "centerAxes": [],
            "edges": {},
            "corner": False,
            "basisBoxes": {c["id"]: c["sourceBBox"] for c in members},
            "currentBoxes": {c["id"]: c["bbox"] for c in members},
            "locked": any(c["locked"] for c in members),
            "rendering": {},
            "groupingEvidence": [
                "authored components / nested script containers / unique interaction binding"
            ],
        }
        native_scales = set().union(*(native_glyph_scales.get(cid, set()) for cid in ids))
        if len(native_scales) == 1:
            g["nativeScale"] = next(iter(native_scales))
            g["nativeScaleReason"] = (
                "current exporter uses one bitmap Time/ImageSeries resource family per node across states"
            )
        if not g["locked"] and selected and min(visual["w"], visual["h"]) > 0:
            # Bounded bitmap decoding and layer raster budget, even for imported archives.
            safe = []
            pixels = 0
            omitted = 0
            for e in selected:
                path = (assets / e.get("asset", "")).resolve()
                if e.get("asset") and path.is_file() and path.is_relative_to(assets):
                    from PIL import Image

                    try:
                        with Image.open(path) as im:
                            size = im.width * im.height
                    except OSError:
                        omitted += 1
                        continue
                    if size > 8_000_000 or pixels + size > 24_000_000:
                        omitted += 1
                        continue
                    pixels += size
                # Large cutout masks are excluded above; no unbounded temporary bitmap.
                if e["rect"]["w"] * e["rect"]["h"] / (visual["w"] * visual["h"]) > 32:
                    omitted += 1
                    continue
                e["rect"]["x"] -= visual["x"]
                e["rect"]["y"] -= visual["y"]
                safe.append(e)
            try:
                im, paint = raster_snapshot(
                    safe,
                    {"width": visual["w"], "height": visual["h"]},
                    assets,
                    max_size=(400, 400),
                    background=(0, 0, 0, 0),
                )
                g["rendering"] = {
                    "omitted": omitted,
                    "unsupported": sum(not p["supported"] for p in paint.values()),
                }
                # A composition mask paints background around a cutout. Use its
                # occlusion to locate visible artwork, without treating the whole
                # background mask as the bear/widget being matched.
                masks = {
                    b["mask"]
                    for b in project["model"].get("compositionBindings", [])
                    if project["nodeComponents"].get(b["anchor"]) in ids
                }
                mask_elements = [
                    copy.deepcopy(e) for e in rendered if e["id"] in masks and e.get("asset")
                ]
                bounded_masks = []
                for e in mask_elements:
                    path = (assets / e["asset"]).resolve()
                    if not path.is_relative_to(assets) or not path.is_file():
                        continue
                    try:
                        with Image.open(path) as bitmap:
                            size = bitmap.width * bitmap.height
                    except OSError:
                        continue
                    if size > 8_000_000 or pixels + size > 24_000_000:
                        g["rendering"]["omitted"] += 1
                        continue
                    pixels += size
                    bounded_masks.append(e)
                mask_elements = bounded_masks
                if mask_elements:
                    from PIL import ImageChops

                    for e in mask_elements:
                        e["rect"]["x"] -= visual["x"]
                        e["rect"]["y"] -= visual["y"]
                    mask_image, _ = raster_snapshot(
                        mask_elements,
                        {"width": visual["w"], "height": visual["h"]},
                        assets,
                        max_size=(400, 400),
                        background=(0, 0, 0, 0),
                    )
                    im.putalpha(
                        ImageChops.multiply(
                            im.getchannel("A"), ImageChops.invert(mask_image.getchannel("A"))
                        )
                    )
                # Neumorphic shadows may have nonzero alpha across the entire
                # resource canvas. Ground the solid body; keep all padding and
                # shadows in the actual group affine and containment footprint.
                alpha = im.getchannel("A")
                crop = alpha.point(lambda a: 255 if a >= 224 else 0).getbbox()
                if not crop:
                    crop = alpha.point(lambda a: 255 if a >= 16 else 0).getbbox()
                if crop:
                    sx, sy = visual["w"] / im.width, visual["h"] / im.height
                    g["matchBox"] = {
                        "x": visual["x"] + crop[0] * sx,
                        "y": visual["y"] + crop[1] * sy,
                        "w": (crop[2] - crop[0]) * sx,
                        "h": (crop[3] - crop[1]) * sy,
                    }
                    templates[alias] = im.crop(crop)
            except (OSError, ValueError) as exc:
                g["rendering"]["error"] = str(exc)[:200]
            finally:
                read_bitmap.cache_clear()
        if alias not in templates:
            g["locked"] = True
            g["lockReason"] = "背景、全屏手势或当前状态没有可定位的可见画面"
        groups.append(g)
    animation_scales = []
    for node in project["model"]["nodes"].values():
        if not any(
            project["model"]["nodes"][uid]["tag"] == "PositionAnimation"
            for uid in node.get("children", [])
        ):
            continue
        prefix = node["id"] + "/"
        aliases = [
            g["id"]
            for g in groups
            if any(cid == node["id"] or cid.startswith(prefix) for cid in g["members"])
        ]
        if len(aliases) > 1:
            animation_scales.append({"wrapper": node["id"], "groups": aliases})
    return {
        "schema": SCHEMA,
        "target": request["target"],
        "state": request["state"],
        "baseDigest": request["baseDigest"],
        "layoutMode": "reference-first",
        "viewport": request["viewport"],
        "coordinateUnit": "normalized-1000-xywh",
        "constraints": request["constraints"],
        "groups": groups,
        "animationScaleSets": animation_scales,
    }, templates


def solve_reflow(catalog, request, matches, attempt=0, *, constraint_hook=None):
    """Try target-driven separation axes; keep old geometry at weak strength.

    Safety, aspect ratio and internal group geometry are hard constraints. Source
    screen positions, edge anchors and order never override a target observation.
    Unmatched visible groups can yield to matched groups; pure global hot areas
    are not treated as visual collision obstacles.
    """
    solver = kiwi.Solver()
    variables = {}
    desired = {}
    frozen = set()
    region = request["constraints"]["safeRegion"]
    current = {c["id"]: c for c in request["components"]}
    for g in catalog["groups"]:
        alias = g["id"]
        b = g["matchBox"]
        x, y, s = [kiwi.Variable(alias + k) for k in ("x", "y", "s")]
        variables[alias] = (x, y, s)
        cb = box_union(list(g.get("currentBoxes", {}).values()))
        old_scale = min(cb["w"] / max(g["bbox"]["w"], 1e-8), cb["h"] / max(g["bbox"]["h"], 1e-8))
        inside_current = (
            cb["x"] >= region["x"] - 0.5
            and cb["y"] >= region["y"] - 0.5
            and cb["x"] + cb["w"] <= region["x"] + region["w"] + 0.5
            and cb["y"] + cb["h"] <= region["y"] + region["h"] + 0.5
        )
        if g["locked"] or (alias not in matches and not inside_current):
            frozen.add(alias)
            # Frozen means the current affine, not identity in the raw source
            # coordinate system. Shared animation peers must see this same scale.
            solver.addConstraint(x == cb["x"] + (b["x"] - g["bbox"]["x"]) * old_scale)
            solver.addConstraint(y == cb["y"] + (b["y"] - g["bbox"]["y"]) * old_scale)
            solver.addConstraint(s == old_scale)
            continue
        if alias in matches:
            t = matches[alias]["bbox"]
            scale = min(t["w"] / b["w"], t["h"] / b["h"])
            dest = {
                "x": t["x"] + (t["w"] - b["w"] * scale) / 2,
                "y": t["y"] + (t["h"] - b["h"] * scale) / 2,
                "w": b["w"] * scale,
                "h": b["h"] * scale,
            }
            weight = kiwi.strength.strong * matches[alias]["confidence"]
        else:
            scale = max(0.03, old_scale)
            dest = {
                "x": cb["x"] + (b["x"] - g["bbox"]["x"]) * scale,
                "y": cb["y"] + (b["y"] - g["bbox"]["y"]) * scale,
                "w": b["w"] * scale,
                "h": b["h"] * scale,
            }
            weight = kiwi.strength.weak
        desired[alias] = dest
        if "nativeScale" in g:
            solver.addConstraint(s == g["nativeScale"])
        solver.addConstraint(s >= max(0.03, scale * 0.6))
        solver.addConstraint(s <= 4)
        # No scale collapse to satisfy an incorrect correspondence.
        for v, val, mult in [(x, dest["x"], 1), (y, dest["y"], 1), (s, scale, max(b["w"], b["h"]))]:
            solver.addConstraint((v == val) | min(kiwi.strength.required / 100, weight * mult))
        if alias in matches:
            # Previous centering / edge placement survives only as a weak stay.
            # It can break ties but cannot pin a group against a reference move.
            for v, val in [
                (x, cb["x"] + (b["x"] - g["bbox"]["x"]) * old_scale),
                (y, cb["y"] + (b["y"] - g["bbox"]["y"]) * old_scale),
            ]:
                solver.addConstraint((v == val) | kiwi.strength.weak)
        for f in [g["bbox"], *g["basisBoxes"].values()]:
            solver.addConstraint(x + (f["x"] - b["x"]) * s >= region["x"])
            solver.addConstraint(y + (f["y"] - b["y"]) * s >= region["y"])
            solver.addConstraint(x + (f["x"] - b["x"] + f["w"]) * s <= region["x"] + region["w"])
            solver.addConstraint(y + (f["y"] - b["y"] + f["h"]) * s <= region["y"] + region["h"])
    for rule in catalog.get("animationScaleSets", []):
        aliases = [a for a in rule["groups"] if a in variables]
        for alias in aliases[1:]:
            solver.addConstraint(variables[alias][2] == variables[aliases[0]][2])
    expressions = {}
    group_for = {}
    for g in catalog["groups"]:
        x, y, s = variables[g["id"]]
        b = g["matchBox"]
        for cid in g["members"]:
            group_for[cid] = g["id"]
            old = g["basisBoxes"][cid]
            expressions[cid] = {
                "x": x + (old["x"] - b["x"]) * s,
                "y": y + (old["y"] - b["y"]) * s,
                "w": old["w"] * s,
                "h": old["h"] * s,
            }
    # One separating constraint per pair of rigid groups (not per glyph pair).
    count = 0
    movable = [g for g in catalog["groups"] if g["id"] not in frozen]
    for i, a in enumerate(movable):
        for b in movable[:i]:
            if a["id"] not in matches and b["id"] not in matches:
                continue
            old_a = box_union(list(a["currentBoxes"].values()))
            old_b = box_union(list(b["currentBoxes"].values()))
            if overlap(old_a, old_b) > 0:
                continue
            aa, bb = desired[a["id"]], desired[b["id"]]
            choices = []
            for axis, dim in [("x", "w"), ("y", "h")]:
                gap_ab = bb[axis] - aa[axis] - aa[dim]
                gap_ba = aa[axis] - bb[axis] - bb[dim]
                choices.extend([(gap_ab, a, b, axis, dim), (gap_ba, b, a, axis, dim)])
            choices.sort(key=lambda c: c[0], reverse=True)
            # Alternate axis solves are deterministic and reported, before any drop.
            rank = (attempt if choices[0][0] < 0 else 0) % 2
            _, first, last, axis, dim = choices[rank]
            fx, fy, fs = variables[first["id"]]
            lx, ly, ls = variables[last["id"]]
            # Transparent image padding can overlap. Separate the rendered
            # artwork, while retaining complete frame containment above.
            f = first["matchBox"]
            l = last["matchBox"]
            fb = first["matchBox"]
            lb = last["matchBox"]
            fv = fx if axis == "x" else fy
            lv = lx if axis == "x" else ly
            solver.addConstraint(
                fv + (f[axis] - fb[axis] + f[dim]) * fs <= lv + (l[axis] - lb[axis]) * ls
            )
            count += 1
    if constraint_hook is not None:
        constraint_hook(solver, expressions, variables)
    solver.updateVariables()
    components = []
    resolved = []
    auxiliary = []
    for g in movable:
        alias = g["id"]
        x, y, s = [v.value() for v in variables[alias]]
        b = g["matchBox"]
        final = {"x": x, "y": y, "w": b["w"] * s, "h": b["h"] * s}
        if alias in matches:
            t = matches[alias]["bbox"]
            inter = overlap(final, t)
            resolved.append(
                {
                    "id": alias,
                    "members": g["members"],
                    "confidence": matches[alias]["confidence"],
                    "requestedBox": t,
                    "resolvedBox": final,
                    "targetOverlapIoU": inter
                    / max(1e-8, final["w"] * final["h"] + t["w"] * t["h"] - inter),
                    "centerDisplacement": math.hypot(
                        final["x"] + final["w"] / 2 - t["x"] - t["w"] / 2,
                        final["y"] + final["h"] / 2 - t["y"] - t["h"] / 2,
                    ),
                }
            )
        moved = False
        rows = []
        for cid in g["members"]:
            old = g["basisBoxes"][cid]
            box = {
                "x": x + (old["x"] - b["x"]) * s,
                "y": y + (old["y"] - b["y"]) * s,
                "w": old["w"] * s,
                "h": old["h"] * s,
            }
            rows.append({"id": cid, "bbox": box})
            moved |= any(abs(box[k] - current[cid]["bbox"][k]) > 0.001 for k in box)
        if alias in matches or moved:
            components.extend(rows)
            if alias not in matches:
                auxiliary.append(alias)
    native_constraints = [
        {"id": g["id"], "scale": g["nativeScale"], "reason": g["nativeScaleReason"]}
        for g in catalog["groups"]
        if "nativeScale" in g
    ]
    return components, {
        "backend": "kiwisolver",
        "layoutMode": "reference-first",
        "sourceConstraintStrength": "weak",
        "nonOverlapConstraints": count,
        "groups": resolved,
        "auxiliaryGroups": auxiliary,
        "attempt": attempt,
        "nativeScaleConstraints": native_constraints,
        "preserveRelations": False,
        "hardConstraints": [
            "canvas containment",
            "uniform group affine",
            "interaction bindings",
            "no new component overlap",
        ],
    }
