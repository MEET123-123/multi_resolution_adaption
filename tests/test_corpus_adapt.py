import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multires_app.pipeline.representative_states import select_states
from multires_app.tools.corpus_adapt import script_signature,summarize


class CorpusAdaptTests(unittest.TestCase):
    def test_reference_batch_isolates_failure_and_resumes_checkpoints(self):
        from multires_app.preview.reference_workflow import save,batch
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp).resolve();(root/'project.json').write_text('{}');(root/'target.png').write_bytes(b'image-test')
            plan={'schema':'corpus-reference-plan/1','jobs':[{'project':'project.json','target':'tablet','state':s,'referenceImage':'target.png'} for s in ('a','b')]}
            save(root/'plan.json',plan)
            with patch('multires_app.preview.reference_workflow.prepare',return_value={'sourceImage':'source.png'}),patch('multires_app.preview.reference_workflow.index_report'),patch(
                'multires_app.preview.reference_workflow.complete',side_effect=[ValueError('bad scene'),{'status':'needs-review','readbackCoverage':{'passed':True}}]) as complete:
                summary=batch(root/'plan.json',root/'out')
                self.assertEqual(summary['statuses'],{'failed':1,'needs-review':1});self.assertEqual(summary['nativeAuditedJobs'],1)
                self.assertEqual(complete.call_count,2)
                batch(root/'plan.json',root/'out');self.assertEqual(complete.call_count,2)

    def test_workbench_input_policy_is_explicit_and_validated(self):
        from multires_app.preview.workbench import validate_config
        self.assertEqual(validate_config({})['interactionSpace'],'source')
        self.assertEqual(validate_config({'interactionSpace':'target'})['interactionSpace'],'target')
        with self.assertRaises(ValueError):validate_config({'interactionSpace':'arbitrary'})

    def test_resource_backoff_changes_preference_and_preserves_authored_rules(self):
        from multires_app.pipeline.scene_clusters import schedule_device
        from multires_app.layout.anchor_solver import RequiredConstraintError
        from types import SimpleNamespace
        p={'components':[]};d={'profile':{},'layouts':[]};policy=SimpleNamespace(name='anchor-theme-layout-v1')
        seen=[]
        def solve(*args):
            factor=d['profile'].get('adaptation',{}).get('anchorLayout',{}).get('resourceScaleFactor',1)
            seen.append(factor)
            if factor>.6:raise RequiredConstraintError('crowded','flow:0:minimum')
            return {'ok':True}
        with patch('multires_app.pipeline.scene_clusters._schedule_device',side_effect=solve):
            self.assertTrue(schedule_device(p,d,policy)['ok'])
        self.assertEqual(seen,[1,.8,.6]);self.assertEqual(d['automaticScaleBackoff']['factor'],.6)
        d={'profile':{'adaptation':{'anchorLayout':{'constraints':[{'id':'manual'}]}}},'layouts':[]}
        with patch('multires_app.pipeline.scene_clusters._schedule_device',side_effect=RequiredConstraintError('manual')) as solve:
            with self.assertRaises(RequiredConstraintError):schedule_device(p,d,policy)
            self.assertEqual(solve.call_count,1)
    def test_sampling_keeps_real_journeys_and_covers_new_controls(self):
        states=[{'id':str(i),'trace':[] if i==0 else [{'humanAction':{'kind':'swipe' if i==2 else 'tap','gesture':'left' if i==2 else 'center'}}]} for i in range(3)]
        p={'graph':{'states':states},'nodeOrder':['clock','dialog'],'nodeComponents':{'clock':'clock','dialog':'dialog'}}
        row=lambda n:[n,0,0,100,100,1]
        d={'layouts':[{'state':'0','rows':[row(0)]},{'state':'1','rows':[row(0)]},{'state':'2','rows':[row(0),row(1)]}]}
        with patch('multires_app.pipeline.scene_pipeline.state_guard',return_value='guard'):
            a=select_states(p,d,2)
        self.assertEqual(a['states'],['0','2']);self.assertFalse(a['exhaustive'])
        self.assertEqual(states[2]['trace'][0]['humanAction']['gesture'],'left')

    def test_script_fingerprint_ignores_derived_bindings_not_source_changes(self):
        model={'root':{'screenWidth':'1080'},'nodes':{'n':{'tag':'Image','attributes':{'src':'a.png'},'parent':None,'children':[]}}}
        other=copy.deepcopy(model);other['layoutBindings']={'inferred':True}
        self.assertEqual(script_signature(model),script_signature(other))
        other['nodes']['n']['attributes']['src']='b.png'
        self.assertNotEqual(script_signature(model),script_signature(other))

    def test_report_never_calls_export_alone_a_visual_match(self):
        from multires_app.preview.reference_workflow import save
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);save(root/'theme-a/result.json',{'theme':'Any theme','sourceUnchanged':True,'manifests':[
                {'manifest':'lockscreen/manifest.xml','targets':[{'target':'tablet','status':'failed','statesLaidOut':3,'error':'native replay failed'}]}]})
            summary=summarize(root)
            self.assertEqual(summary['themes'],1);self.assertEqual(summary['statuses'],{'failed':1})
            self.assertEqual(summary['nativeAuditPassed'],0);self.assertFalse(summary['referenceMatching'])
            self.assertIn('native replay failed',(root/'badcases.jsonl').read_text(encoding='utf8'))


if __name__=='__main__':unittest.main()
