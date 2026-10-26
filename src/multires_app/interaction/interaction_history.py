"""Concrete interaction history, independent of scene/layout equivalence.

The document is JSON data: checkpoints + reversible patches + a branch cursor.
No commands or external effects are executed when a checkpoint is restored.
"""

from __future__ import annotations

import copy
import hashlib
import json


def state_changes(before, after, path=()):
    """Missing and null differ. Lists are atomic; dictionary keys are path tokens."""
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            if key in before and key in after:
                result.extend(state_changes(before[key], after[key], (*path, key)))
            else:
                result.append(
                    {
                        "path": [*path, key],
                        "before": {
                            "exists": key in before,
                            **({"value": copy.deepcopy(before[key])} if key in before else {}),
                        },
                        "after": {
                            "exists": key in after,
                            **({"value": copy.deepcopy(after[key])} if key in after else {}),
                        },
                    }
                )
        return result
    if before == after and type(before) is type(after):
        return []
    return [
        {
            "path": list(path),
            "before": {"exists": True, "value": copy.deepcopy(before)},
            "after": {"exists": True, "value": copy.deepcopy(after)},
        }
    ]


def apply_changes(document, changes, reverse=False):
    result = copy.deepcopy(document)
    for change in reversed(changes) if reverse else changes:
        expected, replacement = (
            (change["after"], change["before"]) if reverse else (change["before"], change["after"])
        )
        path = change["path"]
        if not path:
            actual = {"exists": True, "value": result}
            parent = None
        else:
            parent = result
            for key in path[:-1]:
                parent = parent[key]
            actual = {"exists": path[-1] in parent}
            if actual["exists"]:
                actual["value"] = parent[path[-1]]
        if actual != expected:
            raise ValueError("History patch does not match state at " + repr(path))
        if not path:
            if not replacement["exists"]:
                raise ValueError("Cannot delete the document root")
            result = copy.deepcopy(replacement["value"])
        elif replacement["exists"]:
            parent[path[-1]] = copy.deepcopy(replacement["value"])
        else:
            del parent[path[-1]]
    return result


class InteractionHistory:
    """Keep all concrete events even when their rendered layouts are identical."""

    def __init__(self, initial, *, metadata=None, checkpoint_every=16):
        if checkpoint_every < 1:
            raise ValueError("checkpoint_every must be positive")
        self.metadata = copy.deepcopy(metadata or {})
        self.checkpoint_every = checkpoint_every
        self.cursor = "event-000000"
        self.records = [
            {
                "id": self.cursor,
                "parent": None,
                "depth": 0,
                "event": {"type": "initial"},
                "changes": [],
            }
        ]
        self.checkpoints = {self.cursor: copy.deepcopy(initial)}
        self._index = {self.cursor: self.records[0]}
        self._current = copy.deepcopy(initial)

    @property
    def current(self):
        return copy.deepcopy(self._current)

    @property
    def parent(self):
        return self._index[self.cursor]["parent"]

    def commit(self, event, after, *, commands=(), effects=()):
        # Validate JSON before changing the cursor; no partial commit on failure.
        json.dumps([event, after, commands, effects], ensure_ascii=False, allow_nan=False)
        node = {
            "id": f"event-{len(self.records):06d}",
            "parent": self.cursor,
            "depth": self._index[self.cursor]["depth"] + 1,
            "event": copy.deepcopy(event),
            "changes": state_changes(self._current, after),
            "commands": copy.deepcopy(list(commands)),
            "effects": copy.deepcopy(list(effects)),
        }
        self.records.append(node)
        self._index[node["id"]] = node
        self.cursor = node["id"]
        self._current = copy.deepcopy(after)
        if node["depth"] % self.checkpoint_every == 0:
            self.checkpoints[self.cursor] = self.current
        return copy.deepcopy(node)

    def checkout(self, event_id):
        if event_id not in self._index:
            raise ValueError("Unknown history event: " + str(event_id))
        path = []
        cursor = event_id
        while cursor not in self.checkpoints:
            node = self._index[cursor]
            path.append(node)
            cursor = node["parent"]
        value = copy.deepcopy(self.checkpoints[cursor])
        for node in reversed(path):
            value = apply_changes(value, node["changes"])
        self._current = value
        self.cursor = event_id
        return self.current

    def undo(self):
        parent = self._index[self.cursor]["parent"]
        return self.checkout(parent) if parent else self.current

    def children(self):
        return [
            {"id": n["id"], "event": copy.deepcopy(n["event"])}
            for n in self.records
            if n["parent"] == self.cursor
        ]

    def to_dict(self):
        return copy.deepcopy(
            {
                "schemaVersion": 1,
                "metadata": self.metadata,
                "checkpointEvery": self.checkpoint_every,
                "cursor": self.cursor,
                "records": self.records,
                "checkpoints": self.checkpoints,
            }
        )

    @classmethod
    def from_dict(cls, document):
        if document.get("schemaVersion") != 1:
            raise ValueError("Unsupported history schema")
        records = document["records"]
        if not records or records[0].get("parent") is not None:
            raise ValueError("History needs one initial root")
        root = records[0]["id"]
        history = cls(
            document["checkpoints"][root],
            metadata=document.get("metadata"),
            checkpoint_every=document["checkpointEvery"],
        )
        history.records = copy.deepcopy(records)
        history._index = {}
        for i, node in enumerate(history.records):
            if node["id"] != f"event-{i:06d}" or (i and node["parent"] not in history._index):
                raise ValueError("Duplicate, missing or unordered history parent")
            expected_depth = 0 if i == 0 else history._index[node["parent"]]["depth"] + 1
            if node["depth"] != expected_depth:
                raise ValueError("Invalid history depth")
            history._index[node["id"]] = node
        # Derive checkpoints from patches instead of trusting cached snapshots.
        history.checkpoints = {root: copy.deepcopy(document["checkpoints"][root])}
        for node in history.records[1:]:
            history.checkout(node["parent"])
            value = apply_changes(history._current, node["changes"])
            if node["depth"] % history.checkpoint_every == 0:
                history.checkpoints[node["id"]] = value
        history.checkout(document["cursor"])
        return history


def runtime_state(runtime):
    return copy.deepcopy(
        {
            "variables": runtime.state,
            "inputs": runtime.system,
            "timeMs": runtime.time_ms,
            "effects": runtime.events,
            "diagnostics": runtime.issues,
        }
    )


def needs_runtime_sync(recorded, current):
    # Rendering may add diagnostics between inputs; that is not a user event.
    # Keep those observations in the next snapshot, without an extra undo step.
    for change in state_changes(recorded, current):
        path = change["path"]
        if (
            len(path) >= 2
            and path[0] in {"runtime", "target", "source"}
            and path[1] == "diagnostics"
        ):
            continue
        return True
    return False


def restore_runtime(runtime, state):
    runtime.state = copy.deepcopy(state["variables"])
    runtime.system = copy.deepcopy(state["inputs"])
    runtime.time_ms = state["timeMs"]
    runtime.events = copy.deepcopy(state["effects"])
    runtime.issues = copy.deepcopy(state["diagnostics"])
    runtime.cache.clear()
    runtime.stack.clear()


def model_fingerprint(model):
    data = json.dumps(
        {k: model[k] for k in ("root", "nodes", "variables", "triggers")},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
