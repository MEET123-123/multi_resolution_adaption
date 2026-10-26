"""Immutable runs, independent candidate evaluation, regression and feedback."""

import copy
import json
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from .core import Context, Observation, SCHEMA, digest, evaluate, write_json, validate_options
from .checks import defaults
from .input_checks import InputEvaluator, file_sha
from .replay import ReplayEvaluator
from .responsive import ResponsiveEvaluator
from .gate import compare, exit_code
from .report import build_report, evidence


def clean(project):
    return {k: v for k, v in project.items() if not k.startswith("_")}


def source_digest(path):
    return (
        file_sha(path)
        if path.is_file()
        else digest(
            {
                p.relative_to(path).as_posix(): file_sha(p)
                for p in sorted(path.rglob("*"))
                if p.is_file()
            }
        )
    )


def plugins(extra=()):
    return [InputEvaluator(), *defaults(), ReplayEvaluator(), ResponsiveEvaluator(), *extra]


def evaluate_project(path, options=None, extra=(), project=None):
    p = json.loads(path.read_text(encoding="utf8")) if project is None else project
    targets = (options or {}).get("targets")
    if targets:
        p["devices"] = [d for d in p["devices"] if d["profile"]["id"] in targets]
        if not p["devices"] or set(targets) != {d["profile"]["id"] for d in p["devices"]}:
            raise ValueError("requested target device missing")
    p["_projectFolder"] = str(path.parent.resolve())
    ctx = Context(p, path, options or {})
    source = Path(p.get("source", ""))
    ctx.cache["sourceDigest"] = source_digest(source) if source.exists() else None
    return ctx, evaluate(ctx, plugins(extra))


