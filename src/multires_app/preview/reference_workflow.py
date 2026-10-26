"""Reproducible generation handoff and automatic grounding/writeback pipeline.

Image generators implement a callable request -> {image: local_path, provider}.
No generation service/key is bundled; the file handoff works with desktop
imagegen, a local diffusion service, or a future trusted provider plugin.
"""

from __future__ import annotations

import hashlib
import html
import json
import shutil
import uuid
from pathlib import Path

from PIL import Image


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generation_prompt(resolution):
    width = resolution["width"]
    height = resolution["height"]
    return (
        f"将输入的原始直板机锁屏适配为 {width}×{height} 的目标画布，宽高比严格为 {width}:{height}。"
        "按照组件组重排，组内资源、文字、图标、交互按钮的相对位置和比例保持一致。"
        "保留主题原有视觉风格与内容，完整展示全部组件，组间留白，不重叠、不越界。"
        "每个现有组件只出现一次；不要增加、删除、复制或改画组件，不要改动时间日期数字。"
        "背景铺满画布。不要设备外框、镜头、系统栏、水印或额外标题。只输出目标布局图。"
    )


def generation_bundle(project, source_device, source_layout, target, state, asset_root):
    from multires_app.learning.render import render
    from multires_app.preview.target_layout import build_catalog

    device = next(d for d in project["devices"] if d["profile"]["id"] == target)
    directory = Path(asset_root) / "target-matching" / uuid.uuid4().hex[:12]
    directory.mkdir(parents=True)
    rendering = render(
        project,
        source_device,
        source_layout,
        Path(asset_root) / "assets",
        directory / "source-phone.png",
    )
    catalog, _ = build_catalog(project, target, state, asset_root, layout_mode="reference-first")
    bundle = {
        "schema": "reference-generation-bundle/1",
        "target": target,
        "state": state,
        "resolution": device["profile"]["resolutionPx"],
        "sourceImage": "source-phone.png",
        "sourceSha256": digest(directory / "source-phone.png"),
        "prompt": generation_prompt(device["profile"]["resolutionPx"]),
        "rendering": rendering,
        "catalog": catalog,
        "training": False,
        "manualCoordinates": False,
    }
    save(directory / "generation-request.json", bundle)
    relative = directory.relative_to(asset_root).as_posix()
    return {
        "sourceImage": relative + "/source-phone.png",
        "request": relative + "/generation-request.json",
        "prompt": bundle["prompt"],
        "rendering": rendering,
        "target": target,
        "state": state,
    }


