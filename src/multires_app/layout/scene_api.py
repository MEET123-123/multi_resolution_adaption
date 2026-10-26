"""Scene algorithm contract, plugin loading and transform validation.

Custom algorithms implement layout_scene; they do not need project persistence.
"""

import math
from pathlib import Path
from typing import Protocol
from multires_app.layout.geometry import union


class SceneLayoutAlgorithm(Protocol):
    name: str

    def layout_scene(self, components, source_profile, target_profile, context):
        """Return {component_id:{sx,sy,tx,ty}} in target logical coordinates.

        components: atomic visual stacks plus their buttons, bounds and roles.
        context: state, elements, graph, model, assets, logicalViewport,
        layoutRequest, fixedTransforms, baseTransforms, baseDescriptors,
        allComponentDescriptors. Only affected components are submitted for
        local/overlay requests; reuse makes no algorithm call.
        Translate top-level roots once; scale descendants in the same component.
        """
        ...


class SemanticReflow:
    """Example policy: portrait bands, landscape two columns; atomic stacks."""

    name = "semantic-component-reflow-v1"

    def layout_scene(self, components, source_profile, target_profile, context):
        if context.get("layoutRequest", {}).get("kind") in {"local-reflow", "overlay"}:
            from multires_app.pipeline.scene_clusters import constrained_reflow

            return constrained_reflow(components, context)
        w, h = context["logicalViewport"]["width"], context["logicalViewport"]["height"]
        margin = min(w, h) * 0.035
        gap = margin
        landscape = w / h > 1.15
        if landscape:
            left = (w - 3 * margin) * 0.40
            right = w - left - 3 * margin
            regions = {
                "header": {"x": margin, "y": margin, "w": left, "h": h * 0.57 - margin},
                "footer": {"x": margin, "y": h * 0.59, "w": left, "h": h * 0.41 - margin},
                "content": {"x": left + 2 * margin, "y": margin, "w": right, "h": h - 2 * margin},
            }
        else:
            regions = {
                "header": {"x": margin, "y": margin, "w": w - 2 * margin, "h": h * 0.25 - margin},
                "content": {"x": margin, "y": h * 0.27, "w": w - 2 * margin, "h": h * 0.52},
                "footer": {"x": margin, "y": h * 0.81, "w": w - 2 * margin, "h": h * 0.19 - margin},
            }
        output = {}
        for c in components:
            if c["role"] == "gesture-layer":
                output[c["id"]] = {"sx": 1.0, "sy": 1.0, "tx": 0.0, "ty": 0.0}
        for role in ("header", "content", "footer", "background"):
            lane = [
                c
                for c in components
                if (
                    c["role"] == role
                    or role == "content"
                    and c["role"]
                    in {"popup", "overlay", "dialog", "decoration", "interaction-layer"}
                )
                and c["visible"]
            ]
            bounds = union([c["bounds"] for c in lane])
            if role == "background":
                # Full-canvas source imagery retains its cover composition.
                for c in lane:
                    b = c["bounds"]
                    s = max(w / max(1, b["w"]), h / max(1, b["h"]))
                    output[c["id"]] = {
                        "sx": s,
                        "sy": s,
                        "tx": (w - b["w"] * s) / 2 - b["x"] * s,
                        "ty": (h - b["h"] * s) / 2 - b["y"] * s,
                    }
                continue
            if not bounds["w"] or not bounds["h"]:
                continue
            region = regions[role]
            s = min(region["w"] / bounds["w"], region["h"] / bounds["h"])
            transform = {
                "sx": s,
                "sy": s,
                "tx": region["x"] + (region["w"] - bounds["w"] * s) / 2 - bounds["x"] * s,
                "ty": region["y"] + (region["h"] - bounds["h"] * s) / 2 - bounds["y"] * s,
            }
            for c in lane:
                output[c["id"]] = dict(transform)
        return output


def load_scene_algorithm(spec):
    if spec in {"anchor", "auto", "anchor-theme-layout-v1"}:
        from multires_app.layout.anchor_layout import AnchorThemeLayout

        return AnchorThemeLayout()
    if spec in {"adaptive", "adaptive-theme-layout-v1"}:
        from multires_app.layout.adaptive_layout import AdaptiveThemeLayout

        return AdaptiveThemeLayout()
    if spec in {"reflow", SemanticReflow.name}:
        return SemanticReflow()
    # New plugins need layout_scene, without requiring the old layout method.
    if ":" not in spec:
        raise ValueError("场景算法使用 anchor、adaptive、reflow 或 file.py:Class")
    module_name, cls = spec.rsplit(":", 1)
    if module_name.endswith(".py"):
        import importlib.util

        spec_obj = importlib.util.spec_from_file_location(
            "scene_layout_plugin", Path(module_name).resolve()
        )
        if not spec_obj or not spec_obj.loader:
            raise ValueError("无法加载场景算法")
        module = importlib.util.module_from_spec(spec_obj)
        spec_obj.loader.exec_module(module)
    else:
        import importlib
        from multires_app.compat import resolve_module

        module = importlib.import_module(resolve_module(module_name))
    algorithm = getattr(module, cls)()
    if not callable(getattr(algorithm, "layout_scene", None)):
        raise ValueError("场景算法必须实现 layout_scene")
    return algorithm


def validate_transforms(mapping, components):
    if not isinstance(mapping, dict):
        raise ValueError("layout_scene 必须返回组件变换字典")
    known = {c["id"] for c in components}
    for cid, t in mapping.items():
        if cid not in known:
            raise ValueError("算法返回未知组件: " + cid)
        if set(t) != {"sx", "sy", "tx", "ty"}:
            raise ValueError("组件变换需要 sx/sy/tx/ty")
        if (
            not all(isinstance(v, (int, float)) and math.isfinite(v) for v in t.values())
            or min(t["sx"], t["sy"]) <= 0
        ):
            raise ValueError("组件变换必须为有限数且缩放大于 0")
