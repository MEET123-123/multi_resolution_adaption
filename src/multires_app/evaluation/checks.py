"""Checks on selected layouts, rather than statistics about discarded proposals."""

import math
from .core import Observation


class GeometryEvaluator:
    name = "geometry"
    version = "1.0"

    def evaluate(self, ctx):
        from multires_app.pipeline.scene_clusters import box_union, overlap, transformed_box
        from multires_app.layout.center_intent import center_contracts
        from multires_app.layout.visual_geometry import ensure_visual_bounds

        ensure_visual_bounds(ctx.project)
        p = ctx.project
        sources = {l["state"]: l for l in p.get("sourceDevice", {}).get("layouts", [])}
        components = {c["id"]: c for c in p["components"]}
        for device in p["devices"]:
            did = device["profile"]["id"]
            vp = device["viewport"]
            screen = {"x": 0, "y": 0, "w": vp["width"], "h": vp["height"]}
            sv = p["sourceDevice"]["viewport"]
            original_screen = {"x": 0, "y": 0, "w": sv["width"], "h": sv["height"]}
            layouts = {l["state"]: l for l in device["layouts"]}
            for l in device["layouts"]:
                sid = l["state"]
                if sid not in sources:
                    yield Observation("INPUT.SOURCE_STATE", "unknown", device=did, state=sid)
                    continue
                invalid = [
                    c
                    for c, t in l.get("transforms", {}).items()
                    if not all(
                        isinstance(t.get(k), (float, int)) and math.isfinite(t[k])
                        for k in ("sx", "sy", "tx", "ty")
                    )
                    or min(t.get("sx", 0), t.get("sy", 0)) <= 0
                ]
                yield Observation(
                    "GEO.TRANSFORM",
                    "fail" if invalid else "pass",
                    "P0",
                    device=did,
                    state=sid,
                    members=invalid,
                    message="变换必须有限且缩放为正",
                )
                if invalid:
                    continue
                refs = ctx.geometry(sources[sid])
                raw = ctx.geometry(l)
                boxes = {
                    c: transformed_box(b, l["transforms"][c])
                    for c, b in raw.items()
                    if c in l["transforms"]
                }
                missing = sorted(set(refs) - set(boxes))
                yield Observation(
                    "GEO.INVENTORY",
                    "fail" if missing else "pass",
                    "P0",
                    device=did,
                    state=sid,
                    members=missing,
                    message="源可见组件在目标中必须有对应项",
                )
                tested = []
                for cid in sorted(set(refs) & set(boxes)):
                    c = components[cid]
                    if c.get("role") in {
                        "background",
                        "gesture-layer",
                        "interaction-layer",
                    } or c.get("pointerEffect"):
                        continue
                    ref, b = refs[cid], boxes[cid]
                    # Authored waiting/cropped artwork is not a stable contained
                    # component. Entry and exit phases are checked by replay.
                    if overlap(ref, original_screen) < ref["w"] * ref["h"] - 0.1:
                        continue
                    clipped = max(0.0, b["w"] * b["h"] - overlap(b, screen)) / max(
                        1.0, b["w"] * b["h"]
                    )
                    yield Observation(
                        "GEO.CONTAINMENT",
                        "fail" if clipped > 0.001 else "pass",
                        device=did,
                        state=sid,
                        members=[cid],
                        residual=clipped,
                        evidence={"sourceBounds": ref, "targetBounds": b},
                    )
                    for other, other_ref, other_b in tested:
                        if overlap(ref, other_ref) > 0.01:
                            continue
                        intersection = overlap(b, other_b)
                        residual = intersection / max(
                            1.0, min(b["w"] * b["h"], other_b["w"] * other_b["h"])
                        )
                        if intersection > 1.0 and residual > 0.001:
                            yield Observation(
                                "GEO.NEW_OVERLAP",
                                "review",
                                confidence=0.7,
                                device=did,
                                state=sid,
                                members=sorted([cid, other]),
                                residual=residual,
                                evidence={
                                    "sourceBounds": box_union([ref, other_ref]),
                                    "targetBounds": box_union([b, other_b]),
                                    "sourceOverlap": 0,
                                    "targetOverlap": intersection,
                                },
                            )
                    tested.append((cid, ref, b))
                contracts = center_contracts(p, device, l)
                if not contracts:
                    yield Observation("GEO.GROUP_CENTER", "not_applicable", device=did, state=sid)
                for contract in contracts:
                    error = contract["error"]
                    tolerance = max(
                        0.5, contract["extent"] * ctx.options.get("centerTolerance", 0.001)
                    )
                    yield Observation(
                        "GEO.GROUP_CENTER",
                        "unknown" if error is None else "fail" if error > tolerance else "pass",
                        device=did,
                        state=sid,
                        members=contract["members"],
                        axis=contract["axis"],
                        residual=(error or 0) / max(1, contract["extent"]),
                        evidence={
                            **contract,
                            "tolerance": tolerance,
                            "direction": (
                                ""
                                if error is None
                                else (
                                    "positive"
                                    if contract["actual"] > contract["expected"]
                                    else "negative"
                                )
                            ),
                        },
                        message="源组件组的视觉中心应保留在目标中心",
                    )
                req = l.get("layoutRequest", {})
                base = layouts.get(req.get("baseState"))
                drift = [
                    c
                    for c in req.get("fixedComponents", [])
                    if base and l["transforms"].get(c) != base["transforms"].get(c)
                ]
                yield Observation(
                    "GEO.FIXED_COMPONENT",
                    "fail" if drift else "pass",
                    "P0",
                    device=did,
                    state=sid,
                    members=drift,
                )


