#!/usr/bin/env python3
"""Inspect Huawei lock-screen theme XML and apply explicit layout patches.

The analyzer is intentionally engine-agnostic: it preserves source paths and
unknown XML nodes, and reports heuristics for human review before adaptation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from multires_app.layout.layout_api import load_algorithm, make_layout_plan
from multires_app.preview.previewer import build_preview


VERSION = "0.1.0"
COMMAND_TAGS = {"VariableCommand", "IntentCommand", "UnlockerCommand",
                "ImageCommand", "PlayCommand", "VideoCommand", "SoundCommand",
                "LockscreenCommand", "ProgressCommand", "ParticleCommand",
                "Command"}
GEOMETRY_ATTRS = {"x", "y", "w", "h", "width", "height"}
LAYER_ORDER = {"base-display": 0, "gesture-interaction": 1,
               "state-transition": 2, "system-action": 3}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else "#comment"


def node_key(node: ET.Element, path: str) -> str:
    for attr in ("id", "name"):
        value = node.attrib.get(attr)
        if value:
            return f"{local_name(node.tag)}:{value}"
    return f"{local_name(node.tag)}:{path}"


def parse_xml(data: bytes) -> ET.Element:
    # ElementTree does not resolve external entities; parsing is local-only.
    return ET.fromstring(data)


def source_files(source: Path) -> list[tuple[str, bytes]]:
    if source.is_dir():
        return [(p.relative_to(source).as_posix(), p.read_bytes())
                for p in sorted(source.rglob("*.xml")) if p.is_file()]
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            rows = []
            for info in archive.infolist():
                name = PurePosixPath(info.filename)
                if info.is_dir() or name.suffix.lower() != ".xml":
                    continue
                if ".." in name.parts:
                    continue
                if info.file_size > 20 * 1024 * 1024:
                    continue
                rows.append((name.as_posix(), archive.read(info)))
            return rows
    if source.suffix.lower() == ".xml":
        return [(source.name, source.read_bytes())]
    raise ValueError("输入需为 XML 文件、主题目录或 ZIP/HWT 压缩包")


def infer_engine(paths: list[str], roots: list[ET.Element], override: str) -> str:
    if override != "auto":
        return override
    joined = " ".join(paths).lower()
    xml_text = " ".join(ET.tostring(root, encoding="unicode")[:5000] for root in roots)
    if "base/" in joined or "harmonyos 5" in xml_text.lower():
        return "harmonyos-5+ (inferred)"
    if "manifest.xml" in joined or "lockscreen" in xml_text.lower():
        return "legacy-lockscreen-manifest (inferred; OS range unverified)"
    return "unknown"


def analyze_tree(file_name: str, root: ET.Element) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    triggers: list[dict[str, Any]] = []
    variable_defs: dict[str, dict[str, Any]] = {}
    variable_uses: defaultdict[str, list[str]] = defaultdict(list)
    scene_by_signature: dict[tuple[str, str], dict[str, Any]] = {}
    tag_counts: Counter[str] = Counter()

    def walk(node: ET.Element, path: str, parent_key: str | None = None,
             interaction_owner: str | None = None) -> None:
        tag = local_name(node.tag)
        tag_counts[tag] += 1
        key = node_key(node, path)
        is_visual = tag in {"Image", "AniFrame", "Text", "Time", "Date", "Number", "Video", "Button", "Progress", "Group"} or any(a in node.attrib for a in ("src", "srcExp"))
        if is_visual:
            geometry = {a: node.attrib[a] for a in GEOMETRY_ATTRS if a in node.attrib}
            refs = sorted(set(re.findall(r"[#@]([A-Za-z_][\w.]*)", " ".join(node.attrib.values()))))
            elements.append({
                "key": key, "file": file_name, "path": path, "tag": tag,
                "attributes": dict(node.attrib), "geometry": geometry,
                "parent": parent_key, "variableRefs": refs,
                "assetRefs": {a: node.attrib[a] for a in ("src", "srcExp") if a in node.attrib},
                "hasInteraction": any(local_name(child.tag) in {"Trigger", "Command", "VariableCommand", "IntentCommand"} for child in node.iter() if child is not node),
            })
            for ref in refs:
                variable_uses[ref].append(key)

        if tag == "Var" and node.attrib.get("name"):
            name = node.attrib["name"]
            variable_defs[name] = {"name": name, "file": file_name, "path": path,
                                   "expression": node.attrib.get("expression"),
                                   "persistent": node.attrib.get("persist") or node.attrib.get("globalPersist"),
                                   "hasTrigger": any(local_name(c.tag) == "Trigger" for c in node.iter() if c is not node)}

        if tag == "Trigger":
            owner = interaction_owner or parent_key or "root"
            action = node.attrib.get("action", "variable-change" if owner.startswith("Var:") else "unspecified")
            commands = [{"tag": local_name(c.tag), "attributes": dict(c.attrib)}
                        for c in node.iter() if c is not node and local_name(c.tag) in COMMAND_TAGS]
            signature = (owner, action)
            scene = scene_by_signature.setdefault(signature, {
                "id": f"scene-{len(scene_by_signature)+1:03d}", "source": file_name,
                "owner": owner, "action": action, "commands": [], "triggerPaths": []})
            scene["commands"].extend(commands)
            scene["triggerPaths"].append(path)
            triggers.append({"owner": owner, "action": action, "path": path,
                             "commands": commands})

        children = list(node)
        sibling_counts: Counter[str] = Counter()
        next_interaction_owner = interaction_owner
        if tag == "Button":
            next_interaction_owner = key
        elif tag == "Var" and node.attrib.get("name"):
            next_interaction_owner = f"Var:{node.attrib['name']}"
        elif tag in {"ExternalCommands", "ExternCommand"}:
            next_interaction_owner = key
        for child in children:
            ctag = local_name(child.tag)
            sibling_counts[ctag] += 1
            child_path = f"{path}/{ctag}[{sibling_counts[ctag]}]"
            walk(child, child_path, key, next_interaction_owner)

    walk(root, f"/{local_name(root.tag)}[1]")

    # Scene inference combines explicit event handlers with variable change triggers.
    scenes = list(scene_by_signature.values())
    for name, definition in variable_defs.items():
        if definition["hasTrigger"] and not any(s["owner"] == f"Var:{name}" and s["action"] == "variable-change" for s in scenes):
            scenes.append({"id": f"state-{name}", "source": file_name, "owner": f"Var:{name}",
                           "action": "variable-change", "commands": [], "triggerPaths": [definition["path"]]})
    if not scenes:
        scenes.append({"id": "scene-passive-001", "source": file_name, "owner": "root",
                       "action": "passive-display", "commands": [], "triggerPaths": []})

    for scene in scenes:
        command_tags = {command["tag"] for command in scene["commands"]}
        if scene["action"] == "passive-display":
            layers = ["base-display"]
        else:
            layers = []
            if scene["action"] != "variable-change":
                layers.append("gesture-interaction")
            if scene["action"] == "variable-change" or "VariableCommand" in command_tags:
                layers.append("state-transition")
            if command_tags.intersection({"IntentCommand", "UnlockerCommand", "ProgressCommand"}):
                layers.append("system-action")
            if not layers:
                layers.append("gesture-interaction")
        scene["layers"] = layers
        scene["layer"] = layers[-1]
        scene["layerOrder"] = max(LAYER_ORDER[layer] for layer in layers)

    for item in elements:
        refs = set(item["variableRefs"])
        item["sceneRefs"] = [scene["id"] for scene in scenes
                             if scene["owner"] == item["key"]
                             or scene["owner"].startswith("Var:")
                             and scene["owner"].split(":", 1)[1] in refs]

    # Propose work categories; the user can override every decision in layout-plan.json.
    for item in elements:
        tag, geo = item["tag"], item["geometry"]
        if item["parent"] and tag in {"Image", "Text", "Time", "Date"}:
            category, reason, confidence = "shared-component-candidate", "元素位于分组容器内；检查是否与同组交互/视觉单元共同适配。", 0.62
        elif item["hasInteraction"] or tag == "Button":
            category, reason, confidence = "layout-required", "元素关联交互或命令，适配时需保留命中区域与动作关系。", 0.82
        elif geo:
            category, reason, confidence = "direct-layout", "独立定位元素，可先通过设备断点/坐标规则适配。", 0.70
        else:
            category, reason, confidence = "manual-review", "未发现明确坐标或交互语义，需人工确认。", 0.35
        item["layoutDecision"] = {"category": category, "reason": reason,
                                  "confidence": confidence, "reviewRequired": True}

    scene_layers = {layer: [scene["id"] for scene in scenes if layer in scene["layers"]]
                    for layer in LAYER_ORDER}
    return {"file": file_name, "rootTag": local_name(root.tag), "tagCounts": dict(tag_counts),
            "elements": elements, "scenes": scenes, "triggers": triggers,
            "sceneLayers": scene_layers,
            "variables": list(variable_defs.values()),
            "variableUses": dict(variable_uses)}


def analyze(source: Path, engine: str) -> dict[str, Any]:
    files = source_files(source)
    parsed = []
    failures = []
    for name, data in files:
        try:
            parsed.append((name, parse_xml(data)))
        except ET.ParseError as exc:
            failures.append({"file": name, "error": str(exc)})
    if not parsed:
        raise ValueError("没有找到可解析的 XML 锁屏脚本")
    paths = [name for name, _ in parsed]
    docs = [analyze_tree(name, root) for name, root in parsed]
    all_elements = [e for d in docs for e in d["elements"]]
    shared_shapes: defaultdict[str, list[str]] = defaultdict(list)
    for e in all_elements:
        shape = f"{e['tag']}|" + ",".join(sorted(e["attributes"].keys()))
        shared_shapes[shape].append(e["key"])
    repeated = {shape: keys for shape, keys in shared_shapes.items() if len(keys) > 1}
    for element in all_elements:
        shape = f"{element['tag']}|" + ",".join(sorted(element["attributes"].keys()))
        if shape in repeated and element["layoutDecision"]["category"] == "direct-layout":
            element["layoutDecision"] = {"category": "shared-component-candidate",
                                         "reason": "发现多个相同控件结构；评估是否抽成跨设备公共布局组件。",
                                         "confidence": 0.58, "reviewRequired": True}
    return {"formatVersion": 1, "toolVersion": VERSION, "source": str(source.resolve()),
            "engine": infer_engine(paths, [r for _, r in parsed], engine),
            "files": docs, "repeatedStructures": repeated, "parseWarnings": failures,
            "summary": {"xmlFiles": len(files), "parsedFiles": len(parsed),
                        "elements": len(all_elements),
                        "scenes": sum(len(d["scenes"]) for d in docs),
                        "triggers": sum(len(d["triggers"]) for d in docs)}}


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def apply_patches(script: Path, patch_file: Path, output: Path,
                  target: str | None = None) -> None:
    """Apply explicit patches addressed by a unique XML id/name or stable path."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.parse(script, parser=parser).getroot()
    payload = json.loads(patch_file.read_text(encoding="utf-8"))
    if "devices" in payload:
        if not target:
            raise ValueError("对布局计划回写时必须使用 --target 指定 profile id")
        selected = next((d for d in payload["devices"] if d.get("profile", {}).get("id") == target), None)
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
            raise ValueError(f"patch selector must match exactly one node; got {len(matches)}: {selector}")
        node = matches[0]
        for attr, value in patch.get("set", {}).items():
            node.set(attr, str(value))
        applied.append({"selector": selector, "set": patch.get("set", {})})
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    print(json.dumps({"output": str(output.resolve()), "applied": applied}, ensure_ascii=False, indent=2))


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


