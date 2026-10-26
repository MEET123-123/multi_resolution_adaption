"""Independent sampled Responsive Layout Graph, inspired by ReDeCheck (2017).

No upstream Java code is copied. Width AND height are paths; surfaces, states,
orientations and declared layout modes must be partitioned by the caller.
Intervals describe observed samples, never a proof about unsampled widths.
"""

from .core import digest, Observation


def relations(boxes, viewport, tolerance=0.001):
    from multires_app.pipeline.scene_clusters import overlap

    result = {}
    w, h = viewport["width"], viewport["height"]
    for cid, b in sorted(boxes.items()):
        result["visible:" + cid] = (
            b["x"] < w and b["y"] < h and b["x"] + b["w"] > 0 and b["y"] + b["h"] > 0
        )
        result["inside:" + cid] = (
            b["x"] >= -w * tolerance
            and b["y"] >= -h * tolerance
            and b["x"] + b["w"] <= w * (1 + tolerance)
            and b["y"] + b["h"] <= h * (1 + tolerance)
        )
        result["center-x:" + cid] = abs(b["x"] + b["w"] / 2 - w / 2) <= w * tolerance
        result["center-y:" + cid] = abs(b["y"] + b["h"] / 2 - h / 2) <= h * tolerance
    ids = sorted(boxes)
    for i, a in enumerate(ids):
        for b in ids[:i]:
            aa, bb = boxes[a], boxes[b]
            pair = "|".join([b, a])
            result["overlap:" + pair] = overlap(aa, bb) > 1.0
            result["left:" + pair] = bb["x"] + bb["w"] <= aa["x"] + w * tolerance
            result["above:" + pair] = bb["y"] + bb["h"] <= aa["y"] + h * tolerance
            result["align-x:" + pair] = abs(bb["x"] - aa["x"]) <= w * tolerance
            result["align-y:" + pair] = abs(bb["y"] - aa["y"]) <= h * tolerance
    return result


