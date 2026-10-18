import copy
import unittest
from multires_app.layout.anchor_ir import build_ir, local_geometry, infer_anchor
from multires_app.layout.anchor_solver import anchor_position, solve, RequiredConstraintError
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.pipeline.scene_clusters import transformed_box
from multires_app.export.layout_writeback import patch_script
from xml.etree import ElementTree as ET

SOURCE={'resolutionPx':{'width':1080,'height':2400}}

def comp(name,bounds,**extras):
    return {'id':name,'role':'content','bounds':dict(zip(('x','y','w','h'),bounds)),**extras}

def ctx(w=1260,h=2800,**extras):
    return {'logicalViewport':{'width':w,'height':h},'model':{'root':{'screenWidth':'1080'}},**extras}


class AnchorTests(unittest.TestCase):
    def test_nine_grid_right_margin_and_center(self):
        c=comp('badge',(800,80,200,100),layout={'anchor':{'parent_point':'top_right','self_point':'top_right','offset':[-80,80]},'sizePolicy':'fixed'})
        context=ctx();out=AnchorThemeLayout().layout_scene([c],SOURCE,{'resolutionPx':{'width':1260,'height':2800}},context)
        b=transformed_box(c['bounds'],out['badge']);self.assertAlmostEqual(b['x'],980);self.assertEqual(b['y'],80)
        self.assertTrue(context['adaptationPlan']['validation']['required_ok'])
        a={'parent_point':'center','self_point':'center','offset':[.1,-.1],'offset_unit':'normalized'}
        self.assertEqual(anchor_position(a,{'w':100,'h':200},{'x':0,'y':0,'w':1000,'h':2000},{}),{'x':550.,'y':700.,'w':100,'h':200})

    def test_row_nearby_but_not_distant_or_background(self):
        comps=[comp(str(i),(40+i*60,100,40,40)) for i in range(3)]
        comps += [comp('far',(900,100,40,40)),comp('bg',(0,0,1080,2400),role='background')]
        ir=build_ir(comps,{'width':1080,'height':2400},ctx(),{})
        rows=[g for g in ir['groups'] if g['container']['type']=='row']
        self.assertEqual(len(rows),1);self.assertEqual(set(rows[0]['members']),{'0','1','2'})
        self.assertTrue(all('bg' not in g['members'] for g in ir['groups']))

    def test_custom_gaps_nested_column_and_overlay_order(self):
        comps=[comp('a',(10,10,30,20)),comp('b',(60,10,40,20)),comp('c',(10,60,30,30))]
        options={'groups':[{'id':'row','members':['a','b'],'container':{'type':'row','gaps':[17]}},
                           {'id':'column','members':['row','c'],'container':{'type':'column','gap':12,'align':'start'}}]}
        ir=build_ir(comps,{'width':1080,'height':2400},ctx(),options);geo=local_geometry(ir)['column']
        self.assertEqual(geo['leaves']['b']['x'],47);self.assertEqual(geo['leaves']['c']['y'],32)
        self.assertEqual([e['id'] for e in ir['elements']],['a','b','c'])
        for kind in ['rigid','overlay']:
            options={'groups':[{'id':'g','members':['a','b'],'container':kind}]}
            geo=local_geometry(build_ir(comps[:2],{'width':1080,'height':2400},ctx(),options))['g']
            self.assertEqual(geo['leaves']['b']['x']-geo['leaves']['a']['x'],50)

    def test_cycle_duplicate_and_invalid_offset_are_rejected(self):
        comps=[comp('a',(0,0,30,30))]
        for groups in [[{'id':'g','members':['h']},{'id':'h','members':['g']}],
                       [{'id':'g','members':['a']},{'id':'h','members':['a']}],
                       [{'id':'g','members':['a'],'anchor':{'offset':[float('nan'),0]}}]]:
            with self.assertRaises(ValueError):build_ir(comps,{'width':1080,'height':2400},ctx(),{'groups':groups})

    def test_required_conflict_frozen_and_soft_explanation(self):
        comps=[comp('a',(10,10,100,100))];ir=build_ir(comps,{'width':1080,'height':2400},ctx(),{})
        geo=local_geometry(ir);gid=ir['roots'][0];region={'x':0,'y':0,'w':1260,'h':2800};desired={gid:{'x':10,'y':10,'w':100,'h':100}}
        constraints=[{'type':'position','targets':['a'],'x':40,'priority':'weak'}]
        boxes,v=solve(ir,geo,region,desired,{'a':{'x':10,'y':10,'w':100,'h':100}},constraints)
        self.assertEqual(boxes['a']['x'],10);self.assertTrue(v['soft_violations'])
        constraints[0]['priority']='required'
        with self.assertRaises(RequiredConstraintError):solve(ir,geo,region,desired,{'a':{'x':10,'y':10,'w':100,'h':100}},constraints)

    def test_aspect_change_routes_container_and_keeps_atomic_ratio(self):
        comps=[comp(str(i),(60,100+i*400,250,180)) for i in range(4)]
        context=ctx(1080,675);target={'resolutionPx':{'width':2560,'height':1600}}
        out=AnchorThemeLayout().layout_scene(comps,SOURCE,target,context)
        self.assertEqual(context['adaptationPlan']['route'],'container-solver')
        for c in comps:
            t=out[c['id']];self.assertAlmostEqual(t['sx'],t['sy']);b=transformed_box(c['bounds'],t)
            self.assertGreaterEqual(b['x'],-1e-6);self.assertLessEqual(b['y']+b['h'],675.000001)

    def test_local_public_transform_cannot_be_released(self):
        a=comp('a',(30,30,100,100));b=comp('b',(30,300,100,100))
        context=ctx(fixedTransforms={'a':{'sx':1,'sy':1,'tx':0,'ty':0}},
                    allComponentDescriptors={'a':{'bounds':a['bounds'],'role':'content'}},
                    componentDefinitions={'a':a,'b':b},layoutRequest={'kind':'local-reflow'})
        out=AnchorThemeLayout().layout_scene([b],SOURCE,{'resolutionPx':{'width':1260,'height':2800}},context)
        self.assertNotIn('a',out);self.assertEqual(context['adaptationPlan']['frozenIds'],['a'])

    def test_paired_targets_choose_right_top(self):
        a=infer_anchor({'x':800,'y':80,'w':200,'h':100},{'width':1080,'height':2400},
                       [{'viewport':{'width':1260,'height':2800},'bbox':{'x':980,'y':80,'w':200,'h':100}}])
        self.assertEqual(a['parent_point'],'top_right');self.assertEqual(a['provenance'],'paired designer target')

    def test_lexical_writeback_preserves_comments_entities_and_rejects_resources(self):
        original=b'''<?xml version="1.0"?><Lockscreen>\n<!-- keep spacing -->\n<Image  src='a&amp;b.png' x = '10' y="20"/>\n</Lockscreen>'''
        root=ET.fromstring(original,parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
        snapshot=[(n,dict(n.attrib),list(n)) for n in root.iter()];image=root.find('Image');image.set('x','30')
        output,report=patch_script(original,snapshot,root)
        self.assertEqual(output,original.replace(b"x = '10'",b"x = '30'"));self.assertTrue(report['assetReferencesPreserved'])
        image.set('src','other.png')
        with self.assertRaises(ValueError):patch_script(original,snapshot,root)

    def test_readability_min_scale_and_impossible_required_size(self):
        c=comp('a',(0,0,1080,1000),layout={'minScale':.9})
        with self.assertRaises(RequiredConstraintError):
            AnchorThemeLayout().layout_scene([c],SOURCE,{'resolutionPx':{'width':2560,'height':1600}},ctx(1080,675))

    def test_discrete_collision_search_retains_frozen_box(self):
        from multires_app.layout.anchor_solver import solve_directions
        a=comp('a',(0,0,100,100));b=comp('b',(150,0,100,100))
        ir=build_ir([a,b],{'width':400,'height':200},ctx(),{'ungroupedIds':['a','b']});geo=local_geometry(ir)
        ga,gb=ir['roots'];desired={ga:{'x':300,'y':0,'w':100,'h':100},gb:{'x':100,'y':0,'w':100,'h':100}}
        boxes,v=solve_directions(ir,geo,{'x':0,'y':0,'w':400,'h':200},desired,{'a':desired[ga]},
                                 [{'id':'separation:0','type':'non_overlap','targets':[ga,gb],'direction':'right','gap':0,'priority':'required'}],1,1)
        self.assertEqual(boxes['a']['x'],300);self.assertGreater(v['directionSearch']['attempts'],1)

    def test_authored_groups_accept_hidden_variants_across_scenes(self):
        a=comp('a',(0,0,100,100));b=comp('b',(200,0,100,100));context=ctx(componentDefinitions={'a':a,'b':b})
        ir=build_ir([a],{'width':1080,'height':2400},context,{'groups':[{'id':'g','members':['a','b'],'container':'row'}]})
        self.assertEqual(next(g for g in ir['groups'] if g['id']=='g')['members'],['a'])

    def test_contain_and_explicit_cover_size_policies(self):
        a=comp('a',(100,100,100,100));target={'resolutionPx':{'width':200,'height':300},'adaptation':{'mode':'preserve','anchorLayout':{
            'maxScale':4,'groups':[{'id':'g','members':['a'],'container':'rigid','size_policy':'cover','allow_crop':True,
                'anchor':{'parent_point':'center','self_point':'center','offset':[0,0]}}]}}}
        # Content cannot both cover a different aspect ratio and remain fully visible.
        with self.assertRaises(RequiredConstraintError):AnchorThemeLayout().layout_scene([a],SOURCE,target,ctx(200,300))
        target['adaptation']['anchorLayout']['groups'][0]['size_policy']='contain'
        out=AnchorThemeLayout().layout_scene([a],SOURCE,target,ctx(200,300));self.assertAlmostEqual(out['a']['sx'],2)

    def test_quality_gate_rolls_back_inference_but_keeps_authored_rules(self):
        import json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from multires_app.pipeline.scene_pipeline import create_project
        from multires_app.layout.layout_quality import finalize_anchor_device
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root/'tests') as tmp:
            out=Path(tmp);create_project(root/'samples/scene-theme',{'resolutionPx':{'width':1080,'height':2160}},
                {'profiles':[{'id':'phone','resolutionPx':{'width':1080,'height':2160},'orientations':['portrait']}]},out,max_states=8,max_depth=3)
            p=json.loads((out/'project.json').read_text(encoding='utf-8'));d=p['devices'][0]
            bad={'newClippedComponentSnapshots':1,'newSourceDisjointOverlaps':2,'fixedTransformDrift':0,'screenRelationViolations':0,'screenRelationErrorPx':0}
            good={'newClippedComponentSnapshots':0,'newSourceDisjointOverlaps':0,'fixedTransformDrift':0,'screenRelationViolations':0,'screenRelationErrorPx':0}
            # Isolate scoring here; containment rejection has its own regression.
            with patch('multires_app.layout.layout_quality.quality',side_effect=[bad,good]),patch('multires_app.layout.canvas_bounds.violations',return_value=[]),patch('multires_app.layout.spatial_flow.flow_errors',return_value=[]),patch('multires_app.layout.layout_quality.screen_relation_errors',return_value=[]),patch('multires_app.layout.screen_relations.horizontal_attachment_errors',return_value=[]):finalize_anchor_device(p,d)
            self.assertEqual(d['layoutQuality']['selection'],'baseline-rollback')
            d['profile']['adaptation']={'anchorLayout':{'constraints':[]}}
            with patch('multires_app.layout.layout_quality.quality') as metric:finalize_anchor_device(p,d);metric.assert_not_called()
            self.assertEqual(d['layoutQuality']['selection'],'authored-constraints')

    def test_pixel_weighting_moves_before_unnecessary_shrink(self):
        # Isolate size/position weighting from the independent edge-intent rule.
        a=comp('a',(0,0,200,100));ir=build_ir([a],{'width':1080,'height':2400},ctx(),{'inferScreenRelations':False})
        gid=ir['roots'][0];ir['groups'][0]['anchor']['self_point']='top_left'
        boxes,_=solve(ir,local_geometry(ir),{'x':0,'y':0,'w':400,'h':200},
                      {gid:{'x':350,'y':0,'w':200,'h':100}})
        self.assertAlmostEqual(boxes['a']['w'],200);self.assertAlmostEqual(boxes['a']['x'],200)

    def test_nested_rigid_anchors_use_parent_local_offsets(self):
        a=comp('a',(100,200,50,40));b=comp('b',(200,300,50,40))
        options={'groups':[{'id':'child','members':['a'],'container':'rigid','size_policy':'fixed'},
                           {'id':'parent','members':['child','b'],'container':'rigid'}]}
        ir=build_ir([a,b],{'width':1080,'height':2400},ctx(),options);geo=local_geometry(ir)['parent']
        self.assertEqual(geo['leaves']['a']['x'],0);self.assertEqual(geo['leaves']['a']['y'],0)
        self.assertTrue(geo['fixed_descendant'])
        options['groups'][0]['anchor']={'parent_point':'top_left','self_point':'top_left','offset_unit':'normalized'}
        ir=build_ir([a,b],{'width':1080,'height':2400},ctx(),options)
        self.assertEqual(local_geometry(ir)['parent']['leaves']['a']['x'],0)


if __name__=='__main__':unittest.main()
