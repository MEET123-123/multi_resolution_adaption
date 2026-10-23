import copy
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT.parent/'src'))
from optimize_layout import source_audit, marker_positions
from guard_numeric_proposals import rejected
from multires_app.learning.geometry_head import geometry, decode, tensor_decode


class GeometryHeadTests(unittest.TestCase):
    def request(self):
        return {'schema':'multires-layout/1','sourceViewport':{'width':1080,'height':2340},
                'safeRegion':{'x':0,'y':0,'w':1080,'h':1728},'groups':[
                    {'id':'g0','sourceBox':[0,-38.462,1000,1076.923], 'roles':['content'],
                     'memberCount':2,'container':'overlay','anchor':'center_center',
                     'centerAxes':['x'],'edges':{},'fixed':False}]}

    def test_extreme_predictions_preserve_physical_aspect_and_required_center(self):
        request=self.request();group=request['groups'][0];_,c=geometry(request,group)
        for residual in ([0,0,0],[100,-100,-100],[-100,100,100]):
            x,y,w,h=decode(c,residual)
            self.assertGreater(w,0);self.assertGreater(h,0)
            self.assertAlmostEqual(x+w/2,500)
            self.assertAlmostEqual((w*1080)/(h*1728),(1000*1080)/(1076.923*2340))
        _,_,w,h=decode(c,[0,0,0])
        self.assertAlmostEqual(w,738.4615384615385)
        self.assertAlmostEqual(h,1076.923)

    def test_source_audit_quarantines_backdrop_with_controls_without_target(self):
        sample={'request':self.request(),'label':{'answer':{'groups':{'g0':[1,2,3,4]}}}}
        evidence={'groupMap':{'g0':'root'},'annotations':{'compositionLevel':{'groups':[
            {'id':'root','members':['backdrop','controls']}]},'elementLevel':{'elements':[
                {'component':'backdrop','source_node_id':'photo','tag':'Image','sourceBox':{'w':1080,'h':2520},'asset':'frame.jpg','attributes':{}},
                {'component':'controls','source_node_id':'button','tag':'Button','sourceBox':{'w':100,'h':100},'asset':None,'attributes':{}}]}}}
        original=copy.deepcopy(evidence);audit=source_audit(sample,evidence)
        self.assertEqual(audit['g0']['reason'],'mixed_backdrop_controls');self.assertFalse(audit['g0']['learn'])
        sample['label']['answer']['groups']['g0']=[999,999,999,999]
        self.assertEqual(source_audit(sample,evidence),audit);self.assertEqual(evidence,original)
        evidence['annotations']['elementLevel']['elements'][0]['sourceBox']['w']=500
        self.assertTrue(source_audit(sample,evidence)['g0']['learn'])

    def test_group_markers_keep_root_identity(self):
        self.assertEqual(marker_positions([1,7,8,2,7,8,3],[7,8]),[2,5])

    def test_execution_gate_rejects_review_and_missing_evidence_without_reading_teacher(self):
        clean={'id':'a','render':{'status':'simulated'},'checks':[{'status':'pass'}], 'mae1000AfterSolver':99999}
        review={'id':'b','render':{'status':'simulated'},'checks':[{'status':'review','rule':'GEO.NEW_OVERLAP'}], 'mae1000AfterSolver':0}
        missing={'id':'c','render':{'status':'simulated'},'checks':[]}
        self.assertEqual([c['id'] for c in rejected({'cases':[clean,review,missing]})],['b','c'])

    def test_tensor_and_inference_decoders_agree(self):
        try: import torch
        except ImportError: self.skipTest('torch optional in annotation-only environment')
        _,c=geometry(self.request(),self.request()['groups'][0]);rng=random.Random(23)
        for _ in range(20):
            residual=[rng.uniform(-5,5) for _ in range(3)]
            actual=tensor_decode(torch.tensor([c]),torch.tensor([residual]))[0]*1000
            for a,b in zip(actual.tolist(),decode(c,residual)):self.assertAlmostEqual(a,b,delta=.002)


if __name__=='__main__':unittest.main()
