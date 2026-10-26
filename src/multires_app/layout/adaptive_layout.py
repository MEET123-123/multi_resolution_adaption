"""Aspect-aware theme adaptation: preserve composition or reflow atomic blocks.

Physical resolution selects a policy; all transforms use engine coordinates.
This module never rewrites variables, triggers, resources or app commands.
"""

from __future__ import annotations

import math
from collections import defaultdict

from multires_app.pipeline.scene_clusters import (
    LayoutConflict,
    box_union,
    fit,
    overlap,
    transformed_box,
)
from multires_app.layout.ordered_layout import (
    reference_components,
    two_column_plan,
    apply_plan,
    local_from_source,
)
from multires_app.layout.aesthetic_layout import is_datetime, place_datetime, options_for


def positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be a finite positive number")
    return value


def adaptation_options(source, target):
    options = {**source.get("adaptation", {}), **target.get("adaptation", {})}
    if options.get("mode", "auto") not in {"auto", "preserve", "reflow"}:
        raise ValueError("adaptation.mode must be auto, preserve or reflow")
    for key, default in [("aspectThreshold", 0.18), ("marginRatio", 0.035), ("gapRatio", 0.025)]:
        value = float(options.get(key, default))
        limit = 0.45 if key != "aspectThreshold" else 1.0
        if not math.isfinite(value) or not 0 <= value < limit:
            raise ValueError(f"adaptation.{key} must be in [0, {limit})")
        options[key] = value
    if options.get("flow", "row-major") not in {"row-major", "column-major"}:
        raise ValueError("adaptation.flow must be row-major or column-major")
    options["flow"] = options.get("flow", "row-major")
    options["maxScale"] = positive(options.get("maxScale", 1.0), "adaptation.maxScale")
    for key in ("rowGapPx", "columnGapPx", "marginPx"):
        if key in options:
            value = float(options[key])
            if not math.isfinite(value) or value < 0:
                raise ValueError("adaptation." + key + " must be finite and non-negative")
            options[key] = value
    return options


def classify_change(source, target, viewport=None):
    """A 2x pixel density change alone is not a reason to rearrange a theme."""
    src, dst = source["resolutionPx"], target["resolutionPx"]
    sw, sh = positive(src["width"], "source width"), positive(src["height"], "source height")
    tw, th = positive(dst["width"], "target width"), positive(dst["height"], "target height")
    options = adaptation_options(source, target)
    aspect_delta = max((tw / th) / (sw / sh), (sw / sh) / (tw / th)) - 1
    rotated = (sw > sh) != (tw > th)
    mode = options.get("mode", "auto")
    exceeds = aspect_delta > options["aspectThreshold"] + 1e-9
    strategy = mode if mode != "auto" else ("reflow" if exceeds else "preserve")
    reasons = []
    if mode != "auto":
        reasons.append("explicit profile override")
    if rotated:
        reasons.append("orientation changed")
    if exceeds:
        reasons.append("aspect ratio exceeds threshold")
    if not reasons:
        reasons.append("similar shape; keep original composition")
    return {
        "strategy": strategy,
        "aspectChange": round(aspect_delta, 6),
        "policyVersion": 2,
        "aspectThreshold": options["aspectThreshold"],
        "orientationChanged": rotated,
        "pixelScale": {"x": tw / sw, "y": th / sh},
        "reasons": reasons,
        "coordinateUnit": "theme-engine-logical",
        "reviewRequired": strategy == "reflow",
        "layoutPolicy": (
            "preserve-source"
            if strategy == "preserve"
            else "custom-regions" if options.get("regions") else "ordered-two-columns"
        ),
        "flow": options["flow"],
        "scalePolicy": "uniform",
        "reference": "same-state-source-layout",
    }


def safe_region(source, target, context):
    vp = context["logicalViewport"]
    w, h = positive(vp["width"], "viewport width"), positive(vp["height"], "viewport height")
    px = target.get("resolutionPx", {"width": w, "height": h})
    insets = target.get("safeInsetsPx") or {}
    values = {}
    for edge in ("left", "right", "top", "bottom"):
        raw = float(insets.get(edge, 0))
        if not math.isfinite(raw) or raw < 0:
            raise ValueError("invalid safe inset: " + edge)
        values[edge] = raw * (
            w / positive(px["width"], "target width")
            if edge in ("left", "right")
            else h / positive(px["height"], "target height")
        )
    region = {
        "x": values["left"],
        "y": values["top"],
        "w": w - values["left"] - values["right"],
        "h": h - values["top"] - values["bottom"],
    }
    if region["w"] <= 0 or region["h"] <= 0:
        raise ValueError("safe insets consume the viewport")
    return region


