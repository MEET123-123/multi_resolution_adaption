import copy
import json
import tempfile
import unittest
import struct
from pathlib import Path
from multires_app.learning.contract import make_request, validate_answer, prompt, SCHEMA, parse_answer
from multires_app.learning.algorithm import RecordingAnchor, LearnedAnchor
from multires_app.learning.dataset import assign_split, export_sft, import_reviews
from multires_app.learning.annotations import extract
from multires_app.learning.catalog import Catalog
from multires_app.learning.dataset import input_layout
from multires_app.learning.shard_model import shard
from multires_app.learning.run_report import build_run_report
from multires_app.evaluation.core import digest
from multires_app.pipeline.scene_clusters import transformed_box
from tests.test_anchor_layout import comp, ctx, SOURCE


class LearningTests(unittest.TestCase):
    def fixture_images(self,root):
        from PIL import Image
        for name in ('source.png','current.png'):
            Image.new('RGB',(16,16),'#24385c').save(root/name)

    def capture(self):
        c=comp('clock',(400,500,280,100));context=ctx(1600,1000)
        baseline=RecordingAnchor().layout_scene([c],SOURCE,{'resolutionPx':{'width':1600,'height':1000}},context)
        return c,context,baseline,context['layoutProposal']

    def test_records_source_only_and_accepts_json(self):
        _,context,_,meta=self.capture();self.assertEqual(meta['status'],'recorded')
        self.assertNotIn('targetBounds',json.dumps(meta['request']))
        answer={'schema':SCHEMA,'groups':{g['id']:[100,100,200,100] for g in meta['request']['groups']}}
        self.assertEqual(parse_answer('```json\n'+json.dumps(answer)+'\n```',meta['request']),answer)

    def test_shape_schema_and_inventory(self):
        _,_,_,meta=self.capture();request=meta['request']
        for b in ([0,0,float('nan'),1],[0,0,0,1],[0,0,True,1],[9000,0,1,1]):
            with self.assertRaises(ValueError):validate_answer({'schema':SCHEMA,'groups':{'g0':b}},request)
        with self.assertRaises(ValueError):validate_answer({'schema':SCHEMA,'groups':{}},request)

    def test_model_cannot_break_center_or_uniform_resource_scale(self):
        c,context,_,meta=self.capture();answer={'schema':SCHEMA,'groups':{'g0':[0,950,800,800]}}
        algorithm=LearnedAnchor({meta['key']:{'answer':answer,'provider':'unit-fixture'}})
        out=algorithm.layout_scene([c],SOURCE,{'resolutionPx':{'width':1600,'height':1000}},ctx(1600,1000))
        b=transformed_box(c['bounds'],out['clock'])
        self.assertAlmostEqual(b['x']+b['w']/2,800,places=4)
        self.assertAlmostEqual(out['clock']['sx'],out['clock']['sy'])
        self.assertGreaterEqual(b['y'],-.01);self.assertLessEqual(b['y']+b['h'],1000.01)
        self.assertEqual(algorithm.events[0]['status'],'proposed')

    def test_missing_or_invalid_predictions_are_counted_fallbacks(self):
        c,_,baseline,meta=self.capture()
        for predictions,expected in [({},'fallback-missing'),({meta['key']:{'answer':{}}},'fallback-invalid')]:
            algorithm=LearnedAnchor(predictions)
            out=algorithm.layout_scene([c],SOURCE,{'resolutionPx':{'width':1600,'height':1000}},ctx(1600,1000))
            self.assertEqual(out,baseline);self.assertEqual(algorithm.events[0]['status'],expected)

    def test_three_label_levels_keep_origin(self):
        p={'model':{'nodes':{'n':{'tag':'Time','attributes':{'x':'#screen_width/2','format':'HH:mm'}}}},
            'components':[{'id':'clock','role':'header'}],'nodeOrder':['n'],'nodeComponents':{'n':'clock'},'assetOrder':[]}
        l={'rows':[[0,400,500,280,100,1,-1,'00:41',40]],'controlValues':{'show':1},'timeMs':300}
        result=extract(p,l,{'groups':[]},{'trace':[{'event':'tap'}]})
        self.assertEqual(result['elementLevel']['elements'][0]['attributes']['x'],'#screen_width/2')
        self.assertEqual(result['elementLevel']['elements'][0]['semantic']['origin'],'script tag')
        self.assertTrue(result['compositionLevel']['interactionPath'])

    def sample(self):
        _,_,_,meta=self.capture();answer={'schema':SCHEMA,'groups':{'g0':[100,100,200,100]}}
        return {'id':'sample','device':'tablet','state':'s','requestKey':meta['key'],'request':meta['request'],'split':'train','family':'same-theme',
            'sourceImage':'source.png','targetImage':'current.png','targetReferenceImage':'desired.png',
            'inputLayout':{'schema':SCHEMA,'groups':{'g0':[50,50,100,100]}},
            'rendering':{'source':{'status':'simulated'},'target':{'status':'simulated'}},'label':{'status':'weak','answer':answer}}

    def test_unreviewed_labels_excluded_and_rejections_override_weak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=self.sample();(root/'samples.jsonl').write_text(json.dumps(s)+'\n',encoding='utf8')
            result=export_sft(root,root/'default');self.assertEqual(result['excluded'],{'unreviewed':1})
            batch={'schema':SCHEMA,'reviewer':'unit-fixture','reviews':[{'id':s['id'],'requestKey':s['requestKey'],'status':'rejected'}]}
            (root/'review.json').write_text(json.dumps(batch),encoding='utf8');import_reviews(root,root/'review.json')
            result=export_sft(root,root/'weak',include_weak=True);self.assertEqual(result['excluded'],{'rejected':1})

    def test_approved_sft_uses_only_input_images_not_desired_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture_images(root);s=self.sample();(root/'samples.jsonl').write_text(json.dumps(s)+'\n',encoding='utf8')
            batch={'schema':SCHEMA,'reviewer':'unit-fixture','reviews':[{'id':s['id'],'requestKey':s['requestKey'],'status':'approved','answer':s['label']['answer']}]}
            (root/'review.json').write_text(json.dumps(batch),encoding='utf8');import_reviews(root,root/'review.json')
            result=export_sft(root,root/'sft',input_images='source-and-current')
            example=json.loads((root/'sft/train.jsonl').read_text(encoding='utf8'))
            self.assertEqual(len(example['images']),2);self.assertNotIn('desired.png',json.dumps(example))
            self.assertEqual(example['messages'][1]['content'].count('<image>'),2)

    def test_stale_or_invented_labels_rejected_before_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=self.sample();(root/'samples.jsonl').write_text(json.dumps(s)+'\n',encoding='utf8')
            batch={'schema':SCHEMA,'reviewer':'unit-fixture','reviews':[{'id':s['id'],'requestKey':'wrong','status':'approved','answer':s['label']['answer']}]}
            (root/'review.json').write_text(json.dumps(batch),encoding='utf8')
            with self.assertRaises(ValueError):import_reviews(root,root/'review.json')
            self.assertFalse((root/'reviews.jsonl').exists())

    def test_family_split_never_depends_on_orientation_or_state(self):
        self.assertEqual(assign_split('same-theme'),assign_split('same-theme'))
        self.assertIn(assign_split('same-theme'),{'train','validation','test'})

    def test_bulk_cached_reviews_have_optimistic_concurrency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=self.sample();(root/'samples.jsonl').write_text(json.dumps(s)+'\n',encoding='utf8')
            catalog=Catalog(root);page=catalog.page(limit=1)
            self.assertEqual(page['total'],1);self.assertEqual(page['samples'][0]['baseRevision'],0)
            row={'id':s['id'],'requestKey':s['requestKey'],'baseRevision':0,'status':'approved','answer':s['label']['answer'],
                 'annotationEdits':{'groupSemantics':{'g0':'datetime'},'sceneIntent':'mock lockscreen'}}
            result=catalog.append({'schema':SCHEMA,'reviewer':'unit-fixture','reviews':[row]})
            self.assertEqual(result['revisions'][s['id']],1)
            self.assertEqual(catalog.page(pending=True)['total'],0)
            with self.assertRaises(RuntimeError):catalog.append({'schema':SCHEMA,'reviewer':'unit-fixture','reviews':[row]})
            annotated=input_layout(s,catalog.sample(s['id'])['latestReview'])
            self.assertEqual(annotated['compositionLevel']['sceneIntent'],'mock lockscreen')
            self.assertNotIn('sceneIntent',s['inputLayout'])

    def test_synthetic_opt_in_does_not_mark_mock_as_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture_images(root);s=self.sample();s['label']['status']='synthetic';(root/'samples.jsonl').write_text(json.dumps(s)+'\n',encoding='utf8')
            result=export_sft(root,root/'sft',include_synthetic=True)
            self.assertEqual(result['counts'],{'train':1});self.assertFalse(result['includeWeak'])
            index=json.loads((root/'sft/sample-index.jsonl').read_text(encoding='utf8'))
            self.assertEqual(index['origin'],'synthetic')

    def test_sft_excludes_missing_or_external_input_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=self.sample();s['label']['status']='synthetic'
            for i,name in enumerate(('missing.png','../external.png')):
                s['sourceImage']=name
                (root/'samples.jsonl').write_text(json.dumps(s)+'\n',encoding='utf8')
                result=export_sft(root,root/('sft-'+str(i)),include_synthetic=True,image_root='/srv/data')
                self.assertEqual(result['excluded'],{'missingOrInvalidInputImage':1})
                self.assertEqual(result['counts'],{})

    def test_sharding_keeps_tensor_data_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'source';src.mkdir()
            header={'a':{'dtype':'I16','shape':[4],'data_offsets':[0,8]},'b':{'dtype':'I16','shape':[4],'data_offsets':[8,16]}}
            encoded=json.dumps(header).encode();payload=bytes(range(16))
            (src/'model.safetensors').write_bytes(struct.pack('<Q',len(encoded))+encoded+payload)
            shard(src,root/'sharded',max_bytes=8)
            index=json.loads((root/'sharded/model.safetensors.index.json').read_text(encoding='utf8'))
            for name,expected in [('a',payload[:8]),('b',payload[8:])]:
                data=(root/'sharded'/index['weight_map'][name]).read_bytes();length=struct.unpack('<Q',data[:8])[0]
                self.assertEqual(data[8+length:],expected)

    def test_run_report_distinguishes_valid_output_from_pending_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'dataset').mkdir();s=self.sample()
            s.update(project=str(root/'projects/chusheng/scenes/project.json'),annotations='annotations/sample.json',
                     labels={'trace':[],'script':'labels/cache.json'})
            (root/'dataset/samples.jsonl').write_text(json.dumps(s)+'\n',encoding='utf8')
            (root/'inputs.json').write_text(json.dumps({'themes':[{'id':'chusheng','name':'<theme>'}]}),encoding='utf8')
            (root/'prepare-result.json').write_text(json.dumps({'themes':[{'id':'chusheng','name':'<theme>',
                'states':1,'components':1,'coverage':{}}]}),encoding='utf8')
            (root/'lora-predictions.json').write_text(json.dumps({s['requestKey']:{'status':'valid','answer':s['label']['answer']}}),encoding='utf8')
            result=build_run_report(root);page=(root/'index.html').read_text(encoding='utf8')
            self.assertEqual(result['valid'],1);self.assertTrue(result['complete'])
            self.assertIn('正在等待布局评价',page)
            self.assertNotIn('模型提案未通过协议检查',page)
            self.assertIn('&lt;theme&gt;',page);self.assertNotIn('<theme>',page)


if __name__=='__main__':unittest.main()
