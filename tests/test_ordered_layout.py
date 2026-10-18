import copy
import json
import tempfile
import unittest
from pathlib import Path

from multires_app.layout.adaptive_layout import AdaptiveThemeLayout, classify_change
from multires_app.preview.interaction_server import InteractiveSession
from multires_app.layout.ordered_layout import local_from_source
from multires_app.pipeline.scene_clusters import LayoutConflict, overlap, transformed_box
from multires_app.pipeline.scene_pipeline import create_project

ROOT=Path(__file__).resolve().parents[1]
SOURCE={'resolutionPx':{'width':1080,'height':2160}}
TARGET={'resolutionPx':{'width':2560,'height':1600},'adaptation':{'marginPx':64,'columnGapPx':48,'rowGapPx':32}}


def components():
    return [{'id':str(i),'name':str(i),'role':'content','visible':True,
             'bounds':dict(zip(('x','y','w','h'),b))}
            for i,b in enumerate([(40,40,800,200),(60,500,400,400),(80,1000,600,150),(100,1300,200,100),(120,1700,100,50)])]


def context(**extra):
    return {'logicalViewport':{'width':1080,'height':675},'model':{'root':{'screenWidth':'1080'}},**extra}


class OrderedLayoutTests(unittest.TestCase):
    def test_row_major_two_columns_have_one_scale_and_source_order(self):
        comps=components();ctx=context()
        output=AdaptiveThemeLayout().layout_scene(list(reversed(comps)),SOURCE,TARGET,ctx)
        plan=ctx['adaptationPlan']
        self.assertEqual([(b['members'],b['row'],b['column']) for b in plan['blocks']],
                         [(['0'],0,0),(['1'],0,1),(['2'],1,0),(['3'],1,1),(['4'],2,0)])
        scale=plan['scale'];self.assertEqual(plan['columns'],2)
        self.assertTrue(all(t['sx']==t['sy']==scale for t in output.values()))
        boxes=[transformed_box(c['bounds'],output[c['id']]) for c in comps]
        self.assertAlmostEqual(boxes[0]['w']/boxes[1]['w'],2)
        for i,box in enumerate(boxes):
            self.assertGreaterEqual(box['x'],27);self.assertGreaterEqual(box['y'],27)
            self.assertLessEqual(box['x']+box['w'],1053);self.assertLessEqual(box['y']+box['h'],648)
            for other in boxes[:i]:self.assertEqual(overlap(box,other),0)
        self.assertAlmostEqual(plan['columnGap']*2560/1080,48)
        self.assertAlmostEqual(plan['rowGap']*2560/1080,32)
        self.assertAlmostEqual(plan['blocks'][2]['frame']['y']-sum([plan['blocks'][0]['frame']['y'],plan['blocks'][0]['frame']['h']]),plan['rowGap'])

    def test_column_major_is_explicit_and_uniform(self):
        target=copy.deepcopy(TARGET);target['adaptation']['flow']='column-major';ctx=context()
        output=AdaptiveThemeLayout().layout_scene(components(),SOURCE,target,ctx)
        self.assertEqual([(b['row'],b['column']) for b in ctx['adaptationPlan']['blocks']],[(0,0),(1,0),(2,0),(0,1),(1,1)])
        self.assertEqual(len({t['sx'] for t in output.values()}),1)

    def test_source_anchors_override_responsive_target_order(self):
        comps=components();refs={c['id']:{'bounds':copy.deepcopy(c['bounds'])} for c in comps}
        for c in comps:c['bounds']['y']=2500-c['bounds']['y']
        ctx=context(sourceComponentDescriptors=refs)
        output=AdaptiveThemeLayout().layout_scene(comps,SOURCE,TARGET,ctx)
        self.assertEqual([b['members'][0] for b in ctx['adaptationPlan']['blocks']],['0','1','2','3','4'])
        self.assertLess(transformed_box(comps[0]['bounds'],output['0'])['y'],transformed_box(comps[4]['bounds'],output['4'])['y'])

    def test_explicit_group_keeps_internal_offsets_and_relative_sizes(self):
        comps=components()[:2]
        for c in comps:c['layout']={'group':'art-and-label'}
        ctx=context();output=AdaptiveThemeLayout().layout_scene(comps,SOURCE,TARGET,ctx)
        self.assertEqual(output['0'],output['1'])
        self.assertEqual(len(ctx['adaptationPlan']['blocks']),1)
        a=transformed_box(comps[0]['bounds'],output['0']);b=transformed_box(comps[1]['bounds'],output['1'])
        self.assertAlmostEqual(b['y']-a['y'],460*output['0']['sx'])

    def test_threshold_boundary_and_near_square_rotation_keep_source(self):
        square={'resolutionPx':{'width':1000,'height':1000}}
        self.assertEqual(classify_change(square,{'resolutionPx':{'width':1100,'height':1000},'adaptation':{'aspectThreshold':.1}})['strategy'],'preserve')
        decision=classify_change({'resolutionPx':{'width':1010,'height':1000}},{'resolutionPx':{'width':1000,'height':1010}})
        self.assertTrue(decision['orientationChanged']);self.assertEqual(decision['strategy'],'preserve')

    def test_invalid_spacing_and_no_room_are_explicit(self):
        for key,value in [('rowGapPx',-1),('columnGapPx',float('nan')),('marginPx',-1),('maxScale',0),('flow','masonry')]:
            target=copy.deepcopy(TARGET);target['adaptation'][key]=value
            with self.assertRaises(ValueError):AdaptiveThemeLayout().layout_scene(components(),SOURCE,target,context())
        target=copy.deepcopy(TARGET);target['adaptation']['rowGapPx']=5000
        with self.assertRaises(LayoutConflict):AdaptiveThemeLayout().layout_scene(components(),SOURCE,target,context())

    def test_local_change_keeps_scale_and_authored_source_delta(self):
        old={'x':100,'y':100,'w':100,'h':100};base={'sx':.5,'sy':.5,'tx':10,'ty':20}
        comp={'id':'body','bounds':{'x':150,'y':200,'w':150,'h':100}}
        ctx={'baseDescriptors':{'body':{'bounds':old}},'baseTransforms':{'body':base},
             'baseSourceComponentDescriptors':{'body':{'bounds':old}},
             'sourceComponentDescriptors':{'body':{'bounds':{**old,'x':120,'y':130}}}}
        result=local_from_source([comp],ctx,{'x':0,'y':0,'w':1080,'h':675})['body']
        box=transformed_box(comp['bounds'],result)
        self.assertEqual(result['sx'],.5);self.assertEqual(box,{'x':70,'y':85,'w':75,'h':50})
        ctx['fixedTransforms']={'other':{'sx':1,'sy':1,'tx':0,'ty':0}}
        ctx['allComponentDescriptors']={'other':{'role':'content','bounds':{'x':70,'y':85,'w':75,'h':50}}}
        ctx['sourceComponentDescriptors']['other']={'bounds':{'x':900,'y':900,'w':10,'h':10}}
        with self.assertRaises(LayoutConflict):local_from_source([comp],ctx,{'x':0,'y':0,'w':1080,'h':675})
        with self.assertRaises(LayoutConflict):local_from_source([{'id':'new','bounds':old}],ctx,{'x':0,'y':0,'w':1080,'h':675})

    def test_subsequent_interactions_reuse_previous_component_positions(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            out=Path(tmp)
            source=out/'source';lock=source/'unlock/lockscreen';lock.mkdir(parents=True)
            (lock/'manifest.xml').write_text('''<Lockscreen screenWidth="1080"><Var name="flag" expression="0"/><Var name="count" expression="0"/>
            <Group name="header" x="40" y="80"><Text w="400" h="100" size="40" textExp="'Count '+#count"/></Group>
            <Group name="body" x="100" y="700"><Image w="600" h="600" src="a.png" visibility="eq(#flag,0)"/>
            <Image w="600" h="600" src="b.png" visibility="eq(#flag,1)"/>
            <Button name="switch" w="600" h="600"><Trigger action="up"><VariableCommand name="flag" expression="1-#flag"/><VariableCommand name="count" expression="#count+1"/></Trigger></Button></Group>
            <Group name="footer" x="100" y="2000"><Text text="Footer" w="400" h="100" size="40"/></Group></Lockscreen>''',encoding='utf-8')
            create_project(source,SOURCE,{'profiles':[{'id':'tablet',**TARGET,'orientations':['landscape']}]},out,algorithm_spec='adaptive',max_states=8,max_depth=4)
            project=json.loads((out/'project.json').read_text(encoding='utf-8'))
            session=InteractiveSession(project,'tablet-landscape')
            first=copy.deepcopy(session.device['layouts'][0]['transforms'])
            self.assertEqual(session.device['layouts'][0]['adaptationPlan']['columns'],2)
            owner=next(n['id'] for n in project['model']['nodes'].values() if n['attributes'].get('name')=='switch')
            for _ in range(5):
                result=session.gesture({'owner':owner,'kind':'tap','sample':[.5]*4})
                self.assertEqual(result['layout']['transforms'],first)
            self.assertEqual(session.runtime.lookup('count'),5)


if __name__=='__main__':unittest.main()
