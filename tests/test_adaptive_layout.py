import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from multires_app.layout.adaptive_layout import AdaptiveThemeLayout, classify_change, group_blocks, safe_region
from multires_app.pipeline.scene_clusters import overlap, transformed_box
from multires_app.engine.scene_engine import Runtime, load_model
from multires_app.pipeline.scene_pipeline import create_project, export_package, load_scene_algorithm, source_canvas_profile
from multires_app.tools.theme_adapter import adapt_theme, choose_recipe

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {'resolutionPx': {'width': 1080, 'height': 2160}}


def component(name, role, bounds, **extras):
    return {'id':name, 'name':name, 'role':role, 'visible':True,
            'bounds':dict(zip(('x','y','w','h'),bounds)), **extras}


def context(w=1080, h=2160, **extras):
    return {'logicalViewport':{'width':w,'height':h}, 'model':{'root':{'screenWidth':'1080'}}, **extras}


class AdaptivePolicyTests(unittest.TestCase):
    def test_pixel_density_does_not_trigger_reflow(self):
        decision = classify_change(SOURCE, {'resolutionPx':{'width':2160,'height':4320}})
        self.assertEqual(decision['strategy'],'preserve')
        self.assertEqual(decision['aspectChange'],0)
        self.assertEqual(decision['pixelScale'],{'x':2,'y':2})
        self.assertEqual(load_scene_algorithm('adaptive').name,'adaptive-theme-layout-v1')
        physical={'resolutionPx':{'width':2160,'height':4320}}
        normalized=source_canvas_profile({'root':{'screenWidth':'1080'}},physical)
        self.assertEqual(normalized['resolutionPx'],{'width':1080,'height':2160})
        self.assertEqual(physical['resolutionPx']['width'],2160)

    def test_small_change_large_change_rotation_and_override(self):
        for size, expected in [((1080,2280),'preserve'),((1600,2560),'reflow'),((2560,1600),'reflow')]:
            target={'resolutionPx':dict(zip(('width','height'),size))}
            self.assertEqual(classify_change(SOURCE,target)['strategy'],expected)
        target={'resolutionPx':{'width':2560,'height':1600},'adaptation':{'mode':'preserve'}}
        self.assertEqual(classify_change(SOURCE,target)['strategy'],'preserve')
        for bad in [0,-1,float('nan')]:
            with self.assertRaises(ValueError):classify_change(SOURCE,{'resolutionPx':{'width':bad,'height':1600}})
        with self.assertRaises(ValueError):classify_change(SOURCE,{'resolutionPx':{'width':1080,'height':2160},'adaptation':{'aspectThreshold':-1}})

    def test_safe_insets_are_converted_to_engine_coordinates(self):
        target={'resolutionPx':{'width':2160,'height':4320},'safeInsetsPx':{'top':120,'bottom':80}}
        self.assertEqual(safe_region(SOURCE,target,context()),{'x':0,'y':60,'w':1080,'h':2060})
        target['safeInsetsPx']={'top':4400}
        with self.assertRaises(ValueError):safe_region(SOURCE,target,context())

    def test_preserve_keeps_equal_canvas_and_adjusts_responsive_footer_once(self):
        algo=AdaptiveThemeLayout();target={'resolutionPx':{'width':1080,'height':2160}}
        comps=[component('header','header',(40,60,600,150),layout={'anchor':'top'}),component('body','content',(100,500,800,700)),component('footer','footer',(100,1990,800,100),layout={'anchor':'bottom'})]
        output=algo.layout_scene(comps,SOURCE,target,context())
        self.assertTrue(all(t=={'sx':1,'sy':1,'tx':0,'ty':0} for t in output.values()))
        old=copy.deepcopy(comps[-1]['bounds']);comps[-1]['bounds']['y']+=120
        target={'resolutionPx':{'width':1080,'height':2280}}
        ctx=context(h=2280,sourceComponentDescriptors={'footer':{'bounds':old}})
        output=algo.layout_scene(comps,SOURCE,target,ctx)
        self.assertEqual(output['header']['ty'],0)
        self.assertEqual(output['footer']['ty'],0)  # already responsive, no double shift
        self.assertEqual(output['body']['ty'],60)

    def test_preserve_does_not_clamp_intentional_offscreen_art(self):
        comps=[component('art','header',(-100,-60,500,500)),component('label','header',(100,70,100,50))]
        target=copy.deepcopy(SOURCE);ctx=context()
        output=AdaptiveThemeLayout().layout_scene(comps,SOURCE,target,ctx)
        self.assertTrue(all(t=={'sx':1,'sy':1,'tx':0,'ty':0} for t in output.values()))
        self.assertEqual(AdaptiveThemeLayout().review_layout(comps,output,SOURCE,target,ctx),[])

    def test_safe_area_fit_and_whole_viewport_gesture(self):
        target={'resolutionPx':{'width':1080,'height':2160},'safeInsetsPx':{'top':120,'bottom':100}}
        comps=[component('body','content',(0,0,1080,2160)),component('swipe','gesture-layer',(0,0,1080,2160))]
        output=AdaptiveThemeLayout().layout_scene(comps,SOURCE,target,context())
        box=transformed_box(comps[0]['bounds'],output['body'])
        self.assertGreaterEqual(box['y'],120);self.assertLessEqual(box['y']+box['h'],2060)
        self.assertEqual(output['body']['sx'],output['body']['sy'])
        self.assertEqual(output['swipe'],{'sx':1,'sy':1,'tx':0,'ty':0})

    def test_reflow_preserves_stacks_and_places_distinct_cards_in_cells(self):
        comps=[component('hero','header',(50,50,800,400)),component('card1','content',(50,700,430,430)),
               component('label1','content',(90,730,200,50)),component('card2','content',(600,700,430,430)),
               component('card3','content',(50,1200,430,430)),component('footer','footer',(100,1900,800,100))]
        target={'resolutionPx':{'width':2560,'height':1600}}
        output=AdaptiveThemeLayout().layout_scene(comps,SOURCE,target,context(h=675))
        self.assertEqual(output['card1'],output['label1'])
        boxes={c['id']:transformed_box(c['bounds'],output[c['id']]) for c in comps}
        for b in boxes.values():
            self.assertGreaterEqual(b['x'],-.001);self.assertGreaterEqual(b['y'],-.001)
            self.assertLessEqual(b['x']+b['w'],1080.001);self.assertLessEqual(b['y']+b['h'],675.001)
        self.assertEqual(overlap(boxes['card1'],boxes['card2']),0)
        self.assertEqual(overlap(boxes['card1'],boxes['card3']),0)
        self.assertTrue(all(t['sx']==t['sy'] for t in output.values()))

    def test_explicit_region_and_group_are_honored(self):
        target={'resolutionPx':{'width':2560,'height':1600},'adaptation':{'regions':{'hero':[0,0,.4,1],'cards':[.45,0,.55,1]}}}
        comps=[component('a','content',(0,0,200,200),layout={'region':'hero','group':'character'}),
               component('b','content',(400,0,100,100),layout={'region':'hero','group':'character'}),
               component('c','content',(0,300,200,200),layout={'region':'cards'})]
        output=AdaptiveThemeLayout().layout_scene(comps,SOURCE,target,context(h=675))
        self.assertEqual(output['a'],output['b']);self.assertNotEqual(output['a'],output['c'])
        self.assertEqual(len(group_blocks(comps[:2])),1)
        target['adaptation']['regions']['hero']=[0,0,2,1]
        with self.assertRaises(ValueError):AdaptiveThemeLayout().layout_scene(comps,SOURCE,target,context(h=675))


