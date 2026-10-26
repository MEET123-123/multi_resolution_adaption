"""Validate preview-derived weak dataset without claiming semantic/native correctness.

Usage:
  .venv/Scripts/python.exe scripts/validate_lockscreen_preview_corpus.py ^
      --dataset artifacts/learning/lockscreen-previews-100themes-20261024
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from PIL import Image


def rows(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path.name} line {line_no}: {exc}") from exc


def resolve(root: Path, rel: str) -> Path:
    if not isinstance(rel, str) or not rel or "\\" in rel:
        raise ValueError(f"invalid artifact reference {rel!r}")
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"artifact escapes dataset: {rel!r}")
    if not path.is_file():
        raise ValueError(f"artifact missing: {rel!r}")
    return path


def validate(root: Path, write_report=True):
    root = root.resolve()
    manifest = json.loads((root / "reports" / "manifest.json").read_text(encoding="utf-8"))
    errors = []
    pairs = list(rows(root / "reports" / "pairs.jsonl"))
    themes = list(rows(root / "reports" / "themes.jsonl"))
    cutouts = list(rows(root / "reports" / "cutouts.jsonl"))
    checked_images = set()
    split_by_theme = {}
    split_by_source_sha = {}
    splitcounts = collections.Counter()
    devices = collections.Counter()
    variants = collections.Counter()
    names = set()
    if manifest.get("pairs") != len(pairs):
        errors.append(f"manifest pair count mismatch: {manifest.get('pairs')} vs {len(pairs)}")
    if manifest.get("themesProcessed") != len(themes):
        errors.append(f"theme count mismatch: {manifest.get('themesProcessed')} vs {len(themes)}")
    if manifest.get("sftApproved") is not False or manifest.get("trainReady") is not False:
        errors.append("manifest must clearly forbid unreviewed training")
    for theme in themes:
        key = theme["themeId"]
        if key in split_by_theme:
            errors.append(f"duplicate theme entry {key}")
        split_by_theme[key] = theme["split"]
        if theme.get("licenseReviewed") is not False:
            errors.append(f"unreviewed source {key} incorrectly licensed")
    for row in pairs:
        key = (row["themeId"], row["previewState"], row["device"], row["variant"])
        if key in names:
            errors.append(f"duplicate pair: {key}")
        names.add(key)
        split = row["split"]
        splitcounts[split] += 1
        devices[row["device"]] += 1
        variants[row["variant"]] += 1
        if split != split_by_theme.get(row["themeId"]):
            errors.append(f"split mismatch: {key}")
        sha = row.get("sourceContentSha256")
        if sha in split_by_source_sha and split_by_source_sha[sha] != split:
            errors.append(f"duplicate source content leaked across splits: {key}")
        split_by_source_sha[sha] = split
        if row.get("qualityGate", {}).get("trainApproved") is not False:
            errors.append(f"unreviewed pair wrongly approved: {key}")
        if row.get("provenance", {}).get("rights") != "unknown / review required":
            errors.append(f"source rights mislabeled: {key}")
        target_shape = tuple(row.get("targetDimensions", ()))
        source_shape = tuple(row.get("sourceDimensions", ()))
        for role, desired in (("sourceImage", source_shape), ("targetImage", target_shape)):
            try:
                path = resolve(root, row[role])
                if path not in checked_images:
                    with Image.open(path) as im:
                        if im.size != desired:
                            errors.append(f"{role} dimension mismatch: {key}, got {im.size}, wanted {desired}")
                        im.verify()
                    checked_images.add(path)
            except (ValueError, OSError, KeyError) as exc:
                errors.append(f"{role} decode/path failure: {key}: {exc}")
        canvas = row["targetLayout"].get("canvas", [])
        box = row["targetLayout"].get("region", [])
        if len(canvas) != 2 or len(box) != 4:
            errors.append(f"invalid layout: {key}")
        else:
            x, y, w, h = box
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > canvas[0] + 1 or y + h > canvas[1] + 1:
                errors.append(f"out of bounds layout: {key}")
    for item in cutouts:
        for field in ("asset", "mask"):
            try:
                path = resolve(root, item[field])
                with Image.open(path) as im:
                    if im.width < 1 or im.height < 1:
                        errors.append(f"invalid cutout size: {path}")
                    im.verify()
            except (ValueError, OSError, KeyError) as exc:
                errors.append(f"cutout/mask failure: {field}: {exc}")
    report = {
        "schema": "preview-lockscreen-corpus-validation/1",
        "valid": len(errors) == 0,
        "pairs": len(pairs),
        "themes": len(themes),
        "cutoutCandidates": len(cutouts),
        "verifiedUniqueJpegFiles": len(checked_images),
        "splitCounts": dict(splitcounts),
        "deviceCounts": dict(devices),
        "variantCounts": dict(variants),
        "errorCount": len(errors),
        "errors": errors[:200],
        "nativeVerified": False,
        "semanticSegmentationVerified": False,
        "trainApproved": False,
    }
    if write_report:
        (root / "reports" / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    args = ap.parse_args()
    report = validate(Path(args.dataset))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
