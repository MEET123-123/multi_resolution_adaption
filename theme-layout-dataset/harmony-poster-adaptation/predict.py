"""Predict with source images and group evidence, including fresh engine projects."""
from pathlib import Path
from collections import Counter
import argparse

import torch

from prepare import read, rows, save, jsonl
from build_training_pairs import compact_input
from policy import decode, full_boxes, get_model, prepare_sample, route, violations
from train import MODEL, collect
from hierarchy import build as build_hierarchy, expand
from multires_app.evaluation.core import digest
from multires_app.learning.annotations import extract
from multires_app.learning.contract import validate_answer
from multires_app.learning.render import render


def render_requests(projects, out):
    out.mkdir(parents=True, exist_ok=False)
    collected, skipped = {}, Counter()
    for project_path in projects:
        project = read(project_path)
        sources = {l["state"]: l for l in project["sourceDevice"]["layouts"]}
        states = {s["id"]: s for s in project["graph"]["states"]}
        images = {}
        for device in project["devices"]:
            for layout in device["layouts"]:
                plan = layout.get("adaptationPlan", {})
                proposal = plan.get("layoutProposal")
                if layout.get("layoutRequest", {}).get("kind") == "reuse":
                    skipped["reuse"] += 1
                    continue
                if not proposal or not proposal["request"]["groups"]:
                    continue
                key = proposal["key"]
                if key in collected:
                    continue
                state = layout["state"]
                if state not in images:
                    name = f"images/{digest([str(project_path), state])[:24]}.png"
                    report = render(project, project["sourceDevice"], sources[state],
                                    project_path.parent / "assets", out / name)
                    images[state] = (name, report["status"])
                image, status = images[state]
                if status != "simulated":
                    skipped["incompleteSourceRender"] += 1
                    continue
                evidence_path = f"evidence/{key}.json"
                save(out / evidence_path, {"groupMap": proposal["mapping"],
                     "annotations": extract(project, sources[state], plan, states[state])})
                request, _ = compact_input(proposal["request"])
                collected[key] = {"id": key, "requestKey": key, "request": request,
                                  "sourceImage": image, "evidence": evidence_path,
                                  "state": state, "device": device["profile"]["id"]}
    jsonl(out / "samples.jsonl", collected.values())
    save(out / "manifest.json", {"requests": len(collected), "skipped": skipped})
    print(f"Captured {len(collected)} unique requests", flush=True)


def safe_path(base, relative):
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError(f"Missing or invalid bundle path: {relative}")
    return str(path.relative_to(base.resolve()).as_posix())


def local_repair(item, proposed):
    """Replace an individual risky proposal with its geometric fallback when beneficial."""
    fallback = decode(item, torch.zeros_like(proposed[:, :3]))
    current = proposed.clone()
    repaired = []
    for _ in range(3):
        current_score = float(violations(current, item))
        if current_score <= .0001:
            break
        possible = []
        for index in item["mask"].nonzero().flatten().tolist():
            candidate = current.clone()
            candidate[index] = fallback[index]
            possible.append((float(violations(candidate, item)), index, candidate))
        if not possible:
            break
        value, index, candidate = min(possible, key=lambda p: p[0])
        if value >= current_score - .0001:
            break
        current = candidate
        repaired.append(item["gids"][index])
    return current, repaired


def run_prediction(bundle, adapter, model_path, out, split=None, dynamic=False,
                   preset=None, composition_prior=None):
    tasks, inputs = [], []
    priors = read(composition_prior) if composition_prior else {}
    for row in rows(bundle / "samples.jsonl"):
        if split and row.get("split") != split:
            continue
        if preset and row.get("dynamicPreset", "existing") != preset:
            continue
        sample = {k: row[k] for k in ("id", "requestKey", "sourceImage", "evidence", "request")}
        for key in ("sourceImage", "evidence"):
            sample[key] = safe_path(bundle, sample[key])
        item = prepare_sample(sample, bundle, dynamic=dynamic)
        evidence = read(bundle / sample["evidence"])
        dense = len(sample["request"]["groups"]) > 12 or len(
            evidence["annotations"]["elementLevel"]["elements"]) >= 60
        hierarchy = build_hierarchy(item, evidence) if dense else None
        proxy, structure = hierarchy if hierarchy else (item, None)
        index = None
        if proxy is not None and proxy["mask"].any() and route(item) != "resize":
            index = len(inputs)
            inputs.append(proxy)
        tasks.append((item, proxy, structure, index))
    if not tasks:
        raise ValueError("No input scenes")
    if out.exists():
        raise ValueError("Use a new output file")
    predictions, compositions = {}, {}
    actions = []
    if inputs:
        model, processor, head = get_model(model_path, adapter)
        actions = collect(model, processor, head, inputs, bundle)
    for item, proxy, structure, index in tasks:
        intent = route(item)
        key = item["sample"]["requestKey"]
        if key in predictions or key in compositions:
            raise ValueError("Duplicate request key. Select a single runtime preset.")
        if structure:
            compositions[key] = structure
        if index is None:
            continue
        if structure:
            coarse = decode(proxy, actions[index])
            coarse, local_edits = local_repair(proxy, coarse)
            fallback = decode(item, torch.zeros_like(item["constants"][:, :3]))
            proposed, ops = expand(item, structure, coarse, fallback, priors.get(key, {}))
            structure["operations"] = ops
            structure["localFallbacks"] = local_edits
            holds = structure["heldGroups"]
        else:
            proposed, local_edits = local_repair(item, full_boxes(item, actions[index]))
            holds = [gid for gid, valid in zip(item["gids"], item["mask"]) if not valid]
        answer = {"schema": item["sample"]["request"]["schema"],
                  "groups": {gid: [round(v, 3) for v in box]
                             for gid, box in zip(item["gids"], (proposed * 1000).tolist())}}
        validate_answer(answer, item["sample"]["request"])
        predictions[key] = {"answer": answer, "provider": "harmony-poster",
                            "strategy": intent, "localRepairs": local_edits, "holdGroups": holds}
    save(out, predictions)
    if compositions:
        save(out.with_suffix(".hierarchy.json"), compositions)
    print(f"{len(predictions)} proposals, {len(tasks) - len(predictions)} bypassed, "
          f"{len(compositions)} hierarchical", flush=True)

def main():
    cli = argparse.ArgumentParser()
    sub = cli.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--projects", type=Path, required=True)
    capture.add_argument("--out", type=Path, required=True)
    predict = sub.add_parser("infer")
    predict.add_argument("--bundle", type=Path, required=True)
    predict.add_argument("--adapter", type=Path, required=True)
    predict.add_argument("--model", type=Path, default=MODEL)
    predict.add_argument("--split", choices=("train", "validation", "test"))
    predict.add_argument("--dynamic", action="store_true")
    predict.add_argument("--preset", choices=("short", "long", "existing"))
    predict.add_argument("--composition-prior", type=Path)
    predict.add_argument("--out", type=Path, required=True)
    args = cli.parse_args()
    if args.command == "capture":
        render_requests(list(map(Path, read(args.projects)["projects"])), args.out)
    else:
        run_prediction(args.bundle, args.adapter, args.model, args.out, args.split,
                       args.dynamic, args.preset, args.composition_prior)


if __name__ == "__main__":
    main()
