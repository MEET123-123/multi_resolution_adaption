"""Shared rectangle operations; no runtime or pipeline dependencies."""


def union(rects):
    rects = [r for r in rects if r["w"] > 0 and r["h"] > 0]
    if not rects:
        return {"x": 0, "y": 0, "w": 0, "h": 0}
    x = min(r["x"] for r in rects)
    y = min(r["y"] for r in rects)
    return {
        "x": x,
        "y": y,
        "w": max(r["x"] + r["w"] for r in rects) - x,
        "h": max(r["y"] + r["h"] for r in rects) - y,
    }
