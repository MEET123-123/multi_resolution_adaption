import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from multires_app.engine.scene_engine import load_model
from multires_app.interaction.tablet_interactions import InteractionSession, install_state, bind_gestures, defaults_actions
from multires_app.tools.validate_tablet_theme import audit_package

ROOT=Path(__file__).resolve().parents[1]


def make_model(directory,width=1080,height=2160):
    root=ET.Element('Lockscreen',screenWidth=str(width))
    install_state(root,'bear')
    for index,(name,x,y,w,h,actions) in enumerate([
        ('background',0,0,width,height,{}),
        ('character',100,400,600,700,{'tap':[('react_at','#time')],'hold':[('focus','1-#focus')],'left':[('look','(#look+1)%5')],'right':[('look','(#look+4)%5')]}),
        ('focus-toggle',800,100,200,120,{'tap':[('focus','1-#focus')],'hold':defaults_actions('bear')})]):
        button=ET.SubElement(root,'Button',name=name,x=str(x),y=str(y),w=str(w),h=str(h))
        bind_gestures(button,index,**actions)
    path=directory/'unlock/lockscreen/manifest.xml';path.parent.mkdir(parents=True,exist_ok=True)
    ET.ElementTree(root).write(path,encoding='utf-8')
    return load_model(directory)


class TabletInteractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.model=make_model(Path(self.tmp.name))
    def tearDown(self):self.tmp.cleanup()
    def fresh(self,**kwargs):return InteractionSession(self.model,1080,2160,**kwargs)

    def test_tap_hold_and_swipe_are_exclusive_at_boundaries(self):
        tap=self.fresh();tap.gesture('character',duration=649)
        self.assertEqual(tap.value('react_at'),649);self.assertEqual(tap.value('look'),1)
        self.assertEqual(tap.value('focus'),0);self.assertEqual(tap.unlocks(),0)
        hold=self.fresh();hold.gesture('character',duration=650)
        self.assertEqual(hold.value('focus'),1);self.assertEqual(hold.value('react_at'),-10000)
        left=self.fresh();left.gesture('character',dx=-100,duration=900)
        self.assertEqual(left.value('look'),2);self.assertEqual(left.value('focus'),0)
        self.assertEqual(left.value('react_at'),-10000);self.assertEqual(left.unlocks(),0)

    def test_drift_return_and_ambiguous_diagonal_do_nothing(self):
        s=self.fresh();before=s.saved()
        s.gesture('character',dx=40,duration=900,return_to_start=True)
        self.assertEqual(s.saved(),before);self.assertEqual(s.value('react_at'),-10000)
        s.gesture('character',dx=162,dy=-119)
        self.assertEqual(s.saved(),before);self.assertEqual(s.unlocks(),0)

    def test_upward_unlock_is_single_and_cannot_switch_modes(self):
        s=self.fresh();before=s.saved();s.gesture('character',dy=-150,duration=1200)
        self.assertEqual(s.saved(),before);self.assertEqual(s.unlocks(),1)
        owner=next(n['id'] for n in self.model['nodes'].values() if n['attributes'].get('name')=='character')
        up=next(t for t in self.model['triggers'] if t['owner']==owner and t['action']=='up')
        s.runtime.execute(up)
        self.assertEqual(s.unlocks(),1)

    def test_resume_discards_capture_but_keeps_preferences(self):
        s=self.fresh();s.gesture('character',dx=-100);s.gesture('focus-toggle')
        saved=s.saved();s.down(300,700)
        restored=self.fresh(saved=saved)
        self.assertEqual(restored.saved(),saved);self.assertEqual(restored.value('press_active'),0)
        restored.gesture('focus-toggle',duration=700)
        self.assertEqual(restored.value('look'),1);self.assertEqual(restored.value('focus'),0)
        self.assertEqual(restored.value('motion'),1);self.assertEqual(restored.value('palette_mode'),2)

    def test_power_and_day_night_are_derived_without_overwriting_settings(self):
        s=self.fresh(system={'hour24':19,'battery_level':15})
        self.assertEqual(s.value('palette'),1);self.assertEqual(s.value('effective_motion'),0)
        s.runtime.system.update(hour24=7,battery_level=16);s.runtime.cache.clear()
        self.assertEqual(s.value('palette'),0);self.assertEqual(s.value('effective_motion'),1)
        s.runtime.assign('motion',0);s.runtime.system.update(battery_level=10,battery_state=1);s.runtime.cache.clear()
        self.assertEqual(s.value('effective_motion'),0)

    def test_owner_mismatch_and_clock_reset_cannot_fire_actions(self):
        s=self.fresh();s.down(300,700)
        other=next(n['id'] for n in self.model['nodes'].values() if n['attributes'].get('name')=='focus-toggle')
        up=next(t for t in self.model['triggers'] if t['owner']==other and t['action']=='up')
        s.runtime.execute(up)
        self.assertEqual(s.value('focus'),0);self.assertEqual(s.unlocks(),0)
        s=self.fresh();s.now=1000;s.down(300,700);s.up(300,700,elapsed=-500)
        self.assertEqual(s.value('react_at'),-10000);self.assertEqual(s.unlocks(),0)

    def test_history_restores_pointer_capture_and_preserves_branches(self):
        s=self.fresh(record_history=True);s.down(300,700)
        down=s.journal.cursor;owner=s.captured
        s.up(300,700,elapsed=100)
        first_up=s.journal.cursor
        self.assertEqual(s.value('react_at'),100);self.assertIsNone(s.captured)
        s.checkout(down)
        self.assertEqual(s.captured,owner);self.assertEqual(s.value('press_active'),1);self.assertEqual(s.now,0)
        s.up(300,700,elapsed=700)
        hold_up=s.journal.cursor
        self.assertEqual(s.value('focus'),1);self.assertEqual(s.value('react_at'),-10000)
        s.checkout(down)
        self.assertEqual({n['id'] for n in s.journal.children()},{first_up,hold_up})
        s.checkout(first_up)
        self.assertEqual(s.value('focus'),0);self.assertEqual(s.value('react_at'),100)

    def test_history_recovery_does_not_repeat_unlock(self):
        s=self.fresh(record_history=True);s.gesture('character',dy=-150)
        end=s.journal.cursor
        self.assertEqual(s.unlocks(),1)
        s.checkout('event-000000');self.assertEqual(s.unlocks(),0)
        s.checkout(end);self.assertEqual(s.unlocks(),1)
        self.assertEqual(s.journal.records[-1]['effects'][0]['delivery'],'recorded-only')

    def test_auditor_reports_truncated_archive_as_failure(self):
        path=Path(self.tmp.name)/'broken.hwt';path.write_bytes(b'PK\x03\x04truncated')
        result=audit_package(path)
        self.assertFalse(result['passed']);self.assertIn('BadZipFile',result['errors'][0])

    def test_auditor_detects_malformed_xml_and_unsafe_paths(self):
        path=Path(self.tmp.name)/'bad-xml.hwt'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('description.xml','<HwTheme>')
            z.writestr('C:/outside.bin',b'never extracted')
        result=audit_package(path)
        self.assertFalse(result['passed'])
        self.assertTrue(any('description.xml' in e for e in result['errors']))
        self.assertTrue(any('unsafe ZIP path' in e for e in result['errors']))


if __name__=='__main__':unittest.main()
