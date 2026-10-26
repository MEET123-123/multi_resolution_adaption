"""Evaluate and refine cached feasible layouts, export, then validate HWTs."""

import argparse
import hashlib
import json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from multires_app.layout.aesthetic_optimizer import optimize_device
from multires_app.layout.layout_aesthetics import SOURCES
from multires_app.pipeline.scene_pipeline import export_script
from multires_app.preview.scene_previewer import build_scene_preview
from multires_app.tools.refresh_previews import repack_record
from multires_app.tools.batch_convert import write_index


def update(path, budget=48, reset=False):
    p = json.loads(path.read_text(encoding="utf-8"))
    folder = path.parent
    baseline_path = folder / "aesthetic-before.json"
    source_hash = hashlib.sha256(Path(p["source"]).read_bytes()).hexdigest()
    if not baseline_path.exists() or reset:
        baseline = {
            "sourceSha256": source_hash,
            "devices": {
                d["profile"]["id"]: {l["state"]: l["transforms"] for l in d["layouts"]}
                for d in p["devices"]
            },
        }
        if baseline_path.exists():
            baseline_path.with_suffix(".previous.json").write_bytes(baseline_path.read_bytes())
        baseline_path.write_text(
            json.dumps(baseline, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
    else:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline["sourceSha256"] != source_hash:
        raise ValueError("aesthetic baseline belongs to a different source package: " + str(path))
    for d in p["devices"]:
        for l in d["layouts"]:
            if l["state"] not in baseline["devices"].get(d["profile"]["id"], {}):
                raise ValueError(
                    "scene set changed; create a new aesthetic baseline with --reset-baseline"
                )
            l["transforms"] = baseline["devices"][d["profile"]["id"]][l["state"]]
    p["_projectFolder"] = str(folder.resolve())
    results = {}
    for d in p["devices"]:
        results[d["profile"]["id"]] = optimize_device(p, d, {"candidateBudget": budget})
    exports = [
        export_script(p, d["profile"]["id"], folder / "scripts" / (d["profile"]["id"] + ".xml"))
        for d in p["devices"]
    ]
    for key in list(p):
        if key.startswith("_"):
            p.pop(key)
    temporary = path.with_suffix(".json.aesthetic")
    temporary.write_text(json.dumps(p, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)
    report = {
        "sourceSha256": source_hash,
        "sources": SOURCES,
        "devices": results,
        "nativeDeviceTest": False,
        "humanCalibrated": False,
    }
    (folder / "aesthetic-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    existing = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    existing["aesthetics"] = results
    existing["exports"] = exports
    (folder / "report.json").write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    build_scene_preview(p, folder / "preview.html")
    row = {
        "project": folder.parent.name,
        "devices": len(results),
        "improvedDevices": sum(r["afterScore"] > r["beforeScore"] + 1e-7 for r in results.values()),
        "beforeScore": sum(r["beforeScore"] for r in results.values()) / len(results),
        "afterScore": sum(r["afterScore"] for r in results.values()) / len(results),
        "acceptedSteps": sum(len(r["acceptedSteps"]) for r in results.values()),
        "candidatesTested": sum(r["candidatesTested"] for r in results.values()),
        "validationFailures": sum(bool(r["validationError"]) for r in results.values()),
    }
    print(json.dumps(row, ensure_ascii=True), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, choices=[1, 2, 3, 4], default=3)
    parser.add_argument("--candidate-budget", type=int, default=48)
    parser.add_argument("--reset-baseline", action="store_true")
    parser.add_argument("--project", action="append")
    parser.add_argument("--skip-packages", action="store_true")
    args = parser.parse_args()
    root = Path("theme/adaptation")
    paths = sorted(root.glob("*/scenes/project.json"))
    if args.project:
        paths = [p for p in paths if p.parent.parent.name in args.project]
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed(
            [pool.submit(update, p, args.candidate_budget, args.reset_baseline) for p in paths]
        ):
            rows.append(future.result())
    rows.sort(key=lambda r: r["project"])
    report = {
        "sources": SOURCES,
        "projects": rows,
        "devicePlans": sum(r["devices"] for r in rows),
        "improvedDevices": sum(r["improvedDevices"] for r in rows),
        "acceptedSteps": sum(r["acceptedSteps"] for r in rows),
        "candidatesTested": sum(r["candidatesTested"] for r in rows),
        "validationFailures": sum(r["validationFailures"] for r in rows),
        "humanCalibrated": False,
        "scope": "all cached stable states; mean proxy score per unique geometry, not human preference percentage",
    }
    report["beforeScore"] = (
        sum(r["beforeScore"] * r["devices"] for r in rows) / report["devicePlans"] if rows else None
    )
    report["afterScore"] = (
        sum(r["afterScore"] * r["devices"] for r in rows) / report["devicePlans"] if rows else None
    )
    (root / "aesthetic-validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not args.skip_packages:
        records = json.loads((root / "batch-summary.json").read_text(encoding="utf-8"))
        updated = []
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(repack_record, root, r) for r in records]):
                updated.append(future.result())
        updated.sort(key=lambda r: r["name"])
        write_index(root, updated)
        if any(r["status"] != "validated" or not r["sourceUnchanged"] for r in updated):
            raise SystemExit(1)
    if report["validationFailures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
