"""Conservative best-so-far gate for inferred layouts, not an aesthetic judge."""

import copy
from multires_app.pipeline.scene_clusters import (
    describe_layout,
    transformed_box,
    overlap,
    box_union,
)


def quality(project, device, reference_requests=None, relation_plans=None):
    components = {c["id"]: c for c in project["components"]}
    layouts = {l["state"]: l for l in device["layouts"]}
    source = {l["state"]: describe_layout(project, l) for l in project["sourceDevice"]["layouts"]}
    vp = device["viewport"]
    screen = {"x": 0, "y": 0, "w": vp["width"], "h": vp["height"]}
    sv = project["sourceDevice"]["viewport"]
    source_screen = {"x": 0, "y": 0, "w": sv["width"], "h": sv["height"]}
    result = {
        "newClippedComponentSnapshots": 0,
        "newSourceDisjointOverlaps": 0,
        "fixedTransformDrift": 0,
        "componentSnapshots": 0,
        "screenRelationViolations": 0,
        "screenRelationErrorPx": 0.0,
        "resourceScaleErrorPx": 0.0,
        "glyphScaleDrift": 0,
    }
    device_descriptions = {}
    for layout in device["layouts"]:
        desc = describe_layout(project, layout)
        placed = []
        device_descriptions[layout["state"]] = desc
        for cid, d in desc.items():
            if components[cid]["role"] in {
                "background",
                "gesture-layer",
                "interaction-layer",
                "decoration",
                "popup",
                "overlay",
                "dialog",
            }:
                continue
            b = d["bounds"]
            t = layout["transforms"].get(cid)
            if not t or min(b["w"], b["h"]) <= 0:
                continue
            q = transformed_box(b, t)
            ref = source.get(layout["state"], {}).get(cid, {}).get("bounds", b)
            result["componentSnapshots"] += 1
            if (
                overlap(ref, source_screen) >= ref["w"] * ref["h"] - 0.1
                and overlap(q, screen) < q["w"] * q["h"] - 0.1
            ):
                result["newClippedComponentSnapshots"] += 1
            for prior, original in placed:
                if overlap(ref, original) < 1e-6 and overlap(q, prior) > 0.05 * min(
                    q["w"] * q["h"], prior["w"] * prior["h"]
                ):
                    result["newSourceDisjointOverlaps"] += 1
            placed.append((q, ref))
        req = (reference_requests or {}).get(layout["state"], layout.get("layoutRequest", {}))
        base = layouts.get(req.get("baseState"))
        if base and req.get("kind") in {"local-reflow", "overlay", "reuse"}:
            result["fixedTransformDrift"] += sum(
                layout["transforms"].get(cid) != base["transforms"].get(cid)
                for cid in req.get("fixedComponents", [])
            )
        plan = (relation_plans or {}).get(layout["state"], layout.get("adaptationPlan", {}))
        preferred = plan.get("preferredResourceScale")
        if preferred is not None:
            for cid, d in desc.items():
                if cid in layout["transforms"] and components[cid]["role"] not in {
                    "background",
                    "gesture-layer",
                    "interaction-layer",
                    "decoration",
                }:
                    result["resourceScaleErrorPx"] += abs(
                        layout["transforms"][cid]["sx"] - preferred
                    ) * (d["bounds"]["w"] + d["bounds"]["h"])
        from multires_app.layout.visual_geometry import with_stable_visuals

        errors = screen_relation_errors(
            with_stable_visuals(project, layout, desc), layout["transforms"], plan
        )
        result["screenRelationViolations"] += sum(e > 1 for e in errors)
        result["screenRelationErrorPx"] += sum(errors)
    result["screenRelationErrorPx"] = round(result["screenRelationErrorPx"], 4)
    result["resourceScaleErrorPx"] = round(result["resourceScaleErrorPx"], 4)
    from multires_app.media.resource_scaling import glyph_components

    for cid in glyph_components(project):
        scales = {
            round(l["transforms"][cid]["sx"], 8)
            for l in device["layouts"]
            if cid in l["transforms"] and cid in device_descriptions[l["state"]]
        }
        result["glyphScaleDrift"] += max(0, len(scales) - 1)
    return result


