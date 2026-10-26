"""Regression tests for V4 replay hitboxes and stratified smoke-test selection."""
from __future__ import annotations
import sys, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"src"),str(ROOT/"studio_reverse"/"web_theme_pipeline")]
from multires_app.preview.live_preview import LiveSession
import interaction_families_v4 as v4
import runtime_quality_gate as qa

DATA=ROOT/"artifacts/learning/interaction-families-v4-pilot"

class RuntimeGateTests(unittest.TestCase):
    def test_stratified_smoke_includes_all_available_splits(self):
        rows=list(qa.records(DATA))
        picked=qa.choose_rows(rows,12)
        self.assertEqual(len(picked),12)
        self.assertEqual(set(r["split"] for r in picked),{"train","validation","test"})
        self.assertEqual(len(set(r["family"] for r in picked)),len(v4.FAMILIES))
        self.assertEqual([(r["theme"],r["device"],r["state"]) for r in picked],
                         [(r["theme"],r["device"],r["state"]) for r in qa.choose_rows(rows,12)])

    def test_invalid_limit_rejected(self):
        with self.assertRaises(ValueError): qa.choose_rows(list(qa.records(DATA)),0)

    def test_old_square_target_detects_unadapted_hitbox(self):
        path=DATA/"adapted"/"web-164480271--square.hwt"
        with tempfile.TemporaryDirectory() as tmp:
            session=LiveSession(path,Path(tmp))
            _,issues=qa.replay(session,"carousel",["forward"],v4.base.DEV[2],{"frame":1})
        self.assertTrue(any(x.get("reason")=="target hitbox not adapted" for x in issues),issues)
        self.assertEqual(session.runtime.lookup("frame"),1)

    def test_newly_adapted_square_control_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp=Path(tmp)
            source=v4.construct("carousel")
            target=v4.adapt(source,v4.base.DEV[2],"carousel")
            package=temp/"adapted.hwt"
            v4.package(package,target,{},{"license":"Public domain","page":"test"})
            session=LiveSession(package,temp/"runtime")
            _,issues=qa.replay(session,"carousel",["forward"],v4.base.DEV[2],{"frame":1})
            self.assertFalse(issues,issues)

if __name__=="__main__":
    unittest.main()
