import copy
import json
import math
import unittest
from pathlib import Path
from multires_app.layout.layout_aesthetics import evaluate,reference_relations


def item(x,y,w=.2,h=.1,role='content',font=0):
    return {'box':{'x':x,'y':y,'w':w,'h':h},'role':role,'fontPx':font}


class AestheticTests(unittest.TestCase):
    def test_alignment_and_hierarchy_reward_original_relations(self):
        reference={'a':item(.2,.2,font=24),'b':item(.2,.5,font=24)}
        jittered=copy.deepcopy(reference);jittered['b']['box']['x']+=.035;jittered['b']['box']['w']*=1.5
        good=evaluate(reference,reference);bad=evaluate(jittered,reference)
        self.assertGreater(good['score'],bad['score'])
        self.assertGreater(bad['losses']['alignment'],0);self.assertGreater(bad['losses']['hierarchy'],0)

    def test_intentional_overlap_is_exempt_and_asymmetry_is_not_forced(self):
        reference={'a':item(.05,.1,.5,.5),'b':item(.1,.2,.1,.1)}
        result=evaluate(reference,reference)
        self.assertEqual(result['losses']['overlap'],0)
        self.assertEqual(result['available']['symmetry'],0)
        self.assertEqual(result['losses']['balance'],0)
        self.assertGreater(result['equilibriumDiagnostic'],0)

    def test_new_overlap_small_fonts_and_order_inversion_are_penalized(self):
        ref={'a':item(.1,.1,font=24),'b':item(.1,.5,font=24)}
        bad=copy.deepcopy(ref);bad['b']['box']['y']=.12;bad['a']['fontPx']=12
        result=evaluate(bad,ref)
        self.assertGreater(result['losses']['overlap'],0);self.assertGreater(result['losses']['readability'],0)
        bad['b']['box']['y']=0
        self.assertGreater(evaluate(bad,ref)['losses']['order'],0)

    def test_rhythm_and_background_complexity(self):
        ref={str(i):item(.1+i*.3,.8,.1,.1,role='footer') for i in range(3)}
        bad=copy.deepcopy(ref);bad['1']['box']['x']+=.1;bad['1']['backgroundComplexity']=.8
        result=evaluate(bad,ref)
        self.assertGreater(result['losses']['rhythm'],0);self.assertEqual(result['available']['backgroundComplexity'],1)
        self.assertEqual(result['losses']['backgroundComplexity'],.8)

    def test_missing_metrics_are_explicit_and_weights_validated(self):
        one={'a':item(.2,.2)};result=evaluate(one,one)
        self.assertEqual(result['available']['readability'],0);self.assertFalse(result['humanCalibrated'])
        self.assertIsNone(evaluate({}, {})['score'])
        with self.assertRaises(ValueError):evaluate(one,one,weights={'density':float('nan')})

    def test_optimization_preserves_frozen_and_canvas_and_native_guards(self):
        import tempfile
        from multires_app.pipeline.scene_pipeline import create_project
        from multires_app.layout.aesthetic_optimizer import optimize_device
        from multires_app.layout.anchor_validation import validate_export
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root/'tests') as tmp:
            out=Path(tmp)
            create_project(root/'samples/scene-theme',{'resolutionPx':{'width':1080,'height':2160}},
                {'profiles':[{'id':'tablet','resolutionPx':{'width':2560,'height':1600},'orientations':['landscape']}]},
                out,algorithm_spec='anchor',max_states=8,max_depth=3)
            p=json.loads((out/'project.json').read_text(encoding='utf-8'));p['_projectFolder']=str(out);d=p['devices'][0]
            before=copy.deepcopy(d['layouts']);result=optimize_device(p,d,{'candidateBudget':12})
            self.assertGreaterEqual(result['afterScore'],result['beforeScore'])
            self.assertIsNone(result['validationError']);validate_export(p,d)
            self.assertLessEqual(result['candidatesTested'],12)
            for l in d['layouts']:
                base=next((a for a in d['layouts'] if a['state']==l['layoutRequest'].get('baseState')),None)
                if base:
                    for cid in l['layoutRequest']['fixedComponents']:self.assertEqual(l['transforms'][cid],base['transforms'][cid])

