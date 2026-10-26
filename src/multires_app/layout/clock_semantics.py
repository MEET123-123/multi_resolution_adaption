"""Bitmap clock digits remain layout content even with entrance animations."""

from multires_app.layout.aesthetic_layout import TIME_INPUTS, TIME_TAGS
from multires_app.engine.scene_engine import REF


def promote_clock_components(project):
    model = project["model"]
    nodes = model["nodes"]
    timed = set(TIME_INPUTS)
    while True:
        expanded = timed | {
            name
            for name, node in model.get("variables", {}).items()
            if set(REF.findall(node["attributes"].get("expression", ""))) & timed
        }
        if expanded == timed:
            break
        timed = expanded

    def clock(uid):
        node = nodes[uid]
        a = node["attributes"]
        if node["tag"] in TIME_TAGS:
            return True
        if node["tag"] not in {"Image", "ImageSeries", "Text", "Number", "ImageNumber"}:
            return False
        return any(
            set(REF.findall(a.get(k, ""))) & timed for k in ("srcid", "srcExp", "textExp", "string")
        )

    changed = []
    for component in project["components"]:
        leaves = [
            uid for uid in component["members"] if nodes[uid]["tag"] not in {"Group", "Button"}
        ]
        if component.get("layout") or not leaves or not all(clock(uid) for uid in leaves):
            continue
        if component["role"] == "decoration":
            component["role"] = "header"
            component["roleInference"] = (
                "clock content expressions outrank decorative entrance animation"
            )
            changed.append(component["id"])
        if component["role"] not in {"background", "gesture-layer", "interaction-layer"}:
            component["semanticRole"] = "datetime"
    return changed
