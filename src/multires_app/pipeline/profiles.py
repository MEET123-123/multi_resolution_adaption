"""Convert physical device profiles into manifest coordinate spaces."""

import copy
import math


def source_canvas_profile(model, profile):
    """Normalize physical source pixels to the manifest's coordinate system."""
    result = copy.deepcopy(profile)
    r = profile["resolutionPx"]
    width = float(model["root"].get("screenWidth", r["width"]))
    if not all(math.isfinite(float(v)) and float(v) > 0 for v in (width, r["width"], r["height"])):
        raise ValueError("源画布尺寸必须为有限正数")
    result["resolutionPx"] = {"width": width, "height": width * r["height"] / r["width"]}
    return result


def target_profiles(doc):
    for base in doc["profiles"]:
        r = base.get("resolutionPx")
        if not r or not r.get("width") or not r.get("height"):
            continue
        original = base.get(
            "baseOrientation", "landscape" if r["width"] > r["height"] else "portrait"
        )
        for orientation in base.get("orientations", ["portrait", "landscape"]):
            p = copy.deepcopy(base)
            p["id"] = base["id"] + "-" + orientation
            p["orientation"] = orientation
            if orientation != original:
                p["resolutionPx"] = {"width": r["height"], "height": r["width"]}
            yield p
