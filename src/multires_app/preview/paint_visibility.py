"""Offline raster evidence: account for alpha, screen clipping and later paint.

This is a comparison renderer, not a HarmonyOS implementation. Unsupported
native effects are explicitly reported by its caller.
"""

from collections import Counter
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


@lru_cache(maxsize=256)
def read_bitmap(path):
    with Image.open(path) as im:
        return im.convert("RGBA")


@lru_cache(maxsize=128)
def font(size):
    path = Path("C:/Windows/Fonts/msyh.ttc")
    return (
        ImageFont.truetype(str(path), max(6, int(size)))
        if path.exists()
        else ImageFont.load_default(size=max(6, int(size)))
    )


def runtime_elements(runtime, assets, viewport):
    from multires_app.engine.scene_engine import resolve_asset

    result = [
        e
        for e in runtime.snapshot(assets, visible_only=True)
        if e["visible"] and e["tag"] != "Group"
    ]
    # WaterWallpaper is native, but its authored bitmap is useful evidence. It
    # is inserted in original paint order on BOTH canvases, with no wave shader.
    for node in runtime.model["nodes"].values():
        if node["tag"] != "WaterWallpaper":
            continue
        attrs = node["attributes"]
        uid = node["id"]
        parent = uid
        shown = True
        while parent:
            n = runtime.model["nodes"][parent]
            shown = shown and bool(runtime.value(n["attributes"].get("visibility", 1)))
            parent = n["parent"]
        if not shown:
            continue
        src = runtime.value(attrs["srcExp"], "") if "srcExp" in attrs else attrs.get("src", "")
        result.append(
            {
                "id": uid,
                "tag": "WaterWallpaper",
                "name": "WaterWallpaper",
                "visible": True,
                "alpha": 1.0,
                "rect": {"x": 0, "y": 0, "w": viewport["width"], "h": viewport["height"]},
                "asset": resolve_asset(runtime.model, src),
                "text": "",
                "size": 0,
                "scaleType": "center_crop",
            }
        )
    order = {uid: i for i, uid in enumerate(runtime.model["nodes"])}
    result.sort(key=lambda e: order[e["id"]])
    counts = Counter()
    for e in result:
        e["key"] = e["id"] + "@" + str(counts[e["id"]])
        counts[e["id"]] += 1
        e["scaleType"] = e.get(
            "scaleType", runtime.model["nodes"][e["id"]]["attributes"].get("scaleType", "")
        )
    return result


def raster_snapshot(elements, viewport, asset_root, max_size=(1280, 800), background="#14212b"):
    scale = min(max_size[0] / viewport["width"], max_size[1] / viewport["height"])
    size = (max(1, round(viewport["width"] * scale)), max(1, round(viewport["height"] * scale)))
    canvas = Image.new("RGBA", size, background)
    layers = []
    evidence = {}
    for element in elements:
        b = element["rect"]
        key = element["key"]
        record = {
            "rect": b,
            "asset": element.get("asset", ""),
            "text": element.get("text", ""),
            "alpha": element["alpha"],
            "status": "outside",
            "paintFraction": 0.0,
            "clipFraction": 0.0,
            "paintPixels": 0.0,
            "supported": True,
        }
        evidence[key] = record
        x, y = round(b["x"] * scale), round(b["y"] * scale)
        w, h = round(b["w"] * scale), round(b["h"] * scale)
        if min(w, h) <= 0 or x >= size[0] or y >= size[1] or x + w <= 0 or y + h <= 0:
            continue
        if element["tag"] == "Button":
            record.update(status="hit-region", clipFraction=1.0)
            continue
        path = (asset_root / element.get("asset", "")).resolve()
        bitmap = None
        if (
            element.get("asset")
            and path.is_relative_to(asset_root.resolve())
            and path.is_file()
            and path.suffix.lower() not in {".mp4", ".webm", ".m4v"}
        ):
            original = read_bitmap(str(path))
            bitmap = (
                ImageOps.fit(original, (w, h), Image.Resampling.LANCZOS)
                if element.get("scaleType") == "center_crop"
                else original.resize((w, h), Image.Resampling.LANCZOS)
            )
        elif element["tag"] == "Rectangle":
            color = element.get("color", "#ffffff")
            color = (
                "#" + color[3:] + color[1:3] if color.startswith("#") and len(color) == 9 else color
            )
            try:
                bitmap = Image.new("RGBA", (w, h), color)
            except (ValueError, TypeError):
                record.update(status="unrendered-color-expression", supported=False)
                continue
        elif element["tag"] in {
            "Text",
            "Time",
            "Date",
            "DateTime",
            "Number",
            "CountDownTime",
        } and element.get("text"):
            bitmap = Image.new("RGBA", (w, h))
            color = element.get("color", "#ffffff")
            if color.startswith("#") and len(color) == 9:
                color = "#" + color[3:] + color[1:3]
            try:
                ImageDraw.Draw(bitmap).text(
                    (0, 0),
                    str(element["text"]),
                    font=font(element.get("size", 32) * scale),
                    fill=color,
                )
            except (ValueError, TypeError):
                record.update(status="unrendered-color-expression", supported=False)
                continue
        elif element["tag"] in {"Text", "Time", "Date", "DateTime", "Number", "CountDownTime"}:
            # An empty runtime string paints nothing; it is not an unsupported effect.
            record.update(status="transparent")
            continue
        if bitmap is None:
            record.update(status="unrendered", supported=False)
            continue
        alpha = bitmap.getchannel("A")
        if element["alpha"] < 1:
            alpha = alpha.point(lambda a: round(a * element["alpha"]))
            bitmap.putalpha(alpha)
        total = float(np.asarray(alpha, dtype=np.float32).sum() / 255)
        left, top = max(0, x), max(0, y)
        right, bottom = min(size[0], x + w), min(size[1], y + h)
        patch = bitmap.crop((left - x, top - y, right - x, bottom - y))
        mask = np.asarray(patch.getchannel("A"), dtype=np.float32) / 255
        record.update(
            status="transparent" if total <= 0.01 else "occluded",
            clipFraction=float(mask.sum() / max(total, 0.01)),
        )
        layers.append((key, left, top, mask, total))
        canvas.alpha_composite(patch, (left, top))
    remaining = np.ones((size[1], size[0]), dtype=np.float32)
    for key, x, y, mask, total in reversed(layers):
        region = remaining[y : y + mask.shape[0], x : x + mask.shape[1]]
        pixels = float((mask * region).sum())
        fraction = pixels / max(total, 0.01)
        evidence[key].update(paintPixels=pixels, paintFraction=fraction)
        if pixels >= 0.5 and fraction >= 0.002:
            evidence[key]["status"] = "visible"
        region *= 1 - mask
    return canvas, evidence