def run(paths, out, options=None, extra=(), refiner=None, rounds=1):
    validate_options(options or {})
    out = Path(out).resolve()
    if out.exists():
        raise ValueError("run directory already exists; choose a new run id")
    if not paths:
        raise ValueError("no projects selected")
    for path in paths:
        if out.is_relative_to(Path(path).resolve().parent):
            raise ValueError("evaluation outputs must be separate from source project directories")
    out.mkdir(parents=True)
    options = options or {}
    records, entries, histories = [], [], []
    contexts = {}
    before_hashes = {}
    for path in paths:
        path = Path(path).resolve()
        try:
            project_before = file_sha(path)
            source = Path(json.loads(path.read_text(encoding="utf8"))["source"])
            if source.is_dir() and out.is_relative_to(source.resolve()):
                raise ValueError("output cannot be inside a read-only source theme")
            if source.exists():
                before_hashes[source] = source_digest(source)
            ctx, baseline = evaluate_project(path, options, extra)
            snapshot = out / "inputs" / path.parent.parent.name / "project.json"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, snapshot)
            if file_sha(snapshot) != project_before or file_sha(path) != project_before:
                raise ValueError("cached project changed during evaluation")
            best, selected = ctx, baseline
            for iteration in range(rounds if refiner else 0):
                try:
                    candidate = refiner.propose(
                        copy.deepcopy(clean(best.project)), selected, options
                    )
                    # Layout proposals may not change behavior, identities,
                    # resources, inputs, graph, roles, or captured source poses.
                    immutable = (
                        "source",
                        "sourceProfile",
                        "model",
                        "graph",
                        "components",
                        "nodeOrder",
                        "nodeComponents",
                        "assetOrder",
                        "assets",
                        "sourceDevice",
                    )
                    if any(candidate.get(k) != best.project.get(k) for k in immutable):
                        raise ValueError(
                            "candidate changed behavior, source geometry or resource identity"
                        )
                    if [(d["profile"], d["viewport"]) for d in candidate["devices"]] != [
                        (d["profile"], d["viewport"]) for d in best.project["devices"]
                    ]:
                        raise ValueError("candidate changed evaluation devices")
                    captured = lambda p: [
                        [
                            {
                                k: l.get(k)
                                for k in (
                                    "state",
                                    "rows",
                                    "controlValues",
                                    "overrides",
                                    "timeMs",
                                    "mediaLayout",
                                )
                            }
                            for l in d["layouts"]
                        ]
                        for d in p["devices"]
                    ]
                    if captured(candidate) != captured(best.project):
                        raise ValueError(
                            "candidate changed captured nodes, visibility or interaction state"
                        )
                    cctx, after = evaluate_project(path, options, extra, candidate)
                    decision = compare(selected, after)
                    confirmed = {r["id"] for r in selected if r["status"] == "fail"}
                    accepted = decision["regressionPassed"] and bool(
                        confirmed & set(decision["improvements"])
                    )
                    folder = out / "candidates" / path.parent.parent.name / str(iteration + 1)
                    write_json(folder / "comparison.json", decision)
                    write_json(folder / "before.json", selected)
                    write_json(folder / "after.json", after)
                    write_json(folder / "project.json", clean(candidate))
                    # Export is mandatory for an accepted candidate. The existing
                    # exporter executes required constraints and lexical audits.
                    exports = []
                    if accepted:
                        from multires_app.pipeline.scene_pipeline import export_script

                        for d in candidate["devices"]:
                            did = d["profile"]["id"]
                            if Path(did).name != did or any(c in did for c in "/\\:"):
                                raise ValueError("unsafe device artifact id")
                            exports.append(
                                export_script(candidate, did, folder / "scripts" / (did + ".xml"))
                            )
                        # A reviewable candidate includes its existing assets and
                        # preview. Copy only after the regression/export gates.
                        if (path.parent / "assets").exists():
                            shutil.copytree(path.parent / "assets", folder / "assets")
                        from multires_app.preview.scene_previewer import build_scene_preview

                        build_scene_preview(candidate, folder / "preview.html")
                        write_json(folder / "project.json", clean(candidate))
                        best, selected = cctx, after
                    histories.append(
                        {
                            "project": path.parent.parent.name,
                            "iteration": iteration + 1,
                            "refiner": {"name": refiner.name, "version": refiner.version},
                            "accepted": accepted,
                            "selectionReason": (
                                "confirmed failure improved without regression"
                                if accepted
                                else "no confirmed improvement, regression, or subjective review required"
                            ),
                            "decision": decision,
                            "exports": exports,
                            "nativeValidation": "needs_native_validation",
                        }
                    )
                    if not accepted:
                        break
                except Exception as exc:
                    histories.append(
                        {
                            "project": path.parent.parent.name,
                            "iteration": iteration + 1,
                            "accepted": False,
                            "error": str(exc),
                        }
                    )
                    break
            records.extend(selected)
            write_json(
                out / "responsive" / (path.parent.parent.name + ".json"),
                best.cache.get("responsiveGraphs", []),
            )
            for row in selected:
                contexts[row["id"]] = best
            entries.append(
                {
                    "project": path.parent.parent.name,
                    "projectSha256": project_before,
                    "inputProject": snapshot.relative_to(out).as_posix(),
                    "inputIdentity": ctx.identity,
                    "baselineChecks": len(baseline),
                    "selectedChecks": len(selected),
                    "algorithm": ctx.project.get("algorithmSpec"),
                    "selectedAlgorithm": best.project.get("algorithmSpec"),
                    "devices": [d["profile"] for d in best.project["devices"]],
                }
            )
        except Exception as exc:
            # Corrupt projects remain visible and do not kill the rest of a batch.
            row = {
                "schema": SCHEMA,
                "id": digest(str(path))[:24],
                "cluster": "input-unreadable",
                "project": path.parent.parent.name,
                "device": "",
                "state": "",
                "rule": "INPUT.PROJECT",
                "status": "fail",
                "severity": "P0",
                "confidence": 1.0,
                "residual": 0.0,
                "stage": "input",
                "members": [],
                "axis": "",
                "trace": [],
                "phase": "stable",
                "evidence": {"error": str(exc)},
                "plugin": {"name": "loader", "version": "1.0"},
                "inputIdentity": "unreadable",
                "nativeValidation": "needs_native_validation",
                "rootCauseStatus": "suspected",
            }
            records.append(row)
    # Representative evidence first, then deterministic PASS random audit.
    prioritized = sorted(
        [r for r in records if r["device"] and r["state"] and r["status"] in {"fail", "review"}],
        key=lambda r: (r["status"] != "fail", r["severity"], -r["residual"], r["id"]),
    )
    rendered, seen = 0, set()
    for row in prioritized:
        if row["cluster"] in seen or rendered >= options.get("evidenceBudget", 20):
            continue
        try:
            row["artifacts"] = evidence(contexts[row["id"]], row, out / "cases" / row["id"])
            rendered += 1
            seen.add(row["cluster"])
        except Exception as exc:
            row["evidenceError"] = str(exc)
    audit = sorted(
        [r for r in records if r["status"] == "pass" and r["device"] and r["state"]],
        key=lambda r: digest([options.get("seed", 0), r["id"]]),
    )[: options.get("passSamples", 10)]
    for row in audit:
        try:
            row["artifacts"] = evidence(contexts[row["id"]], row, out / "cases" / row["id"])
        except Exception as exc:
            row["evidenceError"] = str(exc)
    source_unchanged = all(source_digest(path) == sha for path, sha in before_hashes.items())
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    code_root = Path(__file__).resolve().parents[1]
    code_hash = digest(
        {p.relative_to(code_root).as_posix(): file_sha(p) for p in code_root.rglob("*.py")}
    )
    manifest = {
        "schema": SCHEMA,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "engineCommit": commit,
        "workingCodeSha256": code_hash,
        "python": platform.python_version(),
        "options": options,
        "evaluators": [{"name": p.name, "version": p.version} for p in plugins(extra)],
        "projects": entries,
        "sources": [{"name": p.name, "sha256": sha} for p, sha in before_hashes.items()],
        "sourceUnchanged": source_unchanged,
        "rulesetVersion": "1.0",
        "rendererVersion": "offline-pillow/1",
        "nativeValidation": "not_measured",
        "humanCalibration": "not_measured",
    }
    write_json(out / "iterations.json", histories)
    write_json(out / "pass-audit.json", audit)
    summary = build_report(out, records, manifest)
    return summary, 1 if not source_unchanged else exit_code(records)


