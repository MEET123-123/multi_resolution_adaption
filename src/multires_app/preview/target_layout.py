"""Ground an expected image in existing theme groups, then solve safe geometry.

Matchers propose visual boxes, never XML, resources, visibility or expressions.
The independent solver projects these observations onto the current scene.
"""

from __future__ import annotations

import copy
import hashlib
import html
import io
import json
import math
import uuid
from pathlib import Path
from typing import Protocol

import kiwisolver as kiwi
from PIL import Image, ImageDraw

from multires_app.pipeline.scene_clusters import (
    box_union,
    describe_layout,
    overlap,
    transformed_box,
)
from multires_app.preview.layout_refinement import export_request, validate_and_apply

SCHEMA = "target-layout-matches/1"


class TargetMatcher(Protocol):
    def __call__(self, context: dict) -> dict:
        """Return {schema, matches:[{id,bbox:[x,y,w,h],confidence}], unmatched:[]}.

        Boxes enclose the atlas group's visible artwork in the expected image,
        normalized to 0..1000 independently on each axis. IDs are catalog aliases.
        context contains catalog, reference (PIL), templates (PIL), asset_root.
        """


MATCHERS = {}


def register_matcher(name: str, matcher: TargetMatcher):
    if not name or not callable(matcher):
        raise ValueError("matcher 名称/接口无效")
    MATCHERS[name] = matcher


def read_reference(data, viewport=None):
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("目标图不能超过 10MB")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("仅支持 PNG/JPEG/WebP")
            if im.width * im.height > 20_000_000:
                raise ValueError("目标图不能超过 2000 万像素")
            if (
                viewport
                and abs((im.width / im.height) / (viewport["width"] / viewport["height"]) - 1)
                > 0.025
            ):
                raise ValueError("目标图宽高比与设备画布不一致，请裁掉设备外框和系统栏")
            return im.convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取目标图片") from exc


def _inside(b, region, tolerance=0.5):
    return (
        min(b["w"], b["h"]) > 0
        and b["x"] >= region["x"] - tolerance
        and b["y"] >= region["y"] - tolerance
        and b["x"] + b["w"] <= region["x"] + region["w"] + tolerance
        and b["y"] + b["h"] <= region["y"] + region["h"] + tolerance
    )


