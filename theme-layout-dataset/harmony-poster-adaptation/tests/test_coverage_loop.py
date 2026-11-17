"""Regressions for indexed resources and hit-tested coverage guidance."""
import unittest

from coverage_loop import next_traces
from theme_workspace import reference_audit


class CoverageTests(unittest.TestCase):
    def test_indexed_asset_family_replaces_unreferenced_base_literal(self):
        root = "unlock/lockscreen/"
        files = {
            root + "manifest.xml": b'<Lockscreen><Image src="card.png" srcid="#choice"/>'
            b'<Image src="old.png" srcExp="make()"/><Image src="missing.png"/></Lockscreen>',
            root + "card_0.png": b"", root + "card_12.png": b"", root + "card_shadow.png": b"",
        }
        audit = reference_audit(files)
        self.assertEqual(audit["resolved"][0]["family"], [root + "card_0.png", root + "card_12.png"])
        self.assertEqual([r["value"] for r in audit["missing"]], ["missing.png"])
        self.assertEqual(len(audit["dynamic"]), 2)

    def test_frontier_guide_keeps_original_sample_and_depth_cap(self):
        edge = {"from": "s", "to": None, "bounded": True, "trigger": "t", "triggers": ["t"],
                "owner": "button", "action": "swipe", "gesture": "left",
                "sample": [.5, .5, .1, .5], "intent": "scene-switch", "changes": {"theme": 1}}
        graph = {"states": [{"id": "s", "trace": []}], "transitions": [edge]}
        coverage = {"interaction": {"resolvedTriggers": []}}
        self.assertEqual(next_traces(graph, coverage, {"theme"}, 0), [])
        guide = next_traces(graph, coverage, {"theme"}, 1)
        self.assertEqual(guide[0]["actions"][0]["sample"], edge["sample"])


if __name__ == "__main__":
    unittest.main()
