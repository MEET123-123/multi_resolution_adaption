"""只读项目架构盘点：只创建新的 Markdown/JSON 报告，不移动、删除或修改任何已有文件。

从项目根目录执行：.\\.venv\\Scripts\\python.exe scripts\\project_inventory.py
"""
from __future__ import annotations

import ast
import collections
import datetime as dt
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "multires_app"
CODE_DIRS = ("src", "tests", "samples", "scripts", "configs", "studio_reverse", "docs")
DATA_DIRS = ("theme", "new_adaption", "artifacts", "eval", "preview-live", "archives")
SKIP = {".git", ".venv", ".venv-ml", "venv", "__pycache__", ".pytest_cache",
        "node_modules", "dist", "build", ".mypy_cache"}


def files_in(folder):
    if not folder.exists():
        return
    for base, dirs, names in os.walk(folder):
        dirs[:] = [x for x in dirs if x not in SKIP]
        for name in names:
            p = Path(base) / name
            if p.is_file():
                yield p


def shallow(folder):
    if not folder.is_dir():
        return []
    result = []
    for p in folder.iterdir():
        try:
            result.append({"name": p.name, "kind": "dir" if p.is_dir() else "file",
                           "bytes": p.stat().st_size if p.is_file() else None})
        except OSError:
            pass
    return sorted(result, key=lambda x: x["name"].casefold())


def python_map():
    modules, edges, entrypoints, problems = collections.Counter(), collections.Counter(), [], []
    for p in files_in(PKG):
        if p.suffix != ".py":
            continue
        rel = p.relative_to(PKG)
        group = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        modules[group] += 1
        try:
            tree = ast.parse(p.read_text(encoding="utf-8-sig"))
        except (SyntaxError, OSError, UnicodeError) as exc:
            problems.append({"file": str(rel), "problem": str(exc)[:140]})
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                names = []
            for n in names:
                if n.startswith("multires_app."):
                    dst = n.split(".")[1]
                    if dst != group:
                        edges[(group, dst)] += 1
        for node in ast.walk(tree):
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Name)
                    and node.test.left.id == "__name__"
                    and any(isinstance(x, ast.Constant) and x.value == "__main__"
                            for x in node.test.comparators)):
                entrypoints.append(str(p.relative_to(ROOT)).replace("\\", "/"))
                break
    return {
        "source_modules": dict(sorted(modules.items())),
        "absolute_import_edges": [{"from": a, "to": b, "count": n}
                                  for (a, b), n in sorted(edges.items())],
        "entrypoints": sorted(entrypoints),
        "parse_errors": problems,
        "caveat": "静态分析仅覆盖主包中的绝对 import，不能推断全部动态或相对导入。"
    }


def main():
    code = {}
    for name in CODE_DIRS:
        paths = list(files_in(ROOT / name))
        kinds = collections.Counter(p.suffix.lower() or "(none)" for p in paths)
        code[name] = {"count": len(paths), "extensions": dict(kinds.most_common(10)),
                      "subdirectories": [x["name"] for x in shallow(ROOT / name)
                                         if x["kind"] == "dir"][:50]}
    data = {name: shallow(ROOT / name)[:120] for name in DATA_DIRS}
    try:
        status = subprocess.run(["git", "status", "--short", "--branch"], cwd=ROOT,
                                text=True, capture_output=True, timeout=12).stdout[:10000]
    except (OSError, subprocess.TimeoutExpired):
        status = "git status 不可用"
    report = {"root": str(ROOT), "created": dt.datetime.now().isoformat(timespec="seconds"),
              "code": code, "data_dirs_shallow": data, "python": python_map(),
              "git_status": status,
              "note": "只读取当前项目目录与 Python AST；数据目录只列第一级；不删除、不移动、不覆盖旧文件。"}
    folder = ROOT / "artifacts" / "organization"
    folder.mkdir(parents=True, exist_ok=True)
    stem = "architecture-inventory-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    json_path = folder / (stem + ".json")
    md_path = folder / (stem + ".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 代码与目录当前快照", "", "生成于：" + report["created"], "",
             "**只读盘点，没有移动或删除文件。**", "",
             "## 代码目录", "", "| 路径 | 文件数量 | 文件类型 |", "|---|---:|---|"]
    for name, item in code.items():
        ex = ", ".join(f"{k}×{v}" for k, v in list(item["extensions"].items())[:5])
        lines.append(f"| \`{name}/\` | {item['count']} | {ex} |".replace("\\`", "`"))
    lines.extend(["", "## 主包模块", "", "| 分组 | Python 文件数 |", "|---|---:|"])
    for name, total in report["python"]["source_modules"].items():
        lines.append(f"| \`{name}\` | {total} |".replace("\\`", "`"))
    lines.extend(["", "## 主要跨模块绝对 import", "", "| 从 | 到 | 次数 |", "|---|---|---:|"])
    for edge in report["python"]["absolute_import_edges"]:
        lines.append(f"| {edge['from']} | {edge['to']} | {edge['count']} |")
    lines.extend(["", "## 数据与产物一级目录（仅列名字，不代表删除建议）", ""])
    for name, children in data.items():
        lines.append("### " + name)
        lines.append(", ".join(("[" + x["kind"] + "] " + x["name"])
                               for x in children[:45]) or "空/不可访问")
        lines.append("")
    lines.extend(["## Git 状态", "", "```text", status.strip(), "```",
                  "", "完整目录信息与入口脚本见相同前缀的 JSON 报告。"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Created:", md_path)
    print("Created:", json_path)
    print("No existing files modified; no moves or deletions.")


if __name__ == "__main__":
    main()
