"""Pair original and exported scenes by replayed action paths, not cache order."""

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from PIL import Image, ImageDraw

from multires_app.engine.scene_engine import load_model
from multires_app.interaction.interaction_semantics import initialize, perform_action
from multires_app.preview.paint_visibility import runtime_elements, raster_snapshot, font

LABELS = {
    "tap": "点击",
    "double-tap": "双击",
    "long-press": "长按",
    "swipe": "滑动",
    "left": "左滑",
    "right": "右滑",
    "up": "上滑",
    "down": "下滑",
}


def replay_pair(source_model, target_model, source_viewport, target_viewport, trace, assets):
    source = initialize(source_model, source_viewport["width"], source_viewport["height"])
    target = initialize(target_model, target_viewport["width"], target_viewport["height"])
    steps = []
    for step in trace:
        action = step["humanAction"]
        original = perform_action(source, action, assets, verify_hit=True)
        adapted = perform_action(target, action, assets, verify_hit=True)
        steps.append(
            {
                "action": action,
                "sourceValid": original["valid"],
                "targetValid": adapted["valid"],
                "sourceReason": original.get("reason"),
                "targetReason": adapted.get("reason"),
            }
        )
        if not original["valid"] or not adapted["valid"]:
            break
    return source, target, steps


def compare_paint(
    source_elements,
    target_elements,
    source_paint,
    target_paint,
    source_viewport,
    target_viewport,
    roles=None,
):
    """Same node AND resource AND paint visibility, with an independent size floor."""
    source = {e["key"]: e for e in source_elements}
    target = {e["key"]: e for e in target_elements}
    floor = min(
        target_viewport["width"] / source_viewport["width"],
        target_viewport["height"] / source_viewport["height"],
    )
    records = []

    def painted(p):
        return (
            p and p["status"] == "visible" and p["paintPixels"] >= 2 and p["paintFraction"] >= 0.005
        )

    for key in sorted(source.keys() | target.keys()):
        a, b = source.get(key), target.get(key)
        sp, tp = source_paint.get(key), target_paint.get(key)
        node = a or b
        uid = node["id"]
        issues = []
        warnings = []
        role = (roles or {}).get(uid, "content")
        background = role == "background" or node["tag"] == "WaterWallpaper"
        if a is None or b is None:
            issues.append("extra-node" if a is None else "missing-node")
        if (
            a
            and b
            and (a.get("asset", "") != b.get("asset", "") or a.get("text", "") != b.get("text", ""))
        ):
            issues.append("different-content")
        if sp and tp and sp["supported"] and tp["supported"] and node["tag"] != "Button":
            if painted(sp) and not painted(tp):
                issues.append("missing-paint")
            if not painted(sp) and painted(tp):
                issues.append("extra-paint")
        scale = None
        if a and b and min(a["rect"]["w"], a["rect"]["h"]) > 0:
            scale = min(b["rect"]["w"] / a["rect"]["w"], b["rect"]["h"] / a["rect"]["h"])
            if painted(sp) and not background:
                if scale < floor * 0.98:
                    issues.append("too-small")
                if tp and tp["clipFraction"] < sp["clipFraction"] - 0.08:
                    warnings.append("more-clipped")
                if (
                    tp
                    and tp["paintFraction"] < sp["paintFraction"] * 0.6
                    and sp["paintFraction"] - tp["paintFraction"] > 0.15
                ):
                    warnings.append("more-occluded")
        records.append(
            {
                "key": key,
                "node": uid,
                "tag": node["tag"],
                "role": role,
                "source": sp,
                "target": tp,
                "resource": (a or b).get("asset", ""),
                "scale": scale,
                "minimumScale": floor,
                "issues": issues,
                "warnings": warnings,
            }
        )
    return records


def binding_errors(bindings, source, target):
    source = {e["id"]: e for e in source}
    target = {e["id"]: e for e in target}
    errors = []
    checked = 0

    def relative(button, image):
        a = button["rect"]
        b = image["rect"]
        if min(b["w"], b["h"]) <= 0:
            return None
        return [
            (a["x"] - b["x"]) / b["w"],
            (a["y"] - b["y"]) / b["h"],
            a["w"] / b["w"],
            a["h"] / b["h"],
        ]

    for binding in bindings:
        button = binding["button"]
        for uid in binding["anchors"]:
            if not all(u in source and u in target for u in (button, uid)):
                continue
            a = relative(source[button], source[uid])
            b = relative(target[button], target[uid])
            if a is None or b is None:
                continue
            checked += 1
            if max(abs(x - y) for x, y in zip(a, b)) > 0.002:
                errors.append({"button": button, "image": uid, "source": a, "target": b})
    return {"checked": checked, "errors": errors}


