"""Stateful preview session; independent of HTTP and browser page serving."""

from __future__ import annotations
import copy
import datetime
import math
import threading
import time
from pathlib import Path
from multires_app.engine.scene_engine import extract_assets, load_model
from multires_app.interaction.interaction_semantics import hit_test, initialize, perform_action
from multires_app.preview.live_animation import tracks_for
from multires_app.preview.live_geometry import (
    canvas_size,
    inside,
    pick_owner,
    slider_geometry,
    subtree_ids,
)

GESTURES = {"tap", "swipe", "long-press", "double-tap"}


class LiveSession:
    def __init__(self, source: Path, output: Path, filename=None):
        self.source = Path(source)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.name = filename or self.source.name
        self.revision = 0
        self.lock = threading.Lock()
        self.issues = {}
        self.history = []
        self.load()

    def load(self):
        self.model = load_model(self.source)
        self.width, self.height = canvas_size(self.model, self.source)
        self.assets = extract_assets(self.source, self.model, self.output)
        self.reset()

    def reset(self):
        self.runtime = initialize(self.model, self.width, self.height)
        self.base_time = self.runtime.time_ms
        self.epoch = time.monotonic()
        self.issues = dict(self.runtime.issues)
        self.history = []
        self.drag = None
        self.unlocked = False
        self.revision += 1

    def advance(self):
        """Wall clock drives the engine clock so loops and clocks stay live."""
        elapsed = (time.monotonic() - self.epoch) * 1000
        self.runtime.time_ms = self.base_time + elapsed
        now = datetime.datetime.now()
        hour24, minute, second = now.hour, now.minute, now.second
        self.runtime.system.update(
            time=self.runtime.time_ms,
            hour=hour24 % 12 or 12,
            hour24=hour24,
            minute=minute,
            second=second,
            year=now.year,
            month=now.month,
            date=now.day,
            day_of_week=(now.weekday() + 1) % 7,
            **{
                "system.time.hour1": hour24 // 10,
                "system.time.hour2": hour24 % 10,
                "system.time.min1": minute // 10,
                "system.time.min2": minute % 10,
                "system.time.sec1": second // 10,
                "system.time.sec2": second % 10,
            },
        )
        for key in (
            "system.time.hour1",
            "system.time.hour2",
            "system.time.min1",
            "system.time.min2",
        ):
            self.issues.pop("input:" + key, None)
        self.runtime.cache.clear()
        self.step_rebound()

    # ---- Slider / Unlocker drag -------------------------------------------------
    def slider_at(self, x, y, visible=None):
        """Grab area is the StartPoint; the track rect is the fallback.

        Touch-driven sliders claim the whole screen, so a static track always
        wins when both are under the finger.
        """
        if visible is None:
            visible = self.runtime.snapshot(self.assets, visible_only=True, slider_states=True)
        rects = {item["id"]: item["rect"] for item in visible}
        found = []
        for uid, node in self.model["nodes"].items():
            if node["tag"] not in {"Slider", "Unlocker"}:
                continue
            try:
                geometry = slider_geometry(self.model, self.runtime, uid)
            except Exception:
                continue
            if not geometry:
                continue
            if inside(geometry["hit"], (x, y)) or (rects.get(uid) and inside(rects[uid], (x, y))):
                found.append((geometry["mode"] != "track", uid))
        return min(found)[1] if found else None

    def apply_drag(self):
        if not self.drag or self.drag["mode"] != "track":
            return
        name = self.drag["name"]
        ox, oy = self.drag["offset"]
        self.runtime.state[name + ".move_x"] = ox
        self.runtime.state[name + ".move_y"] = oy
        self.runtime.state[name + ".progress"] = self.drag["progress"]
        self.runtime.cache.clear()

    def settle_drag(self, name, offset=(0.0, 0.0), progress=0.0):
        """Clear the injected move_* variables when a drag ends or snaps back."""
        self.runtime.state[name + ".move_x"] = offset[0]
        self.runtime.state[name + ".move_y"] = offset[1]
        self.runtime.state[name + ".progress"] = progress
        self.runtime.cache.clear()

    def drag_offset_at(self, x, y):
        drag = self.drag
        dx, dy = x - drag["press"][0], y - drag["press"][1]
        along = dx * drag["direction"][0] + dy * drag["direction"][1]
        along = max(0.0, min(drag["length"], along))
        drag["progress"] = round(along / drag["length"], 5)
        drag["offset"] = (drag["direction"][0] * along, drag["direction"][1] * along)
        anchor = drag["anchor"]
        point = (anchor[0] + drag["offset"][0], anchor[1] + drag["offset"][1])
        drag["reached"] = any(inside(rect, point) for rect in drag["targets"])
        return drag["offset"]

    def step_rebound(self):
        drag = self.drag
        if not drag or not drag.get("released"):
            return
        v0, acceleration = drag["bounce"]
        elapsed = time.monotonic() - drag["released"]
        travelled = v0 * elapsed + 0.5 * acceleration * elapsed * elapsed
        remaining = drag["released_length"] - travelled
        if remaining <= 0.5 or elapsed > 4:
            self.settle_drag(drag["name"])
            self.drag = None
            return
        direction = drag["direction"]
        drag["offset"] = (direction[0] * remaining, direction[1] * remaining)
        drag["progress"] = round(remaining / drag["length"], 5)
        self.apply_drag()

    def press(self, body):
        self.advance()
        x, y = float(body.get("x", 0)), float(body.get("y", 0))
        visible = self.runtime.snapshot(self.assets, visible_only=True, slider_states=True)
        button = hit_test(
            visible, x, y, model=self.model, viewport={"width": self.width, "height": self.height}
        )
        return {"button": button, "slider": self.slider_at(x, y, visible), "point": [x, y]}

    def press_chains(self, x, y, visible):
        """down/up Triggers of every visible Button under the point.

        Themes in this family stack full-screen Buttons and split the gesture
        between them: one arms the slide on ``down``, another decides the unlock
        from ``#touch_begin_y - #touch_y`` on ``up``. Hit testing keeps only the
        topmost, so the preview dispatches to all of them, matching what the
        platform does for a touch that lands on overlapping Buttons.
        """
        owners = {
            item["id"]
            for item in visible
            if item["tag"] == "Button"
            and item["visible"]
            and item["rect"]["w"] > 0
            and item["rect"]["h"] > 0
            and item["rect"]["x"] <= x <= item["rect"]["x"] + item["rect"]["w"]
            and item["rect"]["y"] <= y <= item["rect"]["y"] + item["rect"]["h"]
        }
        found = {"down": [], "up": []}
        for trigger in self.model["triggers"]:
            if trigger["owner"] in owners and trigger["action"] in {"down", "up"}:
                found[trigger["action"]].append(trigger["id"])
        return found

    def drag_phase(self, body):
        phase = body.get("phase")
        x, y = float(body.get("x", 0)), float(body.get("y", 0))
        if phase == "start":
            self.advance()
            visible = self.runtime.snapshot(self.assets, visible_only=True, slider_states=True)
            owner = body.get("owner") or self.slider_at(x, y, visible)
            if not owner:
                return {"active": False, "message": "该位置没有滑动控件", "plan": self.plan()}
            geometry = slider_geometry(self.model, self.runtime, owner)
            if not geometry:
                return {
                    "active": False,
                    "message": "滑动控件缺少 StartPoint/EndPoint",
                    "plan": self.plan(),
                }
            chained = self.press_chains(x, y, visible)
            self.runtime.system.update(touch_begin_x=x, touch_begin_y=y, touch_x=x, touch_y=y)
            self.drag = {
                **geometry,
                "owner": owner,
                "press": (x, y),
                "offset": (0.0, 0.0),
                "progress": 0.0,
                "reached": False,
                "released": None,
                "released_length": 0.0,
                "follow": subtree_ids(self.model, geometry["startPoint"]),
                "chained": chained,
            }
            for trigger_id in chained["down"]:
                trigger = next(
                    (item for item in self.model["triggers"] if item["id"] == trigger_id), None
                )
                if trigger:
                    self.runtime.execute(
                        trigger,
                        {"touch_begin_x": x, "touch_begin_y": y, "touch_x": x, "touch_y": y},
                    )
            self.apply_drag()
            self.advance()
            return {
                "active": True,
                "owner": owner,
                "name": geometry["name"],
                "mode": geometry["mode"],
                "length": round(geometry["length"], 3),
                "direction": list(geometry["direction"]),
                "anchor": list(geometry["anchor"]),
                "follow": self.drag["follow"],
                "chained": {"down": len(chained["down"]), "up": len(chained["up"])},
                "plan": self.plan(),
            }
        if not self.drag:
            return {"active": False, "message": "没有进行中的拖动", "plan": self.plan()}
        if phase == "move":
            self.advance()
            self.runtime.system.update(touch_x=x, touch_y=y)
            if self.drag["mode"] == "track":
                self.drag_offset_at(x, y)
            else:
                self.drag["offset"] = (x - self.drag["press"][0], y - self.drag["press"][1])
                self.drag["progress"] = round(
                    max(0.0, min(self.drag["length"], math.hypot(*self.drag["offset"])))
                    / self.drag["length"],
                    5,
                )
            self.apply_drag()
            return {
                "active": True,
                "name": self.drag["name"],
                "mode": self.drag["mode"],
                "reached": self.drag["reached"],
                "progress": self.drag["progress"],
                "offset": [round(v, 3) for v in self.drag["offset"]],
                "plan": self.plan(),
            }
        if phase == "end":
            self.advance()
            drag = self.drag
            self.runtime.system.update(touch_x=x, touch_y=y)
            if drag["mode"] == "track":
                self.drag_offset_at(x, y)
            else:
                self.drag["offset"] = (x - drag["press"][0], y - drag["press"][1])
                self.drag["progress"] = round(
                    max(0.0, min(drag["length"], math.hypot(*drag["offset"]))) / drag["length"], 5
                )
            reached = drag["reached"]
            v0, acceleration = drag["bounce"]
            tag = self.model["nodes"][drag["owner"]]["tag"]
            rebounding = False
            executed = 0
            touch = {
                "touch_begin_x": drag["press"][0],
                "touch_begin_y": drag["press"][1],
                "touch_x": x,
                "touch_y": y,
            }
            self.runtime.events = []
            chained = drag["chained"]["up"] + (
                drag["endTriggers"] if (drag["mode"] == "touch" or (drag["mode"] == "track" and reached)) else []
            )
            if chained:
                before = {name: self.runtime.lookup(name) for name in self.model["variables"]}
                for trigger_id in chained:
                    trigger = next(
                        (item for item in self.model["triggers"] if item["id"] == trigger_id), None
                    )
                    if not trigger or trigger["action"] != "up":
                        continue
                    self.runtime.execute(trigger, touch)
                    executed += 1
                self.runtime.settle(before)
                if drag["mode"] == "touch":
                    # No static track, so the only honest signal is whether the
                    # theme actually changed state.
                    reached = (
                        sum(
                            1
                            for name, value in before.items()
                            if self.runtime.lookup(name) != value
                        )
                        > 0
                    )
            if drag["mode"] == "touch":
                self.drag = None
            elif reached and tag == "Unlocker":
                self.unlocked = True
                self.settle_drag(drag["name"], drag["offset"], drag["progress"])
                self.drag = None
                executed += 1
            elif reached and v0 > 0:
                # A page slider returns to its start after firing the target.
                self.settle_drag(drag["name"])
                self.drag = None
                executed += 1
            elif not reached and v0 > 0:
                drag["released"] = time.monotonic()
                drag["released_length"] = math.hypot(*drag["offset"])
                rebounding = True
            else:
                self.settle_drag(drag["name"], drag["offset"], drag["progress"])
                self.drag = None
            if any(
                event["tag"] == "ExternCommand" and event["attributes"].get("command") == "unlock"
                for event in self.runtime.events
            ):
                self.unlocked = True
            self.history.append(
                {
                    "kind": "drag",
                    "owner": drag["name"],
                    "executed": executed,
                    "at": datetime.datetime.now().strftime("%H:%M:%S"),
                }
            )
            self.revision += 1
            return {
                "active": False,
                "reached": reached if drag["mode"] == "track" else None,
                "rebounding": rebounding,
                "mode": drag["mode"],
                "unlocked": self.unlocked,
                "executed": executed,
                "plan": self.plan(),
            }
        raise ValueError("无效拖动阶段")

    def offset_for(self, uid):
        """StartPoint children follow the handle, per the Slider spec."""
        if not self.drag or self.drag["mode"] != "track":
            return None
        start = self.drag["startPoint"]
        node = self.model["nodes"].get(uid)
        while node is not None:
            if node["id"] == start:
                return self.drag["offset"]
            node = self.model["nodes"].get(node["parent"]) if node["parent"] else None
        return None

    def plan(self):
        self.advance()
        elements = []
        for item in self.runtime.snapshot(self.assets, visible_only=True, slider_states=True):
            if not item["visible"] or item["tag"] == "Group":
                continue
            rect = item["rect"]
            if rect["w"] <= 0 or rect["h"] <= 0:
                continue
            offset = self.offset_for(item["id"])
            if offset:
                rect = {**rect, "x": rect["x"] + offset[0], "y": rect["y"] + offset[1]}
            asset = item.get("asset")
            meta = self.assets.get(asset) if asset else None
            tracks = tracks_for(self.model, self.runtime, item["id"], self.assets)
            elements.append(
                {
                    "id": item["id"],
                    "tag": item["tag"],
                    "rect": {k: round(v, 3) for k, v in rect.items()},
                    "alpha": round(item["alpha"], 4),
                    "text": item.get("text") or "",
                    "size": item.get("size") or 0,
                    "color": item.get("color") or "#ffffff",
                    "name": item.get("name") or item["tag"],
                    "url": meta["url"] if meta else None,
                    "kind": (
                        meta["kind"]
                        if meta
                        else (
                            "text"
                            if item["tag"] in {"Text", "Time", "Date", "DateTime", "Number"}
                            else None
                        )
                    ),
                    "tracks": tracks,
                }
            )
        controls = [
            t
            for t in self.model["triggers"]
            if t["action"] in {"down", "up", "move", "long", "tap", "click", "double"}
        ]
        owners = sorted({t["owner"] for t in controls})
        self.issues.update(self.runtime.issues)
        drag = None
        if self.drag:
            drag = {
                "active": True,
                "name": self.drag["name"],
                "mode": self.drag["mode"],
                "progress": self.drag["progress"],
                "reached": self.drag["reached"],
                "rebounding": bool(self.drag.get("released")),
                "offset": [round(v, 3) for v in self.drag["offset"]],
                "direction": list(self.drag["direction"]),
                "length": round(self.drag["length"], 3),
                "follow": self.drag["follow"],
            }
        return {
            "revision": self.revision,
            "name": self.name,
            "source": str(self.source),
            "canvas": {"width": self.width, "height": self.height},
            "timeMs": self.runtime.time_ms,
            "elements": elements,
            "interactive": owners,
            "drag": drag,
            "unlocked": self.unlocked,
            "variables": {
                name: self.runtime.lookup(name) for name in list(self.model["variables"])[:40]
            },
            "diagnostics": [{"key": k, "message": v} for k, v in list(self.issues.items())[:60]],
            "counts": {
                "elements": len(elements),
                "triggers": len(self.model["triggers"]),
                "controls": len(controls),
                "variables": len(self.model["variables"]),
                "assets": len(self.assets),
            },
            "history": self.history[-12:],
        }

    def gesture(self, body):
        self.advance()
        kind = body.get("kind")
        if kind not in GESTURES:
            raise ValueError("无效手势类型")
        x = float(body.get("x", 0))
        y = float(body.get("y", 0))
        to_x = float(body.get("toX", x))
        to_y = float(body.get("toY", y))
        duration = float(body.get("durationMs", 0) or 0)
        visible = self.runtime.snapshot(self.assets, visible_only=True)
        dx, dy = to_x - x, to_y - y
        direction = (
            ("left" if dx < 0 else "right") if abs(dx) > abs(dy) else ("up" if dy < 0 else "down")
        )
        owner = pick_owner(
            visible,
            x,
            y,
            self.model,
            {"width": self.width, "height": self.height},
            gesture=direction if kind == "swipe" else None,
        )
        if not owner:
            return {"hit": False, "message": "该位置没有可响应控件", "plan": self.plan()}
        rect = next(item["rect"] for item in visible if item["id"] == owner)
        sample = [
            (to_x - rect["x"]) / max(rect["w"], 1e-6),
            (to_y - rect["y"]) / max(rect["h"], 1e-6),
            (x - rect["x"]) / max(rect["w"], 1e-6),
            (y - rect["y"]) / max(rect["h"], 1e-6),
        ]
        action = {"owner": owner, "kind": kind, "sample": sample, "durationMs": duration}
        before = copy.deepcopy(self.runtime.state)
        outcome = perform_action(self.runtime, action, self.assets, verify_hit=True)
        if not outcome.get("valid"):
            self.runtime.state = before
            self.runtime.cache.clear()
            return {
                "hit": False,
                "owner": owner,
                "message": outcome.get("reason", "手势未产生引擎事件"),
                "plan": self.plan(),
            }
        self.issues.update(self.runtime.issues)
        self.base_time = self.runtime.time_ms
        self.epoch = time.monotonic()
        self.revision += 1
        self.history.append(
            {
                "kind": kind,
                "owner": self.model["nodes"][owner]["attributes"].get("name")
                or self.model["nodes"][owner]["tag"],
                "executed": len(outcome.get("executed") or []),
                "at": datetime.datetime.now().strftime("%H:%M:%S"),
            }
        )
        return {
            "hit": True,
            "owner": owner,
            "executed": outcome.get("executed"),
            "timeline": outcome.get("timeline"),
            "plan": self.plan(),
        }
