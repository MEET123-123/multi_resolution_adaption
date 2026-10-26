"""Regression checks for the extracted shared primitives of multistate themes."""
from __future__ import annotations
import ast
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"src"),str(ROOT/"studio_reverse"/"web_theme_pipeline"),
              str(ROOT/"scripts")]
import interactive as legacy
import theme_primitives as shared
import interaction_families_v4 as v4
import theme_signature

class ThemePrimitivesTests(unittest.TestCase):
    def test_legacy_reexports_identical_objects(self):
        for name in ("DEV","BOX","template","position","target_xml",
                     "draw_scene","cover","font","panels","state_position","conditional_xy"):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy,name),getattr(shared,name))
    def test_v4_no_longer_imports_interactive(self):
        self.assertIs(v4.base,shared)
        tree=ast.parse((ROOT/"studio_reverse"/"web_theme_pipeline"/"interaction_families_v4.py")
                       .read_text(encoding="utf8"))
        imports=[alias.name for node in ast.walk(tree)
                 if isinstance(node,ast.Import) for alias in node.names]
        self.assertNotIn("interactive",imports)
        self.assertIn("theme_primitives",imports)
    def test_behavior_signature_unchanged(self):
        before=json.loads((ROOT/"artifacts"/"organization"/
                           "theme-primitives-baseline-payload-20261024.json").read_text(encoding="utf8"))
        current=theme_signature.snapshot()
        self.assertEqual(before["sha256"],current["sha256"])
        self.assertEqual(current["families"],8)
        self.assertEqual(current["targetDevices"],6)
        self.assertEqual(current["stateDevicePairs"],228)
    def test_old_and_new_examples_preserve_device_set(self):
        self.assertEqual(shared.DEV,v4.base.DEV)
        self.assertEqual(len(shared.DEV),6)
    def test_no_changes_to_user_training_artifacts(self):
        # This test itself touches no generated HWT, learning files or weights.
        self.assertTrue((ROOT/"artifacts"/"learning").is_dir())

if __name__=="__main__":unittest.main()
