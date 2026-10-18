import unittest
from multires_app.layout.offscreen_geometry import retain_offscreen
from multires_app.layout.anchor_layout import AnchorThemeLayout
from tests.test_anchor_layout import comp,ctx,SOURCE


class OffscreenGeometryTests(unittest.TestCase):
    def test_drawer_angular_constant_is_evaluated(self):
        import math
        from multires_app.engine.scene_engine import Evaluator
        self.assertAlmostEqual(Evaluator(lambda _:0).evaluate('4*atan(1)'),math.pi)
    def test_closed_drawer_does_not_peek_after_height_adaptation(self):
        b={'x':0,'y':-699,'w':1080,'h':688}
        t=retain_offscreen(b,b,{'sx':.5,'sy':.5,'tx':270,'ty':66.5625},
                           {'width':1080,'height':2160},{'x':0,'y':0,'w':1080,'h':675})
        self.assertAlmostEqual(t['ty'],0)
        self.assertAlmostEqual((b['y']+b['h'])*t['sy']+t['ty'],-5.5)
        self.assertEqual(t['tx'],270)

    def test_decoration_solver_keeps_closed_panel_outside(self):
        panel=comp('drawer',(0,-699,1080,688),role='decoration')
        t=AnchorThemeLayout().layout_scene([panel],SOURCE,{'resolutionPx':{'width':1080,'height':675}},ctx(1080,675))['drawer']
        self.assertLessEqual((-699+688)*t['sy']+t['ty'],0)

    def test_open_target_and_authored_exit_remain_unmodified(self):
        src={'x':0,'y':-699,'w':1080,'h':688};opened={**src,'y':0}
        t={'sx':.5,'sy':.5,'tx':270,'ty':0}
        self.assertEqual(retain_offscreen(src,opened,t,{'width':1080,'height':2160},{'x':0,'y':0,'w':1080,'h':675}),t)
        t={**t,'tx':0,'ty':-100}
        self.assertEqual(retain_offscreen(src,src,t,{'width':1080,'height':2160},{'x':0,'y':0,'w':1080,'h':675}),t)

    def test_real_open_close_transactions_and_native_export(self):
        import json,tempfile
        from pathlib import Path
        from PIL import Image
        from multires_app.pipeline.scene_pipeline import create_project,export_script
        from multires_app.pipeline.scene_clusters import transformed_box
        from multires_app.layout.anchor_validation import validate_export
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);lock=root/'unlock/lockscreen';lock.mkdir(parents=True)
            Image.new('RGBA',(1080,200),'white').save(lock/'drawer.png')
            (lock/'manifest.xml').write_text('''<Lockscreen screenWidth="1080">
<Var name="opened" expression="0"/><Var name="pi" expression="4*atan(1)"/>
<Image src="drawer.png" y="-220+220*sin(#opened*#pi/2)"/>
<Button x="400" y="400" w="200" h="200"><Trigger action="up">
<VariableCommand name="opened" expression="1-#opened"/></Trigger></Button></Lockscreen>''',encoding='utf8')
            out=root/'result'
            create_project(root,{'resolutionPx':{'width':1080,'height':2160}},
                           {'profiles':[{'id':'tablet','resolutionPx':{'width':1080,'height':675},'orientations':['landscape']}]},out,max_states=4,max_depth=3)
            p=json.loads((out/'project.json').read_text(encoding='utf8'));d=p['devices'][0];validate_export(p,d)
            idx=next(i for i,u in enumerate(p['nodeOrder']) if p['model']['nodes'][u]['tag']=='Image')
            seen=set()
            for l in d['layouts']:
                opened=int(l['controlValues']['opened']);seen.add(opened)
                row=next(r for r in l['rows'] if r[0]==idx);cid=p['nodeComponents'][p['nodeOrder'][idx]]
                b=transformed_box(dict(zip(('x','y','w','h'),row[1:5])),l['transforms'][cid])
                if opened:self.assertGreater(b['y']+b['h'],0)
                else:self.assertLessEqual(b['y']+b['h'],.01)
            self.assertEqual(seen,{0,1})
            export_script(p,d['profile']['id'],out/'checked.xml')

    def test_binding_in_later_state_is_not_discarded(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from PIL import Image
        from multires_app.engine.scene_engine import load_model,Runtime
        from multires_app.pipeline.scene_pipeline import infer_components
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);lock=root/'unlock/lockscreen';lock.mkdir(parents=True)
            Image.new('RGBA',(100,100),'white').save(lock/'close.png')
            (lock/'manifest.xml').write_text('''<Lockscreen screenWidth="1080"><Var name="opened" expression="0"/>
<Group visibility="eq(#opened,1)"><Image src="close.png" x="200" y="200"/></Group>
<Group visibility="eq(#opened,1)"><Button x="200" y="200" w="100" h="100"><Trigger action="up">
<VariableCommand name="opened" expression="0"/></Trigger></Button></Group></Lockscreen>''',encoding='utf8')
            model=load_model(root);assets={'unlock/lockscreen/close.png':{'width':100,'height':100,'kind':'image'}}
            def replay(model,w,h,state,assets,**kwargs):
                return Runtime(model,w,h,{'opened':int(state['id']==13)},time_ms=2400)
            with patch('multires_app.interaction.interaction_semantics.replay_human_state',side_effect=replay):
                infer_components(model,{'resolutionPx':{'width':1080,'height':2160}},assets,{'opened':0},graph={'states':[{'id':i} for i in range(14)]})
            self.assertEqual(model['layoutBindings']['summary']['samples'],14)
            self.assertEqual(len(model['layoutBindings']['bindings'][0]['anchors']),1)
