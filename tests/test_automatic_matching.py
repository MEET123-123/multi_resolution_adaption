import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image,ImageDraw

from multires_app.preview.automatic_matching import assignment,automatic_matcher,verify
from multires_app.preview.component_refinement import refine_components,partition_button_components


class AutomaticMatchingTests(unittest.TestCase):
    def test_flat_authored_group_partitions_by_complete_hit_footprints(self):
        nodes={'g':{'tag':'Group','children':[]}}
        for u,tag in [('g/a','Image'),('g/b','Image'),('g/left','Button'),('g/right','Button')]:nodes[u]={'tag':tag,'children':[]}
        rows=[[0,10,20,60,60,1],[1,230,20,60,60,1],[2,0,10,80,80,1],[3,220,10,80,80,1]]
        t={'sx':.5,'sy':.5,'tx':10,'ty':15}
        p={'model':{'nodes':nodes},'components':[{'id':'g','name':'flat','role':'content','roots':['g'],'members':list(nodes)}],
            'nodeOrder':['g/a','g/b','g/left','g/right'],'nodeComponents':{u:'g' for u in nodes},
            'sourceDevice':{'viewport':{'width':1000,'height':2000},'layouts':[{'state':'s','rows':rows,'transforms':{}}]},
            'devices':[{'layouts':[{'state':'s','rows':rows,'transforms':{'g':t}}]}]}
        result=partition_button_components(p)
        self.assertEqual(len(result),1);self.assertEqual(len(p['components']),2)
        self.assertEqual(p['nodeComponents']['g/a'],p['nodeComponents']['g/left'])
        self.assertNotEqual(p['nodeComponents']['g/a'],p['nodeComponents']['g/b'])
        self.assertEqual(p['devices'][0]['layouts'][0]['transforms']['g/left'],t)
        self.assertEqual(partition_button_components(p),[])

    def test_featureless_gradient_is_not_a_global_widget_match(self):
        import json
        strip=Image.fromarray(np.repeat(np.arange(100,120,dtype=np.uint8)[:,None,None],100,axis=1).repeat(3,axis=2)).convert('RGBA')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            answer=automatic_matcher({'reference':Image.new('RGB',(240,160),'gray'),'templates':{'gradient':strip},
                'catalog':{'groups':[{'id':'gradient','locked':False}]},'job_directory':Path(tmp)})
            self.assertEqual(answer['matches'],[])
            self.assertEqual(json.loads((Path(tmp)/'automatic-candidates.json').read_text())['uninformativeGroups'],['gradient'])

    def test_assignment_rejects_and_never_duplicates_target(self):
        self.assertEqual(assignment([[.9,.1],[.89,.85],[.1,.2]],.75),[0,1,None])

    def test_monochrome_glyphs_keep_negative_space(self):
        import cv2 as cv
        image=Image.new('RGBA',(48,72));draw=ImageDraw.Draw(image)
        for x in (2,26):draw.rounded_rectangle((x,2,x+18,62),5,fill='#202b32');draw.rectangle((x+6,10,x+12,54),fill=(0,0,0,0))
        target=Image.new('RGB',(150,100),'#e6e9ed');target.paste(image,(62,12),image)
        correct=verify(np.asarray(target),np.asarray(image),[62,12,48,72],cv)
        wrong=verify(np.asarray(target),np.asarray(image),[0,0,48,72],cv)
        self.assertGreater(correct['score'],.95)
        self.assertTrue(wrong is None or wrong['score']<.5)

    def test_code_grounding_reorders_with_no_observations(self):
        rng=np.random.default_rng(17);template=Image.fromarray(rng.integers(0,256,(40,48,3),dtype=np.uint8)).convert('RGBA')
        target=Image.new('RGB',(280,180),'#e0e4ea');target.paste(template,(190,30),template)
        temporary_root=Path(__file__).resolve().parents[1]/'artifacts/test-tmp';temporary_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as tmp:
            answer=automatic_matcher({'reference':target,'templates':{'g0':template},'catalog':{'groups':[{'id':'g0','locked':False}]},'job_directory':Path(tmp),'minimum_confidence':.78})
            self.assertEqual(len(answer['matches']),1)
            x,y,w,h=answer['matches'][0]['bbox']
            self.assertAlmostEqual(x/1000*280,190,delta=3);self.assertAlmostEqual(y/1000*180,30,delta=3)
            self.assertFalse(__import__('json').loads((Path(tmp)/'automatic-candidates.json').read_text())['manualCoordinates'])

    def test_container_split_prefers_local_inferred_physical_binding(self):
        root='r';a='r/a';b='r/b';button='r/a/button';nodes={root:{'tag':'Group','children':[a,b]}}
        for uid in (a,b):nodes[uid]={'tag':'Group','children':[]}
        for uid in (a+'/i1',a+'/i2',b+'/i1',b+'/i2'):nodes[uid]={'tag':'Image','children':[]}
        nodes[button]={'tag':'Button','children':[]}
        binding={'button':button,'anchors':[a+'/i1',b+'/i1'],'status':'inferred'}
        project={'model':{'nodes':nodes,'layoutBindings':{'bindings':[binding]}},'components':[{'id':root,'name':'cards','role':'content','members':list(nodes),'roots':[root]}],
                 'nodeComponents':{u:root for u in nodes},'devices':[{'layouts':[{'transforms':{root:{'sx':1,'sy':1,'tx':2,'ty':3}}}]}]}
        result=refine_components(project)
        self.assertEqual(result['splitContainers'],1);self.assertEqual(binding['anchors'],[a+'/i1'])
        self.assertEqual(project['nodeComponents'][button],project['nodeComponents'][a+'/i1'])
        self.assertEqual(project['devices'][0]['layouts'][0]['transforms'][a]['tx'],2)
        self.assertEqual(refine_components(project)['splitContainers'],0)


if __name__=='__main__':unittest.main()
