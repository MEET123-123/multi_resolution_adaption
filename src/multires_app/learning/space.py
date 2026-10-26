"""Reproducible free-space candidates: occupancy + image contrast, not AI saliency."""

from PIL import Image, ImageFilter
from .contract import normalized


def scene_space(image_path, annotation, viewport, columns=8, rows=12):
    with Image.open(image_path) as image:
        gray = image.convert("L").resize((columns * 16, rows * 16))
        edges = gray.filter(ImageFilter.FIND_EDGES)
    elements = [
        e
        for e in annotation["elementLevel"]["elements"]
        if e["alpha"] > 0
        and e["tag"] not in {"Group", "Button"}
        and e["semantic"]["label"] not in {"background", "background.video"}
    ]
    region = {"w": viewport["width"], "h": viewport["height"]}
    boxes = [normalized(e["visible_bbox"] or e["script_bbox"], region) for e in elements]
    cells = []
    free = []
    for y in range(rows):
        line = []
        for x in range(columns):
            b = [x * 1000 / columns, y * 1000 / rows, 1000 / columns, 1000 / rows]
            overlaps = sum(
                max(0, min(b[0] + b[2], q[0] + q[2]) - max(b[0], q[0]))
                * max(0, min(b[1] + b[3], q[1] + q[3]) - max(b[1], q[1]))
                for q in boxes
            )
            occupancy = min(1.0, overlaps / (b[2] * b[3]))
            patch = edges.crop((x * 16, y * 16, (x + 1) * 16, (y + 1) * 16))
            contrast = sum(patch.getdata()) / (256 * 255)
            cells.append(
                {"cell": [x, y], "occupancy": round(occupancy, 4), "contrast": round(contrast, 4)}
            )
            line.append(occupancy < 0.1 and contrast < 0.12)
        free.append(line)
    # Merge horizontal runs and vertically adjacent identical spans.
    rectangles = []
    active = {}
    for y, line in enumerate(free):
        spans = []
        x = 0
        while x < columns:
            if not line[x]:
                x += 1
                continue
            start = x
            while x < columns and line[x]:
                x += 1
            spans.append((start, x))
        current = {}
        for span in spans:
            if span in active:
                r = active[span]
                r[3] += 1
            else:
                r = [span[0], y, span[1] - span[0], 1]
                rectangles.append(r)
            current[span] = r
        active = current
    candidates = []
    for i, (x, y, w, h) in enumerate(
        sorted(rectangles, key=lambda r: r[2] * r[3], reverse=True)[:8]
    ):
        b = [
            round(x * 1000 / columns, 2),
            round(y * 1000 / rows, 2),
            round(w * 1000 / columns, 2),
            round(h * 1000 / rows, 2),
        ]
        candidates.append(
            {
                "id": "free" + str(i),
                "bbox": b,
                "score": round(w * h / (columns * rows), 4),
                "origin": "grid occupancy + contrast proxy",
                "human_verified": False,
            }
        )
    protected = [
        {
            "id": e["id"],
            "bbox": normalized(e["visible_bbox"] or e["script_bbox"], region),
            "protection": "avoid_overlay",
            "human_verified": False,
            "label_source": e["semantic"]["origin"],
        }
        for e in elements
        if e["semantic"]["label"] in {"character", "face", "body"}
    ]
    return {
        "coordinate_system": "normalized_1000",
        "free_regions": candidates,
        "salient_regions": protected,
        "grid": cells,
        "saliency_status": "not_measured; contrast proxy only",
        "constraint_strength": "inferred",
        "frame_scope": "captured reference frame",
    }