def cover(bounds, viewport):
    scale = max(viewport["width"] / max(1, bounds["w"]), viewport["height"] / max(1, bounds["h"]))
    return {
        "sx": scale,
        "sy": scale,
        "tx": (viewport["width"] - bounds["w"] * scale) / 2 - bounds["x"] * scale,
        "ty": (viewport["height"] - bounds["h"] * scale) / 2 - bounds["y"] * scale,
    }


def gesture_transform(bounds, viewport):
    # A global hit region fills the viewport; only visual elements require
    # uniform scaling. The existing exporter remaps touch coordinates.
    sx, sy = viewport["width"] / max(1, bounds["w"]), viewport["height"] / max(1, bounds["h"])
    return {"sx": sx, "sy": sy, "tx": -bounds["x"] * sx, "ty": -bounds["y"] * sy}


def group_blocks(components):
    """Never split an XML component; combine authored groups and visual stacks.

    A layout.group is explicit. Otherwise only substantially overlapping bounds
    are combined, so a frame, its text and its hot area stay together.
    """
    items = sorted(components, key=lambda c: (c["bounds"]["y"], c["bounds"]["x"], c["id"]))
    parents = list(range(len(items)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for i, a in enumerate(items):
        for j in range(i):
            b = items[j]
            ah, bh = a.get("layout", {}), b.get("layout", {})
            same = bool(ah.get("group")) and ah.get("group") == bh.get("group")
            explicit = ah.get("group") or bh.get("group") or ah.get("region") or bh.get("region")
            area = min(a["bounds"]["w"] * a["bounds"]["h"], b["bounds"]["w"] * b["bounds"]["h"])
            # Shallow shadow overlap must not transitively merge a whole page.
            stacked = (
                not explicit
                and a.get("allowSpatialStack", True)
                and b.get("allowSpatialStack", True)
                and area > 0
                and overlap(a["bounds"], b["bounds"]) / area >= 0.85
            )
            if same or stacked:
                parents[find(i)] = find(j)
    groups = defaultdict(list)
    for i, c in enumerate(items):
        groups[find(i)].append(c)
    return [
        {"components": group, "bounds": box_union(c["bounds"] for c in group)}
        for group in groups.values()
    ]


def grid_pack(blocks, region, gap, output):
    if not blocks:
        return
    best = None
    for cols in range(1, len(blocks) + 1):
        rows = math.ceil(len(blocks) / cols)
        cw, ch = (region["w"] - gap * (cols - 1)) / cols, (region["h"] - gap * (rows - 1)) / rows
        if min(cw, ch) <= 0:
            continue
        scales = [min(cw / max(1, b["bounds"]["w"]), ch / max(1, b["bounds"]["h"])) for b in blocks]
        # Favor the worst legibility, then avoid empty cells and excess rows.
        score = (min(scales), sum(scales) / len(scales), -(cols * rows - len(blocks)), -rows)
        if best is None or score > best[0]:
            best = (score, cols, cw, ch)
    if best is None:
        raise LayoutConflict("no space for component grid")
    _, cols, cw, ch = best
    for index, block in enumerate(blocks):
        cell = {
            "x": region["x"] + (index % cols) * (cw + gap),
            "y": region["y"] + (index // cols) * (ch + gap),
            "w": cw,
            "h": ch,
        }
        transform = fit(block["bounds"], cell)
        for comp in block["components"]:
            output[comp["id"]] = dict(transform)


class AdaptiveThemeLayout:
    name = "adaptive-theme-layout-v1"
    allow_shape_reuse = False  # Alternative asset sizes retain their source proportions.

    def describe_adaptation(self, source, target, context):
        decision = classify_change(source, target, context.get("logicalViewport"))
        decision["aesthetics"] = options_for(source, target)
        return decision

    def review_layout(self, components, transforms, source, target, context):
        region = safe_region(source, target, context)
        issues = []
        placed = []
        root = context.get("model", {}).get("root", {})
        sw = float(root.get("screenWidth", source["resolutionPx"]["width"]))
        sh = sw * source["resolutionPx"]["height"] / source["resolutionPx"]["width"]
        references = context.get("sourceComponentDescriptors", {})
        for comp in components:
            if (
                comp["role"] in {"background", "gesture-layer", "decoration", "interaction-layer"}
                or comp["id"] not in transforms
            ):
                continue
            transform = transforms[comp["id"]]
            box = transformed_box(comp["bounds"], transform)
            minimum = float(comp.get("layout", {}).get("minScale", 0.30))
            if not math.isfinite(minimum) or minimum <= 0:
                raise ValueError("layout.minScale must be positive")
            if min(transform["sx"], transform["sy"]) < minimum:
                issues.append(
                    {
                        "component": comp["id"],
                        "reason": "component below minimum scale",
                        "minimum": minimum,
                    }
                )
            ref = references.get(comp["id"], {}).get(
                "bounds", comp.get("sourceBounds", comp["bounds"])
            )
            cropped = (
                ref["x"] < 0
                or ref["y"] < 0
                or ref["x"] + ref["w"] > sw + 0.5
                or ref["y"] + ref["h"] > sh + 0.5
            )
            intentional_crop = classify_change(source, target)["strategy"] == "preserve" and cropped
            if not intentional_crop and (
                box["x"] < region["x"] - 0.5
                or box["y"] < region["y"] - 0.5
                or box["x"] + box["w"] > region["x"] + region["w"] + 0.5
                or box["y"] + box["h"] > region["y"] + region["h"] + 0.5
            ):
                issues.append({"component": comp["id"], "reason": "component exceeds safe area"})
            for previous, pbox in placed:
                area = min(box["w"] * box["h"], pbox["w"] * pbox["h"])
                if comp["role"] in {"popup", "overlay", "dialog"} or previous["role"] in {
                    "popup",
                    "overlay",
                    "dialog",
                }:
                    continue
                if (
                    area > 0
                    and overlap(box, pbox) / area > 0.05
                    and not overlap(comp["bounds"], previous["bounds"])
                ):
                    issues.append(
                        {
                            "component": comp["id"],
                            "other": previous["id"],
                            "reason": "new overlap after layout",
                        }
                    )
            placed.append((comp, box))
        return issues

    def layout_scene(self, components, source, target, context):
        decision = self.describe_adaptation(source, target, context)
        if decision["strategy"] == "preserve":
            return self.preserve(components, source, target, context)
        kind = context.get("layoutRequest", {}).get("kind")
        if kind == "overlay":
            # Reuse the existing scheduler's fixed-component / modal constraints.
            from multires_app.pipeline.scene_clusters import constrained_reflow

            output = constrained_reflow(components, context)
            context["adaptationPlan"] = {
                "policy": "overlay-on-previous",
                "baseState": context["layoutRequest"].get("baseState"),
            }
            return output
        if kind == "local-reflow" and not adaptation_options(source, target).get("regions"):
            ordinary = [c for c in components if c["role"] not in {"background", "gesture-layer"}]
            output = local_from_source(ordinary, context, safe_region(source, target, context))
            vp = context["logicalViewport"]
            for c in components:
                if c["role"] == "background":
                    output[c["id"]] = cover(c["bounds"], vp)
                elif c["role"] == "gesture-layer":
                    output[c["id"]] = gesture_transform(c["bounds"], vp)
            context["adaptationPlan"] = {
                "policy": "source-guided-local",
                "baseState": context["layoutRequest"].get("baseState"),
                "scale": context.get("basePlan", {}).get("scale"),
            }
            return output
        if kind == "local-reflow":
            from multires_app.pipeline.scene_clusters import constrained_reflow

            return constrained_reflow(components, context)
        return self.reflow(components, source, target, context)

    def reuse_layout(self, components, source, target, context, mapping):
        # A shape match is a cache decision, not permission to replace authored
        # positions or resize an alternative asset to its predecessor's size.
        if classify_change(source, target)["strategy"] == "preserve":
            return self.preserve(components, source, target, context)
        return mapping

    def preserve(self, components, source, target, context):
        vp = context["logicalViewport"]
        region = safe_region(source, target, context)
        root = context.get("model", {}).get("root", {})
        sw = float(root.get("screenWidth", source["resolutionPx"]["width"]))
        sh = sw * source["resolutionPx"]["height"] / source["resolutionPx"]["width"]
        scale = min(region["w"] / sw, region["h"] / sh)
        # In this pipeline target width is the original screenWidth, so the
        # native engine has already applied physical pixel-density scaling.
        base = {
            "sx": scale,
            "sy": scale,
            "tx": region["x"] + (region["w"] - sw * scale) / 2,
            "ty": region["y"] + (region["h"] - sh * scale) / 2,
        }
        context["adaptationPlan"] = {
            "policy": "preserve-source",
            "scale": scale,
            "sourceCanvas": {"width": sw, "height": sh},
            "safeRegion": region,
        }
        references = context.get("sourceComponentDescriptors", {})
        output = {}
        for comp in components:
            if not comp.get("visible", True):
                continue
            b = comp["bounds"]
            role = comp["role"]
            if role == "background":
                output[comp["id"]] = cover(b, vp)
                continue
            if role == "gesture-layer":
                output[comp["id"]] = gesture_transform(b, vp)
                continue
            ref = references.get(comp["id"], {}).get("bounds", comp.get("sourceBounds", b))
            desired = transformed_box(ref, base)
            # Keep current dynamic sizes and motion, translate only the canvas
            # offset. Responsive #screen_height formulas are not shifted twice.
            t = {**base, "tx": desired["x"] - b["x"] * scale, "ty": desired["y"] - b["y"] * scale}
            hint = comp.get("layout", {})
            anchor = hint.get("anchor", "auto")
            if anchor == "auto":
                anchor = "center"
            if anchor in {"top", "bottom", "center"}:
                inset_top = ref["y"] * scale
                inset_bottom = (sh - ref["y"] - ref["h"]) * scale
                top = (
                    region["y"] + inset_top
                    if anchor == "top"
                    else (
                        region["y"] + region["h"] - inset_bottom - b["h"] * scale
                        if anchor == "bottom"
                        else desired["y"]
                    )
                )
                t["ty"] = top - b["y"] * scale
            elif anchor != "original":
                raise ValueError("unsupported layout.anchor: " + anchor)
            # Never clamp individual layers: authored off-screen decorations and
            # animated stacks would detach. New clipping is reported for review.
            output[comp["id"]] = t
        output, placement = place_datetime(components, output, source, target, context, region)
        context["adaptationPlan"]["datetimePlacement"] = placement
        return output

    def reflow(self, components, source, target, context):
        options = adaptation_options(source, target)
        if options.get("regions"):
            context["adaptationPlan"] = {"policy": "custom-regions"}
            return self.region_reflow(components, source, target, context)
        vp = context["logicalViewport"]
        region = safe_region(source, target, context)
        unit = vp["width"] / target["resolutionPx"]["width"]
        margin = (
            options.get("marginPx", min(region["w"], region["h"]) * options["marginRatio"] / unit)
            * unit
        )
        gap = min(region["w"], region["h"]) * options["gapRatio"]
        row_gap = options.get("rowGapPx", gap / unit) * unit
        column_gap = options.get("columnGapPx", gap / unit) * unit
        region = {
            "x": region["x"] + margin,
            "y": region["y"] + margin,
            "w": region["w"] - 2 * margin,
            "h": region["h"] - 2 * margin,
        }
        if min(region["w"], region["h"]) <= 0:
            raise ValueError("adaptation margins consume the safe region")
        output = {}
        ordinary = []
        for comp in components:
            if not comp.get("visible", True):
                continue
            if comp["role"] == "background":
                output[comp["id"]] = cover(comp["bounds"], vp)
            elif comp["role"] == "gesture-layer":
                output[comp["id"]] = gesture_transform(comp["bounds"], vp)
            elif comp["role"] in {"popup", "overlay", "dialog"}:
                panel = {
                    "x": region["x"] + region["w"] * 0.08,
                    "y": region["y"] + region["h"] * 0.08,
                    "w": region["w"] * 0.84,
                    "h": region["h"] * 0.84,
                }
                output[comp["id"]] = fit(comp["bounds"], panel)
            elif comp["role"] in {"decoration", "interaction-layer"}:
                sw = float(
                    context.get("model", {})
                    .get("root", {})
                    .get("screenWidth", source["resolutionPx"]["width"])
                )
                sh = sw * source["resolutionPx"]["height"] / source["resolutionPx"]["width"]
                s = min(vp["width"] / sw, vp["height"] / sh)
                ref = (
                    context.get("sourceComponentDescriptors", {})
                    .get(comp["id"], {})
                    .get("bounds", comp["bounds"])
                )
                output[comp["id"]] = {
                    "sx": s,
                    "sy": s,
                    "tx": (vp["width"] - sw * s) / 2 + (ref["x"] - comp["bounds"]["x"]) * s,
                    "ty": (vp["height"] - sh * s) / 2 + (ref["y"] - comp["bounds"]["y"]) * s,
                }
            else:
                ordinary.append(comp)
        references = reference_components(ordinary, context.get("sourceComponentDescriptors", {}))
        for c in references:
            if is_datetime(c) and not c.get("layout", {}).get("group"):
                c["layout"] = {**c.get("layout", {}), "group": "__datetime__"}
        blocks = group_blocks(references)
        plan = two_column_plan(
            blocks, region, column_gap, row_gap, options["flow"], options["maxScale"]
        )
        context["adaptationPlan"] = plan
        output.update(apply_plan(blocks, plan))
        output, placement = place_datetime(components, output, source, target, context, region)
        context["adaptationPlan"]["datetimePlacement"] = placement
        return output

    def region_reflow(self, components, source, target, context):
        vp = context["logicalViewport"]
        options = adaptation_options(source, target)
        region = safe_region(source, target, context)
        margin = min(region["w"], region["h"]) * options["marginRatio"]
        gap = min(region["w"], region["h"]) * options["gapRatio"]
        region = {
            "x": region["x"] + margin,
            "y": region["y"] + margin,
            "w": region["w"] - 2 * margin,
            "h": region["h"] - 2 * margin,
        }
        x, y, w, h = (region[k] for k in ("x", "y", "w", "h"))
        if w / h > 1.15:
            left = (w - gap) * 0.40
            right = w - left - gap
            lanes = {
                "header": {"x": x, "y": y, "w": left, "h": h * 0.70 - gap / 2},
                "footer": {"x": x, "y": y + h * 0.70 + gap / 2, "w": left, "h": h * 0.30 - gap / 2},
                "content": {"x": x + left + gap, "y": y, "w": right, "h": h},
            }
        else:
            lanes = {
                "header": {"x": x, "y": y, "w": w, "h": h * 0.24 - gap / 2},
                "content": {"x": x, "y": y + h * 0.24 + gap / 2, "w": w, "h": h * 0.57 - gap},
                "footer": {"x": x, "y": y + h * 0.81 + gap / 2, "w": w, "h": h * 0.19 - gap / 2},
            }
        custom = options.get("regions", {})
        for name, box in custom.items():
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                raise ValueError("region must be [x, y, w, h]")
            rx, ry, rw, rh = map(float, box)
            if (
                not all(math.isfinite(v) for v in (rx, ry, rw, rh))
                or min(rx, ry) < 0
                or min(rw, rh) <= 0
                or rx + rw > 1
                or ry + rh > 1
            ):
                raise ValueError("region must fit normalized safe area")
            lanes[name] = {"x": x + rx * w, "y": y + ry * h, "w": rw * w, "h": rh * h}
        output = {}
        groups = defaultdict(list)
        for comp in components:
            if not comp.get("visible", True):
                continue
            if comp["role"] == "background":
                output[comp["id"]] = cover(comp["bounds"], vp)
            elif comp["role"] == "gesture-layer":
                output[comp["id"]] = gesture_transform(comp["bounds"], vp)
            else:
                lane = comp.get("layout", {}).get("region", comp["role"])
                if lane in {"popup", "overlay", "dialog", "decoration", "interaction-layer"}:
                    lane = "content"
                if lane not in lanes:
                    raise ValueError("unknown layout region: " + lane)
                groups[lane].append(comp)
        # Header/footer preserve their internal arrangement; content uses cells.
        for lane, comps in groups.items():
            if lane in {"header", "footer"}:
                transform = fit(box_union(c["bounds"] for c in comps), lanes[lane])
                for c in comps:
                    output[c["id"]] = dict(transform)
            else:
                grid_pack(group_blocks(comps), lanes[lane], gap, output)
        return output
