"""Behavior checks for state transitions, reflow/writeback and ZIP integrity."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from multires_app.engine.scene_engine import Evaluator, ExpressionError, Runtime, load_model, explore
from multires_app.pipeline.scene_pipeline import create_project, export_package, relayout_project

ROOT=Path(__file__).resolve().parents[1]


class SceneFlowTests(unittest.TestCase):
    def test_animation_play_changes_sampled_variable_after_stop(self):
        model=load_model(ROOT/'samples/scene-theme')
        rt=Runtime(model,1080,2160,{'slide':0})
        trigger={'id':'animation-test','commands':[{'id':'play','tag':'Command','attributes':{'target':'slide.animation','value':'play'},'conditions':[]}]}
        rt.execute(trigger)
        self.assertEqual(rt.lookup('slide'),100)

    def test_expression_whitelist_lazy_branch_and_strings(self):
        ev=Evaluator(lambda name:{'x':3}[name])
        self.assertEqual(ev.evaluate("ifelse(eq(#x,3),'n'+int(#x),1/0)"),'n3')
        for expression in ("__import__('os').system('echo fail')","(1).__class__","[x for x in range(3)]"):
            with self.assertRaises(ExpressionError):ev.evaluate(expression)

    def test_state_visibility_and_ordered_down_up(self):
        model=load_model(ROOT/'samples/scene-theme')
        graph=explore(model,{'resolutionPx':{'width':1080,'height':2160}}, {},20,4)
        initial=graph['states'][0]
        down=next(t for t in graph['transitions'] if t['from']==initial['id'] and t['action']=='down')
        up=next(t for t in graph['transitions'] if t['from']==down['to'] and t['owner']==down['owner'] and t['action']=='up')
        target=next(s for s in graph['states'] if s['id']==up['to'])
        self.assertEqual(target['overrides']['panel'],1)
        self.assertEqual(target['overrides']['count'],1)
        self.assertEqual(target['overrides']['pressed'],0)
        self.assertEqual(len([n for n in model['nodes'].values() if n['attributes'].get('name')=='art']),2)
        before=Runtime(model,1080,2160,initial['overrides']).snapshot({})
        after=Runtime(model,1080,2160,target['overrides']).snapshot({})
        before_art=[e['id'] for e in before if e['visible'] and e['name']=='art']
        after_art=[e['id'] for e in after if e['visible'] and e['name']=='art']
        self.assertNotEqual(before_art,after_art)

    def test_export_matches_reflow_and_preserves_archive(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='scene-flow-') as tmp:
            tmp=Path(tmp);source=tmp/'original.hwt'
            with zipfile.ZipFile(source,'w') as z:
                z.write(ROOT/'samples/scene-theme/unlock/lockscreen/manifest.xml','unlock/lockscreen/manifest.xml')
                z.writestr('unrelated.bin',b'unchanged payload\x00')
            original=source.read_bytes()
            profile={'resolutionPx':{'width':1080,'height':2160}}
            targets={'profiles':[{'id':'tablet','device':'Tablet','resolutionPx':{'width':2400,'height':1600},'baseOrientation':'landscape','orientations':['landscape']}]}
            out=tmp/'project'
            create_project(source,profile,targets,out,max_states=20,max_depth=4)
            previous=json.loads((out/'project.json').read_text(encoding='utf-8'))
            relayout_project(out/'project.json',str(ROOT/'samples/custom_scene_layout.py')+':MySceneLayout')
            project=json.loads((out/'project.json').read_text(encoding='utf-8'))
            self.assertEqual(project['algorithm'],'my-scene-layout')
            self.assertEqual(project['graph'],previous['graph'])
            self.assertEqual([r[0] for r in project['devices'][0]['layouts'][0]['rows']],
                             [r[0] for r in previous['devices'][0]['layouts'][0]['rows']])
            target='tablet-landscape';output=tmp/'adapted.hwt'
            export_package(out/'project.json',target,output)
            self.assertEqual(source.read_bytes(),original)
            with zipfile.ZipFile(output) as z:self.assertEqual(z.read('unrelated.bin'),b'unchanged payload\x00')
            adapted=load_model(output)
            state=project['graph']['states'][0];device=project['devices'][0]
            rt=Runtime(adapted,device['viewport']['width'],device['viewport']['height'],state['overrides'])
            layout=device['layouts'][0]
            checked=0
            for scene in device['layouts']:
                sampled=Runtime(adapted,device['viewport']['width'],device['viewport']['height'],scene['overrides'])
                rendered={e['id']:e for e in sampled.snapshot({})}
                for row in scene['rows']:
                    uid=project['nodeOrder'][row[0]];cid=project['nodeComponents'][uid]
                    t=scene['transforms'].get(cid)
                    if not t:continue
                    actual=rendered[uid]['rect']
                    self.assertAlmostEqual(actual['x'],row[1]*t['sx']+t['tx'],places=2)
                    self.assertAlmostEqual(actual['y'],row[2]*t['sy']+t['ty'],places=2)
                    checked+=1
            self.assertGreater(checked,3)
            # Exported gesture coordinates map back to source coordinates.
            drag=next(t for t in adapted['triggers'] if adapted['nodes'].get(t['owner'],{}).get('attributes',{}).get('name')=='drag')
            source_drag=next(n for n in project['model']['nodes'].values() if n['attributes'].get('name')=='drag')
            transform=layout['transforms'][project['nodeComponents'][source_drag['id']]]
            rt.execute(drag,{'touch_begin_x':transform['tx']+200*transform['sx'],'touch_x':transform['tx']+300*transform['sx'],'touch_begin_y':0,'touch_y':0})
            self.assertEqual(rt.state['panel'],1)


if __name__=='__main__':unittest.main()
