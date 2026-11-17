"""Track concrete interaction and asset observations and select verified frontier paths."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json

from dynamic_contract import semantic_dependencies
from multires_app.engine.scene_engine import REF
from multires_app.interaction.interaction_semantics import explore_human, replay_human_state


def references(model):
    return {uid for uid, node in model["nodes"].items() if any(
        node["attributes"].get(key) for key in ("srcExp", "srcid")
    )}


def resource_variables(model):
    closures = semantic_dependencies(model)
    direct = {name for uid in references(model) for key in ("srcExp", "srcid")
              for name in REF.findall(model["nodes"][uid]["attributes"].get(key, ""))}
    return direct | {parent for name in direct for parent in closures.get(name, ())}


def measure(model, graph, assets, profile):
    viewport = profile["resolutionPx"]
    nodes = references(model)
    seen = defaultdict(set)
    missing = defaultdict(set)
    cache = {}
    for state in graph["states"]:
        runtime = replay_human_state(model, viewport["width"], viewport["height"], state, assets, cache=cache)
        for e in runtime.snapshot(assets, visible_only=True):
            if not e["visible"] or e["tag"] == "Group":
                continue
            # Runtime resolves authored path tokens and indexed variants through the engine.
            if e["asset"] in assets:
                seen[e["id"]].add(e["asset"])
            elif e["asset"]:
                missing[e["id"]].add(e["asset"])
    enabled = {t["id"] for t in model["triggers"]
               if t["action"] not in {"resume", "init", "pause", "variable-change"}}
    witnessed, resolved = set(), set()
    for edge in graph["transitions"]:
        triggers = set(edge.get("triggers", [edge["trigger"]])) & enabled
        witnessed.update(triggers)
        if edge.get("to") is not None:
            resolved.update(triggers)
    return {
        "interaction": {"eligible": len(enabled), "executed": len(witnessed),
                        "executedWithReachedDestination": len(resolved),
                        "executedTriggers": sorted(witnessed), "resolvedTriggers": sorted(resolved),
                        "unexecutedTriggers": sorted(enabled - witnessed)},
        "resources": {"packagedAssets": len(assets),
                      "observedAssets": len(set().union(*seen.values())),
                      "nodeAssetPairs": sum(map(len, seen.values())),
                      "dynamicNodes": len(nodes), "dynamicNodesResolved": len(nodes & seen.keys()),
                      "dynamicNodesUnobserved": sorted(nodes - seen.keys()),
                      "observedByNode": {k: sorted(v) for k, v in seen.items()},
                      "visibleUnresolvedByNode": {k: sorted(v) for k, v in missing.items()}},
    }


def next_traces(graph, coverage, resource_vars, max_depth, limit=3):
    """Use paths that really reached the sampled control before a bound stopped admission."""
    settled = set(coverage["interaction"]["resolvedTriggers"])
    states = {s["id"]: s for s in graph["states"]}
    options = []
    for edge in graph["transitions"]:
        if edge.get("to") is not None or not edge.get("bounded"):
            continue
        trace = [step["humanAction"] for step in states[edge["from"]]["trace"]]
        if len(trace) + 1 > max_depth:
            continue
        candidate = {"owner": edge["owner"], "kind": edge["action"], "gesture": edge["gesture"],
                     "sample": edge["sample"], "intent": edge["intent"]}
        priority = (len(set(edge["triggers"]) - settled),
                    len(edge.get("changes", {}).keys() & resource_vars), -len(trace))
        options.append((priority, candidate, trace + [candidate]))
    chosen = []
    owners = set()
    for _, candidate, trace in sorted(options, key=lambda item: item[0], reverse=True):
        if candidate["owner"] in owners:
            continue
        owners.add(candidate["owner"])
        digest = hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()[:12]
        chosen.append({"name": "coverage-" + digest, "actions": trace})
        if len(chosen) == limit:
            break
    return chosen


def explore_rounds(model, profile, assets, max_states, max_depth, max_gestures, rounds):
    original = deepcopy(model.get("inputs", {}))
    authored = original.get("interaction", {}).get("requiredTraces", [])
    variables = resource_variables(model)
    history, reserved = [], []
    witnessed_so_far, assets_so_far = set(), set()
    best = None
    for index in range(rounds):
        divisor = 2 ** (rounds - index - 1)
        suggested_minimum = 1 + sum(len(x["actions"]) for x in authored + reserved)
        state_cap = min(max_states, max(suggested_minimum, (max_states + divisor - 1) // divisor))
        depth_cap = (max_depth + divisor - 1) // divisor
        gesture_cap = (max_gestures + divisor - 1) // divisor
        current = deepcopy(original)
        options = current.setdefault("interaction", {})
        options.update(maxExplorationGestures=gesture_cap, requiredTraces=authored + reserved)
        model["inputs"] = current
        graph = explore_human(model, profile, assets, state_cap, depth_cap)
        coverage = measure(model, graph, assets, profile)
        inter, resource = coverage["interaction"], coverage["resources"]
        score = (inter["executedWithReachedDestination"], inter["executed"],
                 resource["dynamicNodesResolved"], resource["nodeAssetPairs"], len(graph["states"]))
        triggers = set(inter["resolvedTriggers"])
        pairs = {(node, asset) for node, items in resource["observedByNode"].items() for asset in items}
        history.append({"round": index + 1, "states": len(graph["states"]), "coverage": coverage,
                        "newlyReachedTriggers": len(triggers - witnessed_so_far),
                        "newlyObservedAssetPairs": len(pairs - assets_so_far),
                        "boundedTransitions": graph["coverage"]["boundedTransitions"],
                        "budgets": {"states": state_cap, "depth": depth_cap, "gestures": gesture_cap},
                        "guidedTraces": [x["name"] for x in reserved]})
        witnessed_so_far.update(triggers)
        assets_so_far.update(pairs)
        if best is None or score >= best[0]:
            best = score, graph, current, index + 1
        if not graph["coverage"]["truncated"]:
            break
        if index < rounds - 1:
            next_divisor = divisor // 2
            next_depth = (max_depth + next_divisor - 1) // next_divisor
            next_limit = min(3, max(0, ((max_states + next_divisor - 1) // next_divisor
                             - suggested_minimum) // max(1, next_depth)))
            if next_limit:
                existing = {x["name"] for x in authored + reserved}
                reserved += [r for r in next_traces(graph, coverage, variables, next_depth, limit=next_limit)
                             if r["name"] not in existing]
    _, selected, inputs, selected_round = best
    model["inputs"] = inputs
    return selected, {"rounds": history, "selectedRound": selected_round,
                      "cumulativeReachedTriggers": len(witnessed_so_far),
                      "cumulativeObservedAssetPairs": len(assets_so_far)}