def screen_relation_errors(desc, transforms, plan):
    region = plan.get("safeRegion")
    groups = {g["id"]: g for g in plan.get("groups", [])}
    if not region:
        return []
    boxes = {
        cid: transformed_box(d["bounds"], transforms[cid])
        for cid, d in desc.items()
        if cid in transforms and min(d["bounds"]["w"], d["bounds"]["h"]) > 0
    }

    def resolve(gid):
        if gid in boxes:
            return boxes[gid]
        if gid not in groups:
            return None
        parts = [resolve(mid) for mid in groups[gid]["members"]]
        parts = [p for p in parts if p]
        if parts:
            boxes[gid] = box_union(parts)
            return boxes[gid]

    def scale_of(gid):
        if gid in transforms:
            return transforms[gid]["sx"]
        for mid in groups.get(gid, {}).get("members", []):
            value = scale_of(mid)
            if value is not None:
                return value
        return None

    def visual_box(gid):
        if gid in desc and gid in transforms:
            return transformed_box(
                desc[gid].get(
                    "stableVisualBounds", desc[gid].get("visualBounds", desc[gid]["bounds"])
                ),
                transforms[gid],
            )
        g = groups.get(gid, {})
        parts = [
            visual_box(mid) for mid in (g.get("centerReferenceMembers") or g.get("members", []))
        ]
        parts = [p for p in parts if p]
        return box_union(parts) if parts else None

    errors = []
    rail_roots = {gid for r in plan.get("screenDistributions", []) for gid in r["roots"]}
    for gid, g in groups.items():
        q = resolve(gid)
        ref = g["sourceBounds"]
        if not q:
            continue
        if g.get("cornerLocalBounds"):
            frame = g["cornerLocalBounds"]
            scale = scale_of(gid)
            if scale is not None:
                q = {
                    "x": q["x"] + frame["x"] * scale,
                    "y": q["y"] + frame["y"] * scale,
                    "w": frame["w"] * scale,
                    "h": frame["h"] * scale,
                }
        for axis, intent in g.get("screenAttachment", {}).items():
            if gid in rail_roots:
                continue
            size = "w" if axis == "x" else "h"
            scale = scale_of(gid)
            if scale is None:
                continue
            edge_box = q
            if axis == "x" and g.get("horizontalLocalBounds") and not g.get("cornerLocalBounds"):
                frame = g["horizontalLocalBounds"]
                edge_box = {**q, "x": q["x"] + frame["x"] * scale, "w": frame["w"] * scale}
            value = (
                edge_box[axis] - region[axis]
                if intent["edge"] in {"left", "top"}
                else region[axis] + region[size] - edge_box[axis] - edge_box[size]
            )
            from multires_app.layout.screen_relations import attachment_margin

            errors.append(abs(value - attachment_margin(intent, scale)))
        for axis in g.get("centerAxes", []):
            size = "w" if axis == "x" else "h"
            visual = visual_box(gid) or q
            errors.append(abs(visual[axis] + visual[size] / 2 - region[axis] - region[size] / 2))
    for r in plan.get("screenDistributions", []):
        bs = [
            visual_box(gid) if r.get("horizontalGeometry") == "optical" else resolve(gid)
            for gid in r["roots"]
        ]
        if any(b is None for b in bs):
            continue
        n = len(bs)
        scale = scale_of(r["roots"][0])
        if scale is None:
            continue
        vertical = [resolve(gid) for gid in r["roots"]]
        errors.extend(
            abs(
                region["y"]
                + region["h"]
                - b["y"]
                - b["h"] / 2
                - r["targetBottomMargin"]
                - r["referenceHalfHeight"] * scale
            )
            for b in vertical
            if b
        )
        if r["mode"] == "equal-cells":
            errors.extend(
                abs(b["x"] + b["w"] / 2 - region["x"] - region["w"] * (i + 0.5) / n)
                for i, b in enumerate(bs)
            )
        else:
            errors.extend(
                [
                    abs(bs[0]["x"] - region["x"] - r["targetLeftMargin"]),
                    abs(
                        region["x"]
                        + region["w"]
                        - bs[-1]["x"]
                        - bs[-1]["w"]
                        - r["targetRightMargin"]
                    ),
                ]
            )
            values = (
                [b["x"] + b["w"] / 2 for b in bs]
                if r["mode"] == "equal-centers"
                else [b["x"] - a["x"] - a["w"] for a, b in zip(bs, bs[1:])]
            )
            steps = (
                [b - a for a, b in zip(values, values[1:])]
                if r["mode"] == "equal-centers"
                else values
            )
            errors.extend(abs(v - steps[0]) for v in steps[1:])
    return errors