def prepare(project_path, target, state, out):
    from multires_app.learning.render import render
    from multires_app.preview.target_layout import build_catalog, make_atlas

    project_path = Path(project_path).resolve()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    project = json.loads(project_path.read_text(encoding="utf8"))
    # A cached project can contain sixteen devices and hundreds of layouts.
    # Only this target participates in the run; retaining the others made every
    # solver retry deepcopy hundreds of MB of unrelated JSON on an 8GB host.
    project["devices"] = [next(d for d in project["devices"] if d["profile"]["id"] == target)]
    from multires_app.preview.component_refinement import refine_components
    from multires_app.layout.background_layers import split_background_layers

    split_background_layers(project)
    refinement = refine_components(project)
    from multires_app.preview.workbench import clone_scene

    isolated = out / "normalized-source"
    if isolated.exists():
        raise ValueError("规范化缓存已存在，请为新的准备请求指定一个新目录")
    if isolated.is_relative_to(project_path.parent):
        raise ValueError("输出目录不能位于输入 scenes 目录内部")
    clone_scene(project_path.parent, isolated)
    project_path = isolated / "project.json"
    save(project_path, {k: v for k, v in project.items() if not k.startswith("_")})
    device = next(d for d in project["devices"] if d["profile"]["id"] == target)
    source = project["sourceDevice"]
    layout = next(l for l in source["layouts"] if l["state"] == state)
    rendering = render(
        project, source, layout, project_path.parent / "assets", out / "source-phone.png"
    )
    catalog, templates = build_catalog(
        project, target, state, project_path.parent, layout_mode="reference-first"
    )
    groups = [g for g in catalog["groups"] if not g["locked"]]
    for i in range(0, len(groups), 9):
        make_atlas(groups[i : i + 9], templates).save(out / f"groups-{i//9}.png")
    resolution = device["profile"]["resolutionPx"]
    prompt = generation_prompt(resolution)
    request = {
        "schema": "reference-generation-request/1",
        "id": uuid.uuid4().hex,
        "project": str(project_path),
        "themeName": Path(project["source"]).stem,
        "projectSha256": digest(project_path),
        "target": target,
        "state": state,
        "sourceImage": str(out / "source-phone.png"),
        "sourceSha256": digest(out / "source-phone.png"),
        "resolution": resolution,
        "prompt": prompt,
        "rendering": rendering,
        "componentRefinement": refinement,
        "eligibleGroups": [
            {"id": g["id"], "members": g["members"], "roles": g["roles"]} for g in groups
        ],
        "generatorContract": {
            "input": "this JSON + sourceImage",
            "output": {"image": "absolute local image path", "provider": "generator name"},
        },
        "training": False,
        "manualCoordinates": False,
    }
    save(out / "request.json", request)
    save(out / "catalog.json", catalog)
    (out / "prompt.txt").write_text(prompt, encoding="utf8")
    return request


