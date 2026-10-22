"""Create empty human tasks; validate structure without inventing annotations."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def resolve(path):
    return (ROOT / path).resolve()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def new_task(identity, project_path=None, state_id=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identity):
        raise ValueError("任务 ID 仅允许 1–64 位英文字母、数字、短横线或下划线")
    destination = ROOT / "annotations" / (identity + ".json")
    facts_path = ROOT / "annotations" / "facts" / (identity + ".json")
    if destination.exists() or facts_path.exists():
        raise ValueError("任务或事实文件已存在，保留原文件；请换任务 ID")
    task = read(ROOT / "templates" / "task.blank.json")
    task["task_id"] = identity
    if project_path:
        project_path = resolve(project_path)
        project = read(project_path)
        states = {s["id"]: s for s in project["graph"]["states"]}
        if state_id not in states:
            raise ValueError("需要明确指定有效的 --state；先用 states 命令查看")
        layout = next((l for l in project["sourceDevice"]["layouts"] if l["state"] == state_id), None)
        if layout is None:
            raise ValueError("所选状态缺少源设备布局，不能同步证据")
        package = Path(project["source"]).resolve()
        if not package.is_file():
            raise ValueError("找不到原始包，不能生成可靠来源哈希")
        viewport = project["sourceDevice"]["viewport"]
        # Preserve raw parser facts; do not copy inferred semantic roles or
        # teacher target boxes into human annotation fields.
        facts = {"schema": "theme-layout-source-facts/1", "origin": "parser; unreviewed evidence",
                 "project_sha256": sha(project_path), "package_sha256": sha(package),
                 "scene_id": state_id, "source_viewport": viewport,
                 "nodes": project["model"]["nodes"], "visual_node_ids": project["nodeOrder"],
                 "state": states[state_id], "source_layout": layout}
        save(facts_path, facts)
        task["source"].update(package_path=str(package), package_sha256=facts["package_sha256"],
                              project_path=str(project_path), project_sha256=facts["project_sha256"],
                              scene_id=state_id, canvas={"width": viewport["width"], "height": viewport["height"]},
                              coordinate_unit="engine_logical", facts_path=str(facts_path))
    elif state_id:
        raise ValueError("--state 需要配合 --project")
    save(destination, task)
    return {"task": str(destination), "stage": "draft", "humanLabelsCreated": 0}


def validate(task, release=False):
    vocabulary = read(ROOT / "config" / "taxonomy.json")
    errors, warnings = [], []

    def check(condition, message):
        if not condition:
            errors.append(message)

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)

    def box(value):
        return (isinstance(value, list) and len(value) == 4 and all(finite(v) for v in value)
                and min(value[2:]) > 0 and max(abs(v) for v in value) <= 8000)

    def choice(value, key, field):
        if value is not None:
            check(value in vocabulary[key], f"{field}: 不在词表 {key} 中")
        elif release:
            errors.append(f"{field}: 尚未填写")

    try:
        check(task["schema"] == "theme-layout-annotation/1", "schema 不匹配")
        check(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", task["task_id"])), "task_id 非法")
        check(type(task["revision"]) is int and task["revision"] > 0, "revision 必须为正整数")
        source, target, dataset, annotations, review = (task[k] for k in ("source", "target", "dataset", "annotations", "review"))
        choice(review["stage"], "stages", "review.stage")
        structure, destination, quality = (annotations[k] for k in ("source_structure", "target_layout", "quality"))
        for label, section in (("source_structure", structure), ("target_layout", destination)):
            choice(section["status"], "section_statuses", label + ".status")
        groups = structure["groups"]
        check(isinstance(groups, list), "groups 必须为数组")
        index = {}
        for group in groups:
            identity = group["id"]
            check(text(identity) and identity not in index, "组件 ID 必须非空且唯一")
            index[identity] = group
        owners = {}
        for identity, group in index.items():
            parent = group["parent_id"]
            check(parent is None or parent in index, f"{identity}: parent_id 不存在")
            trail, cursor = set(), identity
            while cursor in index:
                if cursor in trail:
                    errors.append(f"{identity}: 组件树存在环")
                    break
                trail.add(cursor)
                cursor = index[cursor]["parent_id"]
            for member in group["member_ids"]:
                check(member not in owners, f"{member}: 叶节点被重复归属")
                owners[member] = identity
            for role in group["roles"]:
                choice(role, "roles", identity + ".roles")
            for key, wordlist in (("internal_layout", "internal_layouts"), ("transform_policy", "transform_policies"),
                                  ("bbox_basis", "bbox_bases"), ("offscreen_policy", "offscreen_policies")):
                choice(group[key], wordlist, identity + "." + key)
            if group["source_bbox_1000"] is not None or release:
                check(box(group["source_bbox_1000"]), f"{identity}: 源框需为有限 xywh，宽高为正")
            if release:
                check(bool(group["roles"]) and text(group["evidence"]), f"{identity}: 缺角色或分组证据")
                check(bool(group["member_ids"]) or any(g["parent_id"] == identity for g in groups), f"{identity}: 空组件")
        roots = {gid for gid, group in index.items() if group["parent_id"] is None}
        if len(roots) > 12:
            warnings.append("根组件超过建议预算，考虑按容器递归拆任务；禁止截断元素")
        references = set(index) | {"source_canvas", "target_canvas", "target_safe_area"}
        for scope, regions in (("source", structure["content_regions"]), ("target", destination["content_regions"])):
            for region in regions:
                check(text(region["id"]) and region["id"] not in references, "区域 ID 必须非空且不与组件/参照物重复")
                references.add(region["id"])
                check(region["kind"] in {"subject", "protected", "whitespace"} and box(region["bbox_1000"]), scope + ": 内容区域类型或框非法")
                check(region["origin"] in {"human", "algorithm_candidate"}, "区域来源非法")
                if release:
                    check(region["origin"] == "human" and text(region["evidence"]), "区域尚未人工核验或缺证据")
        if source.get("facts_path"):
            facts = read(resolve(source["facts_path"]))
            check(facts.get("schema") == "theme-layout-source-facts/1", "源事实 schema 不匹配")
            check(facts["scene_id"] == source["scene_id"], "源事实的状态不一致")
            check(facts["package_sha256"] == source["package_sha256"] and facts["project_sha256"] == source["project_sha256"], "源事实哈希与任务不一致")
            known = set(facts["nodes"])
            check(set(owners) <= known, "成员 ID 不在源节点目录中")
            for group in groups:
                check(set(group["hit_region_ids"]) <= known, f"{group['id']}: 热区引用不存在")
            references |= known
            excluded = set()
            for exclusion in structure["exclusion_sets"]:
                choice(exclusion["reason"], "exclusion_reasons", "exclusion_sets.reason")
                check(set(exclusion["node_ids"]) <= known, "排除节点不存在")
                check(not excluded.intersection(exclusion["node_ids"]), "排除集合重复")
                excluded.update(exclusion["node_ids"])
                if release:
                    check(text(exclusion["evidence"]), "排除集合缺证据")
            check(not excluded.intersection(owners), "同一节点同时被归组和排除")
            if release:
                missing = set(facts["visual_node_ids"]) - set(owners) - excluded
                check(not missing, f"{len(missing)} 个视觉节点未绑定或说明排除理由")
        elif release:
            errors.append("缺少自动提取的 source.facts_path")
        for scope, relations in (("source", structure["relations"]), ("target", destination["relations"])):
            for relation in relations:
                for field, wordlist in (("type", "relations"), ("scope", "scopes"), ("policy", "policies"),
                                       ("strength", "strengths"), ("origin", "relation_origins"), ("bbox_basis", "bbox_bases")):
                    choice(relation[field], wordlist, "relation." + field)
                check(relation["subject"] in references and relation["reference"] in references, "关系对象引用不存在")
                check(relation["scope"] in (scope, "both"), "关系 scope 与所在标注区不符")
                if release:
                    check(text(relation["evidence"]), "关系缺证据说明")
                    check(relation["origin"] != "algorithm_candidate", "未审核的算法关系不能发布")
        for gid, coordinates in destination["group_boxes_1000"].items():
            check(gid in roots and box(coordinates), f"{gid}: 目标框需引用根组件且为合法 xywh")
            if gid in index and box(coordinates):
                x, y, w, h = coordinates
                if min(x, y) < 0 or x + w > 1000 or y + h > 1000:
                    check(index[gid]["offscreen_policy"] in {"state_driven", "allowed_transition"}, f"{gid}: 屏外框缺少显式状态策略")
        for key in ("source_visual", "grouping", "relations", "mapping", "target_visual", "interaction", "writeback"):
            choice(quality[key], "quality_results", "quality." + key)
        if release:
            check(review["stage"] == "released", "stage 尚未 released")
            check(text(review["annotator"]) and text(review["reviewer"]), "缺标注者/复核者")
            check(review["annotator"] != review["reviewer"], "发布要求独立复核；单人复看不能伪称独立双标")
            check(text(review["adjudication_note"]), "缺仲裁/复核说明")
            check(structure["status"] == destination["status"] == "reviewed", "源结构和目标布局尚未审核")
            check(text(structure["scene_type"]) and bool(groups), "缺场景用途或组件树")
            check(dataset["split"] in {"train", "validation", "test"} and text(dataset["theme_family"]), "缺家族与集合")
            check(dataset["family_review"] == "confirmed", "家族划分未复核")
            check(dataset["rights_status"] == "confirmed", "来源使用范围尚未确认")
            check(destination["topology"] == "preserved", "拓扑变化进入独立队列；当前根组发布检查不支持")
            choice(destination["mode"], "layout_modes", "target_layout.mode")
            choice(destination["reference_origin"], "reference_origins", "target_layout.reference_origin")
            check(set(destination["group_boxes_1000"]) == roots, "目标组框未完整覆盖根组")
            check(text(destination["decision_note"]) and text(target["design_brief"]), "缺设计依据或事前 brief")
            check(target["brief_origin"] in {"designer_brief", "product_requirement"}, "brief 必须是事前需求；目标答案推导不能当输入")
            mappings = destination["mapping"]
            check(len(mappings) == len(roots), "需逐根组登记源目标映射")
            check({m["source_group_id"] for m in mappings} == roots and {m["target_group_id"] for m in mappings} == roots, "根组身份映射不完整")
            for section_name, section in (("source", source), ("target", target)):
                canvas = section["canvas"]
                check(isinstance(canvas, dict) and all(finite(canvas[k]) and canvas[k] > 0 for k in ("width", "height")), section_name + ": 缺有效画布")
                check(section["coordinate_unit"] in {"engine_logical", "native_pixel_verified"}, section_name + ": 坐标单位未确认")
            region = target["safe_region"]
            check(isinstance(region, dict) and box([region[k] for k in ("x", "y", "w", "h")]), "目标安全区非法")
            if isinstance(region, dict) and isinstance(target["canvas"], dict):
                check(region["x"] >= 0 and region["y"] >= 0 and region["x"] + region["w"] <= target["canvas"]["width"]
                      and region["y"] + region["h"] <= target["canvas"]["height"], "安全区超出目标画布")
            for key in ("package", "project", "frame"):
                path = source[key + "_path"]
                check(text(path) and resolve(path).is_file(), "source." + key + " 文件缺失")
                if text(path) and resolve(path).is_file():
                    check(sha(resolve(path)) == source[key + "_sha256"], "source." + key + " 哈希不匹配")
            reference = destination["reference_frame_path"]
            check(text(reference) and resolve(reference).is_file(), "缺人工核验的目标渲染参考")
            for key, result in quality.items():
                if key == "notes":
                    continue
                check(result in {"passed", "not_applicable"}, "quality." + key + " 尚未通过")
                if result == "not_applicable":
                    check(key in {"interaction", "writeback"} and text(quality["notes"].get(key)), "仅交互/写回可说明不适用，其他质量项必须通过")
            for local_task in destination["local_tasks"]:
                check(text(local_task) and resolve(local_task).is_file(), "局部任务文件缺失")
            if destination["local_tasks"]:
                warnings.append("已引用局部任务；本工具未验证其标签或执行，不能据此认定局部布局通过")
            if not structure["relations"] or not destination["relations"]:
                check(text(quality["notes"].get("relations")), "关系集合为空时需说明无适用关系的原因")
        else:
            warnings.append("草稿检查允许待填字段；通过不等于标签完成、可训练或设计正确")
        if review["stage"] == "blocked":
            choice(review["blocked_reason"], "blocked_reasons", "review.blocked_reason")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        errors.append(f"字段结构或证据文件错误: {exc}")
    return {"valid": not errors, "mode": "release" if release else "draft", "errors": errors, "warnings": warnings,
            "scope": "格式、引用、哈希、覆盖和发布门禁；未自动验证设计/关系语义、渲染或真机效果"}


def validate_batch(directory, release=False):
    paths = sorted(Path(directory).glob("*.json"))
    errors, families, sources, identities = [], {}, {}, set()
    for path in paths:
        try:
            task = read(path)
            result = validate(task, release)
            errors.extend({"file": str(path), "error": error} for error in result["errors"])
            identity = task["task_id"]
            if identity in identities:
                errors.append({"file": str(path), "error": "重复任务 ID"})
            identities.add(identity)
            family, split = task["dataset"]["theme_family"], task["dataset"]["split"]
            digest = task["source"]["package_sha256"]
            if family and split in {"train", "validation", "test"}:
                if family in families and families[family] != split:
                    errors.append({"file": str(path), "error": "同家族跨集合"})
                families[family] = split
                if digest:
                    if digest in sources and sources[digest] != (family, split):
                        errors.append({"file": str(path), "error": "同源包跨家族或集合"})
                    sources[digest] = (family, split)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            errors.append({"file": str(path), "error": str(exc)})
    return {"valid": bool(paths) and not errors, "tasks": len(paths), "errors": errors,
            "families": families, "note": "近似模板家族仍需人工核验；未自动导出或批准标签"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="创建待人工填写的主题布局任务，或检查任务结构")
    parser.add_argument("command", choices=["init", "states", "validate", "batch"])
    parser.add_argument("--id")
    parser.add_argument("--project")
    parser.add_argument("--state")
    parser.add_argument("--task")
    parser.add_argument("--directory", default="annotations")
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if not args.id:
                raise ValueError("需要 --id")
            result = new_task(args.id, args.project, args.state)
        elif args.command == "states":
            if not args.project:
                raise ValueError("需要 --project")
            project = read(resolve(args.project))
            result = {"states": [{"id": s["id"], "trace": s.get("sourceTrace", s.get("trace", []))}
                                 for s in project["graph"]["states"]]}
        elif args.command == "batch":
            result = validate_batch(resolve(args.directory), args.release)
        else:
            if not args.task:
                raise ValueError("需要 --task")
            result = validate(read(resolve(args.task)), release=args.release)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if result.get("valid") is False else 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
