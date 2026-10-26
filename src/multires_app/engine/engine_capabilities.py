"""Conservative feature reports: parsing is not native platform certification."""

from collections import Counter

SOURCES = {
    "image5": "https://developer.huawei.com/consumer/cn/doc/content/themes-engine-next-base-picmulti-0000002504354947",
    "emotion6": "https://developer.huawei.com/consumer/cn/doc/content/emotioncommand-0000002551079172",
}
CORE = {
    "Lockscreen",
    "Var",
    "Group",
    "Image",
    "Text",
    "Time",
    "Date",
    "DateTime",
    "Number",
    "ImageSeries",
    "Button",
    "Trigger",
    "Triggers",
    "ExternalCommands",
    "VariableCommand",
}
STRUCTURAL = {
    "PositionAnimation",
    "SizeAnimation",
    "AlphaAnimation",
    "VariableAnimation",
    "SourcesAnimation",
    "RotationAnimation",
    "ScaleAnimation",
    "Item",
    "Position",
    "Size",
    "Alpha",
    "AniFrame",
    "Source",
    "Variable",
}


def capability_report(model, target="preserve-source"):
    if target not in {"preserve-source", "harmonyos-4", "harmonyos-5", "harmonyos-6"}:
        raise ValueError("unknown target engine profile: " + target)
    issues = []
    counts = Counter(n["tag"] for n in model["nodes"].values())
    for uid, n in model["nodes"].items():
        tag = n["tag"]
        a = n["attributes"]
        if tag == "EmotionCommand":
            issues.append(
                {
                    "node": uid,
                    "feature": tag,
                    "status": (
                        "requires-native-test" if target == "harmonyos-6" else "version-gated"
                    ),
                    "minimumVersion": "HarmonyOS 6.0; selected devices",
                    "source": SOURCES["emotion6"],
                }
            )
        elif tag == "IntentCommand":
            issues.append(
                {
                    "node": uid,
                    "feature": tag,
                    "status": (
                        "requires-platform-mapping"
                        if target in {"harmonyos-5", "harmonyos-6"}
                        else "native-effect"
                    ),
                    "reason": "Application launch identifiers and parameters need a target platform adapter; original command preserved.",
                }
            )
        elif tag in {"CycleCommand", "GroupCommands", "LoopCommand"}:
            issues.append(
                {
                    "node": uid,
                    "feature": tag,
                    "status": "unsupported-control-flow",
                    "reason": "Native command container is preserved; reference execution must not flatten or simulate it.",
                }
            )
        elif tag not in CORE:
            issues.append(
                {
                    "node": uid,
                    "feature": tag,
                    "status": (
                        "sampled-animation"
                        if tag in STRUCTURAL
                        else (
                            "native-effect"
                            if tag.endswith("Command") or tag == "Command"
                            else "unsupported-feature"
                        )
                    ),
                    "reason": "Preserved in native XML; reference runner does not certify this capability.",
                }
            )
    return {
        "schemaVersion": 1,
        "targetEngine": target,
        "sourceEngine": model.get("inputs", {}).get("sourceEngine", "unspecified"),
        "status": "native-validation-required",
        "nativeDeviceTest": False,
        "tagCounts": dict(counts),
        "issues": issues,
        "summary": dict(Counter(i["status"] for i in issues)),
        "exportPolicy": "Preserve source syntax. A resolution change does not migrate the engine or application identifiers.",
        "sources": SOURCES,
    }
