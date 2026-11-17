"""Discover lockscreen scenes, reuse or reflow per target, and export a theme."""
from pathlib import Path
from types import SimpleNamespace
import argparse
import copy
import json
import math
import re

from prepare import ENGINE, read, save
from scene_report import project_paths, scenes, write_analysis
from theme_workspace import build, fresh, manifest_names, resolved_source_profile, stage_source


def environments(args):
    inputs = read(args.inputs) if args.inputs else {}
    if not args.environments:
        return [("default", inputs)]
    entries = read(args.environments)
    if not isinstance(entries, list) or not entries:
        raise ValueError("Environments require a nonempty array of {id, system}")
    names = set()
    result = []
    for entry in entries:
        name, values = entry["id"], entry["system"]
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name)
                or name.casefold() in names):
            raise ValueError(f"Invalid or duplicate environment ID: {name}")
        if not isinstance(values, dict) or any(
            type(value) not in {str, bool, int, float}
            or isinstance(value, float) and not math.isfinite(value)
            for value in values.values()
        ):
            raise ValueError(f"Environment {name} needs finite scalar system inputs")
        names.add(name.casefold())
        combined = copy.deepcopy(inputs)
        combined.setdefault("system", {}).update(values)
        result.append((name, combined))
    return result


def run(args):
    out = fresh(args.out, [args.source])
    variants = environments(args)
    for name, inputs in variants:
        selected = out / "environments" / name if args.environments else out
        options = inputs.setdefault("interaction", {})
        if args.max_gestures is not None:
            options["maxExplorationGestures"] = args.max_gestures
        input_file = selected / "inputs.json"
        save(input_file, inputs)
        baseline = selected / "baseline"
        guided_inputs = {}
        if args.coverage_rounds > 1:
            discovery = SimpleNamespace(**{**vars(args), "out": selected / "coverage",
                                           "inputs": input_file, "environments": None})
            for variant in discover(discovery):
                report_file = discovery.out / variant["report"]
                guided_inputs[variant["manifest"]] = report_file.with_name("recommended-inputs.json")
        build(SimpleNamespace(
            engine=ENGINE, source=args.source, source_profile=args.source_profile,
            profiles=args.profiles, out=baseline, inputs=input_file, asset_root=None,
            algorithm="multires_app.learning.algorithm:RecordingAnchor",
            max_states=args.max_states, max_depth=args.max_depth,
            components=args.components, anchor_config=args.anchor_config, interaction_space=None,
            inputs_by_manifest=guided_inputs,
        ))
        base_index = baseline / "projects.json"
        baseline_paths = project_paths(base_index)
        final_paths = baseline_paths
        applied = 0
        delivery = baseline / "deliverables" / "delivery-summary.json"
        if args.adapter:
            # Import torch-dependent tools only for explicit optional inference.
            from apply import apply
            from predict import render_requests, run_prediction
            from train import MODEL
            requests = selected / "requests"
            render_requests(baseline_paths, requests)
            if read(requests / "manifest.json")["requests"]:
                predictions = selected / "predictions.json"
                run_prediction(requests, args.adapter.resolve(), (args.model or MODEL).resolve(),
                               predictions, dynamic=True)
                if read(predictions):
                    validated = selected / "validated"
                    apply(base_index, predictions, validated, Path(__file__).with_name("theme_workspace.py"))
                    final_paths = sorted(validated.glob("scenes/variant-*/project.json"),
                                         key=lambda f: int(f.parent.name.rsplit("-", 1)[1]))
                    delivery = validated / "deliverables" / "delivery-summary.json"
                    applied = read(validated / "verification.json")["exportProjectRequests"]
        stages = write_analysis(final_paths, selected / "scenes")
        item = {"environment": name, "scenes": stages, "sceneReport": str(selected / "scenes" / "summary.json"),
                "delivery": str(delivery), "acceptedModelRequests": applied, "modelUsed": applied > 0}
        if guided_inputs:
            item["coverageFeedback"] = str(selected / "coverage" / "summary.json")
        save(selected / "summary.json", item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    if args.environments:
        save(out / "summary.json", {"environments": [read(out / "environments" / n / "summary.json")
                                                    for n, _ in variants]})



def discover(args):
    from multires_app.engine.scene_engine import extract_assets, load_model
    from coverage_loop import explore_rounds
    from multires_app.pipeline.profiles import source_canvas_profile
    from theme_workspace import file_sha

    stage = stage_source(SimpleNamespace(source=args.source, out=args.out, asset_root=None))
    profile = read(args.source_profile) if args.source_profile else None
    manifests = manifest_names(stage.files)
    output = []
    for name, inputs in environments(args):
        interaction = inputs.setdefault("interaction", {})
        interaction.setdefault("coordinateSpace", "source")
        if args.max_gestures is not None:
            interaction["maxExplorationGestures"] = args.max_gestures
        for index, manifest in enumerate(manifests):
            folder = stage.out / "environments" / name / f"variant-{index}"
            folder.mkdir(parents=True)
            model = load_model(stage.root, manifest)
            design = resolved_source_profile(stage, profile, manifest)
            logical_profile = source_canvas_profile(model, design)
            model["layoutSourceViewport"] = logical_profile["resolutionPx"]
            model["inputs"] = inputs
            assets = extract_assets(stage.root, model, folder)
            graph, feedback = explore_rounds(
                model, logical_profile, assets, args.max_states, args.max_depth,
                interaction.get("maxExplorationGestures", 4096), args.coverage_rounds,
            )
            save(folder / "coverage-feedback.json", feedback)
            save(folder / "recommended-inputs.json", model["inputs"])
            report = scenes({"graph": graph, "model": model})
            file = folder / "scenes.json"
            save(file, {"manifest": manifest, "sourceProfile": logical_profile, **report})
            output.append({"environment": name, "manifest": manifest, "states": len(report["states"]),
                           "transitions": len(report["transitions"]), "coverage": report["coverage"],
                           "report": file.relative_to(stage.out).as_posix(),
                           "feedback": (folder / "coverage-feedback.json").relative_to(stage.out).as_posix(),
                           "selectedRound": feedback["selectedRound"]})
    if stage.archive and file_sha(stage.source) != stage.archive["sha256"]:
        raise ValueError("Source HWT changed during exploration")
    save(stage.out / "summary.json", {"variants": output, "nativeValidated": False})
    print(json.dumps({"reports": len(output),
                      "states": sum(v["states"] for v in output)}, ensure_ascii=False), flush=True)
    return output


def exploration_options(parser):
    parser.add_argument("--source", type=Path, required=True, help="source HWT or ZIP")
    parser.add_argument("--source-profile", type=Path, help="otherwise infer source viewport from HWT")
    parser.add_argument("--inputs", type=Path, help="system inputs and required gesture traces")
    parser.add_argument("--environments", type=Path, help="independent {id, system} snapshots")
    parser.add_argument("--max-states", type=int, default=96)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--max-gestures", type=int, help="overrides the engine's normal gesture sample budget")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--coverage-rounds", type=int, choices=range(1, 6), default=1,
                        help="iterate coverage; for run, discover first and feed paths into adaptation")

def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    start = commands.add_parser("run")
    exploration_options(start)
    start.add_argument("--profiles", type=Path, required=True, help="target device profiles")
    start.add_argument("--components", type=Path)
    start.add_argument("--anchor-config", type=Path)
    start.add_argument("--adapter", type=Path, help="optional trained numeric-head adapter")
    start.add_argument("--model", type=Path)
    search = commands.add_parser("discover", help="extract reached scenes without layout or export")
    exploration_options(search)
    existing = commands.add_parser("report")
    existing.add_argument("--projects", type=Path, required=True)
    existing.add_argument("--out", type=Path, required=True)
    args = cli.parse_args()
    if args.command in {"run", "discover"}:
        if args.max_states < 1 or args.max_depth < 0 or (args.max_gestures is not None and args.max_gestures < 1):
            cli.error("invalid exploration limits")
        args.out = args.out.resolve()
        (run if args.command == "run" else discover)(args)
    else:
        args.out.mkdir(parents=True, exist_ok=False)
        write_analysis(project_paths(args.projects), args.out)


if __name__ == "__main__":
    main()
