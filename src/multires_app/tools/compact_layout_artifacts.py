import argparse
import json
from pathlib import Path
from multires_app.pipeline.layout_storage import compact_layout_storage, externalize_anchor_evidence
from multires_app.preview.scene_previewer import build_scene_preview
from multires_app.layout.anchor_tools import write_anchor_artifacts


def update(path):
    before = path.stat().st_size
    p = json.loads(path.read_text(encoding="utf8"))
    stats = compact_layout_storage(p)
    write_anchor_artifacts(p, path.parent)
    externalize_anchor_evidence(p)
    temp = path.with_suffix(".json.compact")
    temp.write_text(json.dumps(p, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    temp.replace(path)
    build_scene_preview(p, path.parent / "preview.html")
    print(
        json.dumps(
            {
                "project": path.parent.parent.name,
                "bytesBefore": before,
                "bytesAfter": path.stat().st_size,
                **stats,
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+")
    args = parser.parse_args()
    for path in sorted(Path("theme/adaptation").glob("*/scenes/project.json")):
        if not args.only or path.parent.parent.name in args.only:
            update(path)


if __name__ == "__main__":
    main()
