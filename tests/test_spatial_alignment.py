import unittest
from multires_app.layout.spatial_alignment import infer_alignments,alignment_errors
from tests.test_anchor_layout import comp,ctx,SOURCE
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.layout.anchor_ir import build_ir,local_geometry
from multires_app.pipeline.scene_clusters import transformed_box


class AlignmentTests(unittest.TestCase):
    def test_near_center_child_line_does_not_conflict_with_group_centering(self):
        cs=[comp('weather',(408,145,341,63),role='header'),comp('clock',(361,213,354,259),role='header'),comp('date',(412,776,260,42))]
        visuals={'weather':{'x':416,'y':150,'w':255,'h':53},'clock':{'x':369,'y':218,'w':335,'h':249},'date':{'x':420,'y':781,'w':247,'h':32}}
        context=ctx(1600,1000,sourceStableVisualBounds=visuals,stableVisualBounds=visuals)
        AnchorThemeLayout().layout_scene(cs,SOURCE,{'resolutionPx':{'width':1600,'height':1000}},context)
        plan=context['adaptationPlan'];soft=[r for r in plan['spatialAlignments'] if r.get('priority')=='strong']
        self.assertEqual(len(soft),1);self.assertIn('centering',soft[0]['relaxation'])
        self.assertTrue(all('x' in g['centerRequiredAxes'] for g in plan['groups']))
        self.assertTrue(plan['validation']['required_ok'])
        self.assertFalse(alignment_errors([{**soft[0],'firstRect':visuals['weather'],'lastRect':visuals['date']}],
            {'weather':{'sx':1,'sy':1,'tx':0,'ty':0},'date':{'sx':1,'sy':1,'tx':20,'ty':0}}))

    def test_resolved_edges_and_centers_on_both_axes(self):
        es=[{'id':n,'bbox':dict(zip(('x','y','w','h'),b)),'current_visual_bbox':dict(zip(('x','y','w','h'),b))}
            for n,b in [('a',(100,100,100,100)),('b',(100,600,200,100)),('c',(700,100,80,200))]]
        r=infer_alignments(es,{'width':1080,'height':2400})
        self.assertTrue(any(e['axis']=='x' and e['factor']==0 for e in r))
        self.assertTrue(any(e['axis']=='y' and e['factor']==0 for e in r))
        ts={e['id']:{'sx':1,'sy':1,'tx':0,'ty':0} for e in es}
        self.assertFalse(alignment_errors(r,ts));ts['b']['tx']=8
        self.assertTrue(alignment_errors(r,ts))

    def test_unrelated_offsets_and_special_layers_are_not_bound(self):
        es=[{'id':str(i),'bbox':{'x':x,'y':100+i*200,'w':50,'h':50},'current_visual_bbox':{'x':x,'y':100+i*200,'w':50,'h':50},'special':i==2}
            for i,x in enumerate((100,103,100))]
        self.assertEqual(infer_alignments(es,{'width':1080,'height':2400}),[])

    def test_separate_roots_keep_shared_left_and_top(self):
        cs=[comp('a',(120,200,100,100)),comp('b',(120,1500,300,150)),comp('c',(820,200,100,300))]
        result=AnchorThemeLayout().layout_scene(cs,SOURCE,{'resolutionPx':{'width':1600,'height':1000}},ctx(1600,1000))
        boxes={c['id']:transformed_box(c['bounds'],result[c['id']]) for c in cs}
        self.assertAlmostEqual(boxes['a']['x'],boxes['b']['x'])
        self.assertAlmostEqual(boxes['a']['y'],boxes['c']['y'])

    def test_inferred_row_preserves_authored_top_instead_of_center(self):
        cs=[comp('a',(100,100,100,100)),comp('b',(220,100,100,110))]
        ir=build_ir(cs,{'width':1080,'height':2400},ctx(),{})
        geo=local_geometry(ir)
        row=next(g for g in ir['groups'] if len(g['members'])==2)
        self.assertEqual(geo[row['id']]['leaves']['a']['y'],geo[row['id']]['leaves']['b']['y'])

    def test_optical_top_alignment_ignores_different_transparent_padding(self):
        cs=[comp('a',(100,100,100,100)),comp('b',(220,90,100,120))]
        visuals={'a':{'x':100,'y':110,'w':100,'h':80},'b':{'x':220,'y':110,'w':100,'h':80}}
        c=ctx(sourceStableVisualBounds=visuals,stableVisualBounds=visuals)
        ir=build_ir(cs,{'width':1080,'height':2400},c,{})
        row=next(g for g in ir['groups'] if len(g['members'])==2);geo=local_geometry(ir)[row['id']]['leaves']
        self.assertAlmostEqual(geo['a']['y']+10,geo['b']['y']+20)

    def test_transient_crossing_does_not_create_fixed_alignment(self):
        cs=[comp('a',(100,200,100,100)),comp('b',(100,1400,200,100))]
        one={c['id']:c['bounds'] for c in cs};two={**one,'b':{**one['b'],'x':180}}
        c=ctx(1600,1000,flowSamples=[(one,one),(two,two)])
        AnchorThemeLayout().layout_scene(cs,SOURCE,{'resolutionPx':{'width':1600,'height':1000}},c)
        self.assertFalse(c['adaptationPlan']['spatialAlignments'])

    def test_shared_alignment_contract_survives_reuse(self):
        from multires_app.layout.spatial_alignment import layout_alignments
        from unittest.mock import patch
        a={'x':100,'y':200,'w':100,'h':100};b={'x':100,'y':1400,'w':200,'h':100}
        base={'state':'a','adaptationPlan':{'spatialAlignments':[{'first':'a','last':'b','axis':'x','factor':0,'scope':'shared-line'}]}}
        reused={'state':'b','adaptationPlan':{'reusedFrom':'a'}}
        p={'sourceDevice':{'viewport':{'width':1080,'height':2400},'layouts':[{'state':'b'}]}}
        with patch('multires_app.layout.visual_geometry.component_visuals',return_value={'a':a,'b':b}):
            rs=layout_alignments(p,{'layouts':[base,reused]},reused)
        self.assertEqual(len(rs),1)
        self.assertEqual(rs[0]['axis'],'x')

    def test_export_rejects_component_that_was_shifted_after_solving(self):
        from multires_app.layout.anchor_validation import validate_export
        from multires_app.layout.anchor_solver import RequiredConstraintError
        from unittest.mock import patch
        a={'x':100,'y':200,'w':100,'h':100};b={'x':100,'y':1400,'w':200,'h':100}
        identity={'sx':1,'sy':1,'tx':0,'ty':0}
        l={'state':'s','transforms':{'a':identity,'b':{**identity,'tx':12}},'adaptationPlan':{
            'policy':'nine-grid-anchor-kiwi','validation':{'required_ok':True},'spatialAlignmentPolicy':'source-visible-shared-lines-v1',
            'spatialAlignments':[{'first':'a','last':'b','axis':'x','factor':0,'scope':'shared-line'}]}}
        p={'sourceDevice':{'viewport':{'width':1080,'height':2400},'layouts':[{'state':'s'}]}}
        d={'profile':{},'layouts':[l]}
        with patch('multires_app.layout.visual_geometry.component_visuals',return_value={'a':a,'b':b}):
            for scope in ('shared-line','container'):
                l['adaptationPlan']['spatialAlignments'][0]['scope']=scope
                with self.assertRaisesRegex(RequiredConstraintError,'alignment changed'):validate_export(p,d)

    def test_frozen_subpixel_roundoff_does_not_make_alignment_unsatisfiable(self):
        cs=[comp('a',(120,200,100,100)),comp('b',(120,1500,300,150))]
        identity={'sx':1,'sy':1,'tx':0,'ty':0}
        c=ctx(1080,2400,fixedTransforms={'a':identity,'b':{**identity,'tx':.00002}})
        AnchorThemeLayout().layout_scene(cs,SOURCE,SOURCE,c)
        self.assertTrue(c['adaptationPlan']['validation']['required_ok'])

    def test_duplicate_frame_evidence_does_not_duplicate_solver_lines(self):
        cs=[comp('a',(120,200,100,100)),comp('b',(120,250,300,200))]
        identity={'sx':1,'sy':1,'tx':0,'ty':0};visuals={c['id']:c['bounds'] for c in cs};counts=[]
        for n in (1,30):
            c=ctx(1080,2400,fixedTransforms={'a':identity,'b':identity},flowSamples=[(visuals,visuals)]*n)
            AnchorThemeLayout().layout_scene(cs,SOURCE,SOURCE,c)
            counts.append(c['adaptationPlan']['validation']['constraint_count'])
            self.assertEqual(len(c['adaptationPlan']['spatialAlignments']),1)
        self.assertEqual(*counts)

    def test_alignment_conflict_identifies_public_peer_for_local_reflow(self):
        from multires_app.layout.anchor_solver import RequiredConstraintError
        cs=[comp('a',(120,200,100,100)),comp('b',(120,1500,300,150))]
        identity={'sx':1,'sy':1,'tx':0,'ty':0}
        c=ctx(1080,2400,fixedTransforms={'a':identity,'b':{**identity,'tx':12}})
        with self.assertRaises(RequiredConstraintError) as caught:AnchorThemeLayout().layout_scene(cs,SOURCE,SOURCE,c)
        self.assertIn(':public-lock-',caught.exception.constraint_id)


if __name__=='__main__':unittest.main()
