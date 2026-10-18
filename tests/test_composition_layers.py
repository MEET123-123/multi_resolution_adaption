import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image,ImageDraw

from multires_app.pipeline.scene_pipeline import create_project
from multires_app.pipeline.scene_clusters import describe_layout
from multires_app.engine.scene_engine import Runtime,load_model

ROOT=Path(__file__).resolve().parents[1]


class CompositionTests(unittest.TestCase):
    def test_cutout_follows_art_without_inflating_grid_and_native_export_matches(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            root=Path(tmp);source=root/'source';lock=source/'lockscreen';lock.mkdir(parents=True)
            (lock/'manifest.xml').write_text('''<Lockscreen screenWidth="1000">
              <Image name="hero" src="hero.png" x="450" y="100" w="400" h="400"/>
              <Image name="mask" src="mask.png" x="0" y="0" w="1000" h="2000"/>
              <Group name="card" x="100" y="1200"><Text text="card" w="400" h="200"/></Group>
              </Lockscreen>''')
            Image.new('RGB',(400,400),'orange').save(lock/'hero.png')
            mask=Image.new('RGBA',(1000,2000),(240,240,240,255));ImageDraw.Draw(mask).rectangle((400,0,999,600),fill=(0,0,0,0));mask.save(lock/'mask.png')
            out=root/'output'
            create_project(source,{'resolutionPx':{'width':1000,'height':2000}},
                           {'profiles':[{'id':'tablet','resolutionPx':{'width':1600,'height':1000},'orientations':['landscape']}]},out,max_states=1,max_depth=0)
            p=json.loads((out/'project.json').read_text(encoding='utf-8'))
            ids={n['attributes'].get('name'):uid for uid,n in p['model']['nodes'].items()}
            self.assertEqual(p['nodeComponents'][ids['hero']],p['nodeComponents'][ids['mask']])
            layout=p['devices'][0]['layouts'][0];cid=p['nodeComponents'][ids['hero']]
            desc=describe_layout(p,layout)[cid];self.assertEqual(desc['bounds']['h'],400)
            native=load_model(source);native_xml=out/'scripts/tablet-landscape.xml'
            (lock/'manifest.xml').write_bytes(native_xml.read_bytes());adapted=load_model(source)
            model=Runtime(adapted,1000,625).snapshot(p['assets']);actual={e['id']:e for e in model}
            t=layout['transforms'][cid]
            for name in ['hero','mask']:
                uid=ids[name];row=next(r for r in layout['rows'] if p['nodeOrder'][r[0]]==uid)
                self.assertAlmostEqual(actual[uid]['rect']['x'],row[1]*t['sx']+t['tx'],places=3)
                self.assertAlmostEqual(actual[uid]['rect']['w'],row[3]*t['sx'],places=3)
            self.assertTrue((out/'interaction-ir.json').exists())
