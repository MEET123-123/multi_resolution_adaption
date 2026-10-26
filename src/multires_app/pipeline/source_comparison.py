"""Unmodified source-phone evaluation and stable packed snapshots."""

import copy
from multires_app.interaction.interaction_semantics import replay_human_state

IDENTITY = {"sx": 1.0, "sy": 1.0, "tx": 0.0, "ty": 0.0}


def source_viewport(project):
    r = project["sourceProfile"]["resolutionPx"]
    w = float(project["model"]["root"].get("screenWidth", r["width"]))
    return {"width": w, "height": w * r["height"] / r["width"]}


def packed_snapshot(project, runtime, state, transforms=None):
    nodes = {uid: i for i, uid in enumerate(project["nodeOrder"])}
    assets = {uid: i for i, uid in enumerate(project["assetOrder"])}
    rows = []
    for e in runtime.snapshot(project["assets"], visible_only=True):
        if not e["visible"] or e["tag"] == "Group" or e["id"] not in nodes:
            continue
        r = e["rect"]
        rows.append(
            [
                nodes[e["id"]],
                *[round(r[k], 3) for k in ("x", "y", "w", "h")],
                round(e["alpha"], 4),
                assets.get(e["asset"], -1),
                e["text"],
                e["size"],
                e["asset"],
                e.get("media"),
            ]
        )
    controls = {
        name: runtime.lookup(name)
        for name in set(project["model"]["controls"])
        | set(project["graph"]["semantics"].get("guardVariables", []))
    }
    from multires_app.media.media_layout import reference

    result = {
        "state": state,
        "rows": rows,
        "transforms": copy.deepcopy(transforms or {}),
        "controlValues": controls,
        "overrides": copy.deepcopy(runtime.state),
        "timeMs": runtime.time_ms,
    }
    ref = reference(runtime, project["assets"], project["nodeOrder"], project["assetOrder"])
    if ref:
        result["mediaLayout"] = ref
    return result


def build_source_device(project):
    vp = source_viewport(project)
    cache = {}
    layouts = []
    project["model"]["assetNames"] = list(project["assets"])
    for state in project["graph"]["states"]:
        rt = replay_human_state(
            project["model"],
            vp["width"],
            vp["height"],
            state,
            project["assets"],
            verify_hit=True,
            cache=cache,
        )
        layouts.append(packed_snapshot(project, rt, state["id"]))
    return {
        "profile": {
            "id": "source-phone",
            "device": "原始直板机",
            "orientation": "portrait",
            "resolutionPx": project["sourceProfile"]["resolutionPx"],
        },
        "viewport": vp,
        "layouts": layouts,
    }
