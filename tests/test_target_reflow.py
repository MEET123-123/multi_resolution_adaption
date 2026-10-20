import copy
import unittest

from multires_app.preview.target_reflow import solve_reflow
from multires_app.preview.target_layout import _validate_catalog, SCHEMA


class ReferenceFirstTests(unittest.TestCase):
    def fixture(self):
        components=[{'id':'a','bbox':{'x':0.,'y':10.,'w':80.,'h':80.},'locked':False},
                    {'id':'b','bbox':{'x':210.,'y':10.,'w':80.,'h':80.},'locked':False}]
        for c in components:c['sourceBBox']=dict(c['bbox'])
        request={'viewport':{'width':400,'height':300},'constraints':{'safeRegion':{'x':0.,'y':0.,'w':400.,'h':300.}},'components':components}
        groups=[{'id':c['id'],'members':[c['id']],'matchBox':dict(c['bbox']),'bbox':dict(c['bbox']),
                 'basisBoxes':{c['id']:dict(c['bbox'])},'currentBoxes':{c['id']:dict(c['bbox'])},
                 'locked':False,'centerAxes':['x'],'edges':{'x':{'edge':'left'}},'corner':False} for c in components]
        catalog={'schema':SCHEMA,'coordinateUnit':'normalized-1000-xywh','viewport':request['viewport'],
                 'layoutMode':'reference-first','groups':groups}
        return request,catalog

    def test_large_reorder_overrides_old_centers_edges_and_order(self):
        request,catalog=self.fixture()
        matches={'a':{'bbox':{'x':280.,'y':180.,'w':80.,'h':80.},'confidence':.95},
                 'b':{'bbox':{'x':20.,'y':20.,'w':100.,'h':100.},'confidence':.95}}
        rows,report=solve_reflow(catalog,request,matches)
        self.assertEqual(report['sourceConstraintStrength'],'weak')
        self.assertTrue(all(g['targetOverlapIoU']>.999 for g in report['groups']))
        boxes={c['id']:c['bbox'] for c in rows}
        self.assertGreater(boxes['a']['x'],boxes['b']['x'])
        self.assertGreater(boxes['a']['y'],boxes['b']['y'])

    def test_unmatched_group_yields_instead_of_freezing_the_reference(self):
        request,catalog=self.fixture()
        rows,report=solve_reflow(catalog,request,{'a':{'bbox':{'x':210.,'y':10.,'w':80.,'h':80.},'confidence':.95}})
        self.assertGreater(report['groups'][0]['targetOverlapIoU'],.999)
        self.assertIn('b',report['auxiliaryGroups'])
        self.assertEqual({c['id'] for c in rows},{'a','b'})

    def test_nested_group_has_one_affine_and_stays_inside(self):
        request,catalog=self.fixture();g=catalog['groups'][0]
        c={'id':'a/text','bbox':{'x':10.,'y':20.,'w':30.,'h':15.},'locked':False}
        c['sourceBBox']=dict(c['bbox']);request['components'].append(c)
        g['members'].append(c['id']);g['basisBoxes'][c['id']]=dict(c['bbox']);g['currentBoxes'][c['id']]=dict(c['bbox'])
        rows,_=solve_reflow(catalog,request,{'a':{'bbox':{'x':30.,'y':180.,'w':100.,'h':100.},'confidence':.9}})
        boxes={c['id']:c['bbox'] for c in rows};a=boxes['a'];text=boxes['a/text']
        self.assertAlmostEqual(text['w']/30,a['w']/80)
        self.assertAlmostEqual(text['x']-a['x'],10*a['w']/80)
        self.assertLessEqual(a['y']+a['h'],300.0001)

    def test_forged_basis_or_stale_current_catalog_rejected(self):
        request,catalog=self.fixture();_validate_catalog(catalog,request)
        for field in ('basisBoxes','currentBoxes'):
            bad=copy.deepcopy(catalog);bad['groups'][0][field]['a']['x']+=1
            with self.assertRaises(ValueError):_validate_catalog(bad,request)

    def test_optical_collision_does_not_use_transparent_padding(self):
        from multires_app.preview.layout_refinement import validate_geometry_change
        desc={'a':{'bounds':{'x':0,'y':0,'w':80,'h':80}},'b':{'bounds':{'x':210,'y':0,'w':80,'h':80}}}
        identity={'sx':1.,'sy':1.,'tx':0.,'ty':0.};before={k:dict(identity) for k in desc}
        after={'a':{**identity,'tx':120},'b':{**identity,'tx':-40}}
        optical={'a':{'x':20,'y':20,'w':40,'h':40},'b':{'x':230,'y':20,'w':40,'h':40}}
        region={'x':0,'y':0,'w':400,'h':300}
        validate_geometry_change(desc,before,after,{'a','b'},set(),region,'s',collision_bounds=optical)
        after['b']['tx']=-70
        with self.assertRaises(ValueError):validate_geometry_change(desc,before,after,{'a','b'},set(),region,'s',collision_bounds=optical)
        after['a']['tx']=350
        with self.assertRaises(ValueError):validate_geometry_change(desc,before,after,{'a'},set(),region,'s',collision_bounds=optical)

    def test_context_digest_changes_when_popup_or_artwork_changes(self):
        from multires_app.preview.layout_refinement import context_digest
        a={'clock':{'role':'datetime','geometry':[['n',[1,2,3,4]]],'content':[['n',0,'',1]]}}
        b=copy.deepcopy(a);b['clock']['content'][0][1]=1
        self.assertNotEqual(context_digest(a),context_digest(b))
        b=copy.deepcopy(a);b['popup']={'role':'dialog','geometry':[],'content':[]}
        self.assertNotEqual(context_digest(a),context_digest(b))

    def test_frozen_animation_peer_uses_current_scale(self):
        request,catalog=self.fixture();catalog['groups'][0]['locked']=True
        catalog['groups'][0]['currentBoxes']['a']={'x':0,'y':10,'w':40,'h':40}
        request['components'][0]['bbox']={'x':0,'y':10,'w':40,'h':40}
        catalog['animationScaleSets']=[{'wrapper':'w','groups':['a','b']}]
        rows,_=solve_reflow(catalog,request,{'b':{'bbox':{'x':200,'y':100,'w':50,'h':50},'confidence':.95}})
        b=next(r['bbox'] for r in rows if r['id']=='b')
        self.assertAlmostEqual(b['w'],40)

    def test_native_bitmap_family_scale_remains_exportable(self):
        request,catalog=self.fixture();catalog['groups'][0].update(nativeScale=.75,nativeScaleReason='shared glyph family')
        rows,report=solve_reflow(catalog,request,{'a':{'bbox':{'x':20,'y':180,'w':100,'h':100},'confidence':.95}})
        a=next(r['bbox'] for r in rows if r['id']=='a')
        self.assertAlmostEqual(a['w'],60);self.assertEqual(report['nativeScaleConstraints'][0]['scale'],.75)


if __name__=='__main__':unittest.main()