def read_records(run):
    return list(iter_records(run))


def iter_records(run):
    with (Path(run) / "observations.jsonl").open(encoding="utf8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


class JsonlRecords:
    def __init__(self, path):
        self.path = path

    def __iter__(self):
        with self.path.open(encoding="utf8") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def find_record(run, case_id):
    """Constant-memory lookup, with stale-index detection and JSONL fallback."""
    import sqlite3
    from contextlib import closing

    run = Path(run)
    if (run / "cases.sqlite3").exists():
        with closing(sqlite3.connect(run / "cases.sqlite3")) as connection:
            size, mtime = connection.execute("SELECT size, mtime FROM metadata").fetchone()
            stat = (run / "observations.jsonl").stat()
            if (size, mtime) != (stat.st_size, stat.st_mtime_ns):
                raise ValueError("observation index is stale; report was modified")
            match = connection.execute("SELECT offset FROM cases WHERE id=?", (case_id,)).fetchone()
        if match:
            with (run / "observations.jsonl").open(encoding="utf8") as stream:
                stream.seek(match[0])
                row = json.loads(stream.readline())
                if row["id"] != case_id:
                    raise ValueError("observation index does not match artifact")
                return row
        raise ValueError("unknown case id")
    return next(r for r in iter_records(run) if r["id"] == case_id)


def parallel_run(paths, out, options=None, extra=(), workers=1, resume=False):
    """Process isolation for independent theme batches; no shared geometry cache."""
    validate_options(options or {})
    if workers == 1 and not resume:
        return run(paths, out, options, extra)
    from concurrent.futures import ProcessPoolExecutor, as_completed

    out = Path(out).resolve()
    if (out.exists() and not resume) or not paths:
        raise ValueError("new output directory and nonempty input list required")
    for path in paths:
        if out.is_relative_to(Path(path).resolve().parent):
            raise ValueError("output must be separate from source projects")
    out.mkdir(parents=True, exist_ok=resume)
    manifests = []
    pending = out / "observations.pending.jsonl"
    pending.write_text("", encoding="utf8")
    options = options or {}
    expected_plugins = [{"name": p.name, "version": p.version} for p in plugins(extra)]

    def collect(relative):
        manifest = json.loads((out / relative / "manifest.json").read_text(encoding="utf8"))
        manifest["_artifactPrefix"] = relative
        manifests.append(manifest)
        repro_path = out / relative / "repro.json"
        repro = (
            json.loads(repro_path.read_text(encoding="utf8")).get("traces", {})
            if repro_path.exists()
            else {}
        )
        with pending.open("a", encoding="utf8") as stream:
            for row in iter_records(out / relative):
                row["artifacts"] = {
                    k: relative + "/" + v for k, v in row.get("artifacts", {}).items()
                }
                if row.get("traceRef") in repro:
                    row["trace"] = repro[row["traceRef"]]
                row["runRef"] = relative + "/manifest.json"
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for index, path in enumerate(paths):
            relative = "projects/" + str(index).zfill(4)
            local = {
                **options,
                "evidenceBudget": 1 if index < options.get("evidenceBudget", 20) else 0,
                "passSamples": 1 if index < options.get("passSamples", 10) else 0,
            }
            folder = out / relative
            completed = folder / "manifest.json"
            if (
                resume
                and completed.exists()
                and (folder / "summary.json").exists()
                and (folder / "observations.jsonl").exists()
            ):
                cached = json.loads(completed.read_text(encoding="utf8"))
                source = Path(json.loads(Path(path).read_text(encoding="utf8"))["source"])
                expected_sha = next(
                    (s["sha256"] for s in cached["sources"] if s["name"] == source.name), None
                )
                if (
                    cached["evaluators"] == expected_plugins
                    and cached["options"] == local
                    and len(cached["projects"]) == 1
                    and cached["projects"][0]["projectSha256"] == file_sha(path)
                    and expected_sha == source_digest(source)
                    and cached["sourceUnchanged"]
                ):
                    collect(relative)
                    continue
            if folder.exists():
                backup = (
                    out
                    / "interrupted"
                    / (str(index).zfill(4) + "-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
                )
                if not folder.resolve().is_relative_to(out) or not backup.resolve().is_relative_to(
                    out
                ):
                    raise ValueError("resume directory escaped run root")
                backup.parent.mkdir(parents=True, exist_ok=True)
                folder.rename(backup)
            futures[pool.submit(run, [path], out / relative, local, extra)] = (path, relative)
        for future in as_completed(futures):
            path, relative = futures[future]
            try:
                future.result()
                collect(relative)
            except Exception as exc:
                error_row = {
                    "schema": SCHEMA,
                    "id": digest([str(path), "worker"])[:24],
                    "cluster": "worker-error",
                    "project": Path(path).parent.parent.name,
                    "rule": "SYSTEM.WORKER_ERROR",
                    "status": "unknown",
                    "severity": "P0",
                    "confidence": 1.0,
                    "device": "",
                    "state": "",
                    "members": [],
                    "residual": 0.0,
                    "axis": "",
                    "stage": "evaluation",
                    "trace": [],
                    "inputIdentity": "unknown",
                    "evidence": {"error": str(exc)},
                    "plugin": {"name": "worker", "version": "1.0"},
                }
                with pending.open("a", encoding="utf8") as stream:
                    stream.write(json.dumps(error_row, ensure_ascii=False) + "\n")
    if not manifests:
        raise ValueError("no worker produced an evaluation manifest")
    manifest = {
        **manifests[0],
        "options": options,
        "parallelWorkers": workers,
        "resumed": resume,
        "projects": [
            (
                {**e, "inputProject": m["_artifactPrefix"] + "/" + e["inputProject"]}
                if e.get("inputProject")
                else e
            )
            for m in manifests
            for e in m["projects"]
        ],
        "sources": [e for m in manifests for e in m["sources"]],
        "sourceUnchanged": (
            all(m["sourceUnchanged"] for m in manifests) if len(manifests) == len(paths) else None
        ),
    }
    summary = build_report(out, JsonlRecords(pending), manifest)
    pending.unlink()
    statuses = summary["statuses"]
    return summary, (
        1 if statuses.get("fail") else 2 if statuses.get("unknown") or statuses.get("review") else 0
    )


def review(run, case_id, label, reviewer, notes="", cause="", preference=""):
    records = {case_id: find_record(run, case_id)}
    if case_id not in records or label not in {"confirm", "dismiss", "unknown"}:
        raise ValueError("unknown case/label")
    path = Path(run) / "reviews.jsonl"
    entry = {
        "caseId": case_id,
        "label": label,
        "reviewer": reviewer,
        "notes": notes,
        "cause": cause,
        "preference": preference,
        "inputIdentity": records[case_id]["inputIdentity"],
        "observationSha256": digest(records[case_id]),
        "time": datetime.now(timezone.utc).isoformat(),
    }
    # Separate append-only review history; automated observations stay immutable.
    with path.open("a", encoding="utf8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def freeze(run, case_id, out, expected="pass", split="validation"):
    records = {case_id: find_record(run, case_id)}
    row = records[case_id]
    reviews = [
        json.loads(line)
        for line in (Path(run) / "reviews.jsonl").read_text(encoding="utf8").splitlines()
        if line.strip()
    ]
    if not any(r["caseId"] == case_id and r["label"] in {"confirm", "dismiss"} for r in reviews):
        raise ValueError("a reviewed label is required before freezing")
    out = Path(out)
    if out.exists():
        raise ValueError("golden fixture exists; never overwrite automatically")
    if expected not in {"pass", "fail", "review", "unknown"} or split not in {
        "train",
        "validation",
        "test",
    }:
        raise ValueError("invalid golden expectation/split")
    repro_file = Path(run) / "repro.json"
    trace = (
        json.loads(repro_file.read_text(encoding="utf8"))
        .get("traces", {})
        .get(row.get("traceRef"), row["trace"])
        if repro_file.exists()
        else row["trace"]
    )
    write_json(
        out,
        {
            "schema": SCHEMA,
            "split": split,
            "case": {**row, "trace": trace},
            "expected": expected,
            "review": next(r for r in reversed(reviews) if r["caseId"] == case_id),
        },
    )


def regression(run, fixtures):
    fixtures = [json.loads(Path(path).read_text(encoding="utf8")) for path in fixtures]
    wanted = {f["case"]["id"] for f in fixtures}
    scopes = {
        (f["case"]["inputIdentity"], f["case"]["device"], f["case"]["state"]) for f in fixtures
    }
    records = {
        r["id"]: r
        for r in iter_records(run)
        if r["id"] in wanted
        or (
            r["rule"] == "GEO.INVENTORY" and (r["inputIdentity"], r["device"], r["state"]) in scopes
        )
    }
    results = []
    splits = {}
    for fixture in fixtures:
        old = fixture["case"]
        theme = old["inputIdentity"]
        if theme in splits and splits[theme] != fixture["split"]:
            raise ValueError("theme leaked across dataset splits")
        splits[theme] = fixture["split"]
        current = records.get(old["id"])
        status = current["status"] if current else "unknown"
        # Positive-only overlaps can be resolved by absence, only if the exact
        # input/device/state inventory check was successfully evaluated.
        if (
            current is None
            and old["rule"] == "GEO.NEW_OVERLAP"
            and any(
                r["inputIdentity"] == theme
                and r["device"] == old["device"]
                and r["state"] == old["state"]
                and r["rule"] == "GEO.INVENTORY"
                and r["status"] == "pass"
                for r in records.values()
            )
        ):
            status = "pass"
        results.append(
            {
                "caseId": old["id"],
                "expected": fixture["expected"],
                "actual": status,
                "passed": status == fixture["expected"],
            }
        )
    return {"cases": results, "passed": bool(results) and all(r["passed"] for r in results)}
