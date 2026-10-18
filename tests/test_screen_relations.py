import unittest
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.layout.anchor_ir import build_ir
from multires_app.pipeline.scene_clusters import transformed_box
from tests.test_anchor_layout import SOURCE,comp,ctx


class ScreenRelationsTests(unittest.TestCase):
    def layout(self,comps,**extra):
        context=ctx(1600,1000,**extra)
        result=AnchorThemeLayout().layout_scene(comps,SOURCE,{'resolutionPx':{'width':2560,'height':1600}},context)
        return {c['id']:transformed_box(c['bounds'],result[c['id']]) for c in comps if c['id'] in result},context

    def test_left_top_right_bottom_survive_landscape_packing(self):
        comps=[comp('left',(0,850,100,100)),comp('top',(420,0,200,100)),comp('right',(980,850,100,100)),comp('bottom',(450,2300,100,100))]
        boxes,context=self.layout(comps)
        self.assertAlmostEqual(boxes['left']['x'],0)
        self.assertAlmostEqual(boxes['top']['y'],0)
        self.assertAlmostEqual(boxes['right']['x']+boxes['right']['w'],1600)
        self.assertAlmostEqual(boxes['bottom']['y']+boxes['bottom']['h'],1000)

    def test_slight_crop_and_vertical_overflow_keep_horizontal_edges(self):
        comps=[comp('left',(-17,-20,578,400)),comp('right',(506,1000,578,400))]
        boxes,context=self.layout(comps)
        self.assertAlmostEqual(boxes['left']['x'],0)
        self.assertAlmostEqual(boxes['right']['x']+boxes['right']['w'],1600)
        edges=context['adaptationPlan']['horizontalEdgeAttachments']
        self.assertEqual(edges['left']['priority'],'required')
        self.assertEqual(edges['right']['edge'],'right')

    def test_large_one_sided_header_and_special_art_keep_edges(self):
        comps=[comp('wide',(116,100,970,349),role='header'),
               comp('art',(0,1300,300,400),role='decoration')]
        boxes,context=self.layout(comps)
        self.assertAlmostEqual(boxes['wide']['x']+boxes['wide']['w'],1600)
        self.assertAlmostEqual(boxes['art']['x'],0)

    def test_half_hidden_radial_menu_keeps_crop_at_right_edge(self):
        c=comp('drawer',(516,550,1060,1020))
        boxes,context=self.layout([c])
        b=boxes['drawer'];scale=b['w']/1060
        self.assertAlmostEqual(b['x'],1600-(1080-516)*scale)
        self.assertAlmostEqual(b['x']+b['w']-1600,(1576-1080)*scale)
        self.assertEqual(context['adaptationPlan']['horizontalEdgeAttachments']['drawer']['marginScale'],'resource')

    def test_left_crop_and_clipped_stable_resources_do_not_reveal_waiting_art(self):
        c=comp('drawer',(-420,550,900,800))
        boxes,_=self.layout([c],stableResources=[{'component':'drawer','id':'image','rect':{'x':0,'y':550,'w':480,'h':800}}])
        b=boxes['drawer'];self.assertAlmostEqual(b['x'],-420*b['w']/900)

    def test_containment_constrains_visible_portion_not_entire_asset(self):
        from multires_app.layout.canvas_bounds import stable_resources
        p={'sourceDevice':{'viewport':{'width':1080,'height':2400}},'components':[{'id':'c','role':'content'}],
           'nodeOrder':['image'],'nodeComponents':{'image':'c'},'model':{'nodes':{'image':{'tag':'Image'}}}}
        source={'rows':[[0,516,500,1060,1000,1]]}
        target={'rows':[[0,516,50,1060,1000,1]]}
        self.assertEqual(stable_resources(p,target,source)[0]['rect'],{'x':516,'y':50,'w':564,'h':1000})

    def test_interior_uses_existing_anchor_and_waiting_page_is_not_snapped(self):
        from multires_app.layout.screen_relations import horizontal_attachment
        for box in [(80,800,200,200),(1080,800,200,200),(0,-400,200,100),(0,0,1080,400)]:
            self.assertFalse(horizontal_attachment(dict(zip(('x','y','w','h'),box)),{'width':1080,'height':2400}))
        c=comp('interior',(350,800,200,200));boxes,context=self.layout([c])
        self.assertFalse(context['adaptationPlan']['horizontalEdgeAttachments'])
        self.assertAlmostEqual(boxes['interior']['x']+boxes['interior']['w']/2,450/1080*1600)

    def test_edge_atoms_are_not_merged_into_an_interior_container(self):
        comps=[comp('edge',(0,800,100,100)),comp('peer',(110,800,100,100))]
        ir=build_ir(comps,{'width':1080,'height':2400},ctx(),{})
        root=next(g for g in ir['groups'] if 'edge' in g['members'])
        self.assertEqual(root['members'],['edge'])

    def test_export_guard_detects_later_edge_drift(self):
        from multires_app.layout.screen_relations import horizontal_attachment_errors
        comps=[{**comp('left',(0,800,100,100)),'name':'left'}];context=ctx(1600,1000)
        transforms=AnchorThemeLayout().layout_scene(comps,SOURCE,{'resolutionPx':{'width':2560,'height':1600}},context)
        project={'model':{'nodes':{'img':{'tag':'Image','attributes':{},'children':[],'parent':None}},'variables':{}},
                 'components':comps,'nodeOrder':['img'],'nodeComponents':{'img':'left'}}
        layout={'state':'s','rows':[[0,0,800,100,100,1,-1,'',0]],'transforms':transforms,'adaptationPlan':context['adaptationPlan']}
        device={'viewport':{'width':1600,'height':1000},'layouts':[layout]}
        self.assertFalse(horizontal_attachment_errors(project,device,layout))
        transforms['left']['tx']+=25
        self.assertEqual(horizontal_attachment_errors(project,device,layout)[0]['actualMargin'],25)

    def test_missing_cached_edge_intent_does_not_disable_validation(self):
        from multires_app.layout.screen_relations import horizontal_attachment_errors
        row=[0,0,800,100,100,1,-1,'',0]
        project={'model':{'inputs':{'interaction':{'coordinateSpace':'source'}},
                         'layoutSourceViewport':{'width':1080,'height':2400},
                         'nodes':{'img':{'tag':'Image','attributes':{},'children':[],'parent':None}},'variables':{}},
                 'components':[{**comp('left',(0,800,100,100)),'name':'left'}],
                 'nodeOrder':['img'],'nodeComponents':{'img':'left'},
                 'sourceDevice':{'viewport':{'width':1080,'height':2400},'layouts':[{'state':'s','rows':[row]}]}}
        layout={'state':'s','rows':[row],'transforms':{'left':{'sx':1,'sy':1,'tx':100,'ty':-500}}}
        device={'viewport':{'width':1600,'height':1000},'layouts':[layout]}
        self.assertEqual(horizontal_attachment_errors(project,device,layout)[0]['actualMargin'],100)

    def test_equal_cell_footer_expands_with_viewport(self):
        comps=[comp(str(i),(180+i*360-40,2280,80,80),role='footer') for i in range(3)]
        boxes,context=self.layout(comps)
        self.assertEqual(context['adaptationPlan']['screenDistributions'][0]['mode'],'equal-cells')
        for i in range(3):self.assertAlmostEqual(boxes[str(i)]['x']+boxes[str(i)]['w']/2,1600*(i+.5)/3)
        self.assertAlmostEqual(boxes['0']['y'],boxes['2']['y'])

    def test_equal_gap_unequal_icons_and_fixed_component(self):
        comps=[comp(str(i),(x,2300,w,80),role='footer') for i,(x,w) in enumerate([(20,60),(460,100),(940,120)])]
        boxes,context=self.layout(comps)
        self.assertEqual(context['adaptationPlan']['screenDistributions'][0]['mode'],'equal-gaps')
        self.assertAlmostEqual(boxes['1']['x']-boxes['0']['x']-boxes['0']['w'],boxes['2']['x']-boxes['1']['x']-boxes['1']['w'])
        fixed={'0':{'sx':1,'sy':1,'tx':0,'ty':-1400}}
        _,context=self.layout(comps,fixedTransforms=fixed)
        self.assertEqual(context['adaptationPlan']['frozenIds'],['0'])

    def test_ir_does_not_merge_a_rail_or_infer_irregular_row(self):
        comps=[comp(str(i),(180+i*360-40,2280,80,80),role='footer') for i in range(3)]
        ir=build_ir(comps,{'width':1080,'height':2400},ctx(),{})
        self.assertEqual(len(ir['screenDistributions']),1)
        self.assertTrue(all(len(g['members'])==1 for g in ir['groups']))
        comps[1]['bounds']['x']+=100
        self.assertFalse(build_ir(comps,{'width':1080,'height':2400},ctx(),{})['screenDistributions'])

    def test_manual_anchor_and_opt_out_win(self):
        c=comp('a',(0,0,100,100),layout={'anchor':'center'})
        ir=build_ir([c],{'width':1080,'height':2400},ctx(),{})
        self.assertFalse(ir['groups'][0].get('screenAttachment'))
        c.pop('layout');ir=build_ir([c],{'width':1080,'height':2400},ctx(),{'inferScreenRelations':False})
        self.assertFalse(ir['groups'][0].get('screenAttachment'))

    def test_quality_gate_counts_lost_attachments(self):
        from multires_app.layout.layout_quality import screen_relation_errors
        comps=[comp('a',(0,0,100,100))];_,context=self.layout(comps)
        desc={'a':{'bounds':comps[0]['bounds']}}
        errors=screen_relation_errors(desc,{'a':{'sx':1,'sy':1,'tx':400,'ty':300}},context['adaptationPlan'])
        self.assertGreater(sum(errors),600)

    def test_script_dimension_alias_preserves_larger_bottom_margin(self):
        from multires_app.layout.screen_relations import scripted_edges
        model={'variables':{'height':{'attributes':{'expression':'#screen_height'}}},
               'nodes':{'image':{'attributes':{'y':'#height-300'}}}}
        intent=scripted_edges({'x':300,'y':2100,'w':100,'h':100},{'width':1080,'height':2400},['image'],model)
        self.assertEqual(intent['y']['edge'],'bottom');self.assertEqual(intent['y']['margin'],200)

    def test_plain_footer_group_split_preserves_buttons_and_refuses_shared_animation(self):
        from multires_app.layout.footer_components import split_footer_components
        import copy
        nodes={'g':{'tag':'Group','children':['a','b','c','ba','bb','bc'],'attributes':{}}}
        for uid in ['a','b','c']:nodes[uid]={'tag':'Image','children':[],'attributes':{}}
        for uid in ['ba','bb','bc']:nodes[uid]={'tag':'Button','children':[],'attributes':{}}
        order=list(nodes);rows=[]
        for i,uid in enumerate(['a','b','c','ba','bb','bc']):rows.append([order.index(uid),140+(i%3)*360,2280,80,80,1,-1,'',0])
        component={**comp('g',(140,2280,800,80),role='footer',roots=['g'],members=order),'name':'footer'}
        project={'model':{'nodes':nodes,'layoutBindings':{'bindings':[{'button':'b'+uid,'anchors':[uid]} for uid in ['a','b','c']]}},
                 'components':[component],'nodeComponents':dict.fromkeys(order,'g'),'nodeOrder':order,
                 'sourceDevice':{'viewport':{'width':1080,'height':2400},'layouts':[{'rows':rows}]}}
        animated=copy.deepcopy(project);animated['model']['nodes']['anim']={'tag':'PositionAnimation','children':[]}
        animated['model']['nodes']['g']['children'].append('anim')
        self.assertFalse(split_footer_components(animated))
        self.assertEqual(len(split_footer_components(project)),1)
        self.assertEqual(project['nodeComponents']['ba'],'a')
        self.assertEqual(project['components'][0]['roots'],['a','ba'])
        self.assertFalse(split_footer_components(project))


if __name__=='__main__':unittest.main()