def complete(
    request_path,
    image_path,
    *,
    provider="external-reference",
    minimum_confidence=0.78,
    generation_metadata=None,
    replay_job=None,
):
    """Consume a generation result; isolate each attempt, compile, reparse HWT.

    Candidate geometry and original project remain reviewable even if compilation
    rejects a safety/visibility invariant. A partial result is never called passed.
    """
    from multires_app.preview.target_layout import match_target_layout
    from multires_app.preview.layout_refinement import write_back
    from multires_app.preview.workbench import clone_scene
    from multires_app.pipeline.scene_pipeline import export_package

    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_text(encoding="utf8"))
    source = Path(request["project"])
    image_path = Path(image_path).resolve()
    if digest(source) != request["projectSha256"]:
        raise ValueError("源项目已变化，请重新准备请求")
    if digest(request["sourceImage"]) != request["sourceSha256"]:
        raise ValueError("原始渲染图已变化")
    run = request_path.parent / "runs" / uuid.uuid4().hex[:12]
    run.mkdir(parents=True)
    result = {
        "schema": "reference-workflow-result/1",
        "request": str(request_path),
        "generator": provider,
        "target": request["target"],
        "state": request["state"],
        "manualCoordinates": False,
        "training": False,
        "nativeDeviceTest": False,
    }
    shutil.copy2(image_path, run / "generated-target.png")
    result["referenceSha256"] = digest(run / "generated-target.png")
    save(run / "request.json", request)
    clone_scene(source.parent, run / "scenes")
    if generation_metadata:
        result["generationMetadata"] = generation_metadata
        save(run / "generation.json", generation_metadata)
    project_path = run / "scenes/project.json"
    project = json.loads(project_path.read_text(encoding="utf8"))
    project["devices"] = [
        next(d for d in project["devices"] if d["profile"]["id"] == request["target"])
    ]
    save(project_path, {k: v for k, v in project.items() if not k.startswith("_")})
    try:
        proposal = match_target_layout(
            project,
            request["target"],
            request["state"],
            image_path.read_bytes(),
            project_path.parent,
            strategy="auto",
            minimum_confidence=minimum_confidence,
            layout_mode="reference-first",
            source_image_bytes=Path(request["sourceImage"]).read_bytes(),
            replay_job=replay_job,
        )
        save(run / "proposal.json", proposal)
        result["matching"] = proposal["matching"]
        result["rendering"] = proposal["rendering"]
        result["report"] = str(project_path.parent / proposal["artifacts"]["report"])
        if not proposal["matching"]["acceptedGroups"]:
            raise ValueError("没有可靠匹配，保持原脚本")
        result["writeback"] = write_back(project_path, proposal)
        package = run / "adapted.hwt"
        result["export"] = export_package(project_path, request["target"], package)
        from multires_app.pipeline.representative_states import select_states

        adapted = json.loads(project_path.read_text(encoding="utf8"))
        device = next(d for d in adapted["devices"] if d["profile"]["id"] == request["target"])
        sampling = select_states(adapted, device, 6)
        states = list(dict.fromkeys([request["state"], *sampling["states"]]))
        del adapted, device
        audits = audit_package_states(project_path, package, request["target"], states)
        result["readback"] = next(a for a in audits if a["state"] == request["state"])
        result["readbackCoverage"] = {
            **sampling,
            "states": states,
            "selectedStates": len(states),
            "passed": all(a["geometryPassed"] for a in audits),
        }
        save(run / "native-audits.json", audits)
        if not result["readbackCoverage"]["passed"]:
            raise ValueError(
                "原生脚本回读坐标与求解布局不一致，候选未通过；请检查位图字形与状态导出能力"
            )
        projection = proposal["matching"].get("projection", {}).get("groups", [])
        result["quality"] = {
            "matchedGroups": proposal["matching"]["acceptedGroups"],
            "eligibleGroups": len(request["eligibleGroups"]),
            "meanTargetIoU": sum(g["targetOverlapIoU"] for g in projection)
            / max(1, len(projection)),
            "lowFitGroups": [g["id"] for g in projection if g["targetOverlapIoU"] < 0.65],
        }
        result["status"] = (
            "needs-review"
            if result["quality"]["lowFitGroups"]
            or result["quality"]["matchedGroups"] < result["quality"]["eligibleGroups"]
            else "compiled"
        )
        result["package"] = str(package)
        result["project"] = str(project_path)
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
    result["directory"] = str(run)
    save(run / "result.json", result)
    save(request_path.parent / "latest.json", result)
    page = '<meta charset="utf-8"><title>生成参考图适配回归</title><style>body{font:16px system-ui;margin:24px}img{max-width:46%;max-height:65vh}pre{white-space:pre-wrap}</style><h1>生成参考图 → 自动匹配 → 原生脚本</h1>'
    page += f'<p>{html.escape(result["status"])} · 人工坐标：无 · 真机验证：未执行</p><img src="{html.escape(Path(request["sourceImage"]).as_uri())}"><img src="generated-target.png">'
    if result.get("report"):
        page += f'<p><a href="{Path(result["report"]).as_uri()}">匹配前后与约束诊断</a></p>'
    page += "<pre>" + html.escape(json.dumps(result, ensure_ascii=False, indent=2)) + "</pre>"
    (run / "report.html").write_text(page, encoding="utf8")
    return result


def audit_package(project_path, package, target, state):
    """Replay original gestures in reparsed native XML and compare final nodes.

    This is an interpreter regression, not an on-device rendering assertion.
    Dynamic media/decoration trajectories are reported separately from stable UI.
    """
    return audit_package_states(project_path, package, target, [state])[0]


def audit_package_states(project_path, package, target, states):
    """Read one archive once, then replay multiple independently initialized journeys."""
    import io
    import json
    import zipfile
    from multires_app.engine.scene_engine import load_model

    project = json.loads(Path(project_path).read_text(encoding="utf8"))
    # A package may contain both the normal and UCD lockscreen manifests.
    native = load_model(Path(package), project["model"]["file"])
    assets = dict(project["assets"])
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            if (
                info.filename in assets
                or not info.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                or info.file_size > 8_000_000
            ):
                continue
            try:
                with Image.open(io.BytesIO(archive.read(info))) as im:
                    assets[info.filename] = {
                        "width": im.width,
                        "height": im.height,
                        "kind": "image",
                    }
            except (OSError, ValueError):
                continue
    native["assetNames"] = list(assets)
    return [_audit_native_state(project, native, assets, target, state) for state in states]


