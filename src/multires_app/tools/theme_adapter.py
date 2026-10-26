"""One entry point for conservative adaptation and explicit redesigned templates."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from multires_app.layout.adaptive_layout import classify_change
from multires_app.tools.batch_convert import source_profile
from multires_app.pipeline.scene_pipeline import create_project, export_package

from multires_app.paths import PROJECT_ROOT as ROOT

NATIVE_THEMES = {"比邻熊 新拟态", "线条小狗 夏日出逃"}


def choose_recipe(source, decision, width, height, requested="generic"):
    if requested not in {"generic", "tablet-preset"}:
        raise ValueError("Unknown recipe")
    if requested == "generic":
        return "generic"
    if source.stem not in NATIVE_THEMES or sorted((width, height)) != [1600, 2560]:
        raise ValueError(
            "tablet-preset only supports the two named themes at 2560×1600 / 1600×2560"
        )
    if decision["strategy"] != "reflow":
        raise ValueError("tablet-preset is for large composition changes")
    return requested


def adapt_theme(
    source, profile, target, out, recipe="generic", design=None, max_states=48, max_depth=4
):
    source = Path(source).resolve()
    out = Path(out).resolve()
    if out == source or out.suffix.lower() in {".hwt", ".zip"}:
        raise ValueError("--out must be a separate output directory")
    width, height = target["resolutionPx"]["width"], target["resolutionPx"]["height"]
    decision = classify_change(profile, target)
    selected = choose_recipe(source, decision, width, height, recipe)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    if selected == "tablet-preset":
        # These are deliberate redesigned small-theme recipes, not an automatic
        # promise to preserve all phone-specific controls and skins.
        path = ROOT / "theme/tablet-2560x1600/build_tablet_themes.py"
        spec = importlib.util.spec_from_file_location("tablet_template_builder", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if design:
            module.DESIGNS = json.loads(Path(design).read_text(encoding="utf-8"))
        details = module.build(source.stem, source_path=source, destination=out)
        if not details["passed"]:
            raise ValueError("Tablet preset validation failed: " + str(details["errors"]))
        package = Path(details["package"])
    else:
        if design:
            raise ValueError("--design requires --recipe tablet-preset")
        orientation = "landscape" if width > height else "portrait"
        target = {
            **target,
            "id": "target",
            "baseOrientation": orientation,
            "orientations": [orientation],
        }
        profiles = {"profiles": [target]}
        create_project(source, profile, profiles, out / "project", "anchor", max_states, max_depth)
        package = out / (source.stem + " adapted.hwt")
        details = export_package(out / "project/project.json", "target-" + orientation, package)
        details["scope"] = (
            "只改写动态锁屏脚本，保留原资源和命令；主题品类元数据/壁纸规范需按目标设备另行处理。"
        )
        if decision["strategy"] == "reflow":
            p = json.loads((out / "project/project.json").read_text(encoding="utf-8"))
            details["reviewCount"] = sum(
                len(l.get("adaptationReview", [])) for l in p["devices"][0]["layouts"]
            )
    result = {
        "source": str(source),
        "package": str(package.resolve()),
        "recipe": selected,
        "decision": decision,
        "sourceUnchanged": digest == hashlib.sha256(source.read_bytes()).hexdigest(),
        "sourceSha256": digest,
        "nativeDeviceTest": False,
        "details": details,
    }
    (out / "adaptation-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-profile", type=Path)
    parser.add_argument("--mode", choices=["auto", "preserve", "reflow"], default="auto")
    parser.add_argument("--aspect-threshold", type=float, default=0.18)
    parser.add_argument("--flow", choices=["row-major", "column-major"], default="row-major")
    parser.add_argument("--row-gap", type=float, help="Vertical spacing in target physical pixels")
    parser.add_argument(
        "--column-gap", type=float, help="Horizontal spacing in target physical pixels"
    )
    parser.add_argument("--margin", type=float, help="Outer margin in target physical pixels")
    parser.add_argument(
        "--max-scale",
        type=float,
        default=1.0,
        help="Maximum content scale in theme logical coordinates",
    )
    parser.add_argument(
        "--no-aesthetics",
        action="store_true",
        help="Disable landscape datetime whitespace placement",
    )
    parser.add_argument("--recipe", choices=["generic", "tablet-preset"], default="generic")
    parser.add_argument("--design", type=Path, help="Native tablet layout JSON")
    parser.add_argument("--max-states", type=int, default=48)
    parser.add_argument("--max-depth", type=int, default=4)
    args = parser.parse_args()
    profile = (
        json.loads(args.source_profile.read_text(encoding="utf-8"))
        if args.source_profile
        else source_profile(args.source)
    )
    options = {
        "mode": args.mode,
        "aspectThreshold": args.aspect_threshold,
        "flow": args.flow,
        "maxScale": args.max_scale,
        "aesthetics": {"enabled": not args.no_aesthetics},
    }
    for key, value in [
        ("rowGapPx", args.row_gap),
        ("columnGapPx", args.column_gap),
        ("marginPx", args.margin),
    ]:
        if value is not None:
            options[key] = value
    target = {"resolutionPx": {"width": args.width, "height": args.height}, "adaptation": options}
    result = adapt_theme(
        args.source,
        profile,
        target,
        args.out,
        args.recipe,
        args.design,
        args.max_states,
        args.max_depth,
    )
    print(
        json.dumps(
            {k: result[k] for k in ["package", "recipe", "decision", "sourceUnchanged"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
