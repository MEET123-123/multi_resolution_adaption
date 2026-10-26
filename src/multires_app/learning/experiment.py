"""Bounded offline/local-model prediction and honest baseline comparisons."""

import base64
import copy
import html
import json
import os
import shutil
import time
import urllib.request
from collections import Counter
from pathlib import Path
from multires_app.evaluation.core import Context, evaluate, write_json
from multires_app.evaluation.gate import compare
from multires_app.evaluation.checks import GeometryEvaluator, ContractEvaluator, AestheticEvaluator
from multires_app.evaluation.input_checks import file_sha
from multires_app.pipeline.scene_clusters import schedule_device, describe_layout, box_union
from multires_app.pipeline.scene_pipeline import export_script
from .algorithm import RecordingAnchor, LearnedAnchor
from .contract import SYSTEM, parse_answer, prompt, validate_answer, normalized
from .dataset import read_samples, read_json, reviewed, actual_boxes, input_layout
from .render import render


def predict(
    directory, out, *, endpoint=None, model=None, replay_teacher=False, split="test", timeout=90
):
    directory = Path(directory).resolve()
    out = Path(out)
    if out.exists():
        raise ValueError("use a new prediction file")
    if not replay_teacher and (not endpoint or not model):
        raise ValueError(
            "endpoint and model required, or explicitly use --replay-teacher for a plumbing smoke check"
        )
    if endpoint and not endpoint.startswith(("http://", "https://")):
        raise ValueError("HTTP endpoint required")
    predictions = {}
    token = os.environ.get("MULTIRES_MODEL_API_KEY")
    labels = reviewed(directory)
    for sample in read_samples(directory):
        if split != "all" and sample["split"] != split:
            continue
        key = sample["requestKey"]
        if key in predictions:
            continue
        start = time.monotonic()
        row = {
            "provider": "teacher-replay" if replay_teacher else model,
            "modelEvidence": not replay_teacher,
        }
        try:
            if replay_teacher:
                answer = copy.deepcopy(sample["label"]["answer"])
            else:
                image = (directory / sample["sourceImage"]).resolve()
                if not image.is_relative_to(directory) or not image.is_file():
                    raise ValueError("source image missing or outside dataset")
                encoded = base64.b64encode(image.read_bytes()).decode()
                content = [
                    {
                        "type": "text",
                        "text": prompt(
                            sample["request"], input_layout(sample, labels.get(sample["id"]))
                        ).replace("<image>\n", "", 1),
                    },
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
                ]
                body = {
                    "model": model,
                    "temperature": 0,
                    "max_tokens": 2048,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": content},
                    ],
                }
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                req = urllib.request.Request(
                    endpoint, json.dumps(body, ensure_ascii=False).encode(), headers
                )
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    data = json.loads(response.read(4_000_000))
                answer = parse_answer(data["choices"][0]["message"]["content"], sample["request"])
                row["usage"] = data.get("usage", {})
            validate_answer(answer, sample["request"])
            row.update(status="valid", answer=answer)
        except Exception as exc:
            row.update(status="invalid", error=type(exc).__name__ + ": " + str(exc)[:300])
        row["seconds"] = round(time.monotonic() - start, 3)
        predictions[key] = row
        # Keep progress if the model service is interrupted.
        write_json(out, predictions)
    if not predictions:
        write_json(out, predictions)
    return {
        "predictions": len(predictions),
        "status": dict(Counter(r["status"] for r in predictions.values())),
        "modelEvidence": not replay_teacher,
        "scope": "raw proposals; constraint-projected geometry must be benchmarked",
    }


