"""Fast dataset/annotation/model/layout experiments with no training dependency."""

import argparse
import json
from pathlib import Path
from multires_app.learning.dataset import build, import_reviews, export_sft, import_pairs, read_json
from multires_app.learning.experiment import predict, benchmark


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="主题领域布局模型 MVP：数据、人工标注、SFT 导出、提案与 A/B 验证"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    mock = commands.add_parser("mock")
    mock.add_argument("--out", required=True)
    mock.add_argument("--themes", type=int, default=6)
    b = commands.add_parser("build")
    b.add_argument("--project", action="append", default=[])
    b.add_argument("--root", default="theme/adaptation")
    b.add_argument("--only", action="append", default=[])
    b.add_argument("--limit", type=int, default=3)
    b.add_argument("--max-states", type=int, default=3)
    b.add_argument("--target", action="append", required=True)
    b.add_argument("--families")
    b.add_argument("--out", required=True)
    b.add_argument("--resume", action="store_true")
    r = commands.add_parser("import-reviews")
    r.add_argument("--dataset", required=True)
    r.add_argument("--reviews", required=True)
    pair = commands.add_parser("import-pairs")
    pair.add_argument("--dataset", required=True)
    pair.add_argument("--manifest", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--dataset", required=True)
    server.add_argument("--port", type=int, default=8767)
    quality = commands.add_parser("audit")
    quality.add_argument("--dataset", required=True)
    quality.add_argument("--out", required=True)
    report = commands.add_parser("report-run")
    report.add_argument("--run", required=True)
    report.add_argument("--annotation-url", default="http://127.0.0.1:8781/")
    report.add_argument("--interaction-url", default="http://127.0.0.1:8782/")
    for command in ("suggest-native-pairs", "import-native-pairs"):
        native = commands.add_parser(command)
        native.add_argument("--source-dataset", required=True)
        native.add_argument("--target-dataset", required=True)
        if command == "suggest-native-pairs":
            native.add_argument("--out", required=True)
            native.add_argument("--limit", type=int, default=100)
        else:
            native.add_argument("--manifest", required=True)
    e = commands.add_parser("export-sft")
    e.add_argument("--dataset", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--include-weak", action="store_true")
    e.add_argument("--allow-incomplete", action="store_true")
    e.add_argument("--image-root")
    e.add_argument("--include-synthetic", action="store_true")
    e.add_argument("--input-images", choices=["source", "source-and-current"], default="source")
    p = commands.add_parser("predict")
    p.add_argument("--dataset", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--endpoint")
    p.add_argument("--model")
    p.add_argument("--replay-teacher", action="store_true")
    p.add_argument("--timeout", type=int, default=90)
    p.add_argument("--split", choices=["train", "validation", "test", "all"], default="test")
    a = commands.add_parser("benchmark")
    a.add_argument("--dataset", required=True)
    a.add_argument("--predictions", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--split", choices=["train", "validation", "test", "all"], default="test")
    a.add_argument("--export", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "mock":
            if not 3 <= args.themes <= 100:
                raise ValueError("mock themes must be 3..100")
            from multires_app.learning.mock_data import generate

            result = generate(args.out, args.themes)
        elif args.command == "build":
            if not 1 <= args.limit <= 1000 or not 1 <= args.max_states <= 100:
                raise ValueError("invalid build budget")
            paths = [Path(p) for p in args.project] or sorted(
                Path(args.root).glob("*/scenes/project.json")
            )
            if args.only:
                paths = [p for p in paths if p.parent.parent.name in args.only]
            paths = paths[: args.limit]
            if not paths:
                raise ValueError("no projects selected")
            result = build(
                paths,
                args.out,
                max_states=args.max_states,
                targets=args.target,
                family_overrides=read_json(args.families) if args.families else None,
                resume=args.resume,
            )
        elif args.command == "import-reviews":
            result = import_reviews(args.dataset, args.reviews)
        elif args.command == "import-pairs":
            result = import_pairs(args.dataset, args.manifest)
        elif args.command == "serve":
            from multires_app.learning.annotation_server import serve

            serve(args.dataset, args.port)
            return 0
        elif args.command == "audit":
            from multires_app.learning.quality import audit

            result = audit(args.dataset, args.out)
        elif args.command == "report-run":
            from multires_app.learning.run_report import build_run_report

            result = build_run_report(
                args.run, annotation_url=args.annotation_url, interaction_url=args.interaction_url
            )
        elif args.command == "suggest-native-pairs":
            from multires_app.learning.pairs import suggestions

            result = suggestions(args.source_dataset, args.target_dataset, args.out, args.limit)
        elif args.command == "import-native-pairs":
            from multires_app.learning.pairs import import_native_pairs

            result = import_native_pairs(args.source_dataset, args.target_dataset, args.manifest)
        elif args.command == "export-sft":
            result = export_sft(
                args.dataset,
                args.out,
                include_weak=args.include_weak,
                include_synthetic=args.include_synthetic,
                allow_incomplete=args.allow_incomplete,
                image_root=args.image_root,
                input_images=args.input_images,
            )
        elif args.command == "predict":
            if not 1 <= args.timeout <= 300:
                raise ValueError("timeout must be 1..300 seconds")
            result = predict(
                args.dataset,
                args.out,
                endpoint=args.endpoint,
                model=args.model,
                replay_teacher=args.replay_teacher,
                split=args.split,
                timeout=args.timeout,
            )
        else:
            result = benchmark(
                args.dataset, args.predictions, args.out, split=args.split, export=args.export
            )
            print(
                json.dumps(
                    {k: v for k, v in result.items() if k not in {"cases", "projects"}},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1 if result["errors"] else 2 if not result["matchedSamples"] else 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
