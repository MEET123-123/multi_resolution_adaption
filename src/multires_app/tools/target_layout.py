"""Expected-image grounding, constrained placement and existing XML writeback."""

import argparse
import importlib
import json
from pathlib import Path

from multires_app.preview.target_layout import (
    build_catalog,
    make_atlas,
    match_target_layout,
    register_matcher,
    resolve_matches,
)
from multires_app.preview.layout_refinement import write_back


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("catalog", "match", "resolve"):
        p = sub.add_parser(name)
        p.add_argument("project", type=Path)
        p.add_argument("--target", required=True)
        p.add_argument("--state", default="state-000")
        p.add_argument("--out", type=Path, required=True)
        if name == "match":
            p.add_argument("--image", type=Path, required=True)
            p.add_argument(
                "--strategy",
                choices=["auto", "template", "qwen", "hybrid", "custom"],
                default="template",
            )
            p.add_argument(
                "--observations",
                type=Path,
                help="Reviewed group boxes JSON; bypasses model inference and records its provenance",
            )
            p.add_argument(
                "--source-image",
                type=Path,
                help="Optional original screenshot; crop templates from script-derived group geometry",
            )
            p.add_argument(
                "--replay-job",
                type=Path,
                help="Reuse verified automatic matches; rerun current solver/writeback",
            )
            p.add_argument(
                "--matcher", help="Trusted local module:callable; used only with --strategy custom"
            )
        if name == "resolve":
            p.add_argument("--catalog", type=Path, required=True)
            p.add_argument("--matches", type=Path, required=True)
        if name != "catalog":
            p.add_argument("--minimum-confidence", type=float, default=0.78)
            p.add_argument("--free-placement", action="store_true")
            p.add_argument(
                "--apply-scope",
                choices=["state", "matching-components"],
                default="matching-components",
            )
            p.add_argument(
                "--apply",
                action="store_true",
                help="Apply verified proposal and write XML, project and preview with backup",
            )
        if name in {"catalog", "match"}:
            p.add_argument(
                "--layout-mode", choices=["preserve", "reference-first"], default="preserve"
            )
    apply = sub.add_parser("apply")
    apply.add_argument("project", type=Path)
    apply.add_argument("--proposal", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "apply":
        result = write_back(args.project, json.loads(args.proposal.read_text(encoding="utf8")))
    else:
        project = json.loads(args.project.read_text(encoding="utf8"))
        if args.command == "catalog":
            catalog, templates = build_catalog(
                project, args.target, args.state, args.project.parent, layout_mode=args.layout_mode
            )
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "catalog.json").write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf8"
            )
            groups = [g for g in catalog["groups"] if not g["locked"]]
            for index in range(0, len(groups), 9):
                make_atlas(groups[index : index + 9], templates).save(
                    args.out / f"atlas-{index//9}.png"
                )
            result = {"catalog": str((args.out / "catalog.json").resolve()), "groups": len(groups)}
        else:
            config = {
                "minimum_confidence": args.minimum_confidence,
                "preserve_relations": not args.free_placement,
                "apply_scope": args.apply_scope,
            }
            if args.command == "match":
                if args.strategy == "custom":
                    if not args.matcher:
                        parser.error("--strategy custom requires --matcher module:callable")
                    module, name = args.matcher.rsplit(":", 1)
                    register_matcher("custom", getattr(importlib.import_module(module), name))
                observations = (
                    json.loads(args.observations.read_text(encoding="utf8"))
                    if args.observations
                    else None
                )
                result = match_target_layout(
                    project,
                    args.target,
                    args.state,
                    args.image.read_bytes(),
                    args.project.parent,
                    strategy=args.strategy,
                    layout_mode=args.layout_mode,
                    observations=observations,
                    source_image_bytes=(
                        args.source_image.read_bytes() if args.source_image else None
                    ),
                    replay_job=args.replay_job,
                    **config,
                )
            else:
                catalog = json.loads(args.catalog.read_text(encoding="utf8"))
                if catalog["target"] != args.target or catalog["state"] != args.state:
                    raise ValueError("目录与指定设备/场景不一致")
                result = resolve_matches(
                    project, catalog, json.loads(args.matches.read_text(encoding="utf8")), **config
                )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
            report = write_back(args.project, result) if args.apply else None
            result = {
                "proposal": str(args.out.resolve()),
                "acceptedGroups": result["matching"]["acceptedGroups"],
                "artifacts": result.get("artifacts", {}),
                "applied": report,
            }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
