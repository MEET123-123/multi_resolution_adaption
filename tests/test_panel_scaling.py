import copy
import json
import tempfile
import unittest
from pathlib import Path

from multires_app.pipeline.scene_pipeline import create_project,export_script
from multires_app.engine.scene_engine import Runtime,load_model
from multires_app.layout.corner_geometry import component_frame,corner_errors
from multires_app.layout.anchor_validation import validate_export
from multires_app.layout.anchor_solver import RequiredConstraintError
from multires_app.layout.spatial_alignment import infer_alignments


class PanelScaleTests(unittest.TestCase):
    def test_nested_animated_variants_and_panel_share_clock_scale(self):
        from PIL import Image
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root=Path(temp);source=root/'source';lock=source/'unlock/lockscreen';lock.mkdir(parents=True)
            for name,size in [('icon',(148,148)),('small',(140,140)),('panel',(1005,1100))]:
                Image.new('RGBA',size,'white').save(lock/(name+'.png'))
            for i in range(10):Image.new('RGBA',(100,200),'white').save(lock/f'digit_{i}.png')
            Image.new('RGBA',(50,200),'white').save(lock/'digit_dot.png')
            variants=[]
            for style,asset in [(0,'icon'),(1,'small')]:
                variants.append(f'''<Group visibility="eq(#style,{style})">
                  <Image name="left{style}" src="{asset}.png" x="160" align="center" y="#screen_height-250"/>
                  <Button x="86" y="#screen_height-250" w="148" h="148"><Trigger action="double"><ExternCommand command="torch"/></Trigger></Button>
                  <Image name="right{style}" src="{asset}.png" x="920" align="center" y="#screen_height-250"/>
                  <Button x="846" y="#screen_height-250" w="148" h="148"><Trigger action="double"><ExternCommand command="camera"/></Trigger></Button></Group>''')
            xml='''<Lockscreen screenWidth="1080"><Var name="style" expression="0"/><Var name="control" expression="0"/>
              <Group name="clock"><Time x="540" y="240" src="digit.png" align="center"/></Group>
              <Group name="shortcuts">'''+''.join(variants)+'''<PositionAnimation><Position x="0" y="300" time="0"/>
                <Position x="0" y="0" time="700"/><Position x="0" y="0" time="100000000"/></PositionAnimation></Group>
              <Group name="settings" visibility="eq(#control,1)"><Group x="37" y="#screen_height-1400">
                <Image name="panel" src="panel.png"/>
                <Button x="100" y="100" w="140" h="140"><Trigger action="up"><VariableCommand name="style" expression="0"/></Trigger></Button>
                <Button x="350" y="100" w="140" h="140"><Trigger action="up"><VariableCommand name="style" expression="1"/></Trigger></Button>
                <Button x="600" y="100" w="140" h="140"><Trigger action="up"><VariableCommand name="control" expression="0"/></Trigger></Button>
              </Group></Group>
              <Button x="0" y="0" w="#screen_width" h="#screen_height"><Trigger action="up">
                <VariableCommand name="control" expression="1" condition="lt(#touch_begin_y-#touch_y,-100)"/>
              </Trigger></Button></Lockscreen>'''
            (lock/'manifest.xml').write_text(xml,encoding='utf-8')
            out=root/'out';create_project(source,{'resolutionPx':{'width':1080,'height':2400}},
                {'profiles':[{'id':'wide','resolutionPx':{'width':1600,'height':1000},'orientations':['landscape']}]},out,max_states=8,max_depth=5)
            p=json.loads((out/'project.json').read_text(encoding='utf-8'));d=p['devices'][0]
            corners=[c for c in p['components'] if c.get('cornerAttachment')]
            self.assertEqual(len(corners),2);self.assertTrue(all(c.get('cornerReferenceNodes') for c in corners))
            panel=next(c for c in p['components'] if c['role']=='dialog');clock=next(c for c in p['components'] if c.get('semanticRole')=='datetime')
            self.assertTrue(p['resourceScaleFamilies']);scale=d['sharedResourceScales'][clock['id']]
            active=[l for l in d['layouts'] if l['controlValues']['control']==1]
            self.assertTrue(active);self.assertTrue(any(l['controlValues']['style']==1 for l in active))
            for layout in d['layouts']:
                self.assertFalse(corner_errors(p,d,layout))
                for cid in [panel['id'],clock['id'],*[c['id'] for c in corners]]:
                    if cid in layout['transforms']:
                        self.assertAlmostEqual(layout['transforms'][cid]['sx'],scale)
                        self.assertAlmostEqual(layout['transforms'][cid]['sy'],scale)
            self.assertTrue(validate_export(p,d))
            broken=copy.deepcopy(d);broken['layouts'][0]['transforms'][clock['id']]['sx']*=.9
            with self.assertRaises(RequiredConstraintError):validate_export(p,broken)
            target=root/'adapted/unlock/lockscreen/manifest.xml';target.parent.mkdir(parents=True);export_script(p,d['profile']['id'],target)
            model=load_model(root/'adapted');model['assetNames']=list(p['assets'])
            for layout in d['layouts']:
                rt=Runtime(model,1080,675,layout['overrides'],time_ms=layout['timeMs']);elements=rt.snapshot(p['assets'])
                raw={e['id']:e for e in elements}
                for corner in corners:
                    ids=[u for u in corner['cornerReferenceNodes'] if raw[u]['visible']]
                    for u in ids:
                        b=raw[u]['rect'];edge=b['x'] if corner['cornerAttachment']['corner']=='bottom-left' else 1080-b['x']-b['w']
                        self.assertAlmostEqual(edge,86*scale,places=3)
                        self.assertAlmostEqual(675-b['y']-b['h'],102*scale,places=3)
            entry=Runtime(model,1080,675,d['layouts'][0]['overrides'],time_ms=0).snapshot(p['assets'])
            self.assertTrue(any(e['rect']['y']+e['rect']['h']>675 for e in entry if e['tag']=='Image' and '/Group[2]/' in e['id']))
            settled={e['id']:e for e in Runtime(model,1080,675,d['layouts'][0]['overrides'],time_ms=2400).snapshot(p['assets'])}
            for e in entry:
                if e['tag']=='Button' and '/Group[2]/' in e['id'] and e['visible']:
                    self.assertAlmostEqual(e['rect']['y']-settled[e['id']]['rect']['y'],300*scale,places=3)
            from multires_app.pipeline.scene_clusters import schedule_device
            class CustomLayout:
                name='custom-layout'
                def layout_scene(self,components,source,target,context):
                    return {c['id']:{'sx':1.,'sy':1.,'tx':0.,'ty':0.} for c in components}
            custom=copy.deepcopy(d);schedule_device(p,custom,CustomLayout())
            self.assertNotIn('sharedResourceScales',custom)
            self.assertNotIn('resourceScaleFamilies',custom)

    def test_corner_and_panel_do_not_form_an_incidental_horizontal_alignment(self):
        def item(uid,b,**extra):return {'id':uid,'bbox':b,'current_visual_bbox':b,**extra}
        panel={'x':90,'y':1000,'w':900,'h':980};left={'x':90,'y':2100,'w':140,'h':140};right={'x':850,'y':2100,'w':140,'h':140}
        rules=infer_alignments([item('panel',panel),item('left',left,cornerShortcut=True),item('right',right,cornerShortcut=True)],{'width':1080,'height':2400})
        self.assertFalse(any(r['axis']=='x' and 'panel' in {r['first'],r['last']} for r in rules))
        self.assertTrue(any(r['axis']=='y' and {r['first'],r['last']}=={'left','right'} for r in rules))


if __name__=='__main__':unittest.main()