def main() -> int:
    parser = argparse.ArgumentParser(description="鸿蒙锁屏主题多分辨率分析/回写原型")
    sub = parser.add_subparsers(dest="command", required=True)
    p_analyze = sub.add_parser("analyze", help="分析主题目录、XML 或 ZIP/HWT 压缩包")
    p_analyze.add_argument("source", type=Path)
    p_analyze.add_argument("--engine", choices=["auto", "harmonyos-5+", "harmonyos-4.x-or-earlier", "unknown"], default="auto")
    p_analyze.add_argument("--out", type=Path, default=Path("multires-analysis.json"))
    p_apply = sub.add_parser("apply", help="根据人工审核的 patch JSON 回写单个 XML 脚本")
    p_apply.add_argument("script", type=Path)
    p_apply.add_argument("--patches", type=Path, required=True)
    p_apply.add_argument("--target", help="布局计划中的目标 profile id")
    p_apply.add_argument("--out", type=Path, required=True)
    p_layout = sub.add_parser("layout", help="将分析结果送入可插拔布局算法")
    p_layout.add_argument("analysis", type=Path)
    p_layout.add_argument("--source-profile", type=Path, required=True)
    p_layout.add_argument("--profiles", type=Path, required=True)
    p_layout.add_argument("--algorithm", default="demo", help="demo 或 Python 算法 module:Class / file.py:Class")
    p_layout.add_argument("--out", type=Path, default=Path("layout-plan.json"))
    p_preview = sub.add_parser("preview", help="从布局计划生成独立 HTML/SVG 预览器")
    p_preview.add_argument("layout_plan", type=Path)
    p_preview.add_argument("--source-image", type=Path, help="将主题包预览图嵌入预览器")
    p_preview.add_argument("--out", type=Path, default=Path("layout-preview.html"))
    p_scenes = sub.add_parser("scenes", help="解析锁屏交互状态，真实素材渲染、组件重排与 XML 回写")
    p_scenes.add_argument("source", type=Path)
    p_scenes.add_argument("--source-profile", type=Path, required=True)
    p_scenes.add_argument("--profiles", type=Path, required=True)
    p_scenes.add_argument("--algorithm", default="anchor", help="anchor 九宫格/容器/约束求解；adaptive 旧版两列重排；或 file.py:Class")
    p_scenes.add_argument("--components", type=Path, help="覆盖自动推断的组件成员与角色")
    p_scenes.add_argument("--anchor-config", type=Path, help="锚点、嵌套容器和关系约束 JSON")
    p_scenes.add_argument("--inputs", type=Path, help="预览系统输入和初始持久变量 JSON")
    p_scenes.add_argument("--max-states", type=int, default=48)
    p_scenes.add_argument("--max-depth", type=int, default=4)
    p_scenes.add_argument("--out", type=Path, default=Path("scene-project"))
    p_export = sub.add_parser("export", help="将场景重排脚本写入新的 HWT 包")
    p_export.add_argument("project", type=Path)
    p_export.add_argument("--target", required=True)
    p_export.add_argument("--out", type=Path, required=True)
    p_relayout = sub.add_parser("relayout", help="使用缓存场景更换布局算法或组件配置")
    p_relayout.add_argument("project", type=Path)
    p_relayout.add_argument("--algorithm", default="anchor")
    p_relayout.add_argument("--components", type=Path)
    p_relayout.add_argument("--anchor-config", type=Path)
    p_serve = sub.add_parser("serve", help="启动本地实时交互解释器和布局聚类预览")
    p_serve.add_argument("project", type=Path)
    p_serve.add_argument("--port", type=int, default=8765)
    p_live = sub.add_parser("live", help="实时预览主题包：导入 HWT、手势执行与动画播放")
    p_live.add_argument("source", type=Path, nargs="?", help="要预览的 .hwt/.zip，留空则启动后在页面里导入")
    p_live.add_argument("--port", type=int, default=8767, help="监听端口，默认 8767（serve 用的是 8766）")
    p_live.add_argument("--out", type=Path, default=Path("preview-live"), help="素材解包目录，默认 preview-live")
    p_live.add_argument("--no-import", action="store_true", help="只预览启动时指定的主题包")
    p_tools = sub.add_parser("tools", help="批量转换、重新布局、打包和服务管理工具")
    p_tools.add_argument("tool_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.command == "tools":
            from multires_app.tools.runner import main as tools_main
            return tools_main(args.tool_args)
        elif args.command == "analyze":
            result = analyze(args.source, args.engine)
            write_json(args.out, result)
            print(json.dumps({"output": str(args.out.resolve()), **result["summary"], "engine": result["engine"]}, ensure_ascii=False, indent=2))
        elif args.command == "apply":
            apply_patches(args.script, args.patches, args.out, args.target)
        elif args.command == "layout":
            analysis_doc = json.loads(args.analysis.read_text(encoding="utf-8"))
            source_profile = json.loads(args.source_profile.read_text(encoding="utf-8"))
            profiles_doc = json.loads(args.profiles.read_text(encoding="utf-8"))
            algorithm = load_algorithm(args.algorithm)
            plan = make_layout_plan(analysis_doc, source_profile, profiles_doc, algorithm)
            write_json(args.out, plan)
            print(json.dumps({"output": str(args.out.resolve()), "algorithm": plan["algorithm"],
                              "profiles": len(plan["devices"]),
                              "ready": sum(d["status"] == "ready" for d in plan["devices"])},
                             ensure_ascii=False, indent=2))
        elif args.command == "scenes":
            from multires_app.pipeline.scene_pipeline import create_project
            profiles=json.loads(args.profiles.read_text(encoding='utf-8'))
            if args.anchor_config:
                for profile in profiles['profiles']:profile.setdefault('adaptation',{})['anchorLayout']=json.loads(args.anchor_config.read_text(encoding='utf-8'))
            result = create_project(args.source,
                                    json.loads(args.source_profile.read_text(encoding="utf-8")),
                                    profiles,
                                    args.out, args.algorithm, args.max_states, args.max_depth, args.components, args.inputs)
            print(json.dumps({k:result[k] for k in ("source","states","transitions","devices","components","diagnosticCount")}, ensure_ascii=False, indent=2))
        elif args.command == "serve":
            from multires_app.preview.interaction_server import serve_project
            serve_project(args.project,args.port)
        elif args.command == "live":
            from multires_app.preview.live_preview import serve_live
            serve_live(args.source, args.port, args.out, not args.no_import)
        elif args.command == "relayout":
            from multires_app.pipeline.scene_pipeline import relayout_project
            print(json.dumps(relayout_project(args.project, args.algorithm, args.components, args.anchor_config), ensure_ascii=False, indent=2))
        elif args.command == "export":
            from multires_app.pipeline.scene_pipeline import export_package
            print(json.dumps(export_package(args.project, args.target, args.out), ensure_ascii=False, indent=2))
        else:
            plan = json.loads(args.layout_plan.read_text(encoding="utf-8"))
            build_preview(plan, args.out, args.source_image)
            print(json.dumps({"output": str(args.out.resolve())}, ensure_ascii=False, indent=2))
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
