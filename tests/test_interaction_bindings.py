import tempfile
import unittest
from pathlib import Path

from multires_app.engine.scene_engine import load_model,Runtime
from multires_app.pipeline.scene_pipeline import infer_components
from multires_app.interaction.interaction_bindings import analyze_bindings
from multires_app.layout.adaptive_layout import group_blocks

ROOT=Path(__file__).resolve().parents[1]


class BindingTests(unittest.TestCase):
    def test_physical_skin_separate_from_remote_effect_and_explicit_override(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            path=Path(tmp)/'lockscreen';path.mkdir()
            (path/'manifest.xml').write_text('''<Lockscreen screenWidth="1000"><Var name="panel" expression="0"/>
              <Image name="skin" x="50" y="50" w="200" h="100" src="a.png"/>
              <Button name="open" x="50" y="50" w="200" h="100"><Trigger action="up"><VariableCommand name="panel" expression="1"/></Trigger></Button>
              <Image name="remote" x="500" y="900" w="300" h="400" src="b.png" visibility="#panel"/>
              </Lockscreen>''')
            m=load_model(Path(tmp));ids={n['attributes'].get('name'):uid for uid,n in m['nodes'].items()}
            samples=[Runtime(m,1000,2000,{'panel':v}).snapshot({}) for v in [0,1]]
            report=analyze_bindings(m,samples,{'width':1000,'height':2000})['bindings'][0]
            self.assertEqual(report['anchors'],[ids['skin']]);self.assertIn(ids['remote'],report['effectTargets'])
            comps=infer_components(m,{'resolutionPx':{'width':1000,'height':2000}},{},{})
            owner={uid:c['id'] for c in comps for uid in c['members']}
            self.assertEqual(owner[ids['open']],owner[ids['skin']]);self.assertNotEqual(owner[ids['open']],owner[ids['remote']])
            explicit=analyze_bindings(m,samples,{'width':1000,'height':2000},[{'button':'open','visuals':['skin']}])
            self.assertEqual(explicit['bindings'][0]['status'],'explicit')
            with self.assertRaises(ValueError):analyze_bindings(m,samples,{'width':1000,'height':2000},[{'button':'remote','visuals':['skin']}])

    def test_card_shadows_do_not_merge_into_one_transitive_block(self):
        cards=[{'id':str(i),'bounds':{'x':i*60,'y':0,'w':100,'h':100}} for i in range(4)]
        self.assertEqual(len(group_blocks(cards)),4)
        cards.append({'id':'clock','bounds':{'x':5,'y':5,'w':50,'h':50}})
        self.assertEqual(len(group_blocks(cards)),4)

    def test_large_offscreen_hot_zone_is_not_a_global_gesture_layer(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            path=Path(tmp)/'lockscreen';path.mkdir()
            (path/'manifest.xml').write_text('<Lockscreen screenWidth="1000"><Button x="0" y="1900" w="1000" h="2000"/></Lockscreen>')
            m=load_model(Path(tmp));comps=infer_components(m,{'resolutionPx':{'width':1000,'height':2000}},{},{})
            self.assertEqual(comps[0]['role'],'interaction-layer')
            self.assertNotEqual(m['layoutBindings']['bindings'][0]['status'],'global-gesture')
