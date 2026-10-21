import copy
import json
import tempfile
import unittest
from pathlib import Path

from multires_app.engine.scene_engine import load_model,Runtime
from multires_app.export.touch_coordinates import pure_touch_definitions,expand_touch
from multires_app.export.interaction_preservation import audit_preservation
from multires_app.interaction.interaction_semantics import initialize,perform_action
from multires_app.interaction.interaction_bindings import analyze_bindings
from multires_app.pipeline.scene_pipeline import create_project,export_script


class CorpusRegressionTests(unittest.TestCase):
    def test_indirect_touch_predicate_preserves_swipe_and_synthesizes_down_capture(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);src=root/'src';(src/'lockscreen').mkdir(parents=True)
            original=b'''<Lockscreen screenWidth="1000">
            <Var name="mode" expression="0"/><Var name="tap" expression="lt(abs(#touch_x-#touch_begin_x),50)"/>
            <Var name="tap2" expression="#tap"/>
            <Rectangle x="100" y="100" w="100" h="100" color="#ffffff"/>
            <Button x="100" y="100" w="100" h="100"><Triggers><Trigger action="down" condition="0"/><Trigger action="up">
            <VariableCommand name="mode" expression="1-#mode" condition="#tap2"/>
            </Trigger></Triggers></Button></Lockscreen>'''
            (src/'lockscreen/manifest.xml').write_bytes(original)
            out=root/'project'
            create_project(src,{'resolutionPx':{'width':1000,'height':2000}},
                {'profiles':[{'id':'tablet','resolutionPx':{'width':1600,'height':1000},'orientations':['landscape']}]},out,max_states=5,max_depth=2)
            p=json.loads((out/'project.json').read_text(encoding='utf8'));d=p['devices'][0]
            for l in d['layouts']:
                for cid in l['transforms']:l['transforms'][cid]={'sx':.4,'sy':.4,'tx':10.,'ty':20.}
                l.pop('adaptationPlan',None)
            script=root/'native/lockscreen/manifest.xml';report=export_script(p,d['profile']['id'],script)
            native=load_model(root/'native');rt=initialize(native,1000,625)
            owner=next(u for u,n in native['nodes'].items() if n['tag']=='Button')
            self.assertTrue(perform_action(rt,{'owner':owner,'kind':'swipe','sample':[.1,.5,.9,.5]}, {},True)['valid'])
            self.assertEqual(rt.lookup('mode'),0)
            self.assertTrue(perform_action(rt,{'owner':owner,'kind':'tap','sample':[.5,.5,.5,.5]}, {},True)['valid'])
            self.assertEqual(rt.lookup('mode'),1)
            self.assertEqual(sum(t['action']=='down' for t in native['triggers']),2)
            self.assertTrue(report['interactionPreservation']['passed'])
            patch=json.loads(script.with_suffix('.patch.json').read_text(encoding='utf8'))
            with self.assertRaises(ValueError):audit_preservation(original,script.read_bytes().replace(b',50)',b',49)'),patch)

    def test_touch_expansion_retains_mutable_values_and_quoted_strings(self):
        defs=pure_touch_definitions([('Var',{'name':'pure','expression':'#touch_x-#touch_begin_x'}),
            ('Var',{'name':'saved','expression':'#touch_x'}),('VariableCommand',{'name':'saved','expression':'1'})])
        self.assertEqual(expand_touch("#pure+#saved+'#pure'",defs),"(#touch_x-#touch_begin_x)+#saved+'#pure'")

    def test_authored_empty_down_trigger_is_preserved_after_capture_insertion(self):
        original=b'<Lockscreen><Button><Triggers><Trigger action="down"/><Trigger action="up"/></Triggers></Button></Lockscreen>'
        capture=''.join('<VariableCommand name="mr_abc_'+f+'" expression="1"/>' for f in ('sx','sy','tx','ty')).encode()
        adapted=original.replace(b'<Trigger action="down"/>',b'<Trigger action="down">'+capture+b'</Trigger>')
        self.assertTrue(audit_preservation(original,adapted,{'changes':[]})['passed'])

    def test_crossing_visual_is_not_a_rigid_button_attachment(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);(root/'lockscreen').mkdir()
            (root/'lockscreen/manifest.xml').write_text('''<Lockscreen screenWidth="1000"><Var name="page" expression="0"/>
              <Rectangle name="crossing" x="100+700*#page" y="100" w="100" h="100"/>
              <Button x="100" y="100" w="100" h="100"/></Lockscreen>''')
            m=load_model(root)
            samples=[Runtime(m,1000,2000,{'page':i}).snapshot({}) for i in [0,1]]
            binding=analyze_bindings(m,samples,{'width':1000,'height':2000})['bindings'][0]
            self.assertFalse(binding['anchors'])
            self.assertTrue(binding['rejectedUnstableAnchors'])

    def test_unbound_hit_area_uses_screen_axes_instead_of_artwork_scale(self):
        from tests.test_anchor_layout import SOURCE,ctx,comp
        from multires_app.layout.anchor_layout import AnchorThemeLayout
        from multires_app.pipeline.scene_clusters import transformed_box
        c=comp('hit',(230,1058,620,1215));c['role']='interaction-layer'
        result=AnchorThemeLayout().layout_scene([c],SOURCE,{'resolutionPx':{'width':2560,'height':1600}},ctx(1080,675))
        q=transformed_box(c['bounds'],result['hit'])
        self.assertAlmostEqual(q['x'],230)
        self.assertAlmostEqual(q['y'],1058*675/2400)
        self.assertAlmostEqual(q['h'],1215*675/2400)

    def test_waiting_page_stays_outside_during_local_reflow(self):
        from tests.test_anchor_layout import SOURCE,ctx,comp
        from multires_app.layout.anchor_layout import AnchorThemeLayout
        from multires_app.pipeline.scene_clusters import transformed_box
        c=comp('page',(1213,200,358,529));context=ctx(1080,675)
        context['layoutRequest']={'kind':'local-reflow','baseState':'s0'}
        context['baseTransforms']={'page':{'sx':.5,'sy':.5,'tx':0,'ty':0}}
        context['waitingRegions']={'page':{'rect':dict(c['bounds']),'edges':[{'axis':'x','edge':'after','gap':133,'sourceExtent':1080}]}}
        result=AnchorThemeLayout().layout_scene([c],SOURCE,{'resolutionPx':{'width':2560,'height':1600}},context)
        self.assertGreaterEqual(transformed_box(c['bounds'],result['page'])['x'],1213-1e-6)

    def test_argb_color_uses_recorded_channels_and_keeps_missing_inputs_explicit(self):
        from multires_app.media.color_support import ColorEvaluator,color_value
        self.assertEqual(ColorEvaluator(lambda n:128).evaluate('argb(255,int(#r),0,30)'),0xff80001e)
        class Colors:
            issues={}
            def lookup(self,name):return 128
        self.assertEqual(color_value('argb(255,#r,0,30)',Colors()),'#ff80001e')
        self.assertEqual(color_value('unsupported(1)',Colors()),'unsupported(1)')

    def test_interleaved_leaf_separation_detects_collision_hidden_by_envelopes(self):
        from multires_app.layout.spatial_flow import infer_leaf_flows,flow_errors
        nodes={u:{'tag':'Rectangle','attributes':{},'parent':None,'children':[]} for u in ['a0','a1','b']}
        p={'model':{'nodes':nodes,'variables':{}},'assets':{},'nodeOrder':['a0','a1','b'],
           'nodeComponents':{'a0':'A','a1':'A','b':'B'},'components':[{'id':c,'role':'content'} for c in ['A','B']]}
        l={'rows':[[0,0,0,100,100,1,-1,'',0],[1,0,300,100,100,1,-1,'',0],[2,0,150,100,100,1,-1,'',0]]}
        flows=infer_leaf_flows(p,l,l,{'width':1000,'height':2000},{'x':0,'y':0,'w':1000,'h':625},.5)
        self.assertEqual(len(flows),2)
        good={c:{'sx':.5,'sy':.5,'tx':0,'ty':0} for c in ['A','B']}
        self.assertFalse(flow_errors(flows,good))
        bad=copy.deepcopy(good);bad['B']['ty']=-60
        self.assertTrue(flow_errors(flows,bad))

    def test_source_disjoint_local_hit_targets_remain_reachable(self):
        from multires_app.layout.spatial_flow import infer_leaf_flows,flow_errors
        nodes={u:{'tag':'Button','attributes':{},'parent':None,'children':[]} for u in ['a','b','global']}
        p={'model':{'nodes':nodes,'variables':{}},'assets':{},'nodeOrder':['a','b','global'],
           'nodeComponents':{'a':'A','b':'B','global':'G'},
           'components':[{'id':c,'role':r} for c,r in [('A','header'),('B','content'),('G','gesture-layer')]]}
        l={'rows':[[0,340,160,558,465,1,-1,'',0],[1,60,711,442,442,1,-1,'',0],
                   [2,0,0,1080,2160,1,-1,'',0]]}
        flows=infer_leaf_flows(p,l,l,{'width':1080,'height':2160},{'x':0,'y':0,'w':1080,'h':675},.5)
        self.assertEqual(len(flows),1)
        self.assertEqual(flows[0]['axis'],'y')
        self.assertNotIn('G',[flows[0]['first'],flows[0]['last']])
        good={c:{'sx':.5,'sy':.5,'tx':0,'ty':0} for c in ['A','B']}
        self.assertFalse(flow_errors(flows,good))
        bad=copy.deepcopy(good);bad['B']['ty']=-100
        self.assertTrue(flow_errors(flows,bad))

    def test_visible_late_ui_overrides_stale_phase_mask_and_keeps_all_glyphs(self):
        from multires_app.media.media_layout import layout_rows,inactive_indices
        rows=[[0,10,20,30,40,1,-1,'0',20],[0,45,20,30,40,1,-1,'1',20]]
        source={'state':'s','rows':copy.deepcopy(rows)}
        p={'model':{'variables':{},'nodes':{'u':{'tag':'Time','attributes':{'alpha':'#time'},'children':[]}}},
           'nodeOrder':['u'],'sourceDevice':{'viewport':{'width':1000,'height':2000},'layouts':[source]}}
        layout={'state':'s','rows':rows,'mediaLayout':{'nodeIndices':[0],'rows':[],'inactiveSceneNodeIndices':[0]}}
        self.assertFalse(inactive_indices(p,layout))
        self.assertEqual(layout_rows(p,layout),rows)

    def test_pointer_readback_uses_actual_touch_and_still_detects_drift(self):
        from multires_app.preview.reference_workflow import _audit_native_state
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);src=root/'src';(src/'lockscreen').mkdir(parents=True)
            (src/'lockscreen/manifest.xml').write_text('''<Lockscreen screenWidth="1000"><Var name="on" expression="0"/>
            <Rectangle x="100" y="100+10*#on" w="100" h="100"/>
            <Button x="100" y="100" w="100" h="100"><Trigger action="up"><VariableCommand name="on" expression="1-#on"/></Trigger></Button>
            <Group x="#touch_x" y="#touch_y"><Rectangle name="cursor" x="5" y="6" w="12" h="12"/></Group></Lockscreen>''')
            out=root/'out';create_project(src,{'resolutionPx':{'width':1000,'height':2000}},
                {'profiles':[{'id':'tablet','resolutionPx':{'width':1600,'height':1000},'orientations':['landscape']}]},out,max_states=3,max_depth=2)
            p=json.loads((out/'project.json').read_text(encoding='utf8'));d=p['devices'][0]
            self.assertTrue(any(c.get('pointerEffect') for c in p['components']))
            script=root/'native/lockscreen/manifest.xml';export_script(p,d['profile']['id'],script)
            native=load_model(root/'native');native['assetNames']=[]
            state=next(s['id'] for s in p['graph']['states'] if s['trace'])
            self.assertTrue(_audit_native_state(p,native,{},d['profile']['id'],state)['geometryPassed'])
            cursor=next(n for n in native['nodes'].values() if n['attributes'].get('name')=='cursor')
            cursor['attributes']['y']='60'
            self.assertFalse(_audit_native_state(p,native,{},d['profile']['id'],state)['geometryPassed'])


if __name__=='__main__':unittest.main()
