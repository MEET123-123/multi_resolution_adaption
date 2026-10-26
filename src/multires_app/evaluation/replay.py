"""Budgeted real human-action transactions on source/target preview runtimes."""

import copy
from .core import Observation, digest


class ReplayEvaluator:
    name = "interaction-replay"
    version = "1.0"

    def evaluate(self, ctx):
        from multires_app.preview.interaction_server import InteractiveSession

        p = ctx.project
        budget = ctx.options.get("replayTraces", 0)
        max_steps = ctx.options.get("replaySteps", 12)
        traces = {}
        for state in p["graph"]["states"]:
            trace = state.get("sourceTrace", state.get("trace", []))
            if trace and all("humanAction" in t for t in trace):
                traces[digest(trace)] = (state["id"], trace)
        selected = sorted(traces.values(), key=lambda t: (-len(t[1]), t[0]))[:budget]
        for device in p["devices"]:
            did = device["profile"]["id"]
            attempted = 0
            for sid, trace in selected:
                try:
                    session = InteractiveSession(p, did)
                    initial = session.capture_state()
                    failed = []
                    for index, step in enumerate(trace[:max_steps]):
                        action = {**step["humanAction"], "surface": "source"}
                        result = session.gesture(action)
                        comparison = result["comparison"]
                        controls = p["graph"].get("semantics", {}).get("behaviorControls", [])
                        mismatch = [
                            name
                            for name in controls
                            if session.runtime.lookup(name) != session.source_runtime.lookup(name)
                        ]
                        if (
                            not all(
                                comparison[k].get("valid")
                                for k in ("sourceOutcome", "targetOutcome")
                            )
                            or mismatch
                        ):
                            failed.append(
                                {
                                    "step": index,
                                    "action": action,
                                    "comparison": comparison,
                                    "controls": mismatch,
                                }
                            )
                            break
                    # Undo checks debugger state recovery only, not authored back navigation.
                    if not failed:
                        # Runtime-sync commits are legitimate extra journal
                        # entries. Restore the actual root, not N guessed undos.
                        session.checkout(session.journal.records[0]["id"])
                        restored = session.capture_state()
                        # response() can discover additional read-side resource
                        # diagnostics. They are coverage evidence, not a change
                        # in the restored script/system/view state.
                        expected = copy.deepcopy(initial)
                        for snapshot in (expected, restored):
                            for surface in ("source", "target"):
                                snapshot[surface].pop("diagnostics", None)
                        if restored != expected:
                            failed.append({"reason": "debugger-undo-state-drift"})
                    status = "fail" if failed else "unknown" if len(trace) > max_steps else "pass"
                    yield Observation(
                        "INTERACTION.TRACE",
                        status,
                        "P0",
                        device=did,
                        state=sid,
                        stage="interaction",
                        evidence={
                            "steps": min(len(trace), max_steps),
                            "requiredSteps": len(trace),
                            "failures": failed,
                            "scope": "offline human transactions; native effects recorded only",
                        },
                    )
                except Exception as exc:
                    yield Observation(
                        "INTERACTION.TRACE",
                        "unknown",
                        "P0",
                        device=did,
                        state=sid,
                        stage="interaction",
                        evidence={"error": str(exc)},
                    )
                attempted += 1
            yield Observation(
                "INTERACTION.TRACE_COVERAGE",
                "pass" if traces and attempted == len(traces) else "unknown",
                stage="interaction",
                device=did,
                evidence={
                    "availableTraces": len(traces),
                    "attemptedTraces": attempted,
                    "traceBudget": budget,
                    "stepBudget": max_steps,
                },
            )
