"""Create dynamic weak supervision by rerunning the original lockscreen scripts."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import argparse
import json
import shutil

from prepare import read, rows, save, sha, jsonl
from build_training_pairs import worker, exclusion, digest, TARGETS
from multires_app.pipeline.projects import create_model_project
from multires_app.engine.scene_engine import load_model
from dynamic_contract import dynamic_group_contract

PROFILES = {
    "short": {"hour": 1, "hour24": 1, "minute": 5, "second": 2, "month": 1, "date": 1,
              "day_of_week": 5, "battery_level": 9, "battery_state": 0},
    "long": {"hour": 11, "hour24": 23, "minute": 58, "second": 59, "month": 11, "date": 28,
             "day_of_week": 7, "battery_level": 100, "battery_state": 0},
}


def profile_for_replay(profile):
    result = dict(profile)
    orientation = result.pop("orientation")
    result["id"] = result["id"].removesuffix("-" + orientation)
    result["baseOrientation"] = orientation
    result["orientations"] = [orientation]
    return result


def create_job(meta, sample, preset, directory, count):
    completed = directory / "pairs" / "worker.json"
    if completed.exists() and read(completed)["status"] == "complete":
        cached = read(completed)
        project_file = directory / "scenes" / "project.json"
        model_input = read(project_file)["model"]["inputs"]["system"]
        if (cached["sourceSha256"] != sample["sourceSha256"] or
                cached["projectSha256"] != sha(project_file) or
                any(model_input.get(k) != v for k, v in PROFILES[preset].items())):
            raise ValueError(f"Existing dynamic job no longer matches: {directory}")
        return directory, list(rows(completed.parent / "pairs.jsonl")), sample, preset
    path = Path(meta["project"])
    if sha(path) != meta["projectSha256"]:
        raise ValueError(f"Modified cached project: {path}")
    if sha(meta["actualSource"]) != sample["sourceSha256"]:
        raise ValueError(f"Modified source archive: {meta['actualSource']}")
    project = read(path)
    shutil.copytree(path.parent / "assets", directory / "scenes" / "assets")
    model = load_model(Path(meta["actualSource"]), sample["manifest"])
    model["inputs"] = project["model"].get("inputs", {})
    settings = model.setdefault("inputs", {})
    settings.setdefault("system", {}).update(PROFILES[preset])
    profiles = [profile_for_replay(d["profile"]) for d in project["devices"]
                if d["profile"]["id"] in TARGETS]
    scenes = directory / "scenes"
    create_model_project(model, project["assets"], project["sourceProfile"], {"profiles": profiles},
                         scenes, "multires_app.learning.algorithm:RecordingAnchor", count, 2)
    worker(scenes / "project.json", directory / "pairs", count)
    records = list(rows(directory / "pairs" / "pairs.jsonl"))
    print(f"Dynamic {preset} {sample['sourceName']}: {len(records)} candidates", flush=True)
    return directory, records, sample, preset


def build(bundle, work, out, names, states, jobs):
    source = list(rows(bundle / "samples.jsonl"))
    original = read(bundle / "manifest.json")
    workers_dir = Path(original["workers"])
    actual_sources = {s["sha256"]: s["path"] for s in original["sources"]}
    workers = {(w["sourceSha256"], w["manifest"]): w for w in
               (read(p) for p in workers_dir.glob("*/worker.json"))}
    chosen = {}
    for prefix in names:
        item = next((s for s in source if s["sourceName"].startswith(prefix)), None)
        if not item:
            raise ValueError(f"No verified package for prefix {prefix}")
        chosen[item["sourceSha256"]] = item
    if set(s["split"] for s in chosen.values()) != {"train", "validation", "test"}:
        raise ValueError("Dynamic audit requires verified themes from all three splits")
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=False)
    for key in ("images", "evidence"):
        shutil.copytree(bundle / key, out / key)
    added = []
    skipped = Counter()
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        tasks = []
        for sample in chosen.values():
            meta = {**workers[sample["sourceSha256"], sample["manifest"]],
                    "actualSource": actual_sources[sample["sourceSha256"]]}
            for preset in PROFILES:
                task = pool.submit(create_job, meta, sample, preset,
                                   work / (sample["sourceSha256"][:9] + "-" + preset), states)
                tasks.append(task)
        for task in as_completed(tasks):
            directory, candidates, seed_sample, preset = task.result()
            for row in candidates:
                reason = exclusion(row)
                folder = directory / "pairs"
                evidence = read(folder / row["evidence"])
                dynamic = dynamic_group_contract(row["request"], evidence)
                if reason or not dynamic:
                    skipped[reason or "no_measured_dynamic_layer"] += 1
                    continue
                identity = digest([row["id"], preset])[:24]
                for key in ("sourceImage", "targetImage", "evidence"):
                    file = folder / row[key]
                    dest = Path(row[key]).parent / (identity + "-" + key + file.suffix)
                    shutil.copy2(file, out / dest)
                    row[key] = dest.as_posix()
                row.update(id=identity, family=seed_sample["family"], split=seed_sample["split"],
                           dynamic={"system": PROFILES[preset], "groups": dynamic}, dynamicPreset=preset)
                row["label"]["origin"] = "script reevaluation under dynamic system inputs; anchor solver"
                added.append(row)
    if set(s["split"] for s in added) != {"train", "validation", "test"}:
        raise RuntimeError(f"Dynamic cases must pass checks across all splits: {dict(skipped)}")
    added.sort(key=lambda row: row["id"])
    jsonl(out / "samples.jsonl", source + added)
    jsonl(out / "dynamic-samples.jsonl", added)
    seconds = Counter((x["split"], x["dynamicPreset"]) for x in added)
    save(out / "manifest.json", {**original, "samples": len(source) + len(added),
         "counts": Counter(s["split"] for s in source + added), "dynamicPresets": PROFILES,
         "dynamicCounts": {f"{split}/{preset}": n for (split, preset), n in seconds.items()},
         "dynamicSkipped": skipped,
         "dynamicSemantics": "Source and target are reevaluated by the script interpreter with the same system input. Each variant retains its original HWT family and split."})
    print(json.dumps({f"{split}/{preset}": n for (split, preset), n in seconds.items()}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--themes", nargs="+", required=True)
    parser.add_argument("--states", type=int, default=5)
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args()
    build(args.bundle, args.work, args.out, args.themes, args.states, args.jobs)
