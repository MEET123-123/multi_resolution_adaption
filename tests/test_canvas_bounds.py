import unittest
from multires_app.layout.anchor_layout import AnchorThemeLayout
from multires_app.layout.anchor_solver import RequiredConstraintError
from multires_app.layout.canvas_bounds import violations,stable_resources
from tests.test_anchor_layout import SOURCE,comp,ctx


class CanvasBoundsTests(unittest.TestCase):
    def test_newly_visible_panel_is_not_frozen_by_first_frame_reuse(self):
        import copy
        from tests.test_human_and_clusters import cluster_fixture
        from multires_app.pipeline.scene_clusters import schedule_device
        from multires_app.pipeline.scene_pipeline import SemanticReflow
        p,d=cluster_fixture();d['profile']['resolutionPx']={'width':2560,'height':1600}
        d['layouts']=d['layouts'][:2]
        for l in d['layouts']:
            body=l['rows'][1];body[2]=-1100
            l['mediaLayout']={'nodeIndices':[1],'rows':[copy.deepcopy(body)]}
        d['layouts'][1]['rows'][1][2]=100
        p['sourceDevice']={'viewport':{'width':1080,'height':2160},'layouts':copy.deepcopy(d['layouts'])}
        class Recording(SemanticReflow):
            name='anchor-theme-layout-v1'
            def layout_scene(self,components,source,target,context):
                return {c['id']:{'sx':1,'sy':1,'tx':0,'ty':0} for c in components}
        schedule_device(p,d,Recording())
        request=d['layouts'][1]['layoutRequest']
        self.assertEqual(request['kind'],'local-reflow')
        self.assertIn('body',request['affectedComponents'])
        self.assertNotIn('body',request['fixedComponents'])
        self.assertIn('clock',request['fixedComponents'])

    def test_waiting_panels_excluded_and_partial_crop_active(self):
        p={'sourceDevice':{'viewport':{'width':100,'height':200}},
           'components':[{'id':'art','role':'content'}],'nodeOrder':['partial','waiting'],
           'nodeComponents':{'partial':'art','waiting':'art'},
           'model':{'nodes':{'partial':{'tag':'Image'},'waiting':{'tag':'Image'}}}}
        layout={'rows':[[0,-10,5,20,20,1],[1,110,5,20,20,1]]}
        result=stable_resources(p,layout,layout)
        self.assertEqual([e['id'] for e in result],['partial'])

    def test_export_rejects_geometry_changed_after_solver(self):
        from multires_app.layout.anchor_validation import validate_export
        p={'sourceDevice':{'viewport':{'width':100,'height':200},'layouts':[{'state':'s','rows':[[0,10,5,20,20,1]]}]},
           'components':[{'id':'art','role':'content'}],'nodeOrder':['image'],
           'nodeComponents':{'image':'art'},'model':{'nodes':{'image':{'tag':'Image'}}}}
        d={'viewport':{'width':100,'height':200},'profile':{},'layouts':[{'state':'s','rows':[[0,10,5,20,20,1]],
           'transforms':{'art':{'sx':1,'sy':1,'tx':100,'ty':0}},
           'adaptationPlan':{'policy':'nine-grid-anchor-kiwi','validation':{'required_ok':True}}}]}
        with self.assertRaises(RequiredConstraintError):validate_export(p,d)

    def test_partial_source_crop_is_fitted(self):
        c=comp('art',(-20,-30,700,900));context=ctx(1080,675)
        out=AnchorThemeLayout().layout_scene([c],SOURCE,{'resolutionPx':{'width':2560,'height':1600}},context)
        self.assertFalse(violations([{'component':'art','rect':c['bounds']}],out,{'x':0,'y':0,'w':1080,'h':675}))

    def test_stable_pose_and_hotspot_fit_while_entry_pose_remains_outside(self):
        c=comp('panel',(100,-1000,400,300))
        stable=[{'component':'panel','rect':{'x':-40,'y':100,'w':1200,'h':800}}]
        context=ctx(1080,675,stableResources=stable)
        out=AnchorThemeLayout().layout_scene([c],SOURCE,{'resolutionPx':{'width':2560,'height':1600}},context)
        self.assertFalse(violations(stable,out,{'x':0,'y':0,'w':1080,'h':675}))
        self.assertLess(c['bounds']['y']*out['panel']['sy']+out['panel']['ty'],0)
        self.assertAlmostEqual(out['panel']['sx'],out['panel']['sy'])

    def test_frozen_outside_stable_resource_is_rejected(self):
        c=comp('panel',(100,100,200,100));stable=[{'component':'panel','rect':c['bounds']}]
        context=ctx(1080,675,stableResources=stable,fixedTransforms={'panel':{'sx':1,'sy':1,'tx':2000,'ty':0}})
        with self.assertRaises(RequiredConstraintError):
            AnchorThemeLayout().layout_scene([c],SOURCE,{'resolutionPx':{'width':2560,'height':1600}},context)
