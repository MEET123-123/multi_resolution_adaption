"""Scene report regressions independent of local HWTs and GPU weights."""
import unittest
from unittest.mock import patch

from scene_report import decisions, paths_from_start


class SceneReportTests(unittest.TestCase):
    def test_bounded_edge_keeps_witness_without_adding_fictional_state(self):
        graph = {"states": [{"id": "start"}, {"id": "open"}], "transitions": [
            {"from": "start", "to": "open"},
            {"from": "open", "to": "start"},
            {"from": "open", "to": None, "bounded": True},
        ]}
        incoming, outgoing = paths_from_start(graph)
        self.assertEqual(list(incoming["open"]), [0])
        self.assertEqual(list(outgoing["open"]), [1, 2])

    def test_unreachable_discovered_state_is_report_error(self):
        with self.assertRaisesRegex(ValueError, "without a path"):
            paths_from_start({"states": [{"id": "start"}, {"id": "stranded"}],
                              "transitions": [{"from": "stranded", "to": "start"}]})

    @patch("scene_report.describe_layout", return_value={})
    def test_preserve_engine_overlay_and_reuse_meaning(self, _describe):
        device = {"layouts": [
            self.layout("start", "full-reflow", None, [], ["clock"]),
            self.layout("menu", "overlay", "start", ["clock"], ["panel"]),
            self.layout("menu-clock-tick", "reuse", "menu", ["clock", "panel"], []),
        ]}
        self.assertEqual([r["mode"] for r in decisions({}, device)], ["full", "partial", "reuse"])

    @patch("scene_report.describe_layout", return_value={})
    def test_reuse_cannot_contain_affected_components(self, _describe):
        device = {"layouts": [
            self.layout("start", "full-reflow", None, [], ["clock"]),
            self.layout("tick", "reuse", "start", [], ["date"]),
        ]}
        with self.assertRaisesRegex(ValueError, "reuse requires"):
            decisions({}, device)

    @staticmethod
    def layout(state, kind, parent, fixed, affected):
        return {"state": state, "layoutRequest": {
            "kind": kind, "baseState": parent, "fixedComponents": fixed,
            "affectedComponents": affected, "evidence": [], "clusterId": "0", "familyId": "0",
        }}


if __name__ == "__main__":
    unittest.main()
