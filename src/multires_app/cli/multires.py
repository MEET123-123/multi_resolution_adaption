"""CLI entry and backwards-compatible exports for the original analyzer API."""

from __future__ import annotations
import json
import sys
import zipfile
from multires_app.cli.parser import build_parser
from multires_app.cli.commands import write_json
from multires_app.engine.theme_analysis import (
    VERSION,
    COMMAND_TAGS,
    GEOMETRY_ATTRS,
    LAYER_ORDER,
    analyze,
    analyze_tree,
    infer_engine,
    local_name,
    node_key,
    parse_xml,
    source_files,
)
from multires_app.export.xml_patches import apply_patches, node_path


def main(argv: list[str] | None = None) -> int:
    from multires_app.cli.commands import execute

    args = build_parser().parse_args(argv)
    try:
        return execute(args)
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