class ContractEvaluator:
    name = "contracts"
    version = "1.0"

    def evaluate(self, ctx):
        from multires_app.layout.screen_relations import horizontal_attachment_errors
        from multires_app.layout.offscreen_geometry import waiting_errors
        from multires_app.layout.corner_geometry import corner_errors
        from multires_app.layout.spatial_flow import layout_flows, flow_errors
        from multires_app.layout.spatial_alignment import layout_alignments, alignment_errors

        p = ctx.project
        for d in p["devices"]:
            for l in d["layouts"]:
                checks = [
                    ("EDGE", lambda: horizontal_attachment_errors(p, d, l)),
                    ("WAITING", lambda: waiting_errors(p, d, l)),
                    ("CORNER", lambda: corner_errors(p, d, l)),
                    ("GAP", lambda: flow_errors(layout_flows(p, d, l), l["transforms"])),
                    (
                        "ALIGNMENT",
                        lambda: alignment_errors(
                            layout_alignments(p, d, l, include_container=True), l["transforms"]
                        ),
                    ),
                ]
                for name, check in checks:
                    try:
                        failures = check()
                        yield Observation(
                            "CONTRACT." + name,
                            "fail" if failures else "pass",
                            device=d["profile"]["id"],
                            state=l["state"],
                            residual=float(len(failures)),
                            evidence={"failures": failures},
                        )
                    except Exception as exc:
                        yield Observation(
                            "CONTRACT." + name,
                            "unknown",
                            device=d["profile"]["id"],
                            state=l["state"],
                            evidence={"error": str(exc)},
                        )


class CoverageEvaluator:
    name = "coverage"
    version = "1.0"

    def evaluate(self, ctx):
        p = ctx.project
        coverage = p.get("graph", {}).get("coverage")
        incomplete = not coverage or any(
            coverage.get(k)
            for k in (
                "truncated",
                "gestureBudgetExceeded",
                "boundedTransitions",
                "unexpandedStates",
            )
        )
        yield Observation(
            "COVERAGE.EXPLORATION",
            "unknown" if incomplete else "pass",
            stage="interaction",
            evidence={"coverage": coverage},
        )
        yield Observation(
            "ENGINE.SUPPORT",
            "unknown" if p.get("diagnostics") else "pass",
            stage="engine",
            evidence={"diagnostics": p.get("diagnostics", {})},
        )
        for filename, rule in [
            ("validation.json", "EXPORT.ARTIFACT"),
            ("live-validation.json", "INTERACTION.ARTIFACT"),
        ]:
            # Historical reports have no binding to this candidate's hash.
            # Retain their evidence but never pass a newly modified candidate.
            import json

            path = ctx.path.parent / filename
            try:
                report = json.loads(path.read_text(encoding="utf8"))
                failures = report.get("failures")
                status = "fail" if isinstance(failures, int) and failures > 0 else "unknown"
                evidence = {
                    "report": filename,
                    "failures": failures,
                    "freshness": "unbound historical artifact",
                }
            except (OSError, ValueError) as exc:
                status, evidence = "unknown", {"report": filename, "error": str(exc)}
            yield Observation(rule, status, "P0", stage="export", evidence=evidence)


class AestheticEvaluator:
    name = "aesthetics"
    version = "1.0"

    def evaluate(self, ctx):
        from multires_app.layout.aesthetic_optimizer import Evaluator

        for d in ctx.project["devices"]:
            evaluator = Evaluator(ctx.project, d)
            for sid, sample in evaluator.by_state.items():
                score = evaluator.score(sample)
                # Ranking evidence only; proxy scores are not a release oracle.
                yield Observation(
                    "AES.PROXY",
                    "pass" if score.get("score") is not None else "unknown",
                    "P2",
                    0.5,
                    device=d["profile"]["id"],
                    state=sid,
                    stage="aesthetic",
                    evidence={"evaluation": score, "humanCalibrated": False},
                )


def defaults():
    return [GeometryEvaluator(), ContractEvaluator(), CoverageEvaluator(), AestheticEvaluator()]
