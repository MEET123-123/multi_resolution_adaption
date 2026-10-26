"""Apply explicitly reviewed JSON patches to a single XML script."""

from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from multires_app.engine.theme_analysis import local_name


def apply_patches(script: Path, patch_file: Path, output: Path, target: str | None = None) -> None:
    """Apply explicit patches addressed by a unique XML id/name or stable path."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.parse(script, parser=parser).getroot()
    payload = json.loads(patch_file.read_text(encoding="utf-8"))
    if "devices" in payload:
        if not target:
            raise ValueError("对布局计划回写时必须使用 --target 指定 profile id")
        selected = next(
            (d for d in payload["devices"] if d.get("profile", {}).get("id") == target), None
        )
        if selected is None:
            raise ValueError(f"布局计划中不存在目标 profile：{target}")
        if selected.get("status") != "ready":
            raise ValueError(f"目标 profile 当前不可回写：{selected.get('status')}")
        payload = {"patches": selected.get("patches", [])}
    patches = payload.get("patches", [])
    nodes = list(root.iter())
    applied = []
    for patch in patches:
        selector = patch.get("selector", {})
        matches = []
        for node in nodes:
            if selector.get("tag") and local_name(node.tag) != selector["tag"]:
                continue
            if selector.get("id") and node.attrib.get("id") != selector["id"]:
                continue
            if selector.get("name") and node.attrib.get("name") != selector["name"]:
                continue
            if selector.get("path") and selector["path"] != node_path(root, node):
                continue
            if not any(selector.get(k) for k in ("id", "name", "path")):
                continue
            matches.append(node)
        if len(matches) != 1:
            raise ValueError(
                f"patch selector must match exactly one node; got {len(matches)}: {selector}"
            )
        node = matches[0]
        for attr, value in patch.get("set", {}).items():
            node.set(attr, str(value))
        applied.append({"selector": selector, "set": patch.get("set", {})})
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    print(
        json.dumps(
            {"output": str(output.resolve()), "applied": applied}, ensure_ascii=False, indent=2
        )
    )


def node_path(root: ET.Element, target: ET.Element) -> str:
    """Return analyzer-compatible positional path for an element."""
    found: str | None = None

    def visit(node: ET.Element, path: str) -> None:
        nonlocal found
        if node is target:
            found = path
            return
        counts: Counter[str] = Counter()
        for child in list(node):
            tag = local_name(child.tag)
            counts[tag] += 1
            visit(child, f"{path}/{tag}[{counts[tag]}]")

    visit(root, f"/{local_name(root.tag)}[1]")
    return found or ""
