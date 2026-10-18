import tempfile
import unittest
from pathlib import Path

from multires_app.layout.aesthetic_layout import datetime_roots, place_datetime, options_for
from multires_app.pipeline.scene_clusters import overlap, transformed_box
from multires_app.engine.scene_engine import load_model,Runtime
from multires_app.pipeline.scene_pipeline import infer_components

ROOT=Path(__file__).resolve().parents[1]
SOURCE={'resolutionPx':{'width':1080,'height':2160}}
TARGET={'resolutionPx':{'width':2560,'height':1600}}
IDENTITY={'sx':1,'sy':1,'tx':0,'ty':0}


def fixture():
    comps=[{'id':'clock','role':'header','semanticRole':'datetime','bounds':{'x':760,'y':440,'w':220,'h':80}},
           {'id':'art','role':'content','bounds':{'x':560,'y':140,'w':500,'h':260}}]
    context={'logicalViewport':{'width':1080,'height':675}}
    return comps,context,{'x':20,'y':20,'w':1040,'h':635}


class AestheticLayoutTests(unittest.TestCase):
    def test_clock_uses_left_top_whitespace_without_changing_scale(self):
        comps,ctx,region=fixture();before={c['id']:dict(IDENTITY) for c in comps}
        result,report=place_datetime(comps,before,SOURCE,TARGET,ctx,region)
        self.assertEqual(report['status'],'moved-to-whitespace')
        clock=transformed_box(comps[0]['bounds'],result['clock'])
        self.assertLess(clock['x'],540);self.assertLess(clock['y'],225)
        self.assertEqual(result['clock']['sx'],1);self.assertEqual(result['art'],before['art'])
        self.assertEqual(overlap(clock,comps[1]['bounds']),0)
        self.assertEqual(before['clock'],IDENTITY)

    def test_full_occupancy_keeps_original_and_reports_reason(self):
        comps,ctx,region=fixture();comps[1]['bounds']=dict(region)
        before={c['id']:dict(IDENTITY) for c in comps}
        result,report=place_datetime(comps,before,SOURCE,TARGET,ctx,region)
        self.assertEqual(result,before);self.assertEqual(report['status'],'kept-no-qualified-whitespace')

    def test_disabled_portrait_and_mixed_group_do_not_detach_clock(self):
        comps,ctx,region=fixture();mapping={c['id']:dict(IDENTITY) for c in comps}
        for target in ({'resolutionPx':{'width':1600,'height':2560}}, {**TARGET,'adaptation':{'aesthetics':{'enabled':False}}}):
            self.assertEqual(place_datetime(comps,mapping,SOURCE,target,ctx,region)[0],mapping)
        ctx['adaptationPlan']={'blocks':[{'members':['clock','art']}]}
        self.assertEqual(place_datetime(comps,mapping,SOURCE,TARGET,ctx,region)[1]['status'],'datetime-is-part-of-mixed-atomic-group')
        with self.assertRaises(ValueError):options_for(SOURCE,{**TARGET,'adaptation':{'aesthetics':{'weights':{'balance':-1}}}})

    def test_bitmap_clock_extracted_with_date_and_button_from_mixed_page(self):
        script='''<Lockscreen screenWidth="1080"><Var name="h" expression="#hour24"/>
        <Group name="page"><Group name="clock"><Image srcExp="'t_'+int(#h/10)+'.png'" x="20" y="20" w="60" h="90"/>
        <Image srcExp="'t_'+int(#minute/10)+'.png'" x="80" y="20" w="60" h="90"/>
        <DateTime x="20" y="120" size="30" format="MM/dd EEEE"/><Button x="20" y="20" w="120" h="90"/></Group>
        <Group name="art"><Image src="dog.png" x="200" y="800" w="700" h="800"/></Group></Group></Lockscreen>'''
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            root=Path(tmp);path=root/'unlock/lockscreen/manifest.xml';path.parent.mkdir(parents=True);path.write_text(script,encoding='utf-8')
            model=load_model(root);roots=datetime_roots(model)
            clock=next(n['id'] for n in model['nodes'].values() if n['attributes'].get('name')=='clock')
            page=next(n['id'] for n in model['nodes'].values() if n['attributes'].get('name')=='page')
            self.assertIn(clock,roots);self.assertNotIn(page,roots)
            comps=infer_components(model,SOURCE,{},{});component=next(c for c in comps if c['id']==clock)
            self.assertEqual(component['semanticRole'],'datetime')
            self.assertTrue(any(model['nodes'][uid]['tag']=='Button' for uid in component['members']))
            rt=Runtime(model,1080,2160);rt.system.update(month=10,date=16,day_of_week=5);rt.cache.clear()
            row=next(e for e in rt.snapshot({}) if e['tag']=='DateTime')
            self.assertEqual(row['text'],'10/16 星期五');self.assertGreater(row['rect']['w'],0)


if __name__=='__main__':unittest.main()
