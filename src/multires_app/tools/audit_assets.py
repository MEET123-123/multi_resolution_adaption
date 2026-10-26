"""Inspect preview resources and unresolved script references."""

import collections
import json
from pathlib import Path
from PIL import Image


def audit(root):
    projects = []
    for path in sorted(root.glob("*/scenes/project.json")):
        p = json.loads(path.read_text(encoding="utf-8"))
        formats = collections.Counter()
        failures = []
        special = []
        for name, meta in p["assets"].items():
            file = path.parent / "assets" / name
            try:
                with Image.open(file) as im:
                    formats[im.format] += 1
            except Exception as exc:
                failures.append({"asset": name, "error": str(exc)})
            if any(c in meta["url"] for c in "#?%"):
                special.append(meta["url"])
        missing = [
            {"node": k, "reason": v}
            for k, v in p["diagnostics"].items()
            if k.endswith((":asset", ":srcExp")) or ":glyph:" in k
        ]
        projects.append(
            {
                "slug": path.parent.parent.name,
                "formats": dict(formats),
                "fileFailures": failures,
                "reservedUrlCharacters": special,
                "unresolvedCount": len(missing),
                "examples": missing[:12],
            }
        )
    return projects


def main():
    import argparse

    argparse.ArgumentParser(description=__doc__).parse_args()
    results = audit(Path("theme/adaptation"))
    Path("theme/adaptation/asset-audit-before.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
