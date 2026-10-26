"""Translate parsed CLI options into application workflow calls."""

import json
from pathlib import Path
from typing import Any
from multires_app.engine.theme_analysis import analyze
from multires_app.export.xml_patches import apply_patches
from multires_app.layout.layout_api import load_algorithm, make_layout_plan
from multires_app.preview.previewer import build_preview


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute(args):
    if args.command == "tools":
        from multires_app.tools.runner import main as tools_main

        return tools_main(args.tool_args)
    elif args.command == "analyze":
        result = analyze(args.source, args.engine)
        write_json(args.out, result)
        print(
            json.dumps(
                {
                    "output": str(args.out.resolve()),
                    **result["summary"],
                    "engine": result["engine"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "apply":
        apply_patches(args.script, args.patches, args.out, args.target)
    elif args.command == "layout":
        analysis_doc = json.loads(args.analysis.read_text(encoding="utf-8"))
        source_profile = json.loads(args.source_profile.read_text(encoding="utf-8"))
        profiles_doc = json.loads(args.profiles.read_text(encoding="utf-8"))
        algorithm = load_algorithm(args.algorithm)
        plan = make_layout_plan(analysis_doc, source_profile, profiles_doc, algorithm)
        write_json(args.out, plan)
        print(
            json.dumps(
                {
                    "output": str(args.out.resolve()),
                    "algorithm": plan["algorithm"],
                    "profiles": len(plan["devices"]),
                    "ready": sum(d["status"] == "ready" for d in plan["devices"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "scenes":
        from multires_app.pipeline.scene_pipeline import create_project

        profiles = json.loads(args.profiles.read_text(encoding="utf-8"))
        if args.anchor_config:
            for profile in profiles["profiles"]:
                profile.setdefault("adaptation", {})["anchorLayout"] = json.loads(
                    args.anchor_config.read_text(encoding="utf-8")
                )
        result = create_project(
            args.source,
            json.loads(args.source_profile.read_text(encoding="utf-8")),
            profiles,
            args.out,
            args.algorithm,
            args.max_states,
            args.max_depth,
            args.components,
            args.inputs,
        )
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "source",
                        "states",
                        "transitions",
                        "devices",
                        "components",
                        "diagnosticCount",
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "serve":
        from multires_app.preview.interaction_server import serve_project

        serve_project(args.project, args.port)
    elif args.command == "live":
        from multires_app.preview.live_preview import serve_live

        serve_live(args.source, args.port, args.out, not args.no_import, args.vlm_config)
    elif args.command == "relayout":
        from multires_app.pipeline.scene_pipeline import relayout_project

        print(
            json.dumps(
                relayout_project(args.project, args.algorithm, args.components, args.anchor_config),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "export":
        from multires_app.pipeline.scene_pipeline import export_package

        print(
            json.dumps(
                export_package(args.project, args.target, args.out), ensure_ascii=False, indent=2
            )
        )
    else:
        plan = json.loads(args.layout_plan.read_text(encoding="utf-8"))
        build_preview(plan, args.out, args.source_image)
        print(json.dumps({"output": str(args.out.resolve())}, ensure_ascii=False, indent=2))
    return 0
