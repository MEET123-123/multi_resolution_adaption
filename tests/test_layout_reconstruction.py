import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import kiwisolver

from multires_app.reconstruction.contract import validate
from multires_app.reconstruction.templates import propose,placements
from multires_app.reconstruction.providers import OpenAICompatible
from multires_app.reconstruction.solver import solve
from multires_app.reconstruction.workflow import prepare,run


class ReconstructionContractTests(unittest.TestCase):
    def request(self):
        return {'inputDigest':'abc','safeRegion':{'x':0,'y':0,'w':1000,'h':625},'relations':[{'id':'hard','strength':'required'}],
                'groups':[{'id':'g0','roles':['content'],'locked':False,'sourceBox':{'x':0,'y':0,'w':100,'h':100},
                           'footprint':{'x':0,'y':0,'w':800,'h':100}}]}

    def test_strict_ids_coverage_digest_and_protected_relations(self):
        request=self.request();answer=propose(request);validate(answer,request)
        mutations=[lambda a:a.update(inputDigest='stale'),lambda a:a['candidates'][0]['groups'].append({'id':'invented','zone':'main','order':0}),
                   lambda a:a['candidates'][0].update(relations=[{'id':'hard','strength':'weak'}]),lambda a:a['candidates'][0].update(xml='<script>'),
                   lambda a:a['candidates'][0]['groups'][0].update(id=['not-a-string'])]
        for mutate in mutations:
            bad=copy.deepcopy(answer);mutate(bad)
            with self.assertRaises(ValueError):validate(bad,request)

    def test_model_can_only_review_actual_prior_candidates(self):
        r=self.request();a={'schema':'layout-reconstruction-advice/1','inputDigest':'abc','candidates':[],
                            'stop':True,'preferredCandidate':'c1','reviewReason':'larger clock'}
        with self.assertRaises(ValueError):validate(a,r)
        validate(a,r,['c1'])

    def test_zone_fit_uses_whole_hit_footprint(self):
        r=self.request();a=propose(r)['candidates'][0];box=placements(r,a)['g0']['bbox']
        # Its 100px artwork must not be enlarged so far that the 800px hot area overflows.
        scale=box['w']/100;self.assertLessEqual(800*scale,550.0001)

    def fixture(self):
        from tests.test_target_reflow import ReferenceFirstTests
        request,catalog=ReferenceFirstTests().fixture();request.update(target='tablet',state='s')
        project={'sourceProfile':{},'sourceDevice':{'viewport':{'width':400,'height':800},'layouts':[{'state':'s'}]},
                 'components':[{'id':c['id']} for c in request['components']],
                 'devices':[{'profile':{'id':'tablet'},'layouts':[{'state':'s'}]}]}
        return project,catalog,request

    def test_source_order_is_weak_and_can_reconstruct(self):
        p,c,r=self.fixture();m={'a':{'bbox':{'x':280.,'y':180.,'w':80.,'h':80.},'confidence':1},
                              'b':{'bbox':{'x':20.,'y':20.,'w':80.,'h':80.},'confidence':1}}
        rows,_=solve(p,c,r,m,{'relations':[]},[{'id':'order','first':'a','last':'b','axis':'x'}])
        boxes={row['id']:row['bbox'] for row in rows};self.assertGreater(boxes['a']['x'],boxes['b']['x'])

    def test_explicit_rule_cannot_be_weakened_by_new_positions(self):
        p,c,r=self.fixture();p['devices'][0]['profile']['adaptation']={'anchorLayout':{'constraints':[{'type':'position','targets':['a'],'x':0}]}}
        m={'a':{'bbox':{'x':50.,'y':10.,'w':80.,'h':80.},'confidence':1},'b':{'bbox':{'x':210.,'y':10.,'w':80.,'h':80.},'confidence':1}}
        rows,report=solve(p,c,r,m,{'relations':[]},[])
        self.assertAlmostEqual(next(row['bbox']['x'] for row in rows if row['id']=='a'),0)
        self.assertIn('position',report['protectedRelations'])

    def test_unknown_explicit_container_rejected_instead_of_dropped(self):
        p,c,r=self.fixture();p['devices'][0]['profile']['adaptation']={'anchorLayout':{'groups':[{'id':'authored'}]}}
        with self.assertRaises(ValueError):solve(p,c,r,{}, {'relations':[]},[])

    def test_follower_outside_component_box_adds_resource_separation_cut(self):
        p,c,r=self.fixture()
        p['components']=[{'id':x,'role':'content'} for x in ['a','b']]
        p['model']={'nodes':{u:{'tag':'Rectangle','attributes':{},'parent':None,'children':[]} for u in ['na','nb']},'variables':{}}
        p.update(nodeOrder=['na','nb'],nodeComponents={'na':'a','nb':'b'},assets={})
        rows=[[0,0,10,180,80,1,-1,'',0],[1,210,10,80,80,1,-1,'',0]]
        p['sourceDevice']['layouts'][0]['rows']=rows;p['devices'][0]['layouts'][0]['rows']=rows
        m={'a':{'bbox':{'x':100.,'y':10.,'w':80.,'h':80.},'confidence':1},
           'b':{'bbox':{'x':210.,'y':10.,'w':80.,'h':80.},'confidence':1}}
        result,report=solve(p,c,r,m,{'relations':[]},[])
        boxes={row['id']:row['bbox'] for row in result};a=boxes['a'];b=boxes['b']
        self.assertGreater(report['resourceSeparationCuts'],0)
        self.assertLessEqual(a['x']+180*a['w']/80,b['x']+.01)

    def test_compatibility_payload_contains_images_without_logging_secret(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path=Path(tmp)/'image.png';Image.new('RGB',(32,32),'red').save(path)
            response=io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]}).encode())
            with patch('multires_app.reconstruction.providers._open',return_value=response) as transport:
                provider=OpenAICompatible({'base_url':'http://127.0.0.1:8000/v1','model':'vision'})
                self.assertEqual(provider({'inputDigest':'x'},[('source',path)],[]),{'ok':True})
                request=transport.call_args.args[0];body=json.loads(request.data)
                self.assertTrue(body['messages'][1]['content'][2]['image_url']['url'].startswith('data:image/jpeg;base64,'))
                self.assertEqual(request.full_url,'http://127.0.0.1:8000/v1/chat/completions')


class ReconstructionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from multires_app.pipeline.scene_pipeline import create_project
        cls.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);cls.root=Path(cls.tmp.name).resolve()
        archive=cls.root/'mock.hwt'
        text=Path('samples/scene-theme/unlock/lockscreen/manifest.xml').read_text(encoding='utf8')
        text=text.replace('交互场景演示','SCENE DEMO').replace('点击次数：','Count: ').replace('拖动此区域改变面板','Swipe here').replace('详情展开','Details')
        with zipfile.ZipFile(archive,'w') as out:
            out.writestr('unlock/lockscreen/manifest.xml',text)
            for name,color in [('red.png','red'),('blue.png','blue')]:
                buffer=io.BytesIO();Image.new('RGB',(760,700),color).save(buffer,format='PNG');out.writestr('unlock/lockscreen/'+name,buffer.getvalue())
        (cls.root/'inputs.json').write_text(json.dumps({'interaction':{'coordinateSpace':'source'}}))
        create_project(archive,{'resolutionPx':{'width':1080,'height':2400}},
                       {'profiles':[{'id':'tablet','resolutionPx':{'width':2560,'height':1600},'orientations':['landscape']}]},
                       cls.root/'scenes',max_states=8,max_depth=3,inputs_file=cls.root/'inputs.json')

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_mock_reconstruction_exports_and_replays_real_script(self):
        from multires_app.preview.workbench import clone_scene
        root=self.root/'poor';clone_scene(self.root/'scenes',root/'scenes')
        path=root/'scenes/project.json';p=json.loads(path.read_text(encoding='utf8'))
        for cid in p['devices'][0]['layouts'][0]['transforms']:p['devices'][0]['layouts'][0]['transforms'][cid]={'sx':.12,'sy':.12,'tx':0,'ty':0}
        path.write_text(json.dumps(p),encoding='utf8');original=path.read_bytes()
        request=prepare(path,'tablet-landscape','state-000',root/'request')
        result=run(request['request'])
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(result['status'],'compiled',result)
        self.assertTrue(result['readbackPassed']);self.assertTrue(Path(result['package']).is_file())
        self.assertTrue(Path(result['project']).with_name('preview.html').is_file())
        self.assertGreater(next(c['metrics']['score'] for c in result['candidates'] if c['id']==result['selected']),result['baseline']['score'])

    def test_actual_render_is_supplied_to_feedback_and_critic_can_choose(self):
        request=prepare(self.root/'scenes/project.json','tablet-landscape','state-000',self.root/'critic')
        calls=[]
        def provider(req,images,feedback):
            calls.append((images,feedback))
            if not feedback:return propose(req)
            self.assertTrue(any('实际求解渲染' in label and Path(path).is_file() for label,path in images))
            self.assertEqual(feedback[0]['status'],'feasible')
            return {'schema':'layout-reconstruction-advice/1','inputDigest':req['inputDigest'],'stop':True,'candidates':[],
                    'preferredCandidate':feedback[0]['candidate'],'reviewReason':'mock critic validates the review interface, not a real model judgement'}
        result=run(request['request'],provider=provider,compile_package=False)
        self.assertEqual(len(calls),2);self.assertEqual(result['selectionPolicy'],'model-visual-review');self.assertTrue(result['selected'])

    def test_changed_request_or_source_rejected(self):
        request=prepare(self.root/'scenes/project.json','tablet-landscape','state-000',self.root/'stale')
        path=Path(request['request']);p=json.loads(path.read_text());p['state']='invented';path.write_text(json.dumps(p))
        with self.assertRaises(ValueError):run(path)

    def test_unknown_model_output_is_reported_without_mutating_source(self):
        request=prepare(self.root/'scenes/project.json','tablet-landscape','state-000',self.root/'bad-provider')
        path=self.root/'scenes/project.json';before=path.read_bytes()
        result=run(request['request'],provider=lambda *args:{'schema':'wrong'},compile_package=False)
        self.assertEqual(result['status'],'unchanged');self.assertIsNone(result['selected']);self.assertTrue(result['errors'])
        self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':unittest.main()