def build_catalog(project, target, state, asset_root, *, layout_mode="preserve"):
    """Stable aliases, existing rigid groups, current-phase artwork and hit areas."""
    from multires_app.evaluation.report import elements
    from multires_app.media.media_layout import layout_rows
    from multires_app.preview.paint_visibility import raster_snapshot, read_bitmap

    project["_referenceAssetRoot"] = str(Path(asset_root).resolve())
    request = export_request(project, target, state)
    device = next(d for d in project["devices"] if d["profile"]["id"] == target)
    layout = next(l for l in device["layouts"] if l["state"] == state)
    if layout_mode == "reference-first":
        from multires_app.preview.target_reflow import build_reflow_catalog

        return build_reflow_catalog(project, request, layout, asset_root)
    if layout_mode != "preserve":
        raise ValueError("未知布局模式")
    descriptions = describe_layout(project, layout)
    current = {c["id"]: c for c in request["components"]}
    if len(current) > 512:
        raise ValueError("单场景最多支持 512 个组件，请先细分场景")
    parent = {cid: cid for cid in current}

    def find(cid):
        while parent[cid] != cid:
            parent[cid] = parent[parent[cid]]
            cid = parent[cid]
        return cid

    def merge(ids):
        ids = sorted(set(ids) & parent.keys())
        for cid in ids[1:]:
            parent[find(cid)] = find(ids[0])

    authored = {}
    for c in current.values():
        if c.get("group"):
            authored.setdefault(c["group"], []).append(c["id"])
    for ids in authored.values():
        merge(ids)
    plan = layout.get("adaptationPlan", {})
    # Reused states omit their group payload; resolve the original template rather
    # than treating clock digits or related text as independent components.
    visited = {state}
    cursor = layout
    while True:
        plan = plan.get("anchorPlan", plan)
        if plan.get("groups"):
            break
        base = plan.get("reusedFrom") or cursor.get("layoutRequest", {}).get("baseState")
        if not base or base in visited:
            break
        visited.add(base)
        cursor = next((l for l in device["layouts"] if l["state"] == base), {})
        plan = cursor.get("adaptationPlan", {})
    group_defs = {g["id"]: g for g in plan.get("groups", [])}

    def leaves(gid, stack=()):
        if gid in stack:
            raise ValueError("布局分组存在循环")
        if gid in current:
            return [gid]
        return [
            cid
            for mid in group_defs.get(gid, {}).get("members", [])
            for cid in leaves(mid, (*stack, gid))
        ]

    for gid in group_defs:
        merge(leaves(gid))
    options = {
        **project["sourceProfile"].get("adaptation", {}).get("anchorLayout", {}),
        **device["profile"].get("adaptation", {}).get("anchorLayout", {}),
    }
    configured = {g["id"]: g for g in options.get("groups", [])}

    def configured_leaves(gid, stack=()):
        if gid in stack:
            raise ValueError("显式布局分组存在循环")
        if gid in current:
            return [gid]
        g = configured.get(gid, group_defs.get(gid, {}))
        return [
            cid for mid in g.get("members", []) for cid in configured_leaves(mid, (*stack, gid))
        ]

    protected = set()
    for rule in options.get("constraints", []):
        if rule.get("priority", "required") == "required":
            protected.update(
                cid for gid in rule.get("targets", []) for cid in configured_leaves(gid)
            )
    for g in [*group_defs.values(), *configured.values()]:
        anchor = g.get("anchor") or {}
        if (isinstance(anchor, dict) and anchor.get("priority") == "required") or g.get(
            "size_policy"
        ) == "fixed":
            protected.update(configured_leaves(g["id"]))
    for c in project["components"]:
        anchor = c.get("layout", {}).get("anchor", {})
        if (isinstance(anchor, dict) and anchor.get("priority") == "required") or c.get(
            "layout", {}
        ).get("sizePolicy") == "fixed":
            protected.add(c["id"])
    clusters = {}
    for cid in current:
        clusters.setdefault(find(cid), []).append(cid)
    rendered = elements(project, {**layout, "rows": layout_rows(project, layout)})
    assets = Path(asset_root).resolve() / "assets"
    groups = []
    templates = {}
    region = request["constraints"]["safeRegion"]
    metas = project.get("assets", {})
    for index, ids in enumerate(sorted((sorted(v) for v in clusters.values()))):
        alias = "g" + str(index)
        members = [current[cid] for cid in ids]
        # Component sizing may exclude follower arrows or buttons. Include their
        # actual rendered/hit rectangles in the required group containment box.
        bounds = box_union(
            [c["bbox"] for c in members]
            + [e["rect"] for e in rendered if project["nodeComponents"].get(e["id"]) in ids]
        )
        related = [g for gid, g in group_defs.items() if set(leaves(gid)) == set(ids)]
        related.sort(key=lambda g: g["id"])
        old = related[0] if related else {}
        visual = box_union(
            [
                transformed_box(
                    descriptions[cid].get("visualBounds", descriptions[cid]["bounds"]),
                    layout["transforms"].get(cid, {"sx": 1, "sy": 1, "tx": 0, "ty": 0}),
                )
                for cid in ids
            ]
        )
        group = {
            "id": alias,
            "members": ids,
            "names": [c["name"] for c in members],
            "roles": sorted({c.get("semanticRole") or c["role"] for c in members}),
            "bbox": bounds,
            "matchBox": visual,
            "centerAxes": old.get("centerRequiredAxes", old.get("centerAxes", [])),
            "edges": copy.deepcopy(old.get("screenAttachment", {})),
            "corner": bool(old.get("cornerAttachment")),
            "locked": any(c["locked"] for c in members) or not _inside(bounds, region),
            "lockReason": "背景/全屏手势层或当前处于屏外/裁切状态",
            "rendering": {},
        }
        if set(ids) & protected:
            group["locked"] = True
            group["lockReason"] = "显式 required 约束或固定尺寸引用的组保持原布局"
        selected = [
            copy.deepcopy(e)
            for e in rendered
            if project["nodeComponents"].get(e["id"]) in ids
            and e["tag"] != "Button"
            and min(e["rect"]["w"], e["rect"]["h"]) > 0
        ]
        # Decode only bounded group imagery. Missing/native effects remain explicit.
        selected_safe = []
        omitted = 0
        pixels = 0
        for e in selected:
            uid_asset = next(
                (
                    project["assetOrder"][r[6]]
                    for r in layout["rows"]
                    if project["nodeOrder"][r[0]] == e["id"] and r[6] >= 0
                ),
                "",
            )
            meta = metas.get(uid_asset, {})
            size = meta.get("width", 0) * meta.get("height", 0)
            if size > 8_000_000 or pixels + size > 24_000_000:
                omitted += 1
                continue
            pixels += size
            selected_safe.append(e)
        selected = selected_safe
        vb = box_union([e["rect"] for e in selected])
        if not group["locked"] and min(vb["w"], vb["h"]) <= 0:
            group["lockReason"] = "当前相位无可见画面、仅有热区，或资源超出渲染预算"
        if min(vb["w"], vb["h"]) > 0 and not group["locked"]:
            for e in selected:
                e["rect"]["x"] -= vb["x"]
                e["rect"]["y"] -= vb["y"]
            try:
                image, paint = raster_snapshot(
                    selected,
                    {"width": vb["w"], "height": vb["h"]},
                    assets,
                    max_size=(320, 240),
                    background=(0, 0, 0, 0),
                )
                crop = image.getchannel("A").getbbox()
                group["rendering"] = {
                    "omitted": omitted,
                    "unsupported": sum(not p["supported"] for p in paint.values()),
                }
                # An incomplete reference can create false correspondences. Do not ground it.
                if crop and not omitted and not group["rendering"]["unsupported"]:
                    sx, sy = vb["w"] / image.width, vb["h"] / image.height
                    group["matchBox"] = {
                        "x": vb["x"] + crop[0] * sx,
                        "y": vb["y"] + crop[1] * sy,
                        "w": (crop[2] - crop[0]) * sx,
                        "h": (crop[3] - crop[1]) * sy,
                    }
                    templates[alias] = image.crop(crop)
                else:
                    group["lockReason"] = "没有完整可渲染的可见资源（含缺失资源或原生效果）"
            except (OSError, ValueError) as exc:
                group["lockReason"] = "组件参考图渲染失败：" + str(exc)[:160]
            finally:
                read_bitmap.cache_clear()
        if alias not in templates:
            group["locked"] = True
        if not group["locked"]:
            group.pop("lockReason", None)
        groups.append(group)
    return {
        "schema": SCHEMA,
        "target": target,
        "state": state,
        "baseDigest": request["baseDigest"],
        "viewport": request["viewport"],
        "coordinateUnit": "normalized-1000-xywh",
        "constraints": request["constraints"],
        "groups": groups,
    }, templates


