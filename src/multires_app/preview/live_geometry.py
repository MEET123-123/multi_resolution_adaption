"""Live preview canvas detection, slider geometry and hit selection."""

import math
from pathlib import Path
from multires_app.interaction.interaction_semantics import hit_test


def canvas_size(model, source):
    """Source logical canvas: script attributes first, packaged preview second."""
    root = model.get("root") or {}
    width = root.get("screenWidth")
    height = root.get("screenHeight")
    if width and height:
        return float(width), float(height)
    from PIL import Image
    import io
    import zipfile

    size = None
    if Path(source).is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if "preview_unlock_0" not in name.lower():
                    continue
                try:
                    with Image.open(io.BytesIO(archive.read(name))) as image:
                        size = image.size
                    break
                except Exception:
                    continue
    width = float(width or 1080)
    height = float(height or (width * size[1] / size[0] if size else width * 2))
    return width, height


def rect_of(attributes, runtime, prefix=""):
    """StartPoint/EndPoint rects may use negative sizes and expressions."""

    def number(key, default=0.0):
        return float(runtime.value(attributes.get(key, default), default, prefix + ":" + key))

    x, y = number("x"), number("y")
    w, h = abs(number("w")), abs(number("h"))
    return {"x": min(x, x + number("w")), "y": min(y, y + number("h")), "w": w, "h": h}


def inside(rect, point):
    return (
        rect["w"] > 0
        and rect["h"] > 0
        and rect["x"] <= point[0] <= rect["x"] + rect["w"]
        and rect["y"] <= point[1] <= rect["y"] + rect["h"]
    )


def subtree_ids(model, uid):
    """Every descendant of uid, in document order."""
    found, stack = [], list(reversed(model["nodes"][uid]["children"]))
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(reversed(model["nodes"][current]["children"]))
    return found


def slider_geometry(model, runtime, owner):
    """StartPoint anchor, drag axis, travel distance and EndPoint targets.

    Two families exist in shipping themes and they need different treatment:

    ``track``  StartPoint/EndPoint are static (or screen-relative), so the handle
               travels a fixed distance and ``#name.move_x/move_y/progress``
               describe the drag. The preview drives those variables.
    ``touch``  StartPoint/EndPoint are written in ``#touch_*``, i.e. the theme
               recomputes the track from the live finger. Faking a travel
               distance would fight the theme, so the preview instead feeds the
               real touch coordinates and fires the EndPoint's own ``up``
               Trigger on release.
    """
    node = model["nodes"][owner]
    if node["tag"] not in {"Slider", "Unlocker"}:
        return None
    children = [model["nodes"][child] for child in node["children"]]
    starts = [child for child in children if child["tag"] == "StartPoint"]
    ends = [child for child in children if child["tag"] == "EndPoint"]
    if not starts or not ends:
        return None
    start = starts[0]
    touch_driven = any(
        "touch_" in str(value)
        for child in (starts + ends)
        for value in child["attributes"].values()
    )
    anchor = rect_of(start["attributes"], runtime, start["id"])
    targets = [rect_of(end["attributes"], runtime, end["id"]) for end in ends]
    # Path lives inside EndPoint and defines the allowed travel direction.
    points = []
    for end in ends:
        path = next(
            (
                model["nodes"][child]
                for child in end["children"]
                if model["nodes"][child]["tag"] == "Path"
            ),
            None,
        )
        if not path:
            continue
        base_x = float(runtime.value(path["attributes"].get("x", 0), 0, path["id"] + ":x"))
        base_y = float(runtime.value(path["attributes"].get("y", 0), 0, path["id"] + ":y"))
        for child in path["children"]:
            item = model["nodes"][child]
            if item["tag"] != "Position":
                continue
            points.append(
                (
                    base_x + float(runtime.value(item["attributes"].get("x", 0), 0, child + ":x")),
                    base_y + float(runtime.value(item["attributes"].get("y", 0), 0, child + ":y")),
                )
            )
        if len(points) >= 2:
            break
    centre = (anchor["x"] + anchor["w"] / 2, anchor["y"] + anchor["h"] / 2)
    if len(points) < 2:
        target = targets[0]
        target_centre = (target["x"] + target["w"] / 2, target["y"] + target["h"] / 2)
        if touch_driven:
            points = [centre, target_centre]
        else:
            # Travel is measured from the StartPoint origin to the closest edge of
            # the EndPoint box, which is what the theme's own geometry implies.
            origin = (anchor["x"], anchor["y"])
            nearest = (
                min(max(origin[0], target["x"]), target["x"] + target["w"]),
                min(max(origin[1], target["y"]), target["y"] + target["h"]),
            )
            if math.hypot(nearest[0] - origin[0], nearest[1] - origin[1]) < 1e-6:
                nearest = target_centre
            points = [origin, nearest]
    dx, dy = points[-1][0] - points[0][0], points[-1][1] - points[0][1]
    length = math.hypot(dx, dy)
    direction = (dx / length, dy / length) if length > 1e-6 else (0.0, -1.0)
    screen = (runtime.system.get("screen_width", 1080), runtime.system.get("screen_height", 2160))
    if length < 1e-6 or (touch_driven and length < 32):
        # A touch-driven track has no static travel; use the screen extent so the
        # progress readout stays meaningful.
        length = max(screen)
    # A touch-driven track has no static grab area, so the whole screen is the
    # target; a static track exposes the StartPoint box.
    hit = {"x": 0.0, "y": 0.0, "w": screen[0], "h": screen[1]} if touch_driven else anchor
    return {
        "name": node["attributes"].get("name") or "slider",
        "anchor": (anchor["x"], anchor["y"]),
        "hit": hit,
        "direction": direction,
        "length": length,
        "targets": targets,
        "mode": "touch" if touch_driven else "track",
        "endTriggers": [
            trigger["id"]
            for trigger in model["triggers"]
            if trigger["owner"] in {end["id"] for end in ends}
        ],
        "bounce": (
            float(runtime.value(node["attributes"].get("bounceInitSpeed", 0), 0, owner + ":speed")),
            float(
                runtime.value(node["attributes"].get("bounceAcceleration", 0), 0, owner + ":accel")
            ),
        ),
        "startPoint": start["id"],
    }


def pick_owner(visible, x, y, model, viewport, gesture=None):
    """hit_test only ranks Buttons; Slider/Unlocker also own Triggers."""
    owner = hit_test(visible, x, y, model=model, viewport=viewport, gesture=gesture)
    if owner:
        return owner
    for element in reversed(visible):
        rect = element["rect"]
        if (
            element["tag"] in {"Slider", "Unlocker"}
            and element["visible"]
            and rect["w"] > 0
            and rect["h"] > 0
            and rect["x"] <= x <= rect["x"] + rect["w"]
            and rect["y"] <= y <= rect["y"] + rect["h"]
        ):
            return element["id"]
    return None
