import unittest
from multires_app.layout.anchor_ir import build_ir
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.pipeline.scene_clusters import transformed_box
from multires_app.layout.spatial_flow import infer_flows,flow_errors
from tests.test_anchor_layout import comp,ctx,SOURCE


class SpatialFlowTests(unittest.TestCase):
    def test_transparent_padding_uses_visible_center(self):
        clock=comp('clock',(53,466,1086,722))
        visual={'x':76,'y':480,'w':928,'h':664}
        c=ctx(1600,1000,sourceStableVisualBounds={'clock':visual},stableVisualBounds={'clock':visual})
        result=AnchorThemeLayout().layout_scene([clock],SOURCE,{'resolutionPx':{'width':1600,'height':1000}},c)
        b=transformed_box(visual,result['clock'])
        self.assertAlmostEqual(b['x']+b['w']/2,800)

    def test_center_has_priority_over_edge_on_same_axis(self):
        a=comp('a',(0,0,1080,100),screenEdgeIntent={'x':{'edge':'left','margin':0}})
        ir=build_ir([a],{'width':1080,'height':2400},ctx(),{})
        g=ir['groups'][0]
        self.assertIn('x',g['centerAxes']);self.assertNotIn('x',g['screenAttachment'])
        self.assertEqual(g['screenAttachment']['y']['edge'],'top')

    def elements(self):
        return [{'id':name,'bbox':b,'source_visual_bbox':b,'current_visual_bbox':b}
                for name,b in [('date',{'x':184,'y':392,'w':494,'h':49}),('clock',{'x':76,'y':480,'w':928,'h':664})]]

    def test_vertical_gap_follows_height_and_rejects_overlap(self):
        flows=infer_flows(self.elements(),{'width':1080,'height':2160},{'x':0,'y':0,'w':1080,'h':675},.5)
        self.assertEqual(len(flows),1);f=flows[0]
        self.assertEqual(f['axis'],'y');self.assertAlmostEqual(f['preferredGap'],39*675/2160)
        t={'date':{'sx':1,'sy':1,'tx':0,'ty':0},'clock':{'sx':1,'sy':1,'tx':0,'ty':-100}}
        self.assertTrue(flow_errors(flows,t));t['clock']['ty']=0
        self.assertFalse(flow_errors(flows,t))

    def test_original_overlap_is_preserved_not_forced_apart(self):
        es=self.elements();es[1]['source_visual_bbox']={**es[1]['bbox'],'y':400}
        self.assertFalse(infer_flows(es,{'width':1080,'height':2160},{'w':1600,'h':1000},.5))

    def test_two_axis_disjoint_uses_shared_horizontal_column(self):
        es=self.elements();es[0]['bbox']['x']=0;es[0]['bbox']['w']=50
        f=infer_flows(es,{'width':1080,'height':2160},{'w':1600,'h':1000},.5)[0]
        self.assertIn(f['axis'],{'x','y'})

    def test_vertical_solver_does_not_allow_new_overlap(self):
        date=comp('date',(184,392,494,49));clock=comp('clock',(53,466,1086,722))
        visuals={e['id']:e['bbox'] for e in self.elements()}
        c=ctx(1080,675,sourceStableVisualBounds=visuals,stableVisualBounds=visuals)
        result=AnchorThemeLayout().layout_scene([date,clock],SOURCE,{'resolutionPx':{'width':1080,'height':675}},c)
        a=transformed_box(visuals['date'],result['date']);b=transformed_box(visuals['clock'],result['clock'])
        self.assertGreater(b['y']-a['y']-a['h'],0)

    def test_callout_and_footer_keep_one_transform(self):
        hint=comp('hint',(37,2020,1027,152),sectionSplit='instructions',members=['image'])
        footer=comp('footer',(28,2180,1023,161),role='footer',bindingButtons=['left','right'])
        c=ctx(1600,1000,model={'root':{'screenWidth':'1080'},'nodes':{'image':{'tag':'Image','attributes':{}}}})
        result=AnchorThemeLayout().layout_scene([hint,footer],SOURCE,{'resolutionPx':{'width':1600,'height':1000}},c)
        self.assertAlmostEqual(result['hint']['sx'],result['footer']['sx'])
        self.assertAlmostEqual(result['hint']['tx'],result['footer']['tx'])
        self.assertAlmostEqual(result['hint']['ty'],result['footer']['ty'])

    def test_background_horizontal_center_and_vertical_top_are_independent(self):
        bg=comp('bg',(0,0,1080,2640),role='background')
        c=ctx(1600,1000)
        result=AnchorThemeLayout().layout_scene([bg],SOURCE,{'resolutionPx':{'width':1600,'height':1000}},c)
        b=transformed_box(bg['bounds'],result['bg'])
        self.assertAlmostEqual(b['x']+b['w']/2,800)
        self.assertAlmostEqual(b['y'],0)
        bg['bounds']['y']=-120
        result=AnchorThemeLayout().layout_scene([bg],SOURCE,{'resolutionPx':{'width':1600,'height':1000}},ctx(1600,1000))
        b=transformed_box(bg['bounds'],result['bg'])
        self.assertAlmostEqual(b['y']+b['h']/2,500)

    def test_nearby_gap_outranks_distant_gap_preferences(self):
        es=self.elements();es.append({'id':'footer','bbox':{'x':184,'y':2000,'w':494,'h':80},'current_visual_bbox':{'x':184,'y':2000,'w':494,'h':80}})
        flows=infer_flows(es,{'width':1080,'height':2160},{'w':1080,'h':675},.5)
        near=next(f for f in flows if f['first']=='date' and f['last']=='clock')
        far=next(f for f in flows if f['first']=='date' and f['last']=='footer')
        self.assertGreater(near['preferenceWeight'],far['preferenceWeight']*10)

    def test_new_label_after_edge_footer_requires_reflow_of_footer(self):
        from multires_app.layout.spatial_flow import insertion_blockers
        footer={'x':0,'y':900,'w':100,'h':100};label={'x':0,'y':1100,'w':100,'h':20}
        relations=[{'first':'footer','last':'label','axis':'y','firstRect':footer,'lastRect':label,'minimumGap':5}]
        identity={'sx':1,'sy':1,'tx':0,'ty':0}
        self.assertEqual(insertion_blockers(relations,{'label':label},{'footer':identity},['label'],['footer'],{'x':0,'y':0,'w':1000,'h':1000}),{'footer'})

    def test_unresolved_zero_first_frame_does_not_hide_resolved_geometry(self):
        from multires_app.media.media_layout import layout_rows
        row=[0,10,20,100,50,1,0,'',0]
        layout={'rows':[row],'mediaLayout':{'nodeIndices':[0],'rows':[[0,10,20,0,0,1,-1,'',0]]}}
        self.assertEqual(layout_rows({'nodeOrder':['image']},layout),[row])
        self.assertEqual(layout['mediaLayout']['rows'][0][3:5],[0,0])

    def test_cover_uses_backdrop_not_foreground_union(self):
        bg=comp('bg',(-100,0,1280,2640),role='background')
        primary={'x':0,'y':0,'w':1080,'h':2640}
        c=ctx(1600,1000,backgroundBounds={'bg':primary},sourceBackgroundBounds={'bg':primary})
        result=AnchorThemeLayout().layout_scene([bg],SOURCE,{'resolutionPx':{'width':1600,'height':1000}},c)
        placed=transformed_box(primary,result['bg'])
        self.assertAlmostEqual(placed['x'],0);self.assertAlmostEqual(placed['w'],1600)

    def test_late_visible_component_is_solved_before_native_family_freezes(self):
        import copy
        from unittest.mock import patch
        from tests.test_human_and_clusters import cluster_fixture
        from multires_app.pipeline.scene_clusters import schedule_device
        p,d=cluster_fixture();d['layouts']=d['layouts'][:2];p['model']['controls']={};p['model']['root']={'screenWidth':'1080'}
        d['profile']['resolutionPx']={'width':1600,'height':1000}
        d['layouts'][0]['rows'][1][3:5]=[0,0]
        p['sourceDevice']={'viewport':{'width':1080,'height':2160},'layouts':copy.deepcopy(d['layouts'])}
        algorithm=AnchorThemeLayout();algorithm.finalize_device=lambda *args:None
        with patch('multires_app.pipeline.scene_pipeline.state_guard',return_value='1'):schedule_device(p,d,algorithm)
        self.assertEqual(d['layouts'][0]['transforms']['body'],d['layouts'][1]['transforms']['body'])
        self.assertLess(d['layouts'][0]['transforms']['body']['sx'],1)

    def test_layered_background_keeps_art_frame_and_separate_backdrop(self):
        from multires_app.layout.background_layers import split_background_layers
        nodes={'g':{'tag':'Group','attributes':{},'children':['photo','art']},
               'photo':{'tag':'Image','attributes':{'x':'540','y':'0','w':'1080','align':'center'},'children':[]},
               'art':{'tag':'Image','attributes':{'x':'100','y':'100','w':'200'},'children':[]}}
        p={'model':{'nodes':nodes},'components':[{'id':'g','name':'scene','role':'background','members':['g','photo','art'],'roots':['g'],'backgroundComposition':True}],
           'nodeComponents':{'g':'g','photo':'g','art':'g'},'nodeOrder':['photo','art'],'assetOrder':['bg.jpg','art.png'],
           'sourceDevice':{'viewport':{'width':1080,'height':2160},'layouts':[{'rows':[[0,0,0,1080,2640,1,0],[1,100,100,200,200,1,1]]}]}}
        split_background_layers(p)
        self.assertEqual(p['nodeComponents']['photo'],'photo');self.assertEqual(p['nodeComponents']['art'],'g')
        self.assertEqual(p['components'][0]['backgroundFrameNodes'],['g','photo','art'])

    def test_storage_compaction_preserves_active_constraints_and_matrices(self):
        from multires_app.pipeline.layout_storage import compact_layout_storage
        ir={'constraints':[{'type':'gap','gap':10}]};matrix={'sx':1,'sy':1,'tx':0,'ty':0}
        p={'anchorIRs':{'active':ir,'unused':{}},'devices':[{'layouts':[{'transforms':{'a':matrix},
           'adaptationPlan':{'layoutIRRef':'active','spatialFlows':[{'axis':'y'}],
           'validation':{'required_ok':True,'soft_violations':[{'id':'preference','residual':2,'expression':'derived text'}]}}}]}]}
        compact_layout_storage(p)
        self.assertIs(p['anchorIRs']['active'],ir);self.assertNotIn('unused',p['anchorIRs'])
        self.assertIs(p['devices'][0]['layouts'][0]['transforms']['a'],matrix)
        self.assertEqual(p['devices'][0]['layouts'][0]['adaptationPlan']['spatialFlowCount'],1)
