"""Local upload → scenes → strategy → reference → match → XML/HWT workbench."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("artifacts/workbench"))
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--algorithm",
        default="anchor",
        help="Trusted operator layout plugin; existing SceneLayoutAlgorithm interface",
    )
    parser.add_argument("--import-project", type=Path, action="append", default=[])
    args = parser.parse_args()
    if not 60 <= args.timeout <= 3600 or not 1 <= args.port <= 65535:
        parser.error("端口或超时范围无效")
    from multires_app.preview.workbench import Workbench
    from multires_app.preview.interaction_server import serve_project

    workbench = Workbench(args.root, args.timeout, args.algorithm)
    for path in args.import_project:
        workbench.import_project(path)
    try:
        serve_project(workbench.projects, args.port, workbench=workbench)
    finally:
        workbench.close()


if __name__ == "__main__":
    main()
