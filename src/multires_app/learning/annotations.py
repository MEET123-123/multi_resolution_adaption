"""Three evidence levels, keeping exact script facts distinct from inference."""

import re
from collections import defaultdict
from multires_app.pipeline.scene_clusters import box_union


def semantic(node, component, dependencies=None):
    attrs = node.get("attributes", {})
    tag = node.get("tag", "")
    if tag in {"Time", "DateTime", "Date"}:
        return {
            "label": {"Time": "clock", "DateTime": "datetime", "Date": "date"}[tag],
            "origin": "script tag",
            "confidence": 1.0,
        }
    refs = set(re.findall(r"#([\w]+)", " ".join(str(v) for v in attrs.values())))
    refs |= {dep for ref in list(refs) for dep in (dependencies or {}).get(ref, set())}
    for label, names in [
        ("clock.hour", {"hour", "hour12", "hour24"}),
        ("clock.minute", {"minute"}),
        ("date.month", {"month"}),
        ("date.weekday", {"day_of_week"}),
        ("date.day", {"date"}),
        ("weather.temperature", {"temperature", "weather_temperature"}),
    ]:
        if refs & names:
            return {"label": label, "origin": "xml variable dependency", "confidence": 0.98}
    text = " ".join(
        str(attrs.get(k, "")) for k in ("name", "id", "src", "srcid", "format", "text")
    ).lower()
    tests = [
        ("camera", r"camera|相机"),
        ("flashlight", r"flashlight|torch|手电"),
        ("settings", r"setting|设置"),
        ("weather", r"weather|天气|temperature"),
        ("date", r"date|日期|月份|星期"),
        ("clock", r"clock|time|hour|minute|时间|时钟"),
        ("battery", r"battery|电量"),
    ]
    for label, pattern in tests:
        if re.search(pattern, text):
            return {"label": label, "origin": "script-token heuristic", "confidence": 0.75}
    if component.get("semanticRole"):
        return {
            "label": component["semanticRole"],
            "origin": "existing component inference",
            "confidence": 0.8,
        }
    if tag in {"Button", "Text", "Video"}:
        return {
            "label": {"Button": "button", "Text": "text", "Video": "background.video"}[tag],
            "origin": "script tag",
            "confidence": 0.9 if tag == "Video" else 1.0,
        }
    return {
        "label": "background" if component.get("role") == "background" else "unknown",
        "origin": (
            "background component inference"
            if component.get("role") == "background"
            else "unresolved"
        ),
        "confidence": 0.7 if component.get("role") == "background" else 0.0,
    }


def extract(project, source_layout, plan, state):
    model = project["model"]
    definitions = {c["id"]: c for c in project["components"]}
    elements = []
    by_component = defaultdict(list)
    dependencies = {
        name: set(re.findall(r"#([\w]+)", var.get("attributes", {}).get("expression", "")))
        for name, var in model.get("variables", {}).items()
    }
    while True:
        expanded = {
            name: refs | {dep for ref in refs for dep in dependencies.get(ref, set())}
            for name, refs in dependencies.items()
        }
        if expanded == dependencies:
            break
        dependencies = expanded
    for number, row in enumerate(source_layout["rows"]):
        uid = project["nodeOrder"][row[0]]
        node = model["nodes"][uid]
        cid = project["nodeComponents"].get(uid)
        semantics = semantic(node, definitions.get(cid, {}), dependencies)
        asset = project["assetOrder"][row[6]] if row[6] >= 0 else None
        meta = project.get("assets", {}).get(asset, {})
        frame = dict(zip(("x", "y", "w", "h"), row[1:5]))
        visible = None
        if (
            meta.get("visualBounds")
            and min(meta.get("width", 0), meta.get("height", 0)) > 0
            and node.get("attributes", {}).get("scaleType") != "center_crop"
        ):
            x, y, w, h = meta["visualBounds"]
            visible = {
                "x": frame["x"] + x / meta["width"] * frame["w"],
                "y": frame["y"] + y / meta["height"] * frame["h"],
                "w": w / meta["width"] * frame["w"],
                "h": h / meta["height"] * frame["h"],
            }
        entry = {
            "id": uid + "@" + str(number),
            "source_node_id": uid,
            "component_identity": cid,
            "component": cid,
            "parent": node.get("parent"),
            "tag": node["tag"],
            "attributes": node.get("attributes", {}),
            "sourceBox": dict(zip(("x", "y", "w", "h"), row[1:5])),
            "script_bbox": frame,
            "visible_bbox": visible,
            "visible_bbox_reason": (
                "alpha metadata projection"
                if visible
                else "not measured; text/native/crop or missing alpha bounds"
            ),
            "alpha": row[5],
            "asset": asset,
            "image_path": meta.get("url"),
            "image_path_reason": (
                "resource reference"
                if asset
                else "dynamic/generated node or non-image interaction; no independent bitmap"
            ),
            "text": row[7],
            "semantic": semantics,
            "geometryOrigin": "script evaluated at captured interaction/time; not native screenshot",
        }
        elements.append(entry)
        if cid:
            by_component[cid].append(entry)
    components = []
    for cid, rows in by_component.items():
        components.append(
            {
                "id": cid,
                "component_identity": cid,
                "members": [r["id"] for r in rows],
                "semanticLabels": sorted({r["semantic"]["label"] for r in rows}),
                "groupRole": definitions.get(cid, {}).get("role"),
                "groupRoleOrigin": "inferred; needs review",
                "sourceBounds": box_union([r["sourceBox"] for r in rows if r["alpha"] > 0]),
            }
        )
    groups = [
        {
            k: g.get(k)
            for k in (
                "id",
                "members",
                "container",
                "anchor",
                "centerAxes",
                "screenAttachment",
                "confidence",
                "sourceBounds",
            )
        }
        for g in plan.get("groups", [])
    ]
    return {
        "schema": "multires-annotations/1",
        "elementLevel": {
            "origin": "exact script attributes + runtime snapshot",
            "elements": elements,
        },
        "semanticLevel": {
            "origin": "mixed; every semantic label has its own provenance",
            "components": components,
        },
        "compositionLevel": {
            "origin": "inferred relationships; not designer ground truth",
            "groups": groups,
            "alignments": plan.get("spatialAlignments", []),
            "distributions": plan.get("screenDistributions", []),
            "sceneKind": source_layout.get("layoutRequest", {}).get(
                "kind", plan.get("requestKind")
            ),
            "interactionPath": state.get("sourceTrace", state.get("trace", [])),
            "stateControls": source_layout.get("controlValues", {}),
            "timeMs": source_layout.get("timeMs"),
            "animationPolicy": "layout reference frame; preserve original transition timeline",
        },
        "unresolved": "tagless semantics, occlusion and higher intent need human review",
    }
