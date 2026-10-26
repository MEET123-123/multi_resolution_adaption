"""Explicit source canvas inputs, shared by preview and native XML export.

Layout can change without changing which original script branch runs. Native
export substitutes only screen dimension references with constant input aliases;
commands, predicates, thresholds and variable names otherwise remain identical.
"""

import hashlib
import math
import re

TOKENS = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|#([A-Za-z_]\w*)")
EXPRESSION_FIELDS = {
    "expression",
    "condition",
    "visibility",
    "x",
    "y",
    "w",
    "h",
    "width",
    "height",
    "size",
    "space",
    "alpha",
    "srcid",
    "srcExp",
    "textExp",
    "string",
    "value",
    "time",
    "delay",
    "play",
    "angle",
    "centerX",
    "centerY",
    "rotation",
    "scale",
    "scaleX",
    "scaleY",
}


def source_canvas(model):
    policy = model.get("inputs", {}).get("interaction", {}).get("coordinateSpace", "target")
    if policy not in {"source", "target"}:
        raise ValueError("coordinateSpace must be source or target")
    if policy == "target":
        return None
    canvas = model.get("layoutSourceViewport", {})
    if not all(
        isinstance(canvas.get(k), (int, float)) and math.isfinite(canvas[k]) and canvas[k] > 0
        for k in ("width", "height")
    ):
        raise ValueError("source coordinate space requires a finite positive layoutSourceViewport")
    return canvas


def replace_references(expression, mapping):
    # A literal such as '#screen_height.png' is a resource name, not an input.
    return TOKENS.sub(lambda m: "#" + mapping[m[1]] if m[1] in mapping else m[0], expression)


def adapt_xml_inputs(root, model):
    from xml.etree import ElementTree as ET

    canvas = source_canvas(model)
    if canvas is None:
        return {}
    digest = hashlib.sha1(repr(sorted(canvas.items())).encode()).hexdigest()[:12]
    aliases = {f"screen_{axis}": f"mr_{digest}_{axis}" for axis in ("width", "height")}
    if set(aliases.values()) & set(model["variables"]):
        raise ValueError("Source canvas alias conflicts with original variable")
    for node in root.iter():
        for key, value in list(node.attrib.items()):
            if key in EXPRESSION_FIELDS:
                node.set(key, replace_references(value, aliases))
    for axis in ("width", "height"):
        root.insert(
            0,
            ET.Element(
                "Var", {"name": aliases["screen_" + axis], "expression": str(float(canvas[axis]))}
            ),
        )
    return {"policy": "source", "viewport": dict(canvas), "aliases": aliases}
