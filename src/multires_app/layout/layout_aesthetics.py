"""Explainable, reference-aware layout proxies. These are not human ratings.

Independent implementations inspired by Ngo/Byrne, LayoutGAN++ and CGL-GAN;
no pretrained network or upstream source code is bundled.
"""

import math
import statistics

SOURCES = [
    {
        "name": "Ngo & Byrne, Another look at a model for evaluating interface aesthetics (2001)",
        "url": "https://eudml.org/doc/207518",
    },
    {
        "name": "LayoutGAN++ / constrained layout optimization (2021)",
        "url": "https://ktrk115.github.io/const_layout/",
    },
    {"name": "CGL-GAN (2022)", "url": "https://arxiv.org/html/2205.00303v3"},
    {"name": "LayoutRectifier (2025)", "url": "https://arxiv.org/html/2508.11177v1"},
]
DEFAULT_WEIGHTS = {
    "alignment": 2.0,
    "overlap": 3.0,
    "balance": 1.0,
    "symmetry": 0.5,
    "rhythm": 1.0,
    "density": 0.75,
    "hierarchy": 2.0,
    "readability": 1.5,
    "backgroundComplexity": 0.75,
    "order": 2.0,
}


def clip(v):
    return max(0.0, min(1.0, v))


def mean(v):
    return statistics.fmean(v) if v else 0.0


def area(b):
    return b["w"] * b["h"]


def center(b):
    return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2


def intersection(a, b):
    return max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])) * max(
        0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    )


def normalized(b, vp):
    return {k: b[k] / vp["width" if k in ("x", "w") else "height"] for k in ("x", "y", "w", "h")}


def edge(b, axis, t):
    return b[axis] + b["w" if axis == "x" else "h"] * t


def reference_relations(reference):
    """Only associate source-aligned objects; unrelated items are not attracted."""
    ids = sorted(reference)
    align = []
    mirror = []
    order = []
    rhythm = []
    for i, a in enumerate(ids):
        aa = reference[a]["box"]
        ac = center(aa)
        for b in ids[:i]:
            bb = reference[b]["box"]
            bc = center(bb)
            for axis in ("x", "y"):
                same_role = reference[a]["role"] == reference[b]["role"]
                centered = (
                    abs(ac[0 if axis == "x" else 1] - 0.5) < 0.015
                    and abs(bc[0 if axis == "x" else 1] - 0.5) < 0.015
                )
                if not (same_role or centered):
                    continue
                choices = [(abs(edge(aa, axis, t) - edge(bb, axis, t)), t) for t in (0.0, 0.5, 1.0)]
                error, t = min(choices)
                if error <= 0.012:
                    align.append((a, b, axis, t))
            if (
                reference[a]["role"] == reference[b]["role"]
                and abs(ac[0] + bc[0] - 1) < 0.035
                and abs(ac[1] - bc[1]) < 0.035
                and abs(math.log(area(aa) / area(bb))) < 0.3
            ):
                mirror.append((a, b))
            if min(aa["x"] + aa["w"], bb["x"] + bb["w"]) - max(aa["x"], bb["x"]) > 0.5 * min(
                aa["w"], bb["w"]
            ):
                if aa["y"] >= bb["y"] + bb["h"]:
                    order.append((b, a))
                elif bb["y"] >= aa["y"] + aa["h"]:
                    order.append((a, b))
    for role in sorted({e["role"] for e in reference.values()}):
        rows = []
        for cid in sorted(
            (i for i in ids if reference[i]["role"] == role),
            key=lambda i: center(reference[i]["box"])[1],
        ):
            cy = center(reference[cid]["box"])[1]
            row = next(
                (r for r in rows if abs(cy - center(reference[r[0]]["box"])[1]) < 0.02), None
            )
            if row is None:
                rows.append([cid])
            else:
                row.append(cid)
        for row in rows:
            if len(row) < 3:
                continue
            row.sort(key=lambda i: center(reference[i]["box"])[0])
            gaps = [
                center(reference[b]["box"])[0] - center(reference[a]["box"])[0]
                for a, b in zip(row, row[1:])
            ]
            if min(gaps) > 0 and statistics.pstdev(gaps) / mean(gaps) < 0.12:
                rhythm.append(row)
    return {"alignment": align, "symmetry": mirror, "order": order, "rhythm": rhythm}