def finalize_anchor_device(project, device):
    """Only inferred preferences can roll back. Authored rules remain authoritative."""
    from multires_app.layout.adaptive_layout import AdaptiveThemeLayout
    from multires_app.pipeline.scene_clusters import schedule_device

    options = device["profile"].get("adaptation", {}).get("anchorLayout", {})
    if options or any(c.get("layout") for c in project["components"]):
        device["layoutQuality"] = {"selection": "authored-constraints", "automaticRollback": False}
        return
    candidate = quality(project, device)
    baseline_device = copy.deepcopy(device)
    baseline = {**project, "devices": [baseline_device], "anchorIRs": {}}
    try:
        schedule_device(baseline, baseline_device, AdaptiveThemeLayout())
    except ValueError as exc:
        device["layoutQuality"] = {
            "selection": "anchor",
            "automaticRollback": False,
            "candidate": candidate,
            "baselineError": str(exc),
            "reviewRequired": True,
        }
        return
    requests = {l["state"]: l["layoutRequest"] for l in device["layouts"]}
    by_state = {l["state"]: l for l in baseline_device["layouts"]}
    # Legacy promotion may have moved common components. Repair those exact
    # transformations before comparing, rather than accepting a weaker request.
    for layout in baseline_device["layouts"]:
        request = requests[layout["state"]]
        base = by_state.get(request.get("baseState"))
        if not base:
            continue
        for cid in request.get("fixedComponents", []):
            if cid in layout["transforms"]:
                layout["transforms"][cid] = copy.deepcopy(
                    base["transforms"].get(cid, {"sx": 1.0, "sy": 1.0, "tx": 0.0, "ty": 0.0})
                )
    previous = quality(
        baseline,
        baseline_device,
        requests,
        {l["state"]: l.get("adaptationPlan", {}) for l in device["layouts"]},
    )
    # Pareto guard: introducing either more clipping or more suspicious overlap
    # is enough to reject an automatic proposal. Never trade frozen invariants.
    keys = (
        "newClippedComponentSnapshots",
        "newSourceDisjointOverlaps",
        "screenRelationViolations",
        "screenRelationErrorPx",
        "resourceScaleErrorPx",
        "glyphScaleDrift",
    )
    worse = any(candidate.get(k, 0) > previous.get(k, 0) for k in keys)
    use_baseline = (
        worse
        and all(previous.get(k, 0) <= candidate.get(k, 0) for k in keys)
        and previous["fixedTransformDrift"] == 0
    )
    if use_baseline and device.get("sharedResourceScales"):
        use_baseline = not any(
            abs(t[axis] - device["sharedResourceScales"][cid]) > 1e-7
            for l in baseline_device["layouts"]
            for cid, t in l["transforms"].items()
            if cid in device["sharedResourceScales"]
            for axis in ("sx", "sy")
        )
    if use_baseline:
        from multires_app.layout.offscreen_geometry import waiting_errors

        use_baseline = not any(
            waiting_errors(project, device, l) for l in baseline_device["layouts"]
        )
    if use_baseline:
        from multires_app.layout.canvas_bounds import stable_resources, violations

        sources = {l["state"]: l for l in project.get("sourceDevice", {}).get("layouts", [])}
        viewport = device["viewport"]
        region = {"x": 0, "y": 0, "w": viewport["width"], "h": viewport["height"]}
        use_baseline = not any(
            violations(
                stable_resources(project, l, sources.get(l["state"])), l["transforms"], region
            )
            for l in baseline_device["layouts"]
        )
        from multires_app.layout.spatial_flow import layout_flows, flow_errors

        use_baseline = use_baseline and not any(
            flow_errors(layout_flows(project, device, l), l["transforms"])
            for l in baseline_device["layouts"]
        )
        from multires_app.layout.spatial_alignment import layout_alignments, alignment_errors

        use_baseline = use_baseline and not any(
            alignment_errors(
                layout_alignments(project, device, l, include_container=True),
                by_state[l["state"]]["transforms"],
            )
            for l in device["layouts"]
        )
        # A legacy result cannot replace the new optical centering contract.
        for original in device["layouts"]:
            plan = original.get("adaptationPlan", {})
            if not plan.get("spatialFlowPolicy"):
                continue
            alternative = by_state[original["state"]]
            if (
                sum(
                    screen_relation_errors(
                        describe_layout(project, alternative), alternative["transforms"], plan
                    )
                )
                > 0.1
            ):
                use_baseline = False
            from multires_app.layout.screen_relations import horizontal_attachment_errors

            if horizontal_attachment_errors(
                project, device, {**alternative, "adaptationPlan": plan}
            ):
                use_baseline = False
    device["layoutQuality"] = {
        "selection": "baseline-rollback" if use_baseline else "anchor",
        "candidate": candidate,
        "baseline": previous,
        "automaticRollback": use_baseline,
        "reviewRequired": True,
        "scope": "component bbox proxies; no designer-intent labels",
        "reason": (
            "inferred layout worsens a geometry proxy; retain best previous candidate"
            if use_baseline
            else (
                "baseline would move frozen components; proposal requires review"
                if worse
                else "proposal does not worsen baseline geometry proxies"
            )
        ),
    }
    if not use_baseline:
        return
    old = {l["state"]: l for l in baseline_device["layouts"]}
    for layout in device["layouts"]:
        proposal = layout.get("adaptationPlan", {})
        chosen = old[layout["state"]]
        layout["transforms"] = chosen["transforms"]
        # Keep the original stricter frozen request; the baseline already passed
        # those exact pairs, even if its own scheduler chose different clusters.
        layout["adaptationReview"] = chosen.get("adaptationReview", []) + [
            {
                "kind": "quality-rollback",
                "reason": "automatic anchor proposal rejected by baseline geometry gate",
            }
        ]
        groups = copy.deepcopy(proposal.get("groups", []))
        desc = describe_layout(project, layout)
        boxes = {
            cid: transformed_box(
                d["bounds"], layout["transforms"].get(cid, {"sx": 1, "sy": 1, "tx": 0, "ty": 0})
            )
            for cid, d in desc.items()
        }
        index = {g["id"]: g for g in groups}

        def bounds(gid):
            if gid in boxes:
                return boxes[gid]
            if gid not in index:
                return None
            parts = [bounds(m) for m in index[gid]["members"]]
            parts = [b for b in parts if b]
            if parts:
                boxes[gid] = box_union(parts)
                return boxes[gid]

        for g in groups:
            b = bounds(g["id"])
            if b:
                g["targetBounds"] = b
            g["bindingStatus"] = "inference retained for review; baseline placement selected"
        layout["adaptationPlan"] = {
            "policy": "nine-grid-anchor-kiwi",
            "route": "baseline-rollback",
            "generator": "adaptive-theme-layout-v1",
            "layoutIRRef": proposal.get("layoutIRRef"),
            "groups": groups,
            "reusedFrom": proposal.get("reusedFrom"),
            "safeRegion": proposal.get("safeRegion"),
            "frozenIds": proposal.get("frozenIds", []),
            "horizontalEdgeAttachments": copy.deepcopy(
                proposal.get("horizontalEdgeAttachments", {})
            ),
            "validation": {
                "required_ok": True,
                "backend": "baseline-geometry-and-frozen-guard",
                "inferredProposalAccepted": False,
                "authoredConstraintsPresent": False,
            },
            "previousPlan": {
                k: v
                for k, v in chosen.get("adaptationPlan", {}).items()
                if k not in {"blocks", "datetimePlacement"}
            },
            "reviewRequired": True,
        }