def _audit_native_state(project, native, assets, target, state):
    import math
    from multires_app.interaction.interaction_semantics import replay_human_state
    from multires_app.evaluation.report import elements
    from multires_app.pipeline.scene_clusters import describe_layout

    device = next(d for d in project["devices"] if d["profile"]["id"] == target)
    layout = next(l for l in device["layouts"] if l["state"] == state)
    graph_state = next(s for s in project["graph"]["states"] if s["id"] == state)
    rt = replay_human_state(
        native,
        device["viewport"]["width"],
        device["viewport"]["height"],
        graph_state,
        assets,
        verify_hit=True,
    )
    actual = {}
    for e in rt.snapshot(assets, visible_only=True):
        if e["visible"]:
            actual.setdefault(e["id"], []).append(e)
    for values in actual.values():
        values.sort(key=lambda e: (round(e["rect"]["y"], 3), e["rect"]["x"]))
    expected = elements(project, layout)
    desc = describe_layout(project, layout)
    dynamic = set()
    pointers = {u for c in project["components"] if c.get("pointerEffect") for u in c["members"]}
    if pointers:
        # Re-evaluate feedback against the touch actually used on the adapted
        # control, rather than the pre-layout mock point saved in the cache.
        pointer_model = {**project["model"], "assetNames": list(project["assets"])}
        live = replay_human_state(
            pointer_model,
            device["viewport"]["width"],
            device["viewport"]["height"],
            graph_state,
            project["assets"],
            verify_hit=False,
        )
        live.system.update({k: v for k, v in rt.system.items() if k.startswith("touch_")})
        live.cache.clear()
        feedback = {
            e["id"]: e for e in live.snapshot(project["assets"], only_ids=pointers) if e["visible"]
        }
        expected = [
            {**e, "rect": feedback[e["id"]]["rect"]} if e["id"] in feedback else e for e in expected
        ]
    for cid, d in desc.items():
        if d["role"] == "decoration" and any(
            any(isinstance(v, str) and v.startswith("animated") for v in g[1][:2])
            for g in d["geometry"]
        ):
            dynamic.add(cid)
    errors = []
    missing = []
    skipped = 0
    occurrences = {}
    expected = sorted(expected, key=lambda e: (e["id"], round(e["rect"]["y"], 3), e["rect"]["x"]))
    for e in expected:
        cid = project["nodeComponents"].get(e["id"])
        b = e["rect"]
        if cid in dynamic and e["id"] not in pointers or e["tag"] == "Button":
            skipped += 1
            continue
        if not e.get("visible", True) or e.get("alpha", 1) <= 0 or min(b["w"], b["h"]) <= 0:
            continue
        instance = occurrences.get(e["id"], 0)
        occurrences[e["id"]] = instance + 1
        if instance >= len(actual.get(e["id"], [])):
            missing.append(e["id"] + "#" + str(instance))
            continue
        a = actual[e["id"]][instance]["rect"]
        error = max(abs(a[k] - b[k]) for k in ("x", "y", "w", "h"))
        errors.append(
            {"id": e["id"], "instance": instance, "maxError": error, "expected": b, "actual": a}
        )
    finite = [r for r in errors if math.isfinite(r["maxError"])]
    worst = sorted(finite, key=lambda r: r["maxError"], reverse=True)
    replay_issues = {k: v for k, v in rt.issues.items() if k.startswith("replay:")}
    return {
        "parsed": True,
        "nativeDeviceTest": False,
        "gestureReplay": True,
        "gesturePassed": not replay_issues,
        "state": state,
        "traceSteps": len(graph_state.get("trace", [])),
        "nodes": len(native["nodes"]),
        "comparedStableNodes": len(errors),
        "skippedDynamicOrHotAreas": skipped,
        "missingNodes": missing,
        "maxGeometryError": worst[0]["maxError"] if worst else None,
        "geometryPassed": not replay_issues
        and bool(errors)
        and not missing
        and len(finite) == len(errors)
        and all(r["maxError"] <= 2 for r in errors),
        "worstNodes": worst[:12],
        "diagnostics": dict(rt.issues),
    }