def benchmark(directory, predictions_file, out, *, split="test", export=False):
    directory = Path(directory).resolve()
    out = Path(out).resolve()
    if out.exists():
        raise ValueError("use a new experiment directory")
    out.mkdir(parents=True)
    (out / "images").mkdir()
    predictions = read_json(predictions_file)
    labels = reviewed(directory)
    samples = [s for s in read_samples(directory) if split == "all" or s["split"] == split]
    by_project = {}
    for s in samples:
        by_project.setdefault(s["project"], []).append(s)
    projects = []
    cases = []
    all_events = []
    errors = []
    raw_human_errors = []
    projection_errors = []
    synthetic_raw = []
    synthetic_final = []
    badcases = []
    for project_path, selected in by_project.items():
        path = Path(project_path)
        if not path.is_file() or any(s["projectSha256"] != file_sha(path) for s in selected):
            errors.append(
                {"project": project_path, "error": "input project changed; rebuild dataset"}
            )
            continue
        p = read_json(path)
        targets = {s["device"] for s in selected}
        p["devices"] = [d for d in p["devices"] if d["profile"]["id"] in targets]
        before_sha = file_sha(path)
        base = copy.deepcopy(p)
        candidate = copy.deepcopy(p)
        algorithm = LearnedAnchor(predictions)
        try:
            for d in base["devices"]:
                schedule_device(base, d, RecordingAnchor())
            for d in candidate["devices"]:
                schedule_device(candidate, d, algorithm)
            raw = copy.deepcopy(base)
            for s in selected:
                prediction = predictions.get(s["requestKey"], {})
                if prediction.get("status") != "valid":
                    badcases.append(
                        {
                            "sample": s["id"],
                            "stage": "model",
                            "rule": "MODEL.JSON",
                            "status": "fail",
                            "error": prediction.get("error", "missing prediction"),
                        }
                    )
                    continue
                validate_answer(prediction["answer"], s["request"])
                rd = next(d for d in raw["devices"] if d["profile"]["id"] == s["device"])
                rl = next(l for l in rd["layouts"] if l["state"] == s["state"])
                plan = rl["adaptationPlan"]
                meta = plan["layoutProposal"]
                groups = {g["id"]: g for g in plan["groups"]}
                desc = describe_layout(raw, rl)
                region = s["request"]["safeRegion"]

                def members(gid):
                    return [
                        cid
                        for m in groups[gid]["members"]
                        for cid in (members(m) if m in groups else [m])
                    ]

                for alias, gid in meta["mapping"].items():
                    ids = members(gid)
                    ref = box_union([desc[cid]["bounds"] for cid in ids])
                    x, y, w, h = prediction["answer"]["groups"][alias]
                    scale = min(
                        w * region["w"] / 1000 / ref["w"], h * region["h"] / 1000 / ref["h"]
                    )
                    left = region["x"] + (x + w / 2) * region["w"] / 1000 - ref["w"] * scale / 2
                    top = region["y"] + (y + h / 2) * region["h"] / 1000 - ref["h"] * scale / 2
                    for cid in ids:
                        rl["transforms"][cid] = {
                            "sx": scale,
                            "sy": scale,
                            "tx": left - ref["x"] * scale,
                            "ty": top - ref["y"] * scale,
                        }
            plugins = [GeometryEvaluator(), ContractEvaluator(), AestheticEvaluator()]
            before = evaluate(Context(base, path), plugins)
            after = evaluate(Context(candidate, path), plugins)
            raw_checks = evaluate(Context(raw, path), plugins)
            selected_states = {(s["device"], s["state"]) for s in selected}
            for stage, checks in [("raw", raw_checks), ("constrained", after)]:
                badcases.extend(
                    {**r, "stage": stage}
                    for r in checks
                    if (r["device"], r["state"]) in selected_states
                    and r["status"] in {"fail", "review", "unknown"}
                )
            decision = compare(before, after)
            tag = selected[0]["sourceSha256"][:16]
            folder = out / "projects" / tag
            write_json(folder / "baseline-observations.json", before)
            write_json(folder / "candidate-observations.json", after)
            write_json(folder / "raw-observations.json", raw_checks)
            write_json(
                folder / "candidate.json",
                {k: v for k, v in candidate.items() if not k.startswith("_")},
            )
            exports = []
            # An explicitly requested local export may be audited even when the
            # quality gate rejects selection. Keep it in a review-only folder;
            # successful XML compilation must never imply quality acceptance.
            if export and any(e["status"] == "proposed" for e in algorithm.events):
                for d in candidate["devices"]:
                    try:
                        did = d["profile"]["id"]
                        if Path(did).name != did or any(c in did for c in "/\\:"):
                            raise ValueError("invalid device ID")
                        export_script(
                            candidate, did, folder / "scripts-for-review" / (did + ".xml")
                        )
                        exports.append(
                            {
                                "device": did,
                                "status": "passed",
                                "qualitySelected": decision["regressionPassed"],
                                "scope": "local candidate audit only",
                            }
                        )
                    except Exception as exc:
                        exports.append(
                            {
                                "device": d["profile"]["id"],
                                "status": "failed",
                                "error": str(exc)[:500],
                            }
                        )
            changed = 0
            for s in selected:
                bd = next(d for d in base["devices"] if d["profile"]["id"] == s["device"])
                cd = next(d for d in candidate["devices"] if d["profile"]["id"] == s["device"])
                bl = next(l for l in bd["layouts"] if l["state"] == s["state"])
                cl = next(l for l in cd["layouts"] if l["state"] == s["state"])
                meta = cl.get("adaptationPlan", {}).get("layoutProposal", {})
                row = predictions.get(s["requestKey"], {})
                before_image = "images/" + s["id"] + "-baseline.png"
                after_image = "images/" + s["id"] + "-candidate.png"
                source_image = "images/" + s["id"] + "-source.png"
                raw_image = "images/" + s["id"] + "-raw.png"
                shutil.copyfile(directory / s["sourceImage"], out / source_image)
                rb = render(base, bd, bl, path.parent / "assets", out / before_image)
                if not (out / source_image).exists():
                    shutil.copyfile(directory / s["sourceImage"], out / source_image)
                rc = render(candidate, cd, cl, path.parent / "assets", out / after_image)
                rd = next(d for d in raw["devices"] if d["profile"]["id"] == s["device"])
                rl = next(l for l in rd["layouts"] if l["state"] == s["state"])
                render(raw, rd, rl, path.parent / "assets", out / raw_image)
                changed_case = bl["transforms"] != cl["transforms"]
                changed += int(changed_case)
                coverage = meta.get("key") == s["requestKey"] and meta.get("status") == "proposed"
                projection = None
                if coverage:
                    boxes = actual_boxes(candidate, cl, cl["adaptationPlan"]["groups"])
                    final = {
                        a: normalized(boxes[g], s["request"]["safeRegion"])
                        for a, g in meta["mapping"].items()
                    }
                    deltas = [
                        abs(v - w)
                        for a, b in row["answer"]["groups"].items()
                        for v, w in zip(b, final[a])
                    ]
                    if deltas:
                        projection = sum(deltas) / len(deltas)
                        projection_errors.append(projection)
                    if s["label"]["status"] == "synthetic":
                        expected = s["label"]["answer"]["groups"]
                        synthetic_raw.append(
                            sum(
                                abs(v - w)
                                for a, b in row["answer"]["groups"].items()
                                for v, w in zip(b, expected[a])
                            )
                            / (4 * len(expected))
                        )
                        synthetic_final.append(
                            sum(
                                abs(v - w) for a, b in final.items() for v, w in zip(b, expected[a])
                            )
                            / (4 * len(expected))
                        )
                review = labels.get(s["id"])
                raw_mae = None
                if row.get("status") == "valid" and review and review["status"] == "approved":
                    diffs = [
                        abs(v - w)
                        for a, b in row["answer"]["groups"].items()
                        for v, w in zip(b, review["answer"]["groups"][a])
                    ]
                    if diffs:
                        raw_mae = sum(diffs) / len(diffs)
                        raw_human_errors.append(raw_mae)
                cases.append(
                    {
                        "id": s["id"],
                        "device": s["device"],
                        "state": s["state"],
                        "split": s["split"],
                        "source": source_image,
                        "baseline": before_image,
                        "candidate": after_image,
                        "raw": raw_image,
                        "changed": changed_case,
                        "modelApplied": coverage,
                        "rawHumanMAE1000": raw_mae,
                        "projectionMAE1000": projection,
                        "rawValid": row.get("status") == "valid",
                        "rendering": {"baseline": rb, "candidate": rc},
                        "proposalStatus": meta.get("status", "unavailable"),
                    }
                )
            projects.append(
                {
                    "project": project_path,
                    "gate": decision,
                    "changedScenes": changed,
                    "exports": exports,
                    "rawCounts": dict(
                        Counter(
                            r["status"]
                            for r in raw_checks
                            if any(
                                (s["device"], s["state"]) == (r["device"], r["state"])
                                and predictions.get(s["requestKey"], {}).get("status") == "valid"
                                for s in selected
                            )
                        )
                    ),
                    "constrainedCounts": dict(
                        Counter(
                            r["status"]
                            for r in after
                            if (r["device"], r["state"]) in selected_states
                        )
                    ),
                }
            )
            all_events.extend(
                {"key": e["key"], "status": e["status"], "provider": e.get("provider", "")}
                for e in algorithm.events
            )
        except Exception as exc:
            errors.append(
                {"project": project_path, "error": type(exc).__name__ + ": " + str(exc)[:800]}
            )
        if file_sha(path) != before_sha:
            raise RuntimeError("benchmark mutated input project")
    mean = lambda xs: sum(xs) / len(xs) if xs else None
    result = {
        "schema": "multires-layout-experiment/1",
        "samples": len(samples),
        "cases": cases,
        "projects": projects,
        "errors": errors,
        "events": dict(Counter(e["status"] for e in all_events)),
        "matchedSamples": sum(c["modelApplied"] for c in cases),
        "modelEvidence": any(r.get("modelEvidence", False) for r in predictions.values()),
        "predictionStatus": dict(
            Counter(predictions.get(s["requestKey"], {}).get("status", "missing") for s in samples)
        ),
        "rawHumanMAE1000": mean(raw_human_errors),
        "humanLabeledSamples": len(raw_human_errors),
        "projectionMAE1000": mean(projection_errors),
        "sourceUnchanged": True,
        "rawSyntheticMAE1000": mean(synthetic_raw),
        "constrainedSyntheticMAE1000": mean(synthetic_final),
        "nativeValidation": "not_measured",
        "generalization": (
            "synthetic_only; real themes not measured"
            if samples and all(s["label"]["status"] == "synthetic" for s in samples)
            else (
                "not_measured"
                if split == "all"
                else "requires human test labels and base/LoRA paired runs"
            )
        ),
    }
    write_json(out / "results.json", result)
    with (out / "badcases.jsonl").open("w", encoding="utf8") as stream:
        for row in badcases:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    esc = lambda v: html.escape(str(v), quote=True)
    cards = []
    for c in cases:
        raw_label = "模型原始布局" if c["rawValid"] else "无有效模型提案（显示基线回退）"
        final_label = "模型提案经约束求解" if c["modelApplied"] else "未应用模型（基线回退）"
        images = "".join(
            "<figure><figcaption>" + label + '</figcaption><img src="' + esc(c[key]) + '"></figure>'
            for label, key in [
                ("源场景", "source"),
                ("锚点基线", "baseline"),
                (raw_label, "raw"),
                (final_label, "candidate"),
            ]
        )
        cards.append(
            "<section><h3>"
            + esc(c["device"] + " / " + c["state"])
            + "</h3><p>提案匹配："
            + str(c["modelApplied"])
            + "；布局变化："
            + str(c["changed"])
            + "；约束投影误差："
            + esc(c["projectionMAE1000"])
            + "</p><div>"
            + images
            + "</div></section>"
        )
    (out / "report.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>布局算法实验</title><style>body{font:15px system-ui;background:#edf1f5;margin:24px;color:#1c3146}section{background:white;padding:16px;margin:18px 0;border-radius:8px}section div{display:flex;gap:12px}figure{margin:0;flex:1}img{max-width:100%;max-height:500px}pre{white-space:pre-wrap}</style><h1>布局算法实验</h1><p>原始主题保持不变。弱标签回放只验证链路；离线图片不代表真机效果。</p><pre>'
        + esc(
            json.dumps(
                {k: v for k, v in result.items() if k not in {"cases", "projects"}},
                ensure_ascii=False,
                indent=2,
            )
        )
        + "</pre>"
        + "".join(cards),
        encoding="utf8",
    )
    return result
