"""Per-check regression gate. No trade of hard failures for mean aesthetic gain."""


def compare(before, after, residual_budget=1e-6, score_budget=0.25):
    old = {r["id"]: r for r in before}
    new = {r["id"]: r for r in after}
    changes = []
    for key, row in old.items():
        current = new.get(key)
        # Positive-only overlap instances may disappear after a fix. Required
        # checks must remain present; missing instances cannot silently pass.
        if current is None:
            if row["status"] == "pass" or row["rule"] not in {"GEO.NEW_OVERLAP"}:
                changes.append({"id": key, "reason": "missing-check", "rule": row["rule"]})
            continue
        if row["plugin"] != current["plugin"]:
            changes.append({"id": key, "reason": "evaluator-version-mismatch"})
        if row["status"] in {"pass", "not_applicable"} and current["status"] in {
            "fail",
            "unknown",
            "review",
        }:
            changes.append(
                {
                    "id": key,
                    "reason": "status-regression",
                    "before": row["status"],
                    "after": current["status"],
                }
            )
        if row["status"] == "unknown" and current["status"] == "fail":
            changes.append({"id": key, "reason": "new-confirmed-failure"})
        if (
            row["status"] == current["status"]
            and current["status"] in {"fail", "review"}
            and current["residual"] > row["residual"] + residual_budget
        ):
            changes.append(
                {
                    "id": key,
                    "reason": "residual-regression",
                    "before": row["residual"],
                    "after": current["residual"],
                }
            )
        if row["rule"].startswith("CONTRACT."):

            def identities(record):
                return {
                    tuple(str(f.get(k, "")) for k in ("component", "first", "last", "axis", "edge"))
                    for f in record["evidence"].get("failures", [])
                    if isinstance(f, dict)
                }

            if identities(current) - identities(row):
                changes.append({"id": key, "reason": "new-contract-failure"})
        if row["rule"] == "AES.PROXY":
            a = row["evidence"].get("evaluation", {}).get("score")
            b = current["evidence"].get("evaluation", {}).get("score")
            if a is not None and b is not None and b < a - score_budget:
                changes.append(
                    {"id": key, "reason": "per-state-aesthetic-regression", "before": a, "after": b}
                )
    for key, row in new.items():
        if key not in old and row["status"] in {"fail", "review", "unknown"}:
            changes.append({"id": key, "reason": "new-" + row["status"], "rule": row["rule"]})
    improvements = [
        key
        for key, row in old.items()
        if row["status"] in {"fail", "review"}
        and (
            (key not in new and row["rule"] == "GEO.NEW_OVERLAP")
            or (
                key in new
                and (
                    new[key]["status"] == "pass"
                    or new[key]["residual"] < row["residual"] - residual_budget
                )
            )
        )
    ]
    return {
        "regressionPassed": not changes,
        "regressions": changes,
        "improvements": improvements,
        "comparedChecks": len(old.keys() & new.keys()),
        "missingChecks": len(old.keys() - new.keys()),
    }


def exit_code(records):
    if any(r["status"] == "fail" for r in records):
        return 1
    if not records or any(r["status"] in {"unknown", "review"} for r in records):
        return 2
    return 0