class AdaptiveIntegrationTests(unittest.TestCase):
    def test_workflow_requires_explicit_redesign_and_does_not_overwrite_source(self):
        decision=classify_change(SOURCE,{'resolutionPx':{'width':2560,'height':1600}})
        named=Path('比邻熊 新拟态.hwt')
        self.assertEqual(choose_recipe(named,decision,2560,1600),'generic')
        self.assertEqual(choose_recipe(named,decision,2560,1600,'tablet-preset'),'tablet-preset')
        with self.assertRaises(ValueError):choose_recipe(Path('another.hwt'),decision,2560,1600,'tablet-preset')
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            tmp=Path(tmp);source=tmp/'test.hwt'
            with zipfile.ZipFile(source,'w') as z:
                z.writestr('unlock/lockscreen/manifest.xml','<Lockscreen screenWidth="1080"><Text x="40" y="50" size="60" text="Clock"/></Lockscreen>')
                z.writestr('payload.bin',b'keep')
            before=source.read_bytes()
            target={'resolutionPx':{'width':1080,'height':2280}}
            with self.assertRaises(ValueError):adapt_theme(source,SOURCE,target,source)
            result=adapt_theme(source,SOURCE,target,tmp/'out',max_states=2,max_depth=1)
            self.assertEqual(result['decision']['strategy'],'preserve')
            self.assertTrue(result['sourceUnchanged'])
            with zipfile.ZipFile(result['package']) as z:self.assertEqual(z.read('payload.bin'),b'keep')
            self.assertEqual(source.read_bytes(),before)

    def test_default_auto_policy_writeback_and_source_archive(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            tmp=Path(tmp);source=tmp/'source.hwt'
            script='''<Lockscreen screenWidth="1080"><Var name="page" expression="0"/>
              <Group name="header" x="40" y="60"><Text text="Clock" size="70"/></Group>
              <Group name="content" x="100" y="500"><Image src="a.png" w="800" h="700"/></Group>
              <Group name="footer" x="100" y="#screen_height-170"><Button name="switch" w="800" h="100"><Triggers><Trigger action="up"><VariableCommand name="page" expression="1-#page"/></Trigger></Triggers></Button></Group>
              </Lockscreen>'''
            with zipfile.ZipFile(source,'w') as z:z.writestr('unlock/lockscreen/manifest.xml',script);z.writestr('unchanged.bin',b'original payload')
            before=source.read_bytes()
            targets={'profiles':[{'id':'similar','resolutionPx':{'width':1080,'height':2280},'orientations':['portrait']},
                                 {'id':'tablet','resolutionPx':{'width':2560,'height':1600},'orientations':['landscape']}]}
            report=create_project(source,SOURCE,targets,tmp/'out',max_states=4,max_depth=1)
            self.assertEqual(report['adaptation']['similar-portrait']['strategy'],'preserve')
            self.assertEqual(report['adaptation']['tablet-landscape']['strategy'],'reflow')
            project=json.loads((tmp/'out/project.json').read_text(encoding='utf-8'))
            for device in project['devices']:
                package=tmp/(device['profile']['id']+'.hwt')
                export_package(tmp/'out/project.json',device['profile']['id'],package)
                adapted=load_model(package)
                self.assertTrue(any(c['attributes'].get('name')=='page' for t in adapted['triggers'] for c in t['commands']))
                for layout in device['layouts']:
                    rt=Runtime(adapted,device['viewport']['width'],device['viewport']['height'],layout['overrides'])
                    actual={e['id']:e['rect'] for e in rt.snapshot({})}
                    for row in layout['rows']:
                        uid=project['nodeOrder'][row[0]];t=layout['transforms'][project['nodeComponents'][uid]]
                        expected=transformed_box(dict(zip(('x','y','w','h'),row[1:5])),t)
                        for key in ('x','y','w','h'):self.assertAlmostEqual(actual[uid][key],expected[key],places=2)
                with zipfile.ZipFile(package) as z:self.assertEqual(z.read('unchanged.bin'),b'original payload')
            self.assertEqual(source.read_bytes(),before)


if __name__=='__main__':unittest.main()
