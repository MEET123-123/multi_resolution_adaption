"""Static XML/theme analysis, independent of command-line argument parsing."""

from __future__ import annotations
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

VERSION = "0.1.0"
COMMAND_TAGS = {
    "VariableCommand",
    "IntentCommand",
    "UnlockerCommand",
    "ImageCommand",
    "PlayCommand",
    "VideoCommand",
    "SoundCommand",
    "LockscreenCommand",
    "ProgressCommand",
    "ParticleCommand",
    "Command",
}
GEOMETRY_ATTRS = {"x", "y", "w", "h", "width", "height"}
LAYER_ORDER = {
    "base-display": 0,
    "gesture-interaction": 1,
    "state-transition": 2,
    "system-action": 3,
}


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
        return [
            (p.relative_to(source).as_posix(), p.read_bytes())
            for p in sorted(source.rglob("*.xml"))
            if p.is_file()
        ]
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

    def walk(
        node: ET.Element,
        path: str,
        parent_key: str | None = None,
        interaction_owner: str | None = None,
    ) -> None:
        tag = local_name(node.tag)
        tag_counts[tag] += 1
        key = node_key(node, path)
        is_visual = tag in {
            "Image",
            "AniFrame",
            "Text",
            "Time",
            "Date",
            "Number",
            "Video",
            "Button",
            "Progress",
            "Group",
        } or any(a in node.attrib for a in ("src", "srcExp"))
        if is_visual:
            geometry = {a: node.attrib[a] for a in GEOMETRY_ATTRS if a in node.attrib}
            refs = sorted(set(re.findall(r"[#@]([A-Za-z_][\w.]*)", " ".join(node.attrib.values()))))
            elements.append(
                {
                    "key": key,
                    "file": file_name,
                    "path": path,
                    "tag": tag,
                    "attributes": dict(node.attrib),
                    "geometry": geometry,
                    "parent": parent_key,
                    "variableRefs": refs,
                    "assetRefs": {a: node.attrib[a] for a in ("src", "srcExp") if a in node.attrib},
                    "hasInteraction": any(
                        local_name(child.tag)
                        in {"Trigger", "Command", "VariableCommand", "IntentCommand"}
                        for child in node.iter()
                        if child is not node
                    ),
                }
            )
            for ref in refs:
                variable_uses[ref].append(key)

        if tag == "Var" and node.attrib.get("name"):
            name = node.attrib["name"]
            variable_defs[name] = {
                "name": name,
                "file": file_name,
                "path": path,
                "expression": node.attrib.get("expression"),
                "persistent": node.attrib.get("persist") or node.attrib.get("globalPersist"),
                "hasTrigger": any(
                    local_name(c.tag) == "Trigger" for c in node.iter() if c is not node
                ),
            }

        if tag == "Trigger":
            owner = interaction_owner or parent_key or "root"
            action = node.attrib.get(
                "action", "variable-change" if owner.startswith("Var:") else "unspecified"
            )
            commands = [
                {"tag": local_name(c.tag), "attributes": dict(c.attrib)}
                for c in node.iter()
                if c is not node and local_name(c.tag) in COMMAND_TAGS
            ]
            signature = (owner, action)
            scene = scene_by_signature.setdefault(
                signature,
                {
                    "id": f"scene-{len(scene_by_signature)+1:03d}",
                    "source": file_name,
                    "owner": owner,
                    "action": action,
                    "commands": [],
                    "triggerPaths": [],
                },
            )
            scene["commands"].extend(commands)
            scene["triggerPaths"].append(path)
            triggers.append({"owner": owner, "action": action, "path": path, "commands": commands})

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
        if definition["hasTrigger"] and not any(
            s["owner"] == f"Var:{name}" and s["action"] == "variable-change" for s in scenes
        ):
            scenes.append(
                {
                    "id": f"state-{name}",
                    "source": file_name,
                    "owner": f"Var:{name}",
                    "action": "variable-change",
                    "commands": [],
                    "triggerPaths": [definition["path"]],
                }
            )
    if not scenes:
        scenes.append(
            {
                "id": "scene-passive-001",
                "source": file_name,
                "owner": "root",
                "action": "passive-display",
                "commands": [],
                "triggerPaths": [],
            }
        )

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
        item["sceneRefs"] = [
            scene["id"]
            for scene in scenes
            if scene["owner"] == item["key"]
            or scene["owner"].startswith("Var:")
            and scene["owner"].split(":", 1)[1] in refs
        ]

    # Propose work categories; the user can override every decision in layout-plan.json.
    for item in elements:
        tag, geo = item["tag"], item["geometry"]
        if item["parent"] and tag in {"Image", "Text", "Time", "Date"}:
            category, reason, confidence = (
                "shared-component-candidate",
                "元素位于分组容器内；检查是否与同组交互/视觉单元共同适配。",
                0.62,
            )
        elif item["hasInteraction"] or tag == "Button":
            category, reason, confidence = (
                "layout-required",
                "元素关联交互或命令，适配时需保留命中区域与动作关系。",
                0.82,
            )
        elif geo:
            category, reason, confidence = (
                "direct-layout",
                "独立定位元素，可先通过设备断点/坐标规则适配。",
                0.70,
            )
        else:
            category, reason, confidence = (
                "manual-review",
                "未发现明确坐标或交互语义，需人工确认。",
                0.35,
            )
        item["layoutDecision"] = {
            "category": category,
            "reason": reason,
            "confidence": confidence,
            "reviewRequired": True,
        }

    scene_layers = {
        layer: [scene["id"] for scene in scenes if layer in scene["layers"]]
        for layer in LAYER_ORDER
    }
    return {
        "file": file_name,
        "rootTag": local_name(root.tag),
        "tagCounts": dict(tag_counts),
        "elements": elements,
        "scenes": scenes,
        "triggers": triggers,
        "sceneLayers": scene_layers,
        "variables": list(variable_defs.values()),
        "variableUses": dict(variable_uses),
    }


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
            element["layoutDecision"] = {
                "category": "shared-component-candidate",
                "reason": "发现多个相同控件结构；评估是否抽成跨设备公共布局组件。",
                "confidence": 0.58,
                "reviewRequired": True,
            }
    return {
        "formatVersion": 1,
        "toolVersion": VERSION,
        "source": str(source.resolve()),
        "engine": infer_engine(paths, [r for _, r in parsed], engine),
        "files": docs,
        "repeatedStructures": repeated,
        "parseWarnings": failures,
        "summary": {
            "xmlFiles": len(files),
            "parsedFiles": len(parsed),
            "elements": len(all_elements),
            "scenes": sum(len(d["scenes"]) for d in docs),
            "triggers": sum(len(d["triggers"]) for d in docs),
        },
    }