def sample_graph(probe, low, high, axis="width", steps=5, budget=17, breakpoints=()):
    """Probe(value)->{relations, ...}; bisect observed relation changes.

    Uniform sampling can discover isolated failures whose endpoints agree;
    boundary±1 sampling covers caller-declared breakpoints. Finite budgets are
    explicit and all unresolved intervals are returned.
    """
    if axis not in {"width", "height"} or low <= 0 or high <= low or steps < 3 or budget < steps:
        raise ValueError("invalid responsive sampling bounds/budget")
    points = {round(low + (high - low) * i / (steps - 1)) for i in range(steps)}
    for boundary in breakpoints:
        points.update(int(v) for v in (boundary - 1, boundary, boundary + 1) if low <= v <= high)
    if len(points) > budget:
        raise ValueError("budget cannot cover initial/boundary samples")
    samples = {}

    def capture(value):
        try:
            sample = probe(value)
            samples[value] = {**sample, "value": value, "status": "observed"}
        except Exception as exc:
            samples[value] = {
                "value": value,
                "status": "unknown",
                "error": str(exc),
                "relations": {},
            }

    for value in sorted(points):
        capture(value)
    while len(samples) < budget:
        keys = sorted(samples)
        intervals = [
            (a, b)
            for a, b in zip(keys, keys[1:])
            if b - a > 1
            and (
                samples[a]["status"] != samples[b]["status"]
                or samples[a].get("relations") != samples[b].get("relations")
            )
        ]
        if not intervals:
            break
        a, b = max(intervals, key=lambda pair: pair[1] - pair[0])
        capture((a + b) // 2)
    keys = sorted(samples)
    changes = []
    for a, b in zip(keys, keys[1:]):
        first, last = samples[a], samples[b]
        if first["status"] != "observed" or last["status"] != "observed":
            changes.append({"between": [a, b], "status": "unknown"})
            continue
        changed = sorted(
            k
            for k in set(first["relations"]) | set(last["relations"])
            if first["relations"].get(k) != last["relations"].get(k)
        )
        if changed:
            changes.append(
                {
                    "between": [a, b],
                    "status": "boundary" if b - a == 1 else "unresolved",
                    "relations": changed,
                }
            )
    anomalies = []
    # Compress plateaus first: binary boundary refinement creates several
    # failing samples in an isolated bad range, not just one bad midpoint.
    runs = []
    for key in keys:
        signature = digest([samples[key]["status"], samples[key]["relations"]])
        if not runs or runs[-1][0] != signature:
            runs.append([signature, key, key])
        else:
            runs[-1][2] = key
    for first, middle, last in zip(runs, runs[1:], runs[2:]):
        a, b, c = first[2], middle[1], last[1]
        if any(samples[v]["status"] != "observed" for v in (a, b, c)):
            continue
        ra, rb, rc = (samples[v]["relations"] for v in (a, b, c))
        changed = sorted(k for k in set(ra) & set(rb) & set(rc) if ra[k] == rc[k] != rb[k])
        if changed:
            anomalies.append(
                {
                    "sampleRange": [a, c],
                    "at": b,
                    "relations": changed,
                    "status": "needs-review",
                    "reason": "short-range relation reversal; may be an intentional breakpoint",
                }
            )
    return {
        "schema": "multires-rlg/1",
        "axis": axis,
        "range": [low, high],
        "budget": budget,
        "samples": [samples[k] for k in keys],
        "changes": changes,
        "anomalies": anomalies,
        "samplingComplete": not any(s["status"] == "unknown" for s in samples.values())
        and not any(c["status"] == "unresolved" for c in changes),
        "exhaustive": False,
        "id": digest([axis, low, high, keys])[:16],
    }


class ResponsiveEvaluator:
    name = "responsive-rlg"
    version = "1.0"

    def evaluate(self, ctx):
        if not ctx.options.get("responsive"):
            yield Observation(
                "RESPONSIVE.COVERAGE",
                "unknown",
                stage="responsive",
                evidence={"reason": "viewport sweep not requested"},
            )
            return
        import copy
        from multires_app.pipeline.scene_clusters import schedule_device, transformed_box
        from multires_app.pipeline.scene_pipeline import load_scene_algorithm
        from multires_app.pipeline.source_comparison import packed_snapshot
        from multires_app.interaction.interaction_semantics import replay_human_state
        from multires_app.layout.visual_geometry import component_visuals
        from multires_app.layout.center_intent import center_contracts

        p = ctx.project
        graphs = []
        for device in p["devices"][: ctx.options.get("responsiveDevices", 1)]:
            did = device["profile"]["id"]
            selected = device["layouts"][: ctx.options.get("responsiveStates", 1)]
            for layout in selected:
                sid = layout["state"]
                state = next(s for s in p["graph"]["states"] if s["id"] == sid)
                for axis in ctx.options.get("responsiveAxes", ["width", "height"]):
                    base = device["viewport"][axis]
                    fraction = ctx.options.get("responsiveFraction", 0.08)

                    def probe(value):
                        candidate = copy.deepcopy(
                            {k: v for k, v in p.items() if not k.startswith("_") and k != "devices"}
                        )
                        vp = {**device["viewport"], axis: value}
                        profile = copy.deepcopy(device["profile"])
                        profile["resolutionPx"][axis] *= value / base
                        model = candidate["model"]
                        model["assetNames"] = list(p["assets"])
                        runtime = replay_human_state(
                            model, vp["width"], vp["height"], state, p["assets"], verify_hit=False
                        )
                        row = packed_snapshot(candidate, runtime, sid)
                        d = {"profile": profile, "viewport": vp, "layouts": [row]}
                        candidate["devices"] = [d]
                        schedule_device(
                            candidate, d, load_scene_algorithm(p.get("algorithmSpec", "anchor"))
                        )
                        boxes = {
                            c: transformed_box(b, row["transforms"][c])
                            for c, b in component_visuals(candidate, row).items()
                            if c in row["transforms"]
                            and next(x for x in p["components"] if x["id"] == c)["role"]
                            not in {"background", "gesture-layer", "interaction-layer"}
                        }
                        contract = center_contracts(candidate, d, row)
                        values = relations(boxes, vp)
                        center_failures = [
                            c
                            for c in contract
                            if c["error"] is not None and c["error"] > max(0.5, c["extent"] * 0.001)
                        ]
                        return {
                            "relations": values,
                            "centerFailures": center_failures,
                            "mode": row.get("adaptationPlan", {}).get("route"),
                            "viewport": vp,
                        }

                    graph = sample_graph(
                        probe,
                        max(1, round(base * (1 - fraction))),
                        round(base * (1 + fraction)),
                        axis,
                        ctx.options.get("responsiveSteps", 5),
                        ctx.options.get("responsiveBudget", 13),
                        ctx.options.get("breakpoints", []),
                    )
                    graph.update(
                        device=did,
                        state=sid,
                        policy="relation changes are candidates; breakpoint changes may be intentional",
                    )
                    graphs.append(graph)
                    failed = [s for s in graph["samples"] if s.get("centerFailures")]
                    unknown = [s for s in graph["samples"] if s["status"] == "unknown"]
                    yield Observation(
                        "RESPONSIVE.CENTER",
                        "fail" if failed else "unknown" if unknown else "pass",
                        device=did,
                        state=sid,
                        axis="x" if axis == "width" else "y",
                        stage="responsive",
                        evidence={
                            "graph": graph["id"],
                            "failedSamples": failed,
                            "unknownSamples": unknown,
                        },
                    )
                    yield Observation(
                        "RESPONSIVE.SHORT_RANGE",
                        "review" if graph["anomalies"] else "unknown" if unknown else "pass",
                        confidence=0.6,
                        device=did,
                        state=sid,
                        axis="x" if axis == "width" else "y",
                        stage="responsive",
                        evidence={
                            "graph": graph["id"],
                            "anomalies": graph["anomalies"],
                            "changes": graph["changes"],
                            "exhaustive": False,
                        },
                    )
        ctx.cache["responsiveGraphs"] = graphs
        yield Observation(
            "RESPONSIVE.COVERAGE",
            "unknown",
            stage="responsive",
            evidence={
                "sampledPaths": len(graphs),
                "totalDevices": len(p["devices"]),
                "exhaustive": False,
                "scope": "bounded width/height paths; unsampled surfaces, states and interior ranges remain unknown",
            },
        )
