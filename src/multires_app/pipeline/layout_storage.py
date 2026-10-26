"""Keep active solver evidence once; omit duplicated display diagnostics."""


def compact_layout_storage(project):
    used = set()
    flows = expressions = 0
    for device in project.get("devices", []):
        for layout in device.get("layouts", []):
            plan = layout.get("adaptationPlan", {})
            if plan.get("layoutIRRef"):
                used.add(plan["layoutIRRef"])
            if "spatialFlows" in plan:
                records = plan.pop("spatialFlows")
                flows += len(records)
                plan["spatialFlowCount"] = len(records)
                plan["spatialFlowRef"] = plan.get("layoutIRRef")
            for item in plan.get("validation", {}).get("soft_violations", []):
                if "expression" in item:
                    item.pop("expression")
                    expressions += 1
    previous = project.get("anchorIRs", {})
    project["anchorIRs"] = {key: value for key, value in previous.items() if key in used}
    return {
        "inactiveIRsRemoved": len(previous) - len(project["anchorIRs"]),
        "duplicatedPlanFlowsRemoved": flows,
        "derivedExpressionStringsRemoved": expressions,
    }


def externalize_anchor_evidence(project):
    """Call after writing anchor-layout.json; runtime geometry stays local."""
    refs = {
        l.get("adaptationPlan", {}).get("layoutIRRef")
        for d in project.get("devices", [])
        for l in d.get("layouts", [])
    } - {None}
    if refs:
        project["anchorIRArtifact"] = "anchor-layout.json"
        project["anchorIRCount"] = len(refs)
        project["anchorIRs"] = {}
    for d in project.get("devices", []):
        for l in d.get("layouts", []):
            validation = l.get("adaptationPlan", {}).get("validation", {})
            issues = validation.get("soft_violations", [])
            validation["softViolationCount"] = validation.get("softViolationCount", len(issues))
            if len(issues) > 12:
                validation["soft_violations"] = issues[:12]
                validation["fullDiagnostics"] = "anchor-layout.json"