def evaluate(items, reference, relations=None, density_ratio=1.0, weights=None):
    """Items are keyed by component id, with normalized box/role/fontPx fields."""
    ids = sorted(set(items) & set(reference))
    items = {i: items[i] for i in ids}
    reference = {i: reference[i] for i in ids}
    if not ids:
        return {"score": None, "losses": {}, "available": {}, "components": 0}
    relations = relations or reference_relations(reference)
    losses = {}
    available = {}
    errors = []
    for a, b, axis, t in relations["alignment"]:
        if a in items and b in items:
            errors.append(
                clip(abs(edge(items[a]["box"], axis, t) - edge(items[b]["box"], axis, t)) / 0.04)
            )
    losses["alignment"] = mean(errors)
    available["alignment"] = len(errors)
    errors = []
    for i, a in enumerate(ids):
        for b in ids[:i]:
            if intersection(reference[a]["box"], reference[b]["box"]) > 1e-8:
                continue
            if items[a]["role"] in {"popup", "overlay", "dialog"} or items[b]["role"] in {
                "popup",
                "overlay",
                "dialog",
            }:
                continue
            errors.append(
                clip(
                    intersection(items[a]["box"], items[b]["box"])
                    / min(area(items[a]["box"]), area(items[b]["box"]))
                )
            )
    losses["overlap"] = mean(errors)
    available["overlap"] = len(errors)

    def mass(data):
        total = sum(area(e["box"]) * e.get("alpha", 1.0) for e in data.values())
        return tuple(
            sum(
                center(e["box"])[axis] * area(e["box"]) * e.get("alpha", 1.0) for e in data.values()
            )
            / max(total, 1e-9)
            for axis in (0, 1)
        )

    actual = mass(items)
    original = mass(reference)
    losses["balance"] = clip(math.dist(actual, original) / 0.3)
    available["balance"] = len(ids)
    errors = [
        clip(abs(sum(center(items[c]["box"])[0] for c in pair) - 1) / 0.1)
        for pair in relations["symmetry"]
        if all(c in items for c in pair)
    ]
    losses["symmetry"] = mean(errors)
    available["symmetry"] = len(errors)
    errors = []
    for row in relations["rhythm"]:
        if not all(c in items for c in row):
            continue
        xs = [center(items[c]["box"])[0] for c in row]
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        errors.append(clip(statistics.pstdev(gaps) / max(mean(gaps), 1e-6)))
    losses["rhythm"] = mean(errors)
    available["rhythm"] = len(errors)
    original_density = sum(area(e["box"]) for e in reference.values())
    actual_density = sum(area(e["box"]) for e in items.values())
    expected = max(1e-8, original_density * density_ratio)
    losses["density"] = clip(abs(math.log(max(actual_density, 1e-8) / expected)) / 1.4)
    available["density"] = len(ids)
    ratios = [math.log(area(items[c]["box"]) / area(reference[c]["box"])) for c in ids]
    losses["hierarchy"] = clip(statistics.pstdev(ratios) / 0.7) if len(ratios) > 1 else 0.0
    available["hierarchy"] = len(ids) if len(ids) > 1 else 0
    errors = [
        1 - clip(items[c]["fontPx"] / reference[c]["fontPx"])
        for c in ids
        if items[c].get("fontPx", 0) > 0 and reference[c].get("fontPx", 0) > 0
    ]
    losses["readability"] = mean(errors)
    available["readability"] = len(errors)
    errors = [
        clip(e["backgroundComplexity"])
        for e in items.values()
        if e.get("backgroundComplexity") is not None
    ]
    losses["backgroundComplexity"] = mean(errors)
    available["backgroundComplexity"] = len(errors)
    errors = [
        float(center(items[a]["box"])[1] > center(items[b]["box"])[1])
        for a, b in relations["order"]
        if a in items and b in items
    ]
    losses["order"] = mean(errors)
    available["order"] = len(errors)
    effective = {
        k: float((weights or {}).get(k, w)) for k, w in DEFAULT_WEIGHTS.items() if available[k]
    }
    if any(not math.isfinite(w) or w < 0 for w in effective.values()) or not sum(
        effective.values()
    ):
        raise ValueError(
            "aesthetic weights must be finite, nonnegative and include an active metric"
        )
    loss = sum(losses[k] * w for k, w in effective.items()) / sum(effective.values())
    return {
        "score": round(100 * (1 - loss), 6),
        "losses": {k: round(v, 8) for k, v in losses.items()},
        "available": available,
        "components": len(ids),
        "density": round(actual_density, 6),
        "equilibriumDiagnostic": round(math.dist(actual, (0.5, 0.5)), 6),
        "method": "reference-aware-geometric-proxy-v1",
        "humanCalibrated": False,
    }