def generate(request_path, generator):
    """Trusted callable hook; a model/backend can change without re-extraction."""
    request = json.loads(Path(request_path).read_text(encoding="utf8"))
    answer = generator(request)
    if not isinstance(answer, dict) or not answer.get("image") or not answer.get("provider"):
        raise ValueError("生成器必须返回 image 路径及 provider")
    return complete(
        request_path,
        answer["image"],
        provider=answer["provider"],
        generation_metadata=answer.get("metadata"),
    )


def batch(plan_path, out, *, generator=None, retry=False, limit=None):
    """Batch any theme/state using file references or a trusted generator plugin."""
    import gc
    from collections import Counter

    plan_path = Path(plan_path).resolve()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    plan = json.loads(plan_path.read_text(encoding="utf8"))
    if plan.get("schema") != "corpus-reference-plan/1" or not isinstance(plan.get("jobs"), list):
        raise ValueError("无效的参考图批处理计划")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be positive")

    def path(value):
        return (plan_path.parent / Path(value)).resolve()

    rows = []
    implementation = hashlib.sha256()
    for p in sorted(Path(__file__).resolve().parents[1].rglob("*.py")):
        implementation.update(p.read_bytes())
    provider_id = (generator.__module__ + ":" + generator.__qualname__) if generator else None
    for job in plan["jobs"][:limit]:
        result = {}
        folder = None
        try:
            project = path(job["project"])
            image = path(job["referenceImage"]) if job.get("referenceImage") else None
            identity = {
                "project": digest(project),
                "target": job["target"],
                "state": job.get("state", "state-000"),
                "image": digest(image) if image else None,
                "sourceImage": digest(path(job["sourceImage"])) if job.get("sourceImage") else None,
                "provider": job.get("provider"),
                "generator": provider_id,
                "implementation": implementation.hexdigest(),
            }
            key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
            folder = out / key
            checkpoint = folder / "batch-result.json"
            if checkpoint.exists() and not retry:
                rows.append(json.loads(checkpoint.read_text(encoding="utf8")))
                continue
            print(
                json.dumps(
                    {
                        "stage": "reference",
                        "job": key,
                        "project": str(project),
                        "target": identity["target"],
                        "state": identity["state"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            attempt = folder / ("attempt-" + uuid.uuid4().hex[:10])
            attempt.mkdir(parents=True)
            request = prepare(project, identity["target"], identity["state"], attempt)
            if job.get("sourceImage"):
                request["sourceImage"] = str(path(job["sourceImage"]))
                request["sourceSha256"] = identity["sourceImage"]
                save(attempt / "request.json", request)
            if image:
                result = complete(
                    attempt / "request.json",
                    image,
                    provider=job.get("provider", "external-reference"),
                    generation_metadata=job.get("generationMetadata"),
                )
            elif generator:
                result = generate(attempt / "request.json", generator)
            else:
                result = {
                    "status": "awaiting-reference",
                    "request": str(attempt / "request.json"),
                    "sourceImage": request["sourceImage"],
                    "directory": str(attempt),
                }
            result["jobId"] = key
            result["inputs"] = identity
            save(checkpoint, result)
            if result.get("generator"):
                save(folder / "latest.json", result)
        except Exception as exc:
            result = {"status": "failed", "error": str(exc), "job": job}
            if folder is not None:
                result.update(jobId=key, inputs=identity)
                save(folder / "batch-result.json", result)
        rows.append(result)
        save(out / "batch-results.json", rows)
        gc.collect()
        index_report(out)
    summary = {
        "schema": "corpus-reference-summary/1",
        "jobs": len(rows),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "automaticMatching": True,
        "manualCoordinates": False,
        "nativeDeviceTest": False,
        "nativeAuditedJobs": sum(r.get("readbackCoverage", {}).get("passed", False) for r in rows),
    }
    save(out / "batch-summary.json", summary)
    save(out / "batch-results.json", rows)
    index_report(out)
    return summary


def index_report(root):
    root = Path(root).resolve()
    cases = []
    page = '<meta charset="utf-8"><title>生图参考适配验证</title><style>body{font:16px system-ui;margin:30px;background:#f3f5f7;color:#172331}section{background:white;border-radius:14px;padding:20px;margin:24px 0}.images{display:flex;gap:15px}figure{flex:1;min-width:0;margin:0}img{width:100%;height:420px;object-fit:contain}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#146d93}</style><h1>原始直板机 → 生图参考 → 代码匹配 → 原生脚本</h1><p>未使用人工匹配坐标。IoU 衡量求解框与自动观察框的拟合，不能代表像素相似度或人工标注精度。未进行真机验证。</p>'
    selected = {}
    for latest in sorted(root.glob("*/latest.json"), key=lambda p: p.stat().st_mtime):
        data = json.loads(latest.read_text(encoding="utf8"))
        req = json.loads(Path(data["request"]).read_text(encoding="utf8"))
        selected[(req["sourceSha256"], req["target"], req["state"])] = latest
    for latest in sorted(selected.values()):
        result = json.loads(latest.read_text(encoding="utf8"))
        request = json.loads(Path(result["request"]).read_text(encoding="utf8"))
        directory = Path(result["directory"])
        after = Path(result["report"]).parent / "after.png" if result.get("report") else None
        label = request.get("themeName", latest.parent.name)
        page += (
            "<section><h2>"
            + html.escape(label)
            + " · "
            + html.escape(result["status"])
            + '</h2><div class="images">'
        )
        for label, path in [
            ("原始直板机", Path(request["sourceImage"])),
            ("生图参考", directory / "generated-target.png"),
            ("约束求解后 · 原素材", after),
        ]:
            if path and path.exists():
                page += (
                    "<figure><figcaption>"
                    + label
                    + '</figcaption><img src="'
                    + path.as_uri()
                    + '"></figure>'
                )
        page += '</div><p><a href="' + (directory / "report.html").as_uri() + '">完整流程</a>'
        if result.get("report"):
            page += ' · <a href="' + Path(result["report"]).as_uri() + '">自动定位与求解诊断</a>'
        if result.get("package"):
            page += ' · <a href="' + Path(result["package"]).as_uri() + '">候选 HWT</a>'
        if (directory / "generation.json").exists():
            page += (
                ' · <a href="' + (directory / "generation.json").as_uri() + '">实际完整提示词</a>'
            )
        page += "</p>"
        readback = result.get("readback", {})
        summary = {
            "quality": result.get("quality"),
            "error": result.get("error"),
            "generator": result["generator"],
            "readback": {
                k: readback.get(k)
                for k in (
                    "gestureReplay",
                    "geometryPassed",
                    "comparedStableNodes",
                    "maxGeometryError",
                    "missingNodes",
                )
            },
            "nativeDeviceTest": False,
            "training": False,
            "manualCoordinates": False,
        }
        page += (
            "<pre>"
            + html.escape(json.dumps(summary, ensure_ascii=False, indent=2))
            + "</pre></section>"
        )
        cases.append({"case": latest.parent.name, **summary})
    (root / "report.html").write_text(page, encoding="utf8")
    save(root / "summary.json", cases)
    return {"cases": len(cases), "report": str(root / "report.html")}
