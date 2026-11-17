"""Publish the engine's actual per-target layout decisions and reachable paths."""
from collections import Counter, defaultdict
from pathlib import Path

from prepare import read, save
from multires_app.pipeline.scene_clusters import describe_layout, same_geometry


MODES = {"full-reflow": "full", "local-reflow": "partial", "overlay": "partial", "reuse": "reuse"}


def paths_from_start(graph):
    """Retain concrete witnesses; bounded edges have no claim to a reached state."""
    states = {s["id"] for s in graph["states"]}
    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    for index, edge in enumerate(graph["transitions"]):
        source, target = edge["from"], edge.get("to")
        if source not in states or target is not None and target not in states:
            raise ValueError("Scene graph references an unknown state")
        outgoing[source].append(index)
        if target is not None:
            incoming[target].append(index)
    root = graph["states"][0]["id"]
    verified, pending = {root}, [root]
    while pending:
        current = pending.pop()
        for index in outgoing[current]:
            target = graph["transitions"][index].get("to")
            if target is not None and target not in verified:
                pending.append(target)
                verified.add(target)
    if verified != states:
        raise ValueError("Scene graph contains states without a path from startup")
    return incoming, outgoing


def scenes(project):
    graph = project["graph"]
    incoming, outgoing = paths_from_start(graph)
    coverage = graph["coverage"]
    states = []
    for state in graph["states"]:
        sid = state["id"]
        states.append({
            "id": sid, "label": state.get("label"), "depth": state["depth"],
            "identity": state.get("signatureHash"),
            "behaviorValues": state["behaviorValues"], "visibleNodes": state["visibleElements"],
            "canonicalTrace": [step["humanAction"] for step in state["trace"]],
            "incomingTransitionIndices": incoming[sid], "outgoingTransitionIndices": outgoing[sid],
            "expanded": sid not in coverage.get("unexpandedStates", []),
        })
    depths = {s["id"]: s["depth"] for s in graph["states"]}
    transitions = []
    for edge in graph["transitions"]:
        record = {key: edge[key] for key in (
            "from", "to", "trigger", "triggers", "owner", "action", "gesture", "sample", "intent",
            "changes", "events", "bounded", "witnesses",
        ) if key in edge}
        if record.get("bounded"):
            record["unreachedReason"] = ("depth-limit" if depths[record["from"]] >= coverage["maxDepth"]
                                         else "state-limit")
        transitions.append(record)
    external = sum(bool(edge.get("events")) for edge in transitions)
    return {
        "states": states, "transitions": transitions,
        "behaviorControls": graph["semantics"]["behaviorControls"],
        "guardVariables": graph["semantics"]["guardVariables"],
        "dataOnlyControls": graph["semantics"]["dataOnlyControls"],
        "coverage": coverage,
        "externalEffectTransitions": external,
        "enumerationScope": "startup-reachable states under the selected simulated system inputs and finite gesture samples",
        "frontierExhaustedForSampler": not coverage["truncated"]
            and not coverage.get("unexpandedStates") and not coverage.get("boundedTransitions"),
        "systemInputs": project["model"].get("inputs", {}).get("system", {}),
    }


def decisions(project, device):
    descriptions = {l["state"]: describe_layout(project, l) for l in device["layouts"]}
    result = []
    for layout in device["layouts"]:
        sid = layout["state"]
        request = layout["layoutRequest"]
        kind = request["kind"]
        frozen = set(request["fixedComponents"])
        affected = set(request["affectedComponents"])
        if frozen & affected:
            raise ValueError(f"{sid}: fixed and affected components overlap")
        base = request.get("baseState")
        if base is not None and base not in descriptions:
            raise ValueError(f"{sid}: layout references an unknown predecessor")
        if kind == "reuse" and (base is None or affected):
            raise ValueError(f"{sid}: reuse requires a reference and no affected component")
        now = descriptions[sid]
        previous = descriptions.get(base, {})
        modeset = set(previous) - {k for k, v in previous.items() if v["role"] in {"background", "gesture-layer"}}
        stable = sum(k in now and same_geometry(previous[k], now[k]) for k in modeset)
        proposal = layout.get("adaptationPlan", {}).get("layoutProposal", {})
        result.append({
            "state": sid, "mode": MODES[kind], "engineKind": kind, "referenceState": base,
            "cluster": request["clusterId"], "family": request["familyId"],
            "fixedComponents": sorted(frozen), "affectedComponents": sorted(affected),
            "removedComponents": sorted(request.get("removedComponents", set(previous) - set(now))),
            "sameGeometryComparedToReference": stable / len(modeset) if modeset else None,
            "visibleComponents": len(now), "evidence": request["evidence"],
            "reviewRequired": request.get("reviewRequired", False),
            "proposal": {"status": proposal.get("status", "no-request"), "requestKey": proposal.get("key")},
        })
    return result


def write_analysis(paths, out):
    variants = []
    for i, path in enumerate(paths):
        project = read(path)
        graph = scenes(project)
        targets = []
        for device in project["devices"]:
            rows = decisions(project, device)
            targets.append({"id": device["profile"]["id"], "viewport": device["viewport"],
                            "counts": dict(Counter(r["engineKind"] for r in rows)),
                            "decisions": rows})
        name = f"variant-{i}.json"
        save(out / name, {"manifest": project["model"]["file"], "sourceProfile": project.get("sourceProfile"),
                          **graph, "targets": targets})
        variants.append({"report": name, "manifest": project["model"]["file"],
                         "states": len(graph["states"]), "transitions": len(graph["transitions"]),
                         "boundedTransitions": graph["coverage"]["boundedTransitions"],
                         "unsampledTriggers": len(graph["coverage"]["unsampledTriggers"]),
                         "frontierExhaustedForSampler": graph["frontierExhaustedForSampler"],
                         "targetModes": {t["id"]: t["counts"] for t in targets}})
    save(out / "summary.json", {"variants": variants, "nativeValidated": False})
    return variants


def project_paths(file):
    return [Path(name).resolve() for name in read(file)["projects"]]
