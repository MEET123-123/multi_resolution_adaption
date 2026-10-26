"""Compile reachable scene controls into native transform expressions."""

import hashlib
import json
import math
from collections import defaultdict


def number(value):
    return format(float(value), ".8g")


def state_guard(project, state, device):
    cache = project.setdefault("_stateGuards", {})
    cache_key = device["profile"]["id"]
    if cache_key in cache:
        return cache[cache_key][state["id"]]
    states = [{"id": l["state"], "controlValues": l["controlValues"]} for l in device["layouts"]]
    # Use all changing controls, so unknown/unsampled states fall through.
    relevant = (
        project["graph"].get("semantics", {}).get("behaviorControls", project["model"]["controls"])
    )
    relevant = list(
        dict.fromkeys(relevant + project["graph"].get("semantics", {}).get("guardVariables", []))
    )
    from multires_app.media.media_layout import playback_guard_names

    # Playback phase may select content, but must never select a new affine.
    # Human page/style controls remain in these guards.
    phase_names = playback_guard_names(project["model"])
    relevant = [name for name in relevant if name not in phase_names]
    varying = [
        name
        for name in relevant
        if len({json.dumps(s["controlValues"].get(name), sort_keys=True) for s in states}) > 1
    ]
    guards = {}
    for entry in states:
        terms = []
        for name in varying:
            value = entry["controlValues"].get(name, 0)
            if isinstance(value, (int, float)) and math.isfinite(value):
                literal = number(value)
            elif isinstance(value, str) and "'" not in value:
                literal = "'" + value + "'"
            else:
                continue
            terms.append(
                f"lt(abs(#{name}-({literal})),0.00001)"
                if isinstance(value, float) and not value.is_integer()
                else f"eq(#{name},{literal})"
            )
        expression = "*".join(terms) or "1"
        name = "mr_state_" + hashlib.sha1(expression.encode()).hexdigest()[:12]
        project.setdefault("_stateGuardExpressions", {})[name] = expression
        guards[entry["id"]] = "#" + name
    cache[cache_key] = guards
    return guards[state["id"]]


def compiled_transform(project, device, cid, field):
    states = {s["id"]: s for s in project["graph"]["states"]}
    fallback = {"sx": 1, "sy": 1, "tx": 0, "ty": 0}[field]
    variants = defaultdict(list)
    guard_values = {}
    for layout in device["layouts"]:
        if cid not in layout["transforms"]:
            inactive = layout.get("mediaLayout", {}).get("inactiveSceneNodeIndices", [])
            if not any(
                project["nodeComponents"].get(project["nodeOrder"][i]) == cid for i in inactive
            ):
                continue
            value = number(fallback)
        else:
            value = number(layout["transforms"][cid][field])
        guard = state_guard(project, states[layout["state"]], device)
        if guard in guard_values and guard_values[guard] != value:
            raise ValueError(
                "同一原生状态条件存在不同布局："
                + cid
                + " / "
                + field
                + "；请使用 matching-components 或增加可区分的交互变量"
            )
        guard_values[guard] = value
        variants[value].append(guard)
    if not variants:
        return number(fallback)
    if len(variants) == 1:
        return next(iter(variants))
    args = []
    for value, guards in variants.items():
        args.extend(["(" + ")+(".join(sorted(set(guards))) + ")", value])
    return "ifelse(" + ",".join(args + [number(fallback)]) + ")"
