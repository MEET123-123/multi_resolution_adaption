"""Persist constraints and accept reviewed edits through the existing scene scheduler."""

import copy
import json
from collections import Counter
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.pipeline.scene_clusters import schedule_device


def write_anchor_artifacts(project, out):
    artifact = out / "anchor-layout.json"
    stored = project.get("anchorIRs", {})
    if project.get("anchorIRArtifact") and not stored and artifact.is_file():
        project.setdefault("sceneAnalysis", {}).setdefault("artifacts", {})[
            "anchorLayout"
        ] = "anchor-layout.json"
        return
    refs = {
        l.get("adaptationPlan", {}).get("layoutIRRef")
        for d in project["devices"]
        for l in d["layouts"]
    } - {None}
    previous = {}
    if project.get("anchorIRArtifact") and not refs <= set(stored) and artifact.is_file():
        previous = json.loads(artifact.read_text(encoding="utf8"))
    available = {**previous.get("layoutIRs", {}), **stored}
    old_scenes = {
        (target, s["state"]): s
        for target, d in previous.get("devices", {}).items()
        for s in d.get("scenes", [])
    }
    devices = {}
    counts = Counter()
    for d in project["devices"]:
        scenes = []
        for l in d["layouts"]:
            p = l.get("adaptationPlan", {})
            if p.get("policy") != "nine-grid-anchor-kiwi":
                continue
            counts[p.get("route", "reuse")] += 1
            validation = p.get("validation", {})
            old = old_scenes.get((d["profile"]["id"], l["state"]), {})
            if validation.get("fullDiagnostics") and old.get("layoutIRRef") == p.get("layoutIRRef"):
                validation = old.get("validation", validation)
            scenes.append(
                {
                    "state": l["state"],
                    "reusedFrom": p.get("reusedFrom"),
                    "groups": p.get("groups", []),
                    "validation": validation,
                    "frozenIds": p.get("frozenIds", []),
                    "layoutIRRef": p.get("layoutIRRef"),
                }
            )
        if scenes:
            devices[d["profile"]["id"]] = {
                "viewport": d["viewport"],
                "resolutionPx": d["profile"]["resolutionPx"],
                "quality": d.get("layoutQuality", {}),
                "scenes": scenes,
            }
    if not devices:
        return
    report = {
        "schemaVersion": 1,
        "algorithm": "anchor-theme-layout-v1",
        "coordinateUnit": "theme-engine-logical",
        "devices": devices,
        "layoutIRs": {key: value for key, value in available.items() if key in refs},
        "summary": dict(counts),
        "nativeDeviceTest": False,
        "reviewRequired": True,
        "sourceDesign": "华为主题多分辨率适配_锚点分组容器布局技术设计_V1.0.docx",
    }
    (out / "anchor-layout.json").write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    project.setdefault("sceneAnalysis", {}).setdefault("artifacts", {})[
        "anchorLayout"
    ] = "anchor-layout.json"


def apply_configuration(project, target, configuration, reason="designer correction"):
    """Transactional, bounded target-wide edit. A failed solve leaves project untouched."""
    if not isinstance(configuration, dict) or set(configuration) - {
        "groups",
        "constraints",
        "minScale",
        "maxScale",
        "gapPx",
        "pairedTargets",
        "ungroupedIds",
        "inferScreenRelations",
        "resourceScalePolicy",
        "resourceScaleFactor",
        "placementPolicy",
        "maxRelativeAxisScale",
        "maxRelativeAreaScale",
        "similarAspectThreshold",
    }:
        raise ValueError("unsupported anchor configuration fields")
    if (
        len(configuration.get("groups", [])) > 512
        or len(configuration.get("constraints", [])) > 2048
    ):
        raise ValueError("anchor configuration exceeds group/constraint limit")
    updated = copy.deepcopy(project)
    device = next(d for d in updated["devices"] if d["profile"]["id"] == target)
    if "inferScreenRelations" in configuration and not isinstance(
        configuration["inferScreenRelations"], bool
    ):
        raise ValueError("inferScreenRelations must be boolean")
    old = copy.deepcopy(device["profile"].get("adaptation", {}).get("anchorLayout", {}))
    device["profile"].setdefault("adaptation", {})["anchorLayout"] = copy.deepcopy(configuration)
    schedule_device(updated, device, AnchorThemeLayout())
    updated["algorithm"] = "anchor-theme-layout-v1"
    updated["algorithmSpec"] = "anchor"
    updated.setdefault("anchorFeedback", []).append(
        {
            "target": target,
            "scope": "target-all-reachable-scenes",
            "reason": reason,
            "originalProposal": old,
            "accepted": True,
            "configuration": copy.deepcopy(configuration),
        }
    )
    return updated
