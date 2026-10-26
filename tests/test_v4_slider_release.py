"""V4 Slider EndPoint regression: successful drag, short drag, cyclic drag."""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"src"),str(ROOT/"studio_reverse/web_theme_pipeline")]
from multires_app.preview.live_preview import LiveSession
from multires_app.preview.live_geometry import slider_geometry
import interaction_families_v4 as g

DATA=ROOT/"artifacts/learning/interaction-families-v4-hitbox-fixed-20261024"
THEME="web-164810189"

def session_and_coords(root,device):
    p=DATA/"themes"/(THEME+".hwt") if device=="source" else DATA/"adapted"/(THEME+"--"+device+".hwt")
    s=LiveSession(p,root)
    slider=next(n for n in s.model["nodes"].values() if n["attributes"].get("name")=="v4-swipe")
    geometry=slider_geometry(s.model,s.runtime,slider["id"])
    anchor=geometry["anchor"]; direction=geometry["direction"]; length=geometry["length"]
    begin=(anchor[0]+geometry["hit"]["w"]*.5,anchor[1]+geometry["hit"]["h"]*.5)
    return s,begin,direction,length

def drag(s,begin,direction,length,amount):
    x,y=begin
    x1=x+direction[0]*length*amount
    y1=y+direction[1]*length*amount
    first=s.drag_phase({"phase":"start","x":x,"y":y})
    second=s.drag_phase({"phase":"move","x":x1,"y":y1})
    final=s.drag_phase({"phase":"end","x":x1,"y":y1})
    return first,second,final

class SliderReleaseTest(unittest.TestCase):
    def test_reach_advances_slide_on_all_devices(self):
        for device in ("source",)+tuple(d[0] for d in g.base.DEV):
            with self.subTest(device=device),tempfile.TemporaryDirectory() as tmp:
                s,begin,direction,length=session_and_coords(Path(tmp),device)
                _,movement,result=drag(s,begin,direction,length,1.1)
                self.assertTrue(result["reached"],result)
                self.assertEqual(int(s.runtime.lookup("slide")),1)
                self.assertGreater(movement["progress"],.95)

    def test_short_drag_does_not_advance(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,begin,direction,length=session_and_coords(Path(tmp),"source")
            _,_,result=drag(s,begin,direction,length,.12)
            self.assertFalse(result["reached"])
            self.assertEqual(int(s.runtime.lookup("slide")),0)

    def test_full_slider_cycles_through_three_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            s,begin,direction,length=session_and_coords(Path(tmp),"source")
            for want in (1,2,0,1):
                _,_,result=drag(s,begin,direction,length,1.1)
                self.assertTrue(result["reached"])
                self.assertEqual(int(s.runtime.lookup("slide")),want)

if __name__=="__main__":unittest.main()
