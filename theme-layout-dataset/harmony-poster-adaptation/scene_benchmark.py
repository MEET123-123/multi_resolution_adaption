"""Replay frozen layout-dataset workers, including reuse states outside the SFT rows."""
from collections import Counter, defaultdict
from pathlib import Path
import argparse
import json

from prepare import DEFAULT_CACHE, read, rows, save, sha
from scene_report import decisions, scenes
from multires_app.learning.algorithm import RecordingAnchor
from multires_app.evaluation.checks import ContractEvaluator, GeometryEvaluator
from multires_app.evaluation.core import Context
from multires_app.pipeline.scene_clusters import schedule_device


def evaluate(bundle, workers, out, split):
    expected = [r for r in rows(bundle / "samples.jsonl") if split == "all" or r["split"] == split]
    if not expected:
        raise ValueError("The selected split has no samples")
    metadata = {}
    for file in workers.glob("*/worker.json"):
        w = read(file)
        if w["status"] == "complete":
            metadata[w["sourceSha256"], w["manifest"]] = w
    packages = defaultdict(list)
    for row in expected:
        packages[row["sourceSha256"], row["manifest"]].append(row)
    out.mkdir(parents=True, exist_ok=False)
    classifications = Counter()
    recorded_modes = Counter()
    statuses = Counter()
    checks_by_split = defaultdict(Counter)
    seen = set()
    results = []
    cache_stats = []
    issues = []
    for instance, targets in packages.items():
        worker = metadata[instance]
        path = Path(worker["project"])
        if sha(path) != worker["projectSha256"]:
            raise ValueError(f"Frozen project no longer matches worker SHA: {path}")
        project = read(path)
        graph = scenes(project)
        sampling = {r["device"]: set(r["states"]) for r in worker["sampling"]}
        planned_devices = [d for d in project["devices"] if d["profile"]["id"] in sampling]
        full_counts = Counter(l["layoutRequest"]["kind"] for d in planned_devices for l in d["layouts"])
        cache_stats.append({"manifest": instance[1], "sha256": instance[0],
                            "states": len(graph["states"]), "transitions": len(graph["transitions"]),
                            "scopeBounded": not graph["frontierExhaustedForSampler"],
                            "recordedKinds": dict(full_counts)})
        selected = set().union(*sampling.values())
        project["sourceDevice"]["layouts"] = [l for l in project["sourceDevice"]["layouts"]
                                               if l["state"] in selected]
        for device in planned_devices:
            device_id = device["profile"]["id"]
            desired = {r["state"]: r for r in targets if r["device"] == device_id}
            if not desired:
                continue
            device["layouts"] = [l for l in device["layouts"] if l["state"] in sampling[device_id]]
            project["devices"] = [device]
            schedule_device(project, device, RecordingAnchor())
            evaluated = {r["state"]: r for r in decisions(project, device)}
            by_state = {l["state"]: l for l in device["layouts"]}
            observations = defaultdict(Counter)
            for evaluator in (GeometryEvaluator(), ContractEvaluator()):
                for check in evaluator.evaluate(Context(project, path)):
                    observations[check.state][check.status] += 1
                    statuses[check.status] += 1
                    if check.status in {"fail", "unknown", "review"}:
                        issues.append({"sourceSha256": instance[0], "manifest": instance[1],
                                       "device": device_id, "state": check.state,
                                       "inSelectedDataset": check.state in desired,
                                       "rule": check.rule, "status": check.status,
                                       "components": check.members, "detail": check.message})
            for decision in evaluated.values():
                classifications[decision["engineKind"]] += 1
            for state, sample in desired.items():
                seen.add(sample["id"])
                actual = evaluated[state]
                recorded = sample["request"]["requestKind"]
                raw = by_state[state].get("adaptationPlan", {}).get("layoutProposal", {})
                matched = raw.get("key") == sample["requestKey"]
                repeat = raw.get("request", {}).get("requestKind")
                validity = observations[state]
                recorded_modes[recorded] += 1
                counts = checks_by_split[sample["split"]]
                counts["samples"] += 1
                counts["requestKeyMatched"] += int(matched)
                counts["requestModeMatched"] += int(repeat == recorded)
                counts["finalKindMatchedRecordedRequest"] += int(actual["engineKind"] == recorded)
                counts["geometryOrContractIssue"] += int(any(validity[x] for x in ("fail", "unknown", "review")))
                results.append({"id": sample["id"], "split": sample["split"], "family": sample["family"],
                                "device": device_id, "state": state, "recordedRequestKind": recorded,
                                "replayedRequestKind": repeat, "requestKeyMatched": matched,
                                "engineKind": actual["engineKind"], "checks": validity})
        print(f"Replayed {len(seen)}/{len(expected)} recorded samples", flush=True)
    if len(seen) != len(expected):
        raise ValueError("Benchmark did not reach every selected dataset case")
    summary = {"samples": len(seen), "families": len({r["family"] for r in expected}),
               "sourceVariants": len(packages), "split": split,
               "bySplit": checks_by_split, "recordedDatasetModes": recorded_modes,
               "replayedModesIncludingNonSFTStates": classifications,
               "checksAcrossReplayedStates": statuses,
               "scope": "reproduction against weak rule-generated records; no expert annotations or device verification",
               "cachedGraphs": cache_stats}
    save(out / "summary.json", summary)
    save(out / "cases.json", results)
    save(out / "issues.json", issues)
    print(json.dumps({k: v for k, v in summary.items() if k != "cachedGraphs"}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_CACHE / "server-bundle")
    parser.add_argument("--workers", type=Path, default=DEFAULT_CACHE / "workers")
    parser.add_argument("--split", choices=("all", "train", "validation", "test"), default="test")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.bundle, args.workers, args.out, args.split)


if __name__ == "__main__":
    main()
