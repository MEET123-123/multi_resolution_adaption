"""Convert native animation tracks to browser playback data."""

from multires_app.engine.scene_engine import resolve_asset

ANIM_TAGS = {
    "PositionAnimation": "pos",
    "AlphaAnimation": "alpha",
    "SizeAnimation": "size",
    "SourcesAnimation": "frames",
}


def frame_list(model, node):
    return [model["nodes"][child]["attributes"] for child in node["children"]]


def tracks_for(model, runtime, uid, assets=None):
    """Animation keyframes resolved at the current state, for client-side playback."""
    tracks = []
    for child in model["nodes"][uid]["children"]:
        node = model["nodes"][child]
        kind = ANIM_TAGS.get(node["tag"])
        if not kind:
            continue
        attributes = node["attributes"]
        raw = frame_list(model, node)
        if not raw:
            continue
        keyframes = []
        for item in raw:
            entry = {"t": float(runtime.value(item.get("time", 0), 0, child + ":time"))}
            if kind == "pos":
                entry["x"] = float(runtime.value(item.get("x", 0), 0, child + ":x"))
                entry["y"] = float(runtime.value(item.get("y", 0), 0, child + ":y"))
            elif kind == "alpha":
                entry["a"] = float(runtime.value(item.get("a", 255), 255, child + ":a"))
            elif kind == "size":
                entry["w"] = float(runtime.value(item.get("w", 0), 0, child + ":w"))
                entry["h"] = float(runtime.value(item.get("h", 0), 0, child + ":h"))
            else:
                entry["src"] = item.get("src", "")
                entry["url"] = (assets or {}).get(resolve_asset(model, entry["src"]), {}).get("url")
                entry["x"] = float(runtime.value(item.get("x", 0), 0, child + ":x"))
                entry["y"] = float(runtime.value(item.get("y", 0), 0, child + ":y"))
            keyframes.append(entry)
        keyframes.sort(key=lambda item: item["t"])
        duration = keyframes[-1]["t"]
        repeat = attributes.get("repeat")
        loop = attributes.get("loop", "false" if repeat == "1" else "true").lower() != "false"
        tracks.append(
            {
                "kind": kind,
                "delay": float(runtime.value(attributes.get("delay", 0), 0, child + ":delay")),
                "duration": duration,
                "loop": loop,
                "keyframes": keyframes,
            }
        )
    return tracks
