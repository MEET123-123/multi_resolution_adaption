"""Write solved component transforms into XML and package HWT candidates.

Retains original script ordering, actions, animation and resource families.
"""

import hashlib
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET
from multires_app.engine.scene_engine import VISUAL
from multires_app.export.state_guards import compiled_transform, state_guard


def export_script(project, target, out):
    project["_referenceAssetRoot"] = str(
        (out.parent.parent if out.parent.name == "scripts" else out.parent).resolve()
    )
    model = project["model"]
    source = Path(project["source"])
    if source.is_file():
        with zipfile.ZipFile(source) as z:
            data = z.read(model["file"])
    else:
        data = (source / model["file"]).read_bytes()
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(data, parser=parser)
    original_nodes = [(n, dict(n.attrib), list(n)) for n in root.iter()]
    # Stable file+positional path remains unique even when names repeat.
    xml_nodes = {}

    def visit(node, path):
        xml_nodes[model["file"] + "::" + path] = node
        counts = defaultdict(int)
        for child in node:
            if not isinstance(child.tag, str):
                continue
            counts[child.tag] += 1
            visit(child, path + f"/{child.tag}[{counts[child.tag]}]")

    visit(root, "/" + root.tag + "[1]")
    device = next((d for d in project["devices"] if d["profile"]["id"] == target), None)
    if not device:
        raise ValueError("未知目标设备: " + target)
    from multires_app.layout.anchor_validation import validate_export

    anchor_validation = validate_export(project, device)
    project.get("_stateGuards", {}).pop(target, None)
    changed = []
    transforms = {}
    macros = {}

    def macro(expression):
        try:
            float(expression)
            return expression
        except ValueError:
            pass
        if expression not in macros:
            name = "mr_layout_" + hashlib.sha1(expression.encode()).hexdigest()[:12]
            if name in model["variables"]:
                raise ValueError("布局变量与源变量冲突")
            macros[expression] = name
        return "#" + macros[expression]

    row_sizes = defaultdict(dict)
    for layout in device["layouts"]:
        for row in layout["rows"]:
            if row[6] >= 0:
                uid = project["nodeOrder"][row[0]]
                meta = project["assets"][project["assetOrder"][row[6]]]
                row_sizes[uid][layout["state"]] = (meta["width"], meta["height"])
    state_index = {s["id"]: s for s in project["graph"]["states"]}
    intrinsic_warnings = []
    for comp in project["components"]:
        transforms[comp["id"]] = {
            field: macro(compiled_transform(project, device, comp["id"], field))
            for field in ("sx", "sy", "tx", "ty")
        }
    roots = {r: c["id"] for c in project["components"] for r in c["roots"]}

    def affine(raw, s, offset="0"):
        if s == "1" and offset == "0":
            return raw
        return f"({s})*({raw})" + (f"+({offset})" if offset != "0" else "")

    def boundary_offset(uid, axis, transform):
        parent = model["nodes"][uid]["parent"]
        parent_cid = project["nodeComponents"].get(parent)
        if parent_cid == project["nodeComponents"].get(uid):
            return "0"
        coordinates = []
        adapted_coordinates = []
        ancestor = parent
        while ancestor and model["nodes"][ancestor]["tag"] != "Lockscreen":
            if model["nodes"][ancestor]["tag"] == "Group":
                coordinates.append(
                    "(" + model["nodes"][ancestor]["attributes"].get(axis, "0") + ")"
                )
                adapted_coordinates.append("(" + xml_nodes[ancestor].get(axis, "0") + ")")
            ancestor = model["nodes"][ancestor]["parent"]
        scale, shift = "s" + axis, "t" + axis
        if not coordinates:
            return transform[shift]
        # Sum the actual adapted ancestor positions, including unowned wrapper
        # Groups between component roots. Immediate-parent lookup misses these.
        return f"({transform[scale]})*({'+'.join(coordinates)})+({transform[shift]})-({'+'.join(adapted_coordinates)})"

    for uid, node in xml_nodes.items():
        cid = project["nodeComponents"].get(uid)
        if not cid:
            continue
        transform = transforms[cid]
        is_root = uid in roots
        for attr, scale, offset in (
            ("x", "sx", "tx"),
            ("y", "sy", "ty"),
            ("w", "sx", None),
            ("width", "sx", None),
            ("h", "sy", None),
            ("height", "sy", None),
            ("size", "sy", None),
            ("space", "sx", None),
        ):
            raw = node.get(attr)
            if raw is None and attr in {"x", "y"} and is_root:
                raw = "0"
            if (
                raw is None
                and attr in {"w", "h"}
                and node.tag == "Video"
                and node.get("isBackground", "false").lower() == "true"
                and node.get("width" if attr == "w" else "height") is None
            ):
                raw = "#screen_width" if attr == "w" else "#screen_height"
            if (
                raw is None
                and attr in {"w", "h"}
                and node.tag in {"Image", "Video"}
                and node.get("width" if attr == "w" else "height") is None
            ):
                # Preserve natural image size under component scale as well.
                from multires_app.engine.scene_engine import resolve_asset

                temp = {**model, "assetNames": list(project["assets"])}
                asset = project["assets"].get(resolve_asset(temp, node.get("src", "")))
                from multires_app.media.media_layout import intrinsic_expression

                indexed_size = intrinsic_expression(
                    model,
                    project["assets"],
                    node.attrib,
                    "width" if attr == "w" else "height",
                    model["nodes"][uid],
                )
                if indexed_size is not None:
                    raw = indexed_size
                    if attr == "w" and re.search(r"[#@]\d", node.get("srcid", "")):
                        intrinsic_warnings.append(
                            uid
                            + ": leading-digit variable in srcid is not interpreted; uniform native frame canvas preserved"
                        )
                elif asset and not node.get("srcExp") and not node.get("srcid"):
                    raw = str(asset["width" if attr == "w" else "height"])
                elif row_sizes.get(uid):
                    dimensions = defaultdict(list)
                    for sid, pair in row_sizes[uid].items():
                        dimensions[str(pair[0 if attr == "w" else 1])].append(
                            state_guard(project, state_index[sid], device)
                        )
                    if len(dimensions) == 1:
                        raw = next(iter(dimensions))
                    else:
                        args = []
                        for size, guards in dimensions.items():
                            args.extend(["(" + ")+(".join(sorted(set(guards))) + ")", size])
                        raw = "ifelse(" + ",".join(args + [next(iter(dimensions))]) + ")"
                        intrinsic_warnings.append(
                            uid + ": dynamic intrinsic dimensions use sampled states"
                        )
                else:
                    intrinsic_warnings.append(uid + ": unresolved intrinsic image dimensions")
            if (
                raw is None
                and attr == "size"
                and node.tag in {"Text", "Time", "Date", "DateTime", "Number"}
            ):
                raw = "42"
            if raw is None:
                continue
            value = affine(
                raw, transform[scale], boundary_offset(uid, attr, transform) if offset else "0"
            )
            if value != node.get(attr):
                node.set(attr, value)
                changed.append({"node": uid, "attribute": attr, "old": raw, "new": value})
        for animation in node:
            if animation.tag in {"SourcesAnimation", "PositionAnimation", "SizeAnimation"}:
                for frame in animation:
                    for attr in ("x", "y", "w", "h"):
                        if attr in frame.attrib:
                            frame.set(
                                attr,
                                affine(
                                    frame.get(attr), transform["sx" if attr in {"x", "w"} else "sy"]
                                ),
                            )
    # Splitting positional peers leaves their style/animation wrappers in the
    # original XML. A shared parent translation must use the same scale as
    # every leaf below it; otherwise its entry distance remains unscaled.
    for uid, node in xml_nodes.items():
        if node.tag != "Group" or uid in project["nodeComponents"]:
            continue
        animations = [a for a in node if a.tag == "PositionAnimation"]
        if not animations:
            continue
        descendants = [
            u
            for u, n in model["nodes"].items()
            if u.startswith(uid + "/") and n["tag"] in VISUAL and n["tag"] != "Group"
        ]
        owners = {project["nodeComponents"].get(u) for u in descendants}
        if not owners or None in owners:
            continue
        for axis in ("x", "y"):
            scales = {transforms[cid]["s" + axis] for cid in owners}
            if len(scales) != 1:
                raise ValueError(
                    "split animation wrapper has incompatible child scales: " + uid + " / " + axis
                )
            scale = next(iter(scales))
            for animation in animations:
                for frame in animation:
                    if axis in frame.attrib:
                        frame.set(axis, affine(frame.get(axis), scale))
    # Capture the chosen component transform before an event mutates its state;
    # remap gesture expressions back to the original component coordinates.
    capture_count = 0
    capture_defined = set()
    from multires_app.export.touch_coordinates import (
        pure_touch_definitions,
        expand_touch,
        has_touch,
        TOKENS,
    )

    touch_definitions = pure_touch_definitions(
        (n["tag"], n["attributes"]) for n in model["nodes"].values()
    )
    touch_owners = {
        t["owner"]
        for t in model["triggers"]
        if any(
            has_touch(expand_touch(v, touch_definitions))
            for n in xml_nodes[t["id"]].iter()
            for k, v in n.attrib.items()
            if k in {"expression", "condition"}
        )
    }
    for trigger in model["triggers"]:
        cid = project["nodeComponents"].get(trigger["owner"])
        if not cid:
            continue
        xml_trigger = xml_nodes[trigger["id"]]
        if trigger["owner"] not in touch_owners:
            continue
        prefix = "mr_" + hashlib.sha1(trigger["owner"].encode()).hexdigest()[:10] + "_"
        if any(name.startswith(prefix) for name in model["variables"]):
            raise ValueError("生成变量与源脚本冲突")
        if prefix not in capture_defined:
            for field in ("sx", "sy", "tx", "ty"):
                root.insert(
                    0,
                    ET.Element(
                        "Var",
                        {
                            "name": prefix + field,
                            "expression": "1" if field in {"sx", "sy"} else "0",
                        },
                    ),
                )
            capture_defined.add(prefix)
            # An unconditional adapter runs before every authored down handler,
            # including conditional/empty ones. The self-equality identifies
            # this generated event without changing its always-true predicate.
            down = ET.Element(
                "Trigger", {"action": "down", "condition": f"eq(#{prefix}sx,#{prefix}sx)"}
            )
            for field in ("sx", "sy", "tx", "ty"):
                ET.SubElement(
                    down,
                    "VariableCommand",
                    {"name": prefix + field, "expression": transforms[cid][field]},
                )
            xml_nodes[model["nodes"][trigger["id"]]["parent"]].insert(0, down)
        for child in xml_trigger.iter():
            for attr in ("expression", "condition"):
                if attr in child.attrib:

                    def remap(m):
                        axis = m[1][-1]
                        shift = "#" + prefix + "t" + axis
                        scale = "#" + prefix + "s" + axis
                        return f"((#{m[1]}-({shift}))/({scale}))"

                    expanded = expand_touch(child.get(attr), touch_definitions)
                    child.set(
                        attr,
                        TOKENS.sub(
                            lambda m: (
                                remap(m)
                                if m[1] in {"touch_x", "touch_y", "touch_begin_x", "touch_begin_y"}
                                else m[0]
                            ),
                            expanded,
                        ),
                    )
        capture_count += 1
    for expression, name in macros.items():
        root.insert(0, ET.Element("Var", {"name": name, "expression": expression}))
    used_guards = set(re.findall(r"#(mr_state_[0-9a-f]+)", ET.tostring(root, encoding="unicode")))
    for name, expression in project.get("_stateGuardExpressions", {}).items():
        if name not in used_guards:
            continue
        if name in model["variables"]:
            raise ValueError("场景变量与源变量冲突")
        root.insert(0, ET.Element("Var", {"name": name, "expression": expression}))
    out.parent.mkdir(parents=True, exist_ok=True)
    from multires_app.export.layout_writeback import patch_script
    from multires_app.media.resource_scaling import generate_glyph_resources

    resources = generate_glyph_resources(project, device, out)
    approved_remaps = {}
    for uid, src in resources["remaps"].items():
        if uid in xml_nodes:
            xml_nodes[uid].set("src", src)
            approved_remaps[id(xml_nodes[uid])] = src
    intrinsic_warnings.extend(resources["warnings"])
    from multires_app.engine.coordinate_space import adapt_xml_inputs

    coordinate_space = adapt_xml_inputs(root, model)
    patched, writeback = patch_script(data, original_nodes, root, approved_remaps, coordinate_space)
    from multires_app.export.interaction_preservation import audit_preservation

    preservation = audit_preservation(data, patched, writeback)
    writeback["interactionPreservation"] = preservation
    out.write_bytes(patched)
    out.with_suffix(".patch.json").write_text(
        json.dumps(writeback, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ET.parse(out)
    return {
        "target": target,
        "script": str(out.resolve()),
        "changedAttributes": len(changed),
        "touchTriggers": capture_count,
        "intrinsicSizingWarnings": sorted(set(intrinsic_warnings)),
        "resourceScaling": {
            "generatedFiles": len(resources["files"]),
            "remappedNodes": len(resources["remaps"]),
            "warnings": resources["warnings"],
        },
        "writebackValidation": {k: v for k, v in writeback.items() if k != "changes"},
        "anchorValidation": anchor_validation,
        "interactionPreservation": preservation,
        "note": "保留原命令/条件/动画，组件变换按状态编译为表达式；需主题引擎实测。",
    }


def export_package(project_path, target, out):
    project = json.loads(project_path.read_text(encoding="utf-8"))
    source = Path(project["source"])
    if not zipfile.is_zipfile(source):
        raise ValueError("重打包需要原始 ZIP/HWT")
    if out.resolve() == source.resolve():
        raise ValueError("输出不得覆盖源主题包")
    script = project_path.parent / "scripts" / (target + ".xml")
    result = export_script(project, target, script)
    replacements = {project["model"]["file"]: script.read_bytes()}
    from multires_app.media.resource_scaling import manifest_resources

    generated = manifest_resources(script)["files"]
    for extra_path in project.get("variantProjects", []):
        extra_path = Path(extra_path)
        extra = json.loads(extra_path.read_text(encoding="utf-8"))
        if Path(extra["source"]).resolve() != source.resolve():
            raise ValueError("锁屏变体必须属于同一个源 HWT")
        extra_script = extra_path.parent / "scripts" / (target + ".xml")
        export_script(extra, target, extra_script)
        replacements[extra["model"]["file"]] = extra_script.read_bytes()
        generated.update(manifest_resources(extra_script)["files"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(out, "w") as output:
        for info in original.infolist():
            output.writestr(info, replacements.get(info.filename, original.read(info)))
        for name, meta in sorted(generated.items()):
            if name in original.namelist():
                raise ValueError("generated resource collides with original: " + name)
            data = Path(meta["file"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != meta["sha256"]:
                raise ValueError("generated resource digest mismatch")
            output.writestr(name, data)
    result["package"] = str(out.resolve())
    result["adaptedManifests"] = list(replacements)
    return result