def build_scene_comparison(project_path, package_path):
    directory = project_path.parent
    p = json.loads(project_path.read_text(encoding="utf-8"))
    device = p["devices"][0]
    sv = p["sourceDevice"]["viewport"]
    tv = device["viewport"]
    source = load_model(Path(p["source"]))
    target = load_model(package_path)
    # Only real external inputs are shared. In particular no target overrides
    # or cached layout variables are injected into the original script.
    inputs = {
        k: copy.deepcopy(v)
        for k, v in p["model"].get("inputs", {}).items()
        if k in {"variables", "system"}
    }
    source["inputs"] = copy.deepcopy(inputs)
    target["inputs"] = copy.deepcopy(inputs)
    asset_root = directory / "assets"
    out = directory / "comparison"
    out.mkdir(exist_ok=True)
    role_by_component = {c["id"]: c["role"] for c in p["components"]}
    roles = {uid: role_by_component[cid] for uid, cid in p["nodeComponents"].items()}
    order = {uid: i for i, uid in enumerate(p["nodeOrder"])}
    scenes = []
    for state in p["graph"]["states"]:
        sr, tr, steps = replay_pair(source, target, sv, tv, state["trace"], p["assets"])
        se = runtime_elements(sr, p["assets"], sv)
        te = runtime_elements(tr, p["assets"], tv)
        original, sp = raster_snapshot(se, sv, asset_root)
        adapted, tp = raster_snapshot(te, tv, asset_root)
        records = compare_paint(se, te, sp, tp, sv, tv, roles)
        for record in records:
            record["number"] = order.get(record["node"], -1) + 1
        controls = {
            n: [sr.lookup(n), tr.lookup(n)]
            for n in p["graph"]["semantics"]["behaviorControls"]
            if sr.lookup(n) != tr.lookup(n)
        }
        bindings = binding_errors(p["model"].get("layoutBindings", {}).get("bindings", []), se, te)
        source_file = "comparison/source-" + state["id"] + ".jpg"
        target_file = "comparison/target-" + state["id"] + ".jpg"
        original.convert("RGB").save(directory / source_file, quality=91)
        adapted.convert("RGB").save(directory / target_file, quality=91)
        pair = Image.new("RGB", (original.width + adapted.width + 60, 850), "#edf1f6")
        draw = ImageDraw.Draw(pair)
        draw.text((15, 8), "原始直板机 · " + state["id"], font=font(22), fill="#223344")
        draw.text(
            (original.width + 45, 8), "适配平板 · 同一操作路径", font=font(22), fill="#223344"
        )
        pair.paste(original, (15, 50))
        pair.paste(adapted, (original.width + 45, 50))
        pair_file = "comparison/pair-" + state["id"] + ".jpg"
        pair.save(directory / pair_file, quality=91)
        errors = sum(len(r["issues"]) for r in records)
        warnings = sum(len(r["warnings"]) for r in records)
        path = [
            LABELS.get(
                step["humanAction"].get("gesture"),
                LABELS.get(step["humanAction"]["kind"], step["humanAction"]["kind"]),
            )
            + " · "
            + step["humanAction"]["owner"].split("::")[-1]
            for step in state["trace"]
        ]
        passed = (
            not errors
            and not controls
            and not bindings["errors"]
            and len(steps) == len(state["trace"])
            and all(s["sourceValid"] and s["targetValid"] for s in steps)
        )
        scenes.append(
            {
                "id": state["id"],
                "path": path,
                "pathHash": hashlib.sha256(
                    json.dumps(state["trace"], sort_keys=True).encode()
                ).hexdigest(),
                "sourceImage": source_file,
                "targetImage": target_file,
                "pairImage": pair_file,
                "steps": steps,
                "sourceTimeMs": sr.time_ms,
                "targetTimeMs": tr.time_ms,
                "controlDifferences": controls,
                "sourceVisible": sum(r["status"] == "visible" for r in sp.values()),
                "targetVisible": sum(r["status"] == "visible" for r in tp.values()),
                "bindings": bindings,
                "errors": errors,
                "warnings": warnings,
                "passed": passed,
                "elements": records,
                "unsupported": sorted(
                    {
                        r["tag"]
                        for r in records
                        if any(v and not v["supported"] for v in (r["source"], r["target"]))
                    }
                ),
            }
        )
    report = {
        "schemaVersion": 1,
        "sourcePackage": Path(p["source"]).name,
        "targetPackage": package_path.name,
        "sourceViewport": sv,
        "targetViewport": tv,
        "sourceResolution": p["sourceProfile"]["resolutionPx"],
        "targetResolution": device["profile"]["resolutionPx"],
        "method": "fresh source/exported script replay of identical action paths, identical clocks; raster alpha and paint order comparison",
        "nativeDeviceTest": False,
        "scenes": scenes,
        "transitions": [
            {k: e.get(k) for k in ("from", "to", "action", "gesture", "owner")}
            for e in p["graph"]["transitions"]
            if e.get("to") and e["to"] != e["from"]
        ],
        "passed": all(s["passed"] for s in scenes),
        "errors": sum(s["errors"] for s in scenes),
        "warnings": sum(s["warnings"] for s in scenes),
        "coverage": p["graph"]["coverage"],
        "scope": "Bounded sampled paths; offline approximation. Native ripple, 3D and external applications are not simulated.",
    }
    (directory / "scene-correspondence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    template = Path(__file__).with_name("scene_correspondence.html").read_text(encoding="utf-8")
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (directory / "scene-correspondence.html").write_text(
        template.replace("__DATA__", payload), encoding="utf-8"
    )
    return report
