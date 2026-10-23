import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('training_pairs', Path(__file__).resolve().parents[1] / 'build_training_pairs.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)


class TrainingPairsTests(unittest.TestCase):
    def request(self):
        return {'schema':'multires-layout/1', 'sourceViewport':{'width':1080,'height':2400},
                'safeRegion':{'x':0,'y':0,'w':1080,'h':675}, 'targetViewport':{'width':1080,'height':675},
                'requestKind':'full-reflow', 'mediaPolicy':'preserve timeline', 'theme':'unneeded identity',
                'groups':[{'id':'g0','sourceBox':[100,200,300,40],'roles':[None,'clock'], 'memberCount':5,
                           'container':'rigid','anchor':'top_center','centerAxes':['x'],'edges':{},'fixed':False,
                           'sourceHints':[{'w':'do not flatten complete node hints'}]}]}

    def test_input_is_source_only_without_hint_dump(self):
        request=self.request(); original=copy.deepcopy(request)
        clean, layout=builder.compact_input(request)
        self.assertEqual(layout['groups'],{'g0':[100,200,300,40]})
        self.assertNotIn('sourceHints',clean['groups'][0]);self.assertNotIn('theme',clean)
        self.assertEqual(clean['groups'][0]['roles'],['clock']);self.assertEqual(original,request)

    def test_template_family_prevents_variant_leakage(self):
        metadata=[{'sourceSha256':name,'scriptSignature':signature,'templateTokens':tokens}
                  for name,signature,tokens in [('a','same',['a','b']),('b','same',['c']),('c','different',['z'])]]
        families=builder.families(metadata)
        self.assertEqual(families['a'],families['b']);self.assertNotEqual(families['a'],families['c'])
        splits=builder.split_families(['f'+str(i) for i in range(12)])
        self.assertEqual(set(splits.values()),{'train','validation','test'})
        self.assertEqual(splits,builder.split_families(reversed(list(splits))))

    def test_bad_render_or_unknown_geometry_never_passes_gate(self):
        pair={'groupCount':4,'rendering':{'source':{'status':'simulated'},'target':{'status':'simulated'}},'checks':[{'status':'pass'}]}
        self.assertIsNone(builder.exclusion(pair))
        pair['checks'][0]['status']='unknown';self.assertIsNotNone(builder.exclusion(pair))
        pair['checks'][0]['status']='pass';pair['rendering']['source']['status']='incomplete';self.assertIsNotNone(builder.exclusion(pair))


if __name__=='__main__': unittest.main()
