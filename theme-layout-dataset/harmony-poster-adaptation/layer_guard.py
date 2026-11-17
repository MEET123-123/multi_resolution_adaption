"""Compare protected alpha coverage using the original paint stack."""
from dynamic_contract import semantic_dependencies
from multires_app.evaluation.report import elements
from multires_app.learning.annotations import semantic
from multires_app.preview.paint_visibility import raster_snapshot, read_bitmap

PROTECTED = {"clock", "date", "datetime", "battery", "qr", "logo", "text"}


def protected_paint(project, device, layout, asset_dir):
    nodes = project["model"]["nodes"]
    layers = elements(project, layout)
    dependencies = semantic_dependencies(project["model"])
    tags = {e["key"]: semantic(nodes[e["id"]], {}, dependencies)["label"].split(".")[0] for e in layers}
    keys = {e["key"] for e in layers if ((tags[e["key"]] in PROTECTED or e["tag"] in {"Text", "Time", "DateTime"})
                                       and e["tag"] != "Button" and not (e["rect"]["w"] >= .8 * device["viewport"]["width"] and e["rect"]["h"] >= .8 * device["viewport"]["height"]))}
    if not keys:
        return {}
    try:
        _, report = raster_snapshot(layers, device["viewport"], asset_dir, max_size=(384, 384))
    finally:
        read_bitmap.cache_clear()
    return {key: v for key, v in report.items() if key in keys and v["supported"]}


def new_occlusion(before, after):
    issues = []
    for key, start in before.items():
        if key not in after or start["paintPixels"] < 3 or start["paintFraction"] < .20:
            continue
        end = after[key]
        loss = start["paintFraction"] - end["paintFraction"]
        clipped = start["clipFraction"] - end["clipFraction"]
        if loss > .12 or clipped > .10:
            issues.append({"rule": "PAINT.PROTECTED_VISIBILITY", "layer": key,
                           "newCoveredFraction": max(0., loss), "newClippedFraction": max(0., clipped)})
    return issues