def make_atlas(groups, templates):
    """Numbered original artwork, on tiles; labels are outside the matched artwork."""
    groups = list(groups)
    cols = 3
    cell_w, cell_h = 270, 210
    image = Image.new(
        "RGB", (cols * cell_w, max(1, math.ceil(len(groups) / cols)) * cell_h), "#e7ebee"
    )
    draw = ImageDraw.Draw(image)
    for index, g in enumerate(groups):
        x = (index % cols) * cell_w
        y = (index // cols) * cell_h
        draw.rectangle((x + 3, y + 3, x + cell_w - 4, y + cell_h - 4), outline="#465c70")
        draw.text((x + 12, y + 9), g["id"], fill="#14212b")
        thumb = templates[g["id"]].copy()
        thumb.thumbnail((cell_w - 24, cell_h - 45), Image.Resampling.LANCZOS)
        image.paste(
            thumb,
            (x + (cell_w - thumb.width) // 2, y + 35 + (cell_h - 45 - thumb.height) // 2),
            thumb,
        )
    return image


def template_matcher(context):
    import numpy as np
    from multires_app.preview.reference_matching import match_template

    reference = context["reference"].copy()
    reference.thumbnail((640, 640), Image.Resampling.LANCZOS)
    vp = context["catalog"]["viewport"]
    matches = []
    unmatched = []
    for g in context["catalog"]["groups"]:
        if g["locked"]:
            continue
        b = g["matchBox"]
        template = context["templates"][g["id"]]
        match = match_template(
            reference,
            template,
            (b["w"] / vp["width"] * reference.width, b["h"] / vp["height"] * reference.height),
            scales=(0.4, 0.55, 0.7, 0.85, 1.0, 1.2, 1.45, 1.8, 2.2),
        )
        if match:
            bitmap = template.resize((match["w"], match["h"]), Image.Resampling.LANCZOS)
            mask = np.asarray(bitmap.getchannel("A"), dtype=float) / 255
            patch = reference.crop(
                (match["x"], match["y"], match["x"] + match["w"], match["y"] + match["h"])
            )
            source_mean = (np.asarray(bitmap.convert("RGB"), dtype=float) * mask[:, :, None]).sum(
                axis=(0, 1)
            ) / max(1, mask.sum())
            target_mean = (np.asarray(patch, dtype=float) * mask[:, :, None]).sum(
                axis=(0, 1)
            ) / max(1, mask.sum())
            if abs(source_mean - target_mean).mean() / 255 > 0.24:
                unmatched.append(
                    {"id": g["id"], "reason": "灰度形状相似，但颜色证据不一致，拒绝跨组件误匹配"}
                )
                continue
        if not match or match["confidence"] < 0.78 or match["peakMargin"] < 0.035:
            unmatched.append({"id": g["id"], "reason": "模板相似度不足或有多个候选位置"})
            continue
        matches.append(
            {
                "id": g["id"],
                "bbox": [
                    match["x"] / reference.width * 1000,
                    match["y"] / reference.height * 1000,
                    match["w"] / reference.width * 1000,
                    match["h"] / reference.height * 1000,
                ],
                "confidence": match["confidence"],
                "peakMargin": match["peakMargin"],
            }
        )
    return {
        "schema": SCHEMA,
        "matches": matches,
        "unmatched": unmatched,
        "provider": "masked-template",
    }


def validate_matches(answer, catalog, minimum_confidence=0.78):
    if not isinstance(answer, dict) or answer.get("schema") != SCHEMA:
        raise ValueError("匹配结果 schema 无效")
    rows = answer.get("matches")
    if not isinstance(rows, list) or len(rows) > len(catalog["groups"]):
        raise ValueError("匹配结果数量无效")
    known = {g["id"]: g for g in catalog["groups"]}
    seen = set()
    accepted = {}
    diagnostics = []
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("匹配项必须为对象")
        alias = item.get("id")
        if not isinstance(alias, str) or alias not in known or alias in seen:
            raise ValueError("未知或重复组 ID：" + str(alias))
        seen.add(alias)
        box = item.get("bbox")
        confidence = item.get("confidence")
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
            or min(box[2:]) <= 0
            or min(box[:2]) < 0
            or box[0] + box[2] > 1000.5
            or box[1] + box[3] > 1000.5
        ):
            raise ValueError("匹配 bbox 必须为目标图内归一化 [x,y,w,h]：" + alias)
        if (
            type(confidence) not in (int, float)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("置信度无效：" + alias)
        if known[alias]["locked"] or confidence < minimum_confidence:
            diagnostics.append(
                {
                    "id": alias,
                    "reason": known[alias].get("lockReason", "低于置信度阈值"),
                    "confidence": confidence,
                }
            )
            continue
        vp = catalog["viewport"]
        b = dict(
            zip(
                ("x", "y", "w", "h"),
                (
                    box[0] * vp["width"] / 1000,
                    box[1] * vp["height"] / 1000,
                    box[2] * vp["width"] / 1000,
                    box[3] * vp["height"] / 1000,
                ),
            )
        )
        accepted[alias] = {"bbox": b, "confidence": confidence}
    unmatched = answer.get("unmatched", [])
    if not isinstance(unmatched, list) or len(unmatched) > len(known):
        raise ValueError("unmatched 格式无效")
    for item in unmatched:
        if not isinstance(item, dict) or item.get("id") not in known or item["id"] in seen:
            raise ValueError("未知或重复未匹配组")
        seen.add(item["id"])
        diagnostics.append({"id": item["id"], "reason": str(item.get("reason", "未匹配"))[:300]})
    for alias, g in known.items():
        if alias not in seen:
            diagnostics.append(
                {"id": alias, "reason": g.get("lockReason", "匹配器未返回对应项，保持原布局")}
            )
    return accepted, diagnostics


def _validate_catalog(catalog, request):
    if (
        catalog.get("schema") != SCHEMA
        or catalog.get("coordinateUnit") != "normalized-1000-xywh"
        or catalog.get("viewport") != request["viewport"]
    ):
        raise ValueError("组件目录 schema/坐标系/画布不匹配")
    groups = catalog.get("groups")
    known = {c["id"] for c in request["components"]}
    seen = set()
    aliases = set()
    if not isinstance(groups, list) or len(groups) > 512:
        raise ValueError("组件目录组数无效")
    for g in groups:
        if not isinstance(g, dict) or not isinstance(g.get("id"), str) or g["id"] in aliases:
            raise ValueError("组件目录组 ID 无效")
        aliases.add(g["id"])
        ids = g.get("members")
        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(cid, str) or cid not in known or cid in seen for cid in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("组件目录包含未知或重复成员")
        seen.update(ids)
        if catalog.get("layoutMode") == "reference-first":
            originals = {c["id"]: c for c in request["components"]}
            if g.get("basisBoxes") != {cid: originals[cid]["sourceBBox"] for cid in ids}:
                raise ValueError("组件目录脚本几何基准已变化")
            if g.get("currentBoxes") != {cid: originals[cid]["bbox"] for cid in ids}:
                raise ValueError("组件目录当前几何已变化")
        for key in ("bbox", "matchBox"):
            b = g.get(key)
            if (
                not isinstance(b, dict)
                or set(b) != {"x", "y", "w", "h"}
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in b.values())
                or min(b["w"], b["h"]) < 0
                or (not g.get("locked") and min(b["w"], b["h"]) <= 0)
            ):
                raise ValueError("组件目录几何无效")
        if not isinstance(g.get("centerAxes", []), list) or any(
            axis not in {"x", "y"} for axis in g.get("centerAxes", [])
        ):
            raise ValueError("居中轴无效")
        if not isinstance(g.get("edges", {}), dict):
            raise ValueError("边缘约束无效")
        for axis, intent in g.get("edges", {}).items():
            if (
                axis not in {"x", "y"}
                or not isinstance(intent, dict)
                or intent.get("edge")
                not in ({"left", "right"} if axis == "x" else {"top", "bottom"})
            ):
                raise ValueError("边缘方向无效")
    if seen != known:
        raise ValueError("组件目录未覆盖当前场景全部组件")


def _solve(catalog, request, matches, preserve_relations):
    """Uniform group affines with required safety; model boxes are soft goals."""
    solver = kiwi.Solver()
    variables = {}
    group_for = {}
    current = {c["id"]: c for c in request["components"]}
    region = request["constraints"]["safeRegion"]
    groups = catalog["groups"]
    desired = {}
    for g in groups:
        alias = g["id"]
        b = g["matchBox"]
        x, y, s = (kiwi.Variable(alias + k) for k in ("x", "y", "s"))
        variables[alias] = (x, y, s)
        for cid in g["members"]:
            group_for[cid] = alias
        if alias not in matches:
            solver.addConstraint(x == b["x"])
            solver.addConstraint(y == b["y"])
            solver.addConstraint(s == 1)
            continue
        target = matches[alias]["bbox"]
        scale = min(target["w"] / b["w"], target["h"] / b["h"])
        desired[alias] = {
            "x": target["x"] + (target["w"] - b["w"] * scale) / 2,
            "y": target["y"] + (target["h"] - b["h"] * scale) / 2,
            "w": b["w"] * scale,
            "h": b["h"] * scale,
        }
        solver.addConstraint(s >= 0.03)
        solver.addConstraint(s <= 4)
        footprint = g["bbox"]
        solver.addConstraint(x + (footprint["x"] - b["x"]) * s >= region["x"])
        solver.addConstraint(y + (footprint["y"] - b["y"]) * s >= region["y"])
        solver.addConstraint(
            x + (footprint["x"] - b["x"] + footprint["w"]) * s <= region["x"] + region["w"]
        )
        solver.addConstraint(
            y + (footprint["y"] - b["y"] + footprint["h"]) * s <= region["y"] + region["h"]
        )
        strength = kiwi.strength.strong * matches[alias]["confidence"]
        for value, preferred in ((x, desired[alias]["x"]), (y, desired[alias]["y"]), (s, scale)):
            weight = (
                min(kiwi.strength.required / 100, strength * max(b["w"], b["h"]))
                if value is s
                else strength
            )
            solver.addConstraint((value == preferred) | weight)
        if preserve_relations:
            # Keep existing visual centers; source intent is carried by the anchor plan.
            for axis in g["centerAxes"]:
                dim = "w" if axis == "x" else "h"
                v = x if axis == "x" else y
                # MatchBox is the visible artwork, not the possibly asymmetric hit region.
                solver.addConstraint(v + b[dim] * s / 2 == region[axis] + region[dim] / 2)
            for axis, intent in g["edges"].items():
                if axis not in {"x", "y"}:
                    continue
                dim = "w" if axis == "x" else "h"
                v = x if axis == "x" else y
                bb = g["bbox"]
                if intent["edge"] in {"left", "top"}:
                    margin = bb[axis] - region[axis]
                    solver.addConstraint(
                        v + (bb[axis] - b[axis]) * s
                        == region[axis] + (margin * s if g["corner"] else margin)
                    )
                else:
                    margin = region[axis] + region[dim] - bb[axis] - bb[dim]
                    solver.addConstraint(
                        v + (bb[axis] - b[axis] + bb[dim]) * s
                        == region[axis] + region[dim] - (margin * s if g["corner"] else margin)
                    )
    expressions = {}
    for cid, c in current.items():
        alias = group_for[cid]
        g = next(g for g in groups if g["id"] == alias)
        b = g["matchBox"]
        x, y, s = variables[alias]
        old = c["bbox"]
        expr = {
            "x": x + (old["x"] - b["x"]) * s,
            "y": y + (old["y"] - b["y"]) * s,
            "w": old["w"] * s,
            "h": old["h"] * s,
        }
        expressions[cid] = expr
        if alias in matches:
            solver.addConstraint(expr["x"] >= region["x"])
            solver.addConstraint(expr["y"] >= region["y"])
            solver.addConstraint(expr["x"] + expr["w"] <= region["x"] + region["w"])
            solver.addConstraint(expr["y"] + expr["h"] <= region["y"] + region["h"])

    # Non-overlap directions are taken from the expected layout if it separates the
    # pair; otherwise retain their old axis order. Existing overlaps stay allowed.
    def projected(c):
        alias = group_for[c["id"]]
        if alias not in desired:
            return c["bbox"]
        g = next(g for g in groups if g["id"] == alias)
        b = g["matchBox"]
        d = desired[alias]
        s = d["w"] / b["w"]
        old = c["bbox"]
        return {
            "x": d["x"] + (old["x"] - b["x"]) * s,
            "y": d["y"] + (old["y"] - b["y"]) * s,
            "w": old["w"] * s,
            "h": old["h"] * s,
        }

    ids = [cid for cid, c in current.items() if not c["locked"] and _inside(c["bbox"], region)]
    count = 0
    for i, cid in enumerate(ids):
        for other in ids[:i]:
            if group_for[cid] == group_for[other] or not (
                {group_for[cid], group_for[other]} & matches.keys()
            ):
                continue
            a, b = current[cid]["bbox"], current[other]["bbox"]
            if overlap(a, b) > 0:
                continue
            choices = []
            for aa, bb in ((projected(current[cid]), projected(current[other])), (a, b)):
                for axis, dim in (("x", "w"), ("y", "h")):
                    if aa[axis] + aa[dim] <= bb[axis]:
                        choices.append((bb[axis] - aa[axis] - aa[dim], cid, other, axis, dim))
                    if bb[axis] + bb[dim] <= aa[axis]:
                        choices.append((aa[axis] - bb[axis] - bb[dim], other, cid, axis, dim))
                if choices:
                    break
            if choices:
                _, first, last, axis, dim = max(choices)
                solver.addConstraint(
                    expressions[first][axis] + expressions[first][dim] <= expressions[last][axis]
                )
                count += 1
    solver.updateVariables()
    components = []
    resolved = []
    for g in groups:
        alias = g["id"]
        if alias not in matches:
            continue
        x, y, s = (v.value() for v in variables[alias])
        b = g["matchBox"]
        final = {"x": x, "y": y, "w": b["w"] * s, "h": b["h"] * s}
        resolved.append(
            {
                "id": alias,
                "members": g["members"],
                "confidence": matches[alias]["confidence"],
                "requestedBox": matches[alias]["bbox"],
                "resolvedBox": final,
                "targetOverlapIoU": overlap(final, matches[alias]["bbox"])
                / max(
                    1e-8,
                    final["w"] * final["h"]
                    + matches[alias]["bbox"]["w"] * matches[alias]["bbox"]["h"]
                    - overlap(final, matches[alias]["bbox"]),
                ),
                "centerDisplacement": math.hypot(
                    final["x"]
                    + final["w"] / 2
                    - matches[alias]["bbox"]["x"]
                    - matches[alias]["bbox"]["w"] / 2,
                    final["y"]
                    + final["h"] / 2
                    - matches[alias]["bbox"]["y"]
                    - matches[alias]["bbox"]["h"] / 2,
                ),
            }
        )
        for cid in g["members"]:
            old = current[cid]["bbox"]
            box = {
                "x": x + (old["x"] - b["x"]) * s,
                "y": y + (old["y"] - b["y"]) * s,
                "w": old["w"] * s,
                "h": old["h"] * s,
            }
            # Whole authored groups must be included even if one member stays still.
            components.append({"id": cid, "bbox": box})
    return components, {
        "backend": "kiwisolver",
        "nonOverlapConstraints": count,
        "groups": resolved,
        "preserveRelations": preserve_relations,
    }


def resolve_matches(
    project,
    catalog,
    answer,
    *,
    minimum_confidence=0.78,
    preserve_relations=True,
    apply_scope="matching-components",
):
    if not 0 <= minimum_confidence <= 1 or apply_scope not in {"state", "matching-components"}:
        raise ValueError("匹配配置无效")
    request = export_request(project, catalog["target"], catalog["state"])
    if request["baseDigest"] != catalog["baseDigest"]:
        raise ValueError("布局已变化，请重新匹配")
    _validate_catalog(catalog, request)
    matches, diagnostics = validate_matches(answer, catalog, minimum_confidence)
    initial = len(matches)
    projection = {}
    components = []
    # Conservative degradation is explicit: freeze the least certain group and
    # solve again if this scene or any propagated scene cannot accept the proposal.
    while matches:
        try:
            if catalog.get("layoutMode") == "reference-first":
                from multires_app.preview.target_reflow import solve_reflow

                candidates = []
                errors = []
                for attempt in range(2):
                    try:
                        cs, pr = solve_reflow(catalog, request, matches, attempt)
                        validate_and_apply(
                            project,
                            {
                                **request["responseTemplate"],
                                "applyScope": apply_scope,
                                "origin": "expected-image-matcher",
                                "propagationPolicy": "compatible-layout",
                                "components": cs,
                            },
                        )
                        score = sum(g["targetOverlapIoU"] * g["confidence"] for g in pr["groups"])
                        candidates.append((score, cs, pr))
                    except (kiwi.UnsatisfiableConstraint, ValueError) as exc:
                        errors.append(exc)
                if not candidates:
                    raise errors[-1]
                _, components, projection = max(candidates, key=lambda c: c[0])
                # A feasible solution can still move a false observation far
                # from its target to avoid collisions. Do not count that as a
                # successful automatic match. Preserve its evidence and solve
                # the remaining observations again.
                if answer.get("automatic"):
                    poor = [g for g in projection["groups"] if g["targetOverlapIoU"] < 0.35]
                    if poor:
                        rejected = min(poor, key=lambda g: (g["targetOverlapIoU"], g["confidence"]))
                        matches.pop(rejected["id"])
                        diagnostics.append(
                            {
                                "id": rejected["id"],
                                "reason": "自动定位与约束求解位置不一致，已撤回匹配并重新求解",
                                "code": "automatic-projection-rejected",
                                "targetOverlapIoU": rejected["targetOverlapIoU"],
                            }
                        )
                        continue
            else:
                components, projection = _solve(catalog, request, matches, preserve_relations)
                proposal = {
                    **request["responseTemplate"],
                    "applyScope": apply_scope,
                    "origin": "expected-image-matcher",
                    "components": components,
                }
                validate_and_apply(project, proposal)
            break
        except (kiwi.UnsatisfiableConstraint, ValueError) as exc:
            alias = min(matches, key=lambda a: (matches[a]["confidence"], a))
            matches.pop(alias)
            diagnostics.append(
                {"id": alias, "reason": "约束/跨状态校验冲突，保持原布局：" + str(exc)[:350]}
            )
    else:
        components = []
        projection = {
            "backend": "kiwisolver",
            "groups": [],
            "preserveRelations": preserve_relations,
        }
    native_ids = {g["id"] for g in projection.get("nativeScaleConstraints", [])}
    diagnostics.extend(
        {
            "id": g["id"],
            "reason": (
                "当前导出器的位图字库需跨场景统一尺寸，目标尺寸已投影到可导出的大小；请查看对比图"
                if g["id"] in native_ids
                else "目标框与可导出的组比例、完整画布边界或非重叠条件不一致；请查看实际求解框和对比图"
            ),
        }
        for g in projection.get("groups", [])
        if g["targetOverlapIoU"] < 0.5
    )
    return {
        **request["responseTemplate"],
        "applyScope": apply_scope,
        "origin": "expected-image-matcher",
        "components": components,
        "propagationPolicy": (
            "compatible-layout" if catalog.get("layoutMode") == "reference-first" else "component"
        ),
        "matching": {
            "method": answer.get("provider", "custom"),
            "schema": SCHEMA,
            "minimumConfidence": minimum_confidence,
            "candidateGroups": initial,
            "acceptedGroups": len(matches),
            "totalGroups": len(catalog["groups"]),
            "layoutMode": catalog.get("layoutMode", "preserve"),
            "providerEvidence": {
                k: answer[k]
                for k in ("reviewedProvider", "modelError", "notes", "recoveredByTemplate")
                if k in answer
            },
            "diagnostics": diagnostics,
            "projection": projection,
            "noTraining": True,
            "scope": (
                "参考图位置优先；原布局关系为弱约束。未匹配可见组可让位，背景与全屏手势固定；不复制目标图中的重复组件。"
                if catalog.get("layoutMode") == "reference-first"
                else "只调整已有组件组的几何；未匹配组保持原布局。动画/状态/资源身份不变。"
            ),
        },
    }


def match_target_layout(
    project,
    target,
    state,
    image_bytes,
    asset_root,
    *,
    strategy="template",
    minimum_confidence=0.78,
    preserve_relations=True,
    apply_scope="matching-components",
    layout_mode="preserve",
    observations=None,
    source_image_bytes=None,
    replay_job=None,
):
    if not 0 <= minimum_confidence <= 1 or apply_scope not in {"state", "matching-components"}:
        raise ValueError("匹配配置无效")
    request = export_request(project, target, state)
    reference = read_reference(image_bytes, request["viewport"])
    catalog, templates = build_catalog(project, target, state, asset_root, layout_mode=layout_mode)
    matcher = MATCHERS.get(strategy)
    if matcher is None:
        raise ValueError("未知目标布局匹配器：" + str(strategy))
    job = Path(asset_root) / "target-matching" / uuid.uuid4().hex[:12]
    job.mkdir(parents=True)
    reference.save(job / "expected.png")
    source_evidence = []
    observation_templates = {}
    if source_image_bytes is not None:
        source_device = project.get("sourceDevice")
        if not source_device:
            raise ValueError("缺少原始直板机场景缓存")
        source = read_reference(source_image_bytes)
        source.save(job / "source.png")
        if (
            abs(
                (source.width / source.height)
                / (source_device["viewport"]["width"] / source_device["viewport"]["height"])
                - 1
            )
            > 0.025
        ):
            # Real captures can be another phone height. Re-evaluate the original
            # script at that viewport; stretching cached screenshot boxes changes
            # bottom anchors and was incorrectly rejecting all real pairs.
            from multires_app.interaction.interaction_semantics import replay_human_state
            from multires_app.pipeline.source_comparison import packed_snapshot

            vp = {
                "width": source_device["viewport"]["width"],
                "height": source_device["viewport"]["width"] * source.height / source.width,
            }
            graph_state = next(s for s in project["graph"]["states"] if s["id"] == state)
            model = {**project["model"], "assetNames": list(project["assets"])}
            rt = replay_human_state(
                model, vp["width"], vp["height"], graph_state, project["assets"], verify_hit=False
            )
            source_device = {
                **source_device,
                "viewport": vp,
                "layouts": [packed_snapshot(project, rt, state)],
            }
        source_project = {**project, "devices": [source_device]}
        source_catalog, source_templates = build_catalog(
            source_project,
            source_device["profile"]["id"],
            state,
            asset_root,
            layout_mode=layout_mode,
        )
        source_groups = {tuple(sorted(g["members"])): g for g in source_catalog["groups"]}
        vp = source_device["viewport"]
        for group in catalog["groups"]:
            sg = source_groups.get(tuple(sorted(group["members"])))
            if group["locked"] or not sg or sg["locked"]:
                continue
            b = sg["matchBox"]
            box = [
                b["x"] / vp["width"] * source.width,
                b["y"] / vp["height"] * source.height,
                (b["x"] + b["w"]) / vp["width"] * source.width,
                (b["y"] + b["h"]) / vp["height"] * source.height,
            ]
            if (
                box[0] < 0
                or box[1] < 0
                or box[2] > source.width
                or box[3] > source.height
                or min(box[2] - box[0], box[3] - box[1]) < 8
            ):
                continue
            observed = source.crop(tuple(round(v) for v in box)).convert("RGBA")
            # A screenshot crop includes wallpaper between glyphs. Retain the
            # script-rendered alpha support so background features cannot steal
            # the correspondence from the actual clock/card/icon.
            silhouette = source_templates.get(sg["id"])
            if silhouette is not None:
                observed.putalpha(
                    silhouette.convert("RGBA")
                    .getchannel("A")
                    .resize(observed.size, Image.Resampling.LANCZOS)
                )
            observation_templates[group["id"]] = observed
            source_evidence.append(
                {
                    "id": group["id"],
                    "pixelBox": box,
                    "basis": "source-script-group-geometry",
                    "alphaSupport": (
                        "source-script-render" if silhouette is not None else "opaque-observation"
                    ),
                    "manualCoordinates": False,
                }
            )
        (job / "source-observations.json").write_text(
            json.dumps(source_evidence, ensure_ascii=False, indent=2), encoding="utf8"
        )
    (job / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf8"
    )
    eligible = [g for g in catalog["groups"] if not g["locked"]]
    for index in range(0, len(eligible), 9):
        make_atlas(eligible[index : index + 9], templates).save(job / f"atlas-{index//9}.png")
    context = {
        "catalog": catalog,
        "reference": reference,
        "templates": templates,
        "asset_root": Path(asset_root),
        "job_directory": job,
        "minimum_confidence": minimum_confidence,
        "observation_templates": observation_templates,
    }
    try:
        if observations is not None:
            for field in ("target", "state", "baseDigest"):
                if field in observations and observations[field] != catalog[field]:
                    raise ValueError("定位标注与当前布局不匹配：" + field)
            validate_matches(observations, catalog, minimum_confidence)
            answer = {
                **observations,
                "provider": "reviewed-group-boxes",
                "reviewedProvider": str(observations.get("provider", "manual"))[:160],
            }
        elif replay_job:
            import shutil

            previous = Path(replay_job).resolve()
            old_catalog = json.loads((previous / "catalog.json").read_text(encoding="utf8"))
            if any(
                old_catalog[k] != catalog[k]
                for k in ("baseDigest", "target", "state", "layoutMode")
            ):
                raise ValueError("自动匹配缓存与当前布局不一致")
            with Image.open(previous / "expected.png") as old_image:
                if (
                    old_image.convert("RGB").size != reference.size
                    or old_image.convert("RGB").tobytes() != reference.tobytes()
                ):
                    raise ValueError("自动匹配缓存的参考图不一致")
            answer = json.loads((previous / "matches.json").read_text(encoding="utf8"))
            if answer.get("automatic") is not True:
                raise ValueError("只允许重放代码自动匹配证据")
            answer = {
                **answer,
                "replayedFrom": str(previous),
                "notes": [
                    *answer.get("notes", []),
                    "复用自动定位证据，重新运行当前约束求解和反写校验。",
                ],
            }
            for name in ("automatic-candidates.json", "automatic-overlay.png"):
                if (previous / name).is_file():
                    shutil.copy2(previous / name, job / name)
        else:
            answer = matcher(context)
    except Exception as exc:
        (job / "error.json").write_text(
            json.dumps({"error": str(exc), "strategy": strategy}, ensure_ascii=False),
            encoding="utf8",
        )
        raise
    (job / "matches.json").write_text(
        json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf8"
    )
    result = resolve_matches(
        project,
        catalog,
        answer,
        minimum_confidence=minimum_confidence,
        preserve_relations=preserve_relations,
        apply_scope=apply_scope,
    )
    result["reference"] = {
        "sha256": hashlib.sha256(image_bytes).hexdigest(),
        "size": list(reference.size),
    }
    if source_image_bytes is not None:
        result["reference"]["sourceObservationGroups"] = len(source_evidence)
    relative = job.relative_to(asset_root).as_posix()
    result["artifacts"] = {
        name: relative + "/" + filename
        for name, filename in [
            ("expected", "expected.png"),
            ("before", "before.png"),
            ("after", "after.png"),
            ("report", "report.html"),
            ("catalog", "catalog.json"),
        ]
    }
    updated, validation = validate_and_apply(project, result)
    # Reuse the bounded offline painter; importing it does not import ML libraries.
    from multires_app.learning.render import render

    d = next(d for d in project["devices"] if d["profile"]["id"] == target)
    l = next(l for l in d["layouts"] if l["state"] == state)
    after = next(
        l
        for dev in updated["devices"]
        if dev["profile"]["id"] == target
        for l in dev["layouts"]
        if l["state"] == state
    )
    result["rendering"] = {
        "before": render(project, d, l, Path(asset_root) / "assets", job / "before.png"),
        "after": render(updated, d, after, Path(asset_root) / "assets", job / "after.png"),
    }
    result["validation"] = {
        "affectedStates": validation["affectedStates"],
        "skippedStates": validation.get("skippedStates", []),
        "nativeDeviceTest": False,
    }
    (job / "proposal.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    _write_report(job, result)
    return result


def _write_report(job, result):
    text = html.escape(
        json.dumps(
            {
                "matching": result["matching"],
                "rendering": result["rendering"],
                "validation": result["validation"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    title = html.escape(result["target"] + " / " + result["state"])
    figures = "".join(
        f'<figure><figcaption>{label}</figcaption><img src="{name}.png" alt="{label}"></figure>'
        for name, label in [
            ("before", "匹配前"),
            ("expected", "目标布局图"),
            ("after", "约束求解后（原素材）"),
        ]
    )
    page = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>目标布局匹配</title>'
        "<style>body{font:15px system-ui;background:#101720;color:#e8eff8;margin:24px}section{display:flex;gap:16px}"
        "figure{margin:0;flex:1;min-width:0}img{max-width:100%;max-height:65vh;object-fit:contain}figcaption{margin:12px 0}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1a2432;padding:16px}a{color:#85e2cf}</style>"
        f'<h1>目标布局匹配 · {title}</h1><p>采纳 {result["matching"]["acceptedGroups"]} 组；'
        "参考图优先模式允许未匹配可见组让位。离线渲染不等同于真机；目标图中的新增/重复实例需单独建模。</p><section>"
        + figures
        + '</section><p><a href="proposal.json">布局建议 JSON</a> · <a href="catalog.json">组件组目录</a> · '
        '<a href="matches.json">匹配器输出</a></p><h2>匹配与约束诊断</h2><pre>'
        + text
        + "</pre></html>"
    )
    (job / "report.html").write_text(page, encoding="utf8")


register_matcher("template", template_matcher)


def _qwen(context):
    from multires_app.preview.target_layout_vlm import local_qwen_matcher

    return local_qwen_matcher(context)


register_matcher("qwen", _qwen)


def hybrid_matcher(context):
    """Semantic correspondence first, then local pixel alignment, without training.

    NCC is deliberately local after grounding: a duplicated clock elsewhere in
    the target must not steal an already established semantic correspondence.
    """
    from multires_app.preview.reference_matching import match_template

    try:
        answer = _qwen(context)
    except ValueError as exc:
        answer = template_matcher(context)
        answer["provider"] = "masked-template (VLM unavailable)"
        answer["modelError"] = str(exc)
        answer["notes"] = ["视觉模型未完成，本次结果来自模板匹配；可用组件组校正继续。"]
        return answer
    answer = copy.deepcopy(answer)
    reference = context["reference"].copy()
    reference.thumbnail((960, 960), Image.Resampling.LANCZOS)
    groups = {g["id"]: g for g in context["catalog"]["groups"]}
    evidence = []
    for row in answer["matches"]:
        alias = row["id"]
        template = context["templates"].get(alias)
        if template is None:
            continue
        x, y, w, h = row["bbox"]
        x = x * reference.width / 1000
        y = y * reference.height / 1000
        w = w * reference.width / 1000
        h = h * reference.height / 1000
        left = max(0, int(x - w * 0.35))
        top = max(0, int(y - h * 0.35))
        right = min(reference.width, math.ceil(x + w * 1.35))
        bottom = min(reference.height, math.ceil(y + h * 1.35))
        region = reference.crop((left, top, right, bottom))
        b = groups[alias]["matchBox"]
        scale = min(w / b["w"], h / b["h"])
        match = match_template(
            region,
            template,
            (b["w"] * scale, b["h"] * scale),
            scales=(0.75, 0.85, 0.925, 1.0, 1.075, 1.15, 1.25),
        )
        if match and match["confidence"] >= 0.72 and match["peakMargin"] >= 0.025:
            row["semanticBox"] = row["bbox"]
            row["bbox"] = [
                (left + match["x"]) / reference.width * 1000,
                (top + match["y"]) / reference.height * 1000,
                match["w"] / reference.width * 1000,
                match["h"] / reference.height * 1000,
            ]
            row["pixelEvidence"] = {"ncc": match["confidence"], "peakMargin": match["peakMargin"]}
            evidence.append(
                {"id": alias, "status": "local-template-refined", **row["pixelEvidence"]}
            )
    # High quality, unambiguous reused artwork can recover model omissions.
    fallback = template_matcher(context)
    returned = {r["id"] for r in answer["matches"]}
    recovered = [r for r in fallback["matches"] if r["id"] not in returned]
    answer["matches"].extend(recovered)
    returned.update(r["id"] for r in recovered)
    answer["unmatched"] = [r for r in answer.get("unmatched", []) if r["id"] not in returned]
    answer["provider"] = "qwen+local-masked-ncc"
    answer["refinementEvidence"] = evidence
    answer["recoveredByTemplate"] = [r["id"] for r in recovered]
    return answer


register_matcher("hybrid", hybrid_matcher)


def _automatic(context):
    from multires_app.preview.automatic_matching import automatic_matcher

    return automatic_matcher(context)


register_matcher("auto", _automatic)
