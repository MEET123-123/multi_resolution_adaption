"""Apply engine constraints, reject regressions, then collect XML and assets."""
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import argparse
import copy
import importlib.util
import shutil

from prepare import read, save
from anchor import PosterAnchor
from layer_guard import new_occlusion, protected_paint
from multires_app.evaluation.report import elements
from multires_app.evaluation.checks import ContractEvaluator, GeometryEvaluator
from multires_app.evaluation.core import Context
from multires_app.learning.render import render
from multires_app.pipeline.layout_storage import compact_layout_storage, externalize_anchor_evidence
from multires_app.pipeline.scene_analysis import attach_analysis
from multires_app.preview.scene_previewer import build_scene_preview
from multires_app.pipeline.scene_clusters import schedule_device


def checks(project, path):
    context = Context(project, path)
    result = {}
    for evaluator in (GeometryEvaluator(), ContractEvaluator()):
        for issue in evaluator.evaluate(context):
            if issue.status in {"fail", "review", "unknown"}:
                key = (issue.device, issue.state, issue.rule, issue.status, tuple(sorted(issue.members)))
                result[key] = issue
    return result


def worsened(baseline, current):
    return [value for key, value in current.items()
            if key not in baseline or value.residual > baseline[key].residual + .005]


def layouts(project):
    for device in project["devices"]:
        for layout in device["layouts"]:
            yield device, layout


def guard(projects, proposals, out):
    before = [(path, read(path)) for path in projects]
    audit = [(path, checks(project, path)) for path, project in before]
    history = []
    accepted = dict(proposals)
    for attempt in range(4):
        failures = []
        applied = set()
        rejected = set()
        checked = []
        for (path, project), (_, historical) in zip(before, audit):
            candidate = copy.deepcopy(project)
            algorithm = PosterAnchor(accepted)
            for device in candidate["devices"]:
                schedule_device(candidate, device, algorithm)
            checked.append(candidate)
            refs = {(d["profile"]["id"], l["state"]): (d, l) for d, l in layouts(project)}
            by_state = {(d["profile"]["id"], l["state"]): l.get("adaptationPlan", {}).get("layoutProposal", {}).get("key")
                        for d, l in layouts(candidate)}
            for issue in worsened(historical, checks(candidate, path)):
                failures.append(asdict(issue))
                key = by_state.get((issue.device, issue.state))
                if key in accepted:
                    rejected.add(key)
            for device, layout in layouts(candidate):
                meta = layout.get("adaptationPlan", {}).get("layoutProposal", {})
                if meta.get("status") != "proposed":
                    continue
                applied.add(meta["key"])
                preview = out / "gate-renders" / (meta["key"] + ".png")
                audit_render = render(candidate, device, layout, path.parent / "assets", preview)
                original_d, original_l = refs[device["profile"]["id"], layout["state"]]
                asset_dir = path.parent / "assets"
                baseline_paint = protected_paint(project, original_d, original_l, asset_dir)
                candidate_paint = protected_paint(candidate, device, layout, asset_dir)
                visibility = new_occlusion(baseline_paint, candidate_paint)
                if visibility:
                    rejected.add(meta["key"])
                    failures.extend({"request": meta["key"], **issue} for issue in visibility)
                if audit_render["status"] != "simulated":
                    rejected.add(meta["key"])
                    failures.append({"request": meta["key"], "render": audit_render})
        history.append({"attempt": attempt + 1, "applied": len(applied), "failures": failures,
                        "rejected": sorted(rejected)})
        save(out / "gate-history.json", history)
        print(f"Gate {attempt + 1}: {len(applied)} applied, {len(failures)} regressions", flush=True)
        if not failures:
            return {k: accepted[k] for k in applied}, audit, checked
        if not rejected or attempt == 2:
            accepted.clear()
        else:
            for key in rejected:
                del accepted[key]
    raise RuntimeError("Engine replay could not preserve the baseline")


def apply(project_file, predictions, out, helper):
    projects = [Path(path) for path in read(project_file)["projects"]]
    out.mkdir(parents=True, exist_ok=False)
    accepted, audit, checked = guard(projects, read(predictions), out)
    accepted_file = (out / "accepted-predictions.json").resolve()
    save(accepted_file, accepted)
    plugin = str(Path(__file__).with_name("anchor.py").resolve()) + ":PosterAnchor"
    candidates, existing, exported_keys = [], [], set()
    for ix, ((original, original_checks), current) in enumerate(zip(audit, checked)):
        parent = out / "scenes" / f"variant-{ix}"
        shutil.copytree(original.parent, parent)
        candidate = parent / original.name
        # Keep the exact component ownership and transforms seen by the gate.
        # Generic relayout re-infers components and can invalidate request keys.
        current["algorithmSpec"] = plugin
        compact_layout_storage(current)
        attach_analysis(current, parent)
        externalize_anchor_evidence(current)
        save(candidate, current)
        save(parent / "layout-clusters.json", {d["profile"]["id"]: d["layoutClusters"]
                                              for d in current["devices"]})
        build_scene_preview(current, parent / "preview.html")
        current = read(candidate)
        new = worsened(original_checks, checks(current, candidate))
        if new:
            save(out / "export-regressions.json", [asdict(issue) for issue in new])
            raise RuntimeError("Serialized project changed checked geometry")
        for _, layout in layouts(current):
            meta = layout.get("adaptationPlan", {}).get("layoutProposal", {})
            if meta.get("status") == "proposed":
                exported_keys.add(meta["key"])
        candidates.append(candidate)
        for device, layout in layouts(current):
            target = device["profile"]["id"]
            visible = []
            for order, element in enumerate(elements(current, layout)):
                row_index = int(element["key"].rsplit("@", 1)[1])
                asset_index = layout["rows"][row_index][6]
                visible.append({"id": element["key"], "sourceNode": element["id"],
                                "tag": element["tag"], "paintOrder": order,
                                "asset": current["assetOrder"][asset_index] if asset_index >= 0 else None,
                                "box": element["rect"], "dynamicTextSnapshot": element["text"],
                                "alpha": element["alpha"]})
            save(out / "layer-layouts" / f"variant-{ix}" / target / (layout["state"] + ".json"),
                 {"device": target, "state": layout["state"], "canvas": device["viewport"],
                  "coordinateSpace": "target-logical", "layers": visible})
        existing.extend(asdict(issue) for issue in original_checks.values())
    if exported_keys != set(accepted):
        raise RuntimeError("Proposals changed between validation and XML writeback")
    spec = importlib.util.spec_from_file_location("theme_workspace", helper)
    deliver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deliver)
    delivery = deliver.collect(candidates, out / "deliverables")
    save(out / "verification.json", {"engineChecks": "no new regressions vs baseline",
         "acceptedRequests": len(accepted), "exportProjectRequests": len(exported_keys), "baselineReviewCount": len(existing),
         "baselineReviewByRule": dict(Counter(issue["rule"] for issue in existing)),
         "nativeValidation": "not_measured", "delivery": delivery})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--helper", type=Path, default=Path(__file__).with_name("theme_workspace.py"))
    args = parser.parse_args()
    apply(args.projects, args.predictions, args.out, args.helper)
