"""Run from the workspace: python samples/interaction_state_demo.py.

Third tap opens detail; detail opens settings; back preserves chosen settings.
Rules consume events and current data, rather than enumerating screen instances.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from multires_app.interaction.interaction_history import InteractionHistory, state_changes


def initial_state():
    return {"variables": {"persistent": {"motion": 1}, "session": {"tapCount": 0}},
            "navigation": [{"route": "home", "local": {"scrollY": 0}}],
            "gesture": {"phase": "idle", "owner": None}, "clockMs": 0}


def reduce_event(current, event):
    state = copy.deepcopy(current)
    time_ms = event["timeMs"]
    if time_ms < state["clockMs"]:
        raise ValueError("Events must have monotonic logical time")
    state["clockMs"] = time_ms
    route = state["navigation"][-1]["route"]
    kind = event["type"]
    if kind == "tap-character" and route == "home":
        state["variables"]["session"]["tapCount"] += 1
        if state["variables"]["session"]["tapCount"] % 3 == 0:
            state["navigation"].append({"route": "detail", "local": {"scrollY": 0}})
    elif kind == "open-settings" and route == "detail":
        state["navigation"].append({"route": "settings", "local": {"tab": "motion"}})
    elif kind == "set-motion" and route == "settings" and event.get("value") in {0, 1, 2}:
        state["variables"]["persistent"]["motion"] = event["value"]
    elif kind == "page-back" and len(state["navigation"]) > 1:
        state["navigation"].pop()  # No rollback of persistent/session variables.
    else:
        raise ValueError("Event is unavailable in the current route: " + kind)
    return state, [{"rule": kind, "changes": state_changes(current, state)}]


def dispatch(history, kind, **payload):
    event = {"type": kind, "timeMs": history.current["clockMs"] + 100, **payload}
    state, commands = reduce_event(history.current, event)
    return history.commit(event, state, commands=commands)


def run_demo():
    history = InteractionHistory(initial_state(), metadata={"example": "third-tap / nested-settings / back / undo / branch"}, checkpoint_every=4)
    steps = []
    for kind, payload in [("tap-character", {}), ("tap-character", {}), ("tap-character", {}),
                          ("open-settings", {}), ("set-motion", {"value": 0}), ("page-back", {}), ("page-back", {})]:
        node = dispatch(history, kind, **payload)
        state = history.current
        steps.append({"event": node["id"], "action": kind, "route": state["navigation"][-1]["route"],
                      "tapCount": state["variables"]["session"]["tapCount"], "motion": state["variables"]["persistent"]["motion"]})
    home = history.cursor
    history.undo()
    undo_state = history.current
    history.checkout("event-000004")  # Settings before the first choice.
    alternate = dispatch(history, "set-motion", value=2)
    history.checkout("event-000004")
    branches = history.children()
    history.checkout(home)
    return history, {"steps": steps, "undoAfterPageBack": undo_state,
                     "alternateBranch": alternate["id"], "branchesAtSettings": branches}


if __name__ == "__main__":
    history, summary = run_demo()
    output = ROOT / "samples" / "interaction-state-demo"
    output.mkdir(parents=True, exist_ok=True)
    (output / "interaction-history.json").write_text(json.dumps(history.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
