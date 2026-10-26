"""Detect raster cutout masks by reading alpha; never rewrite source bitmaps."""

from pathlib import Path
from PIL import Image
from multires_app.pipeline.scene_clusters import overlap


def inspect_alpha(assets, directory):
    for name, asset in assets.items():
        if "alphaGeometry" in asset or asset.get("kind") == "video":
            continue
        # Large layers are the ones that can occlude a rearranged scene.
        if asset["width"] < 500 or asset["height"] < 1000:
            continue
        path = (directory / "assets" / name).resolve()
        if not path.is_relative_to((directory / "assets").resolve()):
            raise ValueError("invalid asset path")
        with Image.open(path) as im:
            if "A" not in im.getbands() and "transparency" not in im.info:
                continue
            a = im.convert("RGBA").getchannel("A")
            hist = a.histogram()
            total = im.width * im.height
            hole = a.point(lambda v: 255 if v < 32 else 0).getbbox()
            asset["alphaGeometry"] = {
                "transparentFraction": sum(hist[:32]) / total,
                "opaqueFraction": sum(hist[224:]) / total,
                "hole": list(hole) if hole else None,
            }


def find_compositions(elements, assets, viewport):
    output = []
    canvas = viewport["width"] * viewport["height"]
    for i, mask in enumerate(elements):
        b = mask["rect"]
        asset = assets.get(mask.get("asset"), {})
        alpha = asset.get("alphaGeometry", {})
        if mask["tag"] != "Image" or not mask["visible"] or b["w"] * b["h"] < canvas * 0.8:
            continue
        if (
            not 0.02 < alpha.get("transparentFraction", 0) < 0.45
            or alpha.get("opaqueFraction", 0) < 0.55
        ):
            continue
        raw = alpha.get("hole")
        if not raw:
            continue
        hole = {
            "x": b["x"] + raw[0] / asset["width"] * b["w"],
            "y": b["y"] + raw[1] / asset["height"] * b["h"],
            "w": (raw[2] - raw[0]) / asset["width"] * b["w"],
            "h": (raw[3] - raw[1]) / asset["height"] * b["h"],
        }
        candidates = []
        for e in elements[:i]:
            q = e["rect"]
            area = q["w"] * q["h"]
            if e["tag"] == "Image" and e["visible"] and canvas * 0.03 < area < canvas * 0.65:
                covered = overlap(q, hole) / max(1, min(area, hole["w"] * hole["h"]))
                if covered > 0.5:
                    candidates.append((covered, e["id"]))
        if candidates:
            output.append(
                {
                    "mask": mask["id"],
                    "anchor": max(candidates)[1],
                    "reason": "large opaque layer with transparent cutout over preceding artwork",
                }
            )
    return output


def attach_compositions(components, relations, nodes):
    from multires_app.interaction.interaction_bindings import attach_components

    # Reuse rigid union logic; mask is a visual follower, not a grid-sized card.
    output = attach_components(
        components,
        {"bindings": [{"button": r["mask"], "anchors": [r["anchor"]]} for r in relations]},
        nodes,
    )
    masks = {r["mask"] for r in relations}
    for c in output:
        c["layoutFollowers"] = [uid for uid in c["members"] if uid in masks]
    return output
