"""Final export guard, also checking geometry edits made after constraint solving."""

import math
from multires_app.layout.anchor_solver import RequiredConstraintError
from multires_app.pipeline.scene_clusters import describe_layout, transformed_box, box_union


def validate_export(project, device):
    layouts = {l["state"]: l for l in device["layouts"]}
    checks = 0
    from multires_app.layout.canvas_bounds import stable_resources, violations

    source_layouts = {l["state"]: l for l in project.get("sourceDevice", {}).get("layouts", [])}
    constraints = (
        device["profile"].get("adaptation", {}).get("anchorLayout", {}).get("constraints", [])
    )
    for layout in device["layouts"]:
        from multires_app.layout.scene_membership import scene_membership_errors

        membership = scene_membership_errors(project, device, layout)
        if membership:
            raise RequiredConstraintError(
                "Scene membership changed before export: "
                + str(membership[:5])
                + "; state="
                + layout["state"]
            )
        plan = layout.get("adaptationPlan", {})
        if plan.get("policy") == "component-refinement-preserved-transforms":
            if layout["transforms"] != plan.get("baselineTransforms"):
                raise RequiredConstraintError(
                    "refined component changed without a verified reference plan; state="
                    + layout["state"]
                )
            checks += len(layout["transforms"])
            continue
        if (
            plan.get("policy") == "reviewed-layout-json"
            and plan.get("placementPolicy") == "reference-first"
        ):
            # Reference-first replaces source screen anchors, not physical or
            # interaction validity. Recheck those contracts at every export.
            from multires_app.preview.layout_refinement import (
                validate_geometry_change,
                reference_collision_bounds,
                collision_exemptions,
            )
            from multires_app.layout.adaptive_layout import safe_region
            from multires_app.layout.offscreen_geometry import waiting_errors

            desc = describe_layout(project, layout)
            before = plan["referenceBaselineTransforms"]
            changed = {cid for cid, t in layout["transforms"].items() if t != before.get(cid)}
            groups = {}
            for c in project["components"]:
                if c.get("layout", {}).get("group"):
                    groups.setdefault(c["layout"]["group"], []).append(c["id"])
            region = safe_region(
                project["sourceProfile"], device["profile"], {"logicalViewport": device["viewport"]}
            )
            validate_geometry_change(
                desc,
                before,
                layout["transforms"],
                changed,
                collision_exemptions(desc, True),
                region,
                layout["state"],
                groups,
                reference_collision_bounds(project, device["profile"]["id"], layout["state"]),
            )
            waiting = waiting_errors(project, device, layout)
            if waiting:
                raise RequiredConstraintError(
                    "waiting artwork entered canvas before its interaction: " + str(waiting[:1])
                )
            for cid, t in layout["transforms"].items():
                if not all(math.isfinite(v) for v in t.values()) or min(t["sx"], t["sy"]) <= 0:
                    raise RequiredConstraintError("invalid export transform: " + cid)
            checks += len(changed)
            continue
        if plan.get("policy") != "nine-grid-anchor-kiwi":
            continue
        from multires_app.layout.visual_scaling import resource_scale
        from multires_app.layout.adaptive_layout import safe_region

        options = {
            **project.get("sourceProfile", {}).get("adaptation", {}).get("anchorLayout", {}),
            **device["profile"].get("adaptation", {}).get("anchorLayout", {}),
        }
        if options.get("minContainFraction", 0) > 0:
            region = safe_region(
                project["sourceProfile"], device["profile"], {"logicalViewport": device["viewport"]}
            )
            floor = resource_scale(
                project["sourceDevice"]["viewport"], region, options, device["profile"]
            )["minScale"]
            roles = {c["id"]: c["role"] for c in project["components"]}
            for cid, t in layout["transforms"].items():
                if (
                    roles.get(cid) not in {"background", "gesture-layer", "interaction-layer"}
                    and min(t["sx"], t["sy"]) < floor - 1e-6
                ):
                    raise RequiredConstraintError(
                        "source element became too small to correspond: "
                        + cid
                        + "; state="
                        + layout["state"]
                    )
        for cid, scale in device.get("sharedResourceScales", {}).items():
            if cid in layout["transforms"] and any(
                abs(layout["transforms"][cid][axis] - scale) > 1e-7 for axis in ("sx", "sy")
            ):
                raise RequiredConstraintError("shared panel/clock resource scale drift: " + cid)
        if not plan.get("validation", {}).get("required_ok"):
            raise RequiredConstraintError("unvalidated anchor layout: " + layout["state"])
        from multires_app.layout.screen_relations import horizontal_attachment_errors

        edges = horizontal_attachment_errors(project, device, layout)
        if edges:
            raise RequiredConstraintError(
                "source horizontal edge attachment changed before export: "
                + str(edges[:1])
                + "; state="
                + layout["state"]
            )
        from multires_app.layout.offscreen_geometry import waiting_errors

        waiting = waiting_errors(project, device, layout)
        if waiting:
            raise RequiredConstraintError(
                "waiting artwork entered canvas before its interaction: " + str(waiting[:1])
            )
        if any(c.get("cornerAttachment") for c in project.get("components", [])):
            from multires_app.layout.corner_geometry import corner_errors

            errors = corner_errors(project, device, layout)
            if errors:
                raise RequiredConstraintError(
                    "corner shortcut detached from screen edge before export: "
                    + str(errors[:1])
                    + "; state="
                    + layout["state"]
                )
        if plan.get("spatialAlignmentPolicy"):
            from multires_app.layout.spatial_alignment import layout_alignments, alignment_errors

            failures = alignment_errors(
                layout_alignments(project, device, layout, include_container=True),
                layout["transforms"],
            )
            if failures:
                raise RequiredConstraintError(
                    "source component alignment changed before export: "
                    + str(failures[:1])
                    + "; state="
                    + layout["state"]
                )
        region = plan.get(
            "safeRegion",
            {"x": 0, "y": 0, "w": device["viewport"]["width"], "h": device["viewport"]["height"]},
        )
        failures = violations(
            stable_resources(project, layout, source_layouts.get(layout["state"])),
            layout["transforms"],
            region,
        )
        if failures:
            raise RequiredConstraintError(
                "stable resource outside canvas before export: "
                + str(failures[:1])
                + "; state="
                + layout["state"]
            )
        if plan.get("spatialFlowPolicy") == "source-disjoint-adaptive-gaps":
            from multires_app.layout.spatial_flow import layout_flows, flow_errors

            failures = flow_errors(layout_flows(project, device, layout), layout["transforms"])
            if failures:
                raise RequiredConstraintError(
                    "source-disjoint resources overlap or lost axis gap: "
                    + str(failures[:1])
                    + "; state="
                    + layout["state"]
                )
        # Centering is an independent contract, including scenes without a
        # spatial-flow policy (for example a single centered clock group).
        from multires_app.layout.layout_quality import screen_relation_errors

        strict = {
            **plan,
            "screenDistributions": [],
            "groups": [
                {**g, "screenAttachment": {}, "centerAxes": g.get("centerRequiredAxes", [])}
                for g in plan.get("groups", [])
            ],
        }
        from multires_app.layout.visual_geometry import with_stable_visuals

        errors = screen_relation_errors(
            with_stable_visuals(project, layout, describe_layout(project, layout)),
            layout["transforms"],
            strict,
        )
        if any(e > 0.1 for e in errors):
            raise RequiredConstraintError(
                "source optical center changed before export; state=" + layout["state"]
            )
        for cid, t in layout["transforms"].items():
            if not all(math.isfinite(v) for v in t.values()) or min(t["sx"], t["sy"]) <= 0:
                raise RequiredConstraintError("invalid export transform: " + cid)
        request = layout.get("layoutRequest", {})
        base = layouts.get(request.get("baseState"))
        if base:
            for cid in request.get("fixedComponents", []):
                if layout["transforms"].get(cid) != base["transforms"].get(cid):
                    raise RequiredConstraintError("frozen component moved before export: " + cid)
        if not constraints:
            continue
        desc = describe_layout(project, layout)
        boxes = {
            cid: transformed_box(
                d["bounds"], layout["transforms"].get(cid, {"sx": 1, "sy": 1, "tx": 0, "ty": 0})
            )
            for cid, d in desc.items()
        }
        current = layout
        seen = set()
        while current.get("adaptationPlan", {}).get("reusedFrom") and current["state"] not in seen:
            seen.add(current["state"])
            current = layouts[current["adaptationPlan"]["reusedFrom"]]
        groups = {g["id"]: g for g in current.get("adaptationPlan", {}).get("groups", [])}

        def group_box(gid):
            if gid in boxes:
                return boxes[gid]
            if gid not in groups:
                return None
            members = [group_box(c) for c in groups[gid]["members"]]
            members = [b for b in members if b]
            if members:
                boxes[gid] = box_union(members)
                return boxes[gid]

        for gid in groups:
            group_box(gid)

        def edge(b, k):
            return {
                "left": b["x"],
                "x": b["x"],
                "top": b["y"],
                "y": b["y"],
                "right": b["x"] + b["w"],
                "bottom": b["y"] + b["h"],
                "cx": b["x"] + b["w"] / 2,
                "center_x": b["x"] + b["w"] / 2,
                "cy": b["y"] + b["h"] / 2,
                "center_y": b["y"] + b["h"] / 2,
                "w": b["w"],
                "h": b["h"],
            }[k]

        for c in constraints:
            if c.get("priority", "required") != "required":
                continue
            targets = c.get("targets", [])
            if any(t not in boxes for t in targets):
                continue
            kind = c.get("type", c.get("kind"))
            a = boxes[targets[0]]
            b = boxes[targets[1]] if len(targets) > 1 else None
            valid = True
            if kind == "align":
                valid = (
                    abs(
                        edge(a, c.get("edge", "center_x"))
                        - edge(b, c.get("other_edge", c.get("edge", "center_x")))
                        - c.get("offset", 0)
                    )
                    < 0.01
                )
            elif kind in {"gap", "non_overlap", "preserve_order"}:
                direction = c.get("direction", "right")
                distance = {
                    "right": b["x"] - a["x"] - a["w"],
                    "left": a["x"] - b["x"] - b["w"],
                    "below": b["y"] - a["y"] - a["h"],
                    "above": a["y"] - b["y"] - b["h"],
                }[direction]
                valid = (
                    abs(distance - c.get("gap", 0)) < 0.01
                    if kind == "gap"
                    else distance >= c.get("gap", 0) - 0.01
                )
            elif kind in {"position", "size", "min_size", "max_size"}:
                fields = ("x", "y") if kind == "position" else ("w", "h")
                for field in fields:
                    if field in c:
                        valid &= (
                            a[field] >= c[field] - 0.01
                            if kind == "min_size"
                            else (
                                a[field] <= c[field] + 0.01
                                if kind == "max_size"
                                else abs(a[field] - c[field]) < 0.01
                            )
                        )
            elif kind == "equal_size":
                valid = all(abs(a[field] - b[field]) < 0.01 for field in c.get("axes", ["w", "h"]))
            elif kind == "containment":
                b = b or plan.get(
                    "safeRegion",
                    {
                        "x": 0,
                        "y": 0,
                        "w": device["viewport"]["width"],
                        "h": device["viewport"]["height"],
                    },
                )
                valid = (
                    a["x"] >= b["x"] - 0.01
                    and a["y"] >= b["y"] - 0.01
                    and a["x"] + a["w"] <= b["x"] + b["w"] + 0.01
                    and a["y"] + a["h"] <= b["y"] + b["h"] + 0.01
                )
            if not valid:
                raise RequiredConstraintError(
                    "required relation violated before export: "
                    + str(c.get("id", kind))
                    + "; state="
                    + layout["state"]
                )
            checks += 1
    return {"required_ok": True, "authoredRelationChecks": checks}
