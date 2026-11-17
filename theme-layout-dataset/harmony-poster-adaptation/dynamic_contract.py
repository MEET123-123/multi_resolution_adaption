"""Source-only dynamic group contracts derived from evaluated XML and glyph metrics."""
from collections import defaultdict
from multires_app.preview.paint_visibility import font

DYNAMIC_TAGS = {"Time": "clock", "DateTime": "datetime", "Date": "date",
                "CountDownTime": "countdown"}
DYNAMIC_ROLES = {"clock", "date", "datetime", "battery", "countdown"}


def dynamic_group_contract(request, evidence):
    annotations = evidence["annotations"]
    tree = {g["id"]: g["members"] for g in annotations["compositionLevel"]["groups"]}
    roles = defaultdict(list)
    for e in annotations["elementLevel"]["elements"]:
        semantic = e.get("semantic", {}).get("label", "").split(".")[0]
        kind = DYNAMIC_TAGS.get(e["tag"], semantic)
        if kind in DYNAMIC_ROLES and e["tag"] in {*DYNAMIC_TAGS, "Text", "Number", "ImageSeries", "Image"}:
            roles[e["component"]].append((kind, e))

    def entries(gid):
        if gid not in tree:
            return roles[gid]
        return [entry for member in tree[gid] for entry in entries(member)]

    viewport = request["sourceViewport"]
    result = {}
    for group in request["groups"]:
        gid = group["id"]
        leaves = entries(evidence["groupMap"][gid])
        if not leaves:
            continue
        widths = []
        for _, e in leaves:
            text = e.get("text", "")
            raw_size = str(e["attributes"].get("size", ""))
            if text and raw_size.replace(".", "", 1).isdigit():
                widths.append(font(float(raw_size)).getlength(text))
        boxes = [e["sourceBox"] for _, e in leaves]
        left = min(b["x"] for b in boxes)
        right = max(b["x"] + b["w"] for b in boxes)
        top = min(b["y"] for b in boxes)
        bottom = max(b["y"] + b["h"] for b in boxes)
        result[gid] = {"roles": sorted({role for role, _ in leaves}),
                       "sourceInkOrGlyphExtent": [round((right - left) / viewport["width"], 4),
                                                   round((bottom - top) / viewport["height"], 4)],
                       "maxTextAdvance": round(max(widths, default=0) / viewport["width"], 4)
                       if widths else None,
                       "digitLayers": sum(e["tag"] in ("Time", "ImageSeries") for _, e in leaves)}
    return result


def semantic_dependencies(model):
    """Close XML variable dependencies so indirect bitmap clocks stay protected."""
    import re

    direct = {
        name: set(re.findall(r"#([\w]+)", var.get("attributes", {}).get("expression", "")))
        for name, var in model.get("variables", {}).items()
    }
    for name in direct:
        resolved = set(direct[name])
        unseen = list(resolved)
        while unseen:
            ref = unseen.pop()
            more = direct.get(ref, set()) - resolved
            resolved.update(more)
            unseen.extend(more)
        direct[name] = resolved
    return direct
