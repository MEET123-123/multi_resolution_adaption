"""Pluggable layout algorithm contract and a deliberately simple demo adapter.

User algorithms can implement ``layout(elements, source_profile,
target_profile, context)`` and return a mapping from element key to XML
geometry attributes. Keep device-specific logic out of the analyzer.
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import re
from pathlib import Path
from typing import Any, Protocol


class LayoutAlgorithm(Protocol):
    name: str

    def layout(
        self,
        elements: list[dict[str, Any]],
        source_profile: dict[str, Any],
        target_profile: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Return ``{element_key: {xml_attribute: value}}`` for adapted geometry."""
        ...


def _number(value: Any) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*", str(value))
    return float(match.group(1)) if match else None


class NormalizedStretchDemo:
    """Demo-only independent X/Y scaling. Replace with a real composition algorithm."""

    name = "normalized-stretch-demo"

    def layout(
        self,
        elements: list[dict[str, Any]],
        source_profile: dict[str, Any],
        target_profile: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        source = source_profile["resolutionPx"]
        target = target_profile["resolutionPx"]
        sx = target["width"] / source["width"]
        sy = target["height"] / source["height"]
        adapted: dict[str, dict[str, Any]] = {}
        for element in elements:
            attrs = element.get("attributes", {})
            geometry: dict[str, Any] = {}
            for attr in ("x", "y", "w", "h", "width", "height"):
                raw = attrs.get(attr)
                value = _number(raw)
                if value is None:
                    continue
                scale = sx if attr in {"x", "w", "width"} else sy
                geometry[attr] = round(value * scale, 2)
            if geometry:
                adapted[element["key"]] = geometry
        return adapted


class PreserveEngineLayout:
    """Preserve all authored geometry and let the theme engine evaluate expressions."""

    name = "preserve-engine"

    def layout(
        self,
        elements: list[dict[str, Any]],
        source_profile: dict[str, Any],
        target_profile: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        return {}


def load_algorithm(spec: str) -> LayoutAlgorithm:
    """Load built-in class or ``python.module:Class`` / ``file.py:Class``."""
    if spec in {"demo", "normalized-stretch-demo"}:
        return NormalizedStretchDemo()
    if spec in {"preserve", "preserve-engine"}:
        return PreserveEngineLayout()
    if ":" not in spec:
        raise ValueError("算法写法应为 demo 或 module.path:ClassName / file.py:ClassName")
    module_spec, class_name = spec.rsplit(":", 1)
    if module_spec.endswith(".py") or "/" in module_spec or "\\" in module_spec:
        path = Path(module_spec).resolve()
        if not path.is_file():
            raise ValueError(f"找不到算法模块：{path}")
        loader_spec = importlib.util.spec_from_file_location("multires_user_algorithm", path)
        if loader_spec is None or loader_spec.loader is None:
            raise ValueError(f"无法加载算法模块：{path}")
        module = importlib.util.module_from_spec(loader_spec)
        loader_spec.loader.exec_module(module)
    else:
        from multires_app.compat import resolve_module

        module = importlib.import_module(resolve_module(module_spec))
    cls = getattr(module, class_name, None)
    if cls is None:
        raise ValueError(f"算法类不存在：{class_name}")
    algorithm = cls()
    if not callable(getattr(algorithm, "layout", None)):
        raise ValueError("算法类必须实现 layout(elements, source_profile, target_profile, context)")
    return algorithm


def make_layout_plan(
    analysis: dict[str, Any],
    source_profile: dict[str, Any],
    profiles: dict[str, Any],
    algorithm: LayoutAlgorithm,
) -> dict[str, Any]:
    elements = [element for file_doc in analysis["files"] for element in file_doc["elements"]]
    scenes = [scene for file_doc in analysis["files"] for scene in file_doc["scenes"]]
    devices = []
    for base_profile in profiles.get("profiles", []):
        base_resolution = base_profile.get("resolutionPx")
        orientations = base_profile.get("orientations", ["portrait", "landscape"])
        base_orientation = base_profile.get("baseOrientation")
        if (
            not base_orientation
            and base_resolution
            and base_resolution.get("width")
            and base_resolution.get("height")
        ):
            base_orientation = (
                "landscape" if base_resolution["width"] > base_resolution["height"] else "portrait"
            )
        base_orientation = base_orientation or "portrait"
        for orientation in orientations:
            profile = copy.deepcopy(base_profile)
            profile["baseProfileId"] = base_profile.get("id")
            profile["id"] = f"{base_profile.get('id', 'profile')}-{orientation}"
            profile["orientation"] = orientation
            if (
                not base_resolution
                or not base_resolution.get("width")
                or not base_resolution.get("height")
            ):
                devices.append(
                    {
                        "profile": profile,
                        "status": "needs-measurement",
                        "elements": [],
                        "patches": [],
                        "scenes": scenes,
                        "warnings": ["目标分辨率未填写，跳过布局计算。"],
                    }
                )
                continue
            width, height = base_resolution["width"], base_resolution["height"]
            if orientation != base_orientation:
                width, height = height, width
            profile["resolutionPx"] = {"width": width, "height": height}
            profile["baseResolutionPx"] = dict(base_resolution)
            mapping = algorithm.layout(
                elements, source_profile, profile, {"analysis": analysis, "scenes": scenes}
            )
            if not isinstance(mapping, dict):
                raise ValueError(
                    "布局算法 layout() 必须返回 {element_key: {attribute: value}} 字典"
                )
            output_elements = []
            patches = []
            warnings = []
            for element in elements:
                attrs = element.get("attributes", {})
                rect = mapping.get(element["key"], {})
                source_rect = {
                    key: attrs[key]
                    for key in ("x", "y", "w", "h", "width", "height")
                    if key in attrs
                }
                if not rect and getattr(algorithm, "name", "") != "preserve-engine":
                    warnings.append(f"{element['key']}: 算法未返回几何数据，预览沿用原坐标/跳过。")
                output_elements.append(
                    {
                        "key": element["key"],
                        "tag": element["tag"],
                        "file": element["file"],
                        "path": element["path"],
                        "attributes": attrs,
                        "sceneRefs": element.get("sceneRefs", []),
                        "layoutDecision": element.get("layoutDecision"),
                        "sourceRect": source_rect,
                        "rect": rect,
                    }
                )
                if rect:
                    selector: dict[str, str] = {"tag": element["tag"], "path": element["path"]}
                    for ident in ("id", "name"):
                        if attrs.get(ident):
                            selector.pop("path", None)
                            selector[ident] = attrs[ident]
                            break
                    patches.append({"selector": selector, "set": rect})
            devices.append(
                {
                    "profile": profile,
                    "status": "ready",
                    "elements": output_elements,
                    "scenes": scenes,
                    "patches": patches,
                    "warnings": warnings,
                }
            )
    result = {
        "formatVersion": 1,
        "analysisSource": analysis.get("source"),
        "algorithm": getattr(algorithm, "name", algorithm.__class__.__name__),
        "sourceProfile": source_profile,
        "devices": devices,
    }
    if getattr(algorithm, "name", "") == "preserve-engine":
        result["adaptationNote"] = (
            "保留主题脚本原有坐标与引擎表达式；预览只做原始设计稿映射，不代表鸿蒙引擎实机效果。"
        )
        for device in devices:
            if device["status"] == "ready":
                device["warnings"].append(
                    "当前算法不改写 XML 坐标；需在目标设备上验证主题引擎的 screen_width/screen_height 表达式。"
                )
    return result
