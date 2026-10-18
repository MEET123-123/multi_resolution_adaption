import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

from multires_app.interaction.interaction_semantics import explore_human, initialize, perform_action, hit_test, replay_human_state, state_signature, analyze_interactions
from multires_app.preview.interaction_server import InteractiveSession
from multires_app.engine.scene_engine import load_model
from multires_app.pipeline.scene_pipeline import SemanticReflow, create_project, export_package, state_guard
from multires_app.engine.scene_engine import Runtime
from multires_app.pipeline.scene_clusters import schedule_device, reuse_transform, describe_layout

ROOT=Path(__file__).resolve().parents[1]
SOURCE={'resolutionPx':{'width':1080,'height':2160}}


class HumanInteractionTests(unittest.TestCase):
    def test_time_derived_visibility_has_distinct_writeback_guards(self):
        xml='''<Lockscreen screenWidth="1080"><Var name="phase" expression="int(#time/2400)%2"/><Var name="count" expression="0"/>
        <Group visibility="eq(#phase,0)"><Rectangle x="100" y="300" w="200" h="200"/></Group>
        <Group visibility="eq(#phase,1)"><Rectangle x="500" y="800" w="300" h="300"/></Group>
        <Button x="100" y="100" w="100" h="100"><Trigger action="up"><VariableCommand name="count" expression="#count+1"/></Trigger></Button></Lockscreen>'''
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            root=Path(tmp);path=root/'source/unlock/lockscreen/manifest.xml';path.parent.mkdir(parents=True);path.write_text(xml,encoding='utf-8')
            profiles={'profiles':[{'id':'phone','resolutionPx':{'width':1080,'height':2160},'orientations':['portrait']}]}
            create_project(root/'source',SOURCE,profiles,root/'out',max_states=8,max_depth=4)
            p=json.loads((root/'out/project.json').read_text(encoding='utf-8'))
            self.assertEqual(len(p['graph']['states']),2);self.assertIn('phase',p['graph']['semantics']['guardVariables'])
            self.assertNotEqual(state_guard(p,p['graph']['states'][0],p['devices'][0]),state_guard(p,p['graph']['states'][1],p['devices'][0]))

    def test_gesture_budget_reports_unexpanded_states(self):
        model=load_model(ROOT/'samples/scene-theme');model['inputs']={'interaction':{'maxExplorationGestures':1}}
        graph=explore_human(model,SOURCE,{},16,5)
        self.assertTrue(graph['coverage']['truncated']);self.assertTrue(graph['coverage']['gestureBudgetExceeded'])
        self.assertEqual(graph['coverage']['attemptedGestureSamples'],1)
        self.assertTrue(graph['coverage']['unexpandedStates'])

    def test_selective_button_geometry_matches_full_snapshot(self):
        model=load_model(ROOT/'samples/scene-theme');rt=initialize(model,1080,2160)
        for button in (e for e in rt.snapshot({}) if e['tag']=='Button'):
            selected=next(e for e in rt.snapshot({},only_ids={button['id']}) if e['id']==button['id'])
            self.assertEqual(selected,button)

    def test_alpha_animation_phase_does_not_create_a_new_behavior_state(self):
        xml='''<Lockscreen screenWidth="1080"><Image src="missing.png" srcid="''" w="100" h="100"><AlphaAnimation loop="true"><Alpha time="0" a="0"/><Alpha time="1000" a="255"/></AlphaAnimation></Image></Lockscreen>'''
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            path=Path(tmp)/'unlock/lockscreen/manifest.xml';path.parent.mkdir(parents=True);path.write_text(xml,encoding='utf-8')
            other=Path(tmp)/'unlock/ucdlockscreen/manifest.xml';other.parent.mkdir();other.write_text('<Lockscreen/>',encoding='utf-8')
            model=load_model(Path(tmp));semantics=analyze_interactions(model)
            a=Runtime(model,1080,2160,time_ms=0);b=Runtime(model,1080,2160,time_ms=500)
            self.assertEqual(state_signature(a,a.snapshot({}),semantics),state_signature(b,b.snapshot({}),semantics))
            self.assertTrue(any('srcid is not numeric' in text for text in a.issues.values()))

    def test_cached_replay_keeps_independent_runtime_and_initial_state(self):
        model=load_model(ROOT/'samples/scene-theme');graph=explore_human(model,SOURCE,{},16,5);cache={}
        for state in graph['states']+list(reversed(graph['states'])):
            actual=replay_human_state(model,1080,2160,state,{},cache=cache)
            expected=replay_human_state(model,1080,2160,state,{})
            self.assertEqual(actual.state,expected.state);self.assertEqual(actual.system,expected.system)
            actual.assign('count',999)
        self.assertNotEqual(cache['[]'].state.get('count'),999)

    def test_long_press_and_double_tap_are_complete_transactions(self):
        xml='''<Lockscreen screenWidth="1080"><Var name="held" expression="0"/><Var name="twice" expression="0"/>
        <Button x="100" y="100" w="200" h="200" longTouchTime="800"><Trigger action="long"><VariableCommand name="held" expression="#held+1"/></Trigger>
        <Trigger action="double"><VariableCommand name="twice" expression="#twice+1"/></Trigger></Button></Lockscreen>'''
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            path=Path(tmp)/'unlock/lockscreen/manifest.xml';path.parent.mkdir(parents=True);path.write_text(xml,encoding='utf-8')
            model=load_model(Path(tmp));rt=initialize(model,1080,2160);owner=next(n['id'] for n in model['nodes'].values() if n['tag']=='Button')
            result=perform_action(rt,{'owner':owner,'kind':'long-press','sample':[.5]*4,'durationMs':1500},{})
            self.assertTrue(result['valid']);self.assertEqual(rt.lookup('held'),1);self.assertEqual(result['timeline'][0]['timeMs'],1520)
            result=perform_action(rt,{'owner':owner,'kind':'double-tap','sample':[.5]*4},{})
            self.assertTrue(result['valid']);self.assertEqual(rt.lookup('twice'),1)

    def test_global_gesture_layer_hit_policy_is_explicit(self):
        model={'nodes':{'small':{'attributes':{}},'global':{'attributes':{}}}}
        elements=[{'id':'small','tag':'Button','visible':True,'rect':{'x':100,'y':100,'w':100,'h':100}},
                  {'id':'global','tag':'Button','visible':True,'rect':{'x':0,'y':0,'w':1080,'h':2160}}]
        viewport={'width':1080,'height':2160}
        self.assertEqual(hit_test(elements,150,150,model,viewport),'small')
        model['nodes']['global']['attributes']['interceptTouch']='true'
        self.assertEqual(hit_test(elements,150,150,model,viewport),'global')
        model['nodes']['global']['attributes'].clear();model['inputs']={'interaction':{'hitPolicy':'strict-z-order'}}
        self.assertEqual(hit_test(elements,150,150,model,viewport),'global')

    def test_complete_gestures_remove_pressed_and_counter_scenes(self):
        model=load_model(ROOT/'samples/scene-theme')
        graph=explore_human(model,SOURCE,{},32,12)
        self.assertEqual(len(graph['states']),2)
        self.assertIn('count',graph['semantics']['dataOnlyControls'])
        self.assertTrue(all(s['overrides'].get('pressed',0)==0 for s in graph['states']))
        tap=next(e for e in graph['transitions'] if e['action']=='tap' and e['changes'].get('count')==1)
        self.assertEqual([event['event'] for event in tap['timeline']],['down','up'])
        self.assertEqual(tap['timeline'][0]['begin'],tap['timeline'][-1]['begin'])
        self.assertGreater(graph['coverage']['mergedWitnesses'],0)

    def test_future_counter_guard_is_not_abstracted(self):
        xml='''<Lockscreen screenWidth="1080"><Var name="hits" expression="0"/><Var name="open" expression="0"/>
        <Group visibility="#open"><Text text="reachable" x="0" y="0"/></Group>
        <Button x="100" y="100" w="200" h="200"><Trigger action="up"><VariableCommand name="hits" expression="#hits+1"/>
        <VariableCommand name="open" expression="1" condition="eq(#hits,3)"/></Trigger></Button></Lockscreen>'''
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            path=Path(tmp)/'unlock/lockscreen/manifest.xml';path.parent.mkdir(parents=True);path.write_text(xml,encoding='utf-8')
            graph=explore_human(load_model(Path(tmp)),SOURCE,{},8,5)
            self.assertIn('hits',graph['semantics']['behaviorControls'])
            self.assertTrue(any(s['overrides'].get('hits')==3 and s['overrides'].get('open')==1 for s in graph['states']))

    def test_occluded_button_cannot_generate_a_reachable_scene(self):
        xml='''<Lockscreen screenWidth="1080"><Var name="flag" expression="0"/><Group visibility="#flag"><Text text="hidden"/></Group>
        <Button name="bottom" x="100" y="100" w="200" h="200"><Trigger action="up"><VariableCommand name="flag" expression="1"/></Trigger></Button>
        <Button name="top" x="100" y="100" w="200" h="200"><Trigger action="up"><VariableCommand name="data" expression="#data+1"/></Trigger></Button></Lockscreen>'''
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            path=Path(tmp)/'unlock/lockscreen/manifest.xml';path.parent.mkdir(parents=True);path.write_text(xml,encoding='utf-8')
            graph=explore_human(load_model(Path(tmp)),SOURCE,{},12,5)
            self.assertTrue(all(not s['overrides'].get('flag',0) for s in graph['states']))
            self.assertGreater(graph['coverage']['rejectedGestures'].get('occluded-by-later-button',0),0)


def cluster_fixture():
    specs=[('clock','header',(30,30,200,100)),('body','content',(100,300,800,1000)),('footer','footer',(30,1900,900,100)),
           ('dialog','popup',(300,600,400,400)),('newpage','content',(0,0,1080,2160))]
    nodes={name:{'id':name,'tag':'Image','attributes':{'name':name},'parent':None,'children':[]} for name,_,_ in specs}
    comps=[{'id':name,'name':name,'role':role,'members':[name],'roots':[name]} for name,role,_ in specs]
    def row(name,asset=0,rect=None):
        index=next(i for i,s in enumerate(specs) if s[0]==name);return [index,*(rect or specs[index][2]),1.,asset,'',0]
    layouts=[{'state':'s0','rows':[row('clock'),row('body'),row('footer')]},
             {'state':'s1','rows':[row('clock'),row('body',1),row('footer')]},
             {'state':'s2','rows':[row('clock'),row('body',rect=(100,300,700,900)),row('footer')]},
             {'state':'s3','rows':[row('clock'),row('body'),row('footer'),row('dialog')]},
             {'state':'s4','rows':[row('newpage')]}]
    for layout in layouts:layout.update(transforms={},controlValues={},overrides={})
    project={'model':{'nodes':nodes,'variables':{}},'components':comps,'nodeOrder':[s[0] for s in specs],
             'nodeComponents':{s[0]:s[0] for s in specs},'assets':{},'assetOrder':['a.png','b.png'],'sourceProfile':SOURCE,
             'graph':{'states':[{'id':l['state']} for l in layouts],'transitions':[{'from':'s0','to':f's{i}'} for i in range(1,5)]}}
    device={'profile':{'id':'tablet'},'viewport':{'width':1080,'height':675},'layouts':layouts}
    return project,device


class ClusteringTests(unittest.TestCase):
    def test_shared_dependency_diamonds_are_analyzed_as_a_set(self):
        project,device=cluster_fixture()
        for i in range(31):
            name='v'+str(i);expression='1' if i==0 else f'#v{i-1}+#v{i-1}'
            var={'attributes':{'name':name,'expression':expression},'children':[]}
            project['model']['variables'][name]=var
        project['model']['nodes']['body']['attributes']['x']='#v30'
        first=describe_layout(project,device['layouts'][0])
        self.assertNotIn('v30',project['_volatileNames'])
        project['model']['variables']['v0']['attributes']['expression']='#time';project.pop('_volatileNames')
        second=describe_layout(project,device['layouts'][0])
        self.assertNotEqual(first['body']['geometry'],second['body']['geometry'])

    def test_similar_layout_reuse_preserves_scale_aspect(self):
        a={'geometry':['new'],'bounds':{'x':0,'y':0,'w':101,'h':100}}
        b={'geometry':['base'],'bounds':{'x':0,'y':0,'w':100,'h':100}}
        result=reuse_transform(a,b,{'sx':.5,'sy':.5,'tx':10,'ty':20})
        self.assertEqual(result['sx'],result['sy'])

    def test_nested_popup_writeback_cancels_parent_transform(self):
        xml='''<Lockscreen screenWidth="1080"><Var name="open" expression="0"/>
        <Group name="body" x="100" y="100"><Rectangle x="0" y="0" w="880" h="1700"/>
        <Button x="0" y="0" w="200" h="100"><Trigger action="up"><VariableCommand name="open" expression="1"/></Trigger></Button>
        <Group name="dialog" x="100" y="500" visibility="#open"><Rectangle x="0" y="0" w="400" h="400"/>
        <Button x="0" y="0" w="400" h="400"><Trigger action="up"><VariableCommand name="open" expression="0"/></Trigger></Button></Group>
        </Group></Lockscreen>'''
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            tmp=Path(tmp);original=tmp/'original.hwt'
            with zipfile.ZipFile(original,'w') as z:z.writestr('unlock/lockscreen/manifest.xml',xml)
            out=tmp/'project';profiles={'profiles':[{'id':'tablet','device':'Tablet','resolutionPx':{'width':2400,'height':1600},'orientations':['landscape']}]}
            create_project(original,SOURCE,profiles,out,max_states=12,max_depth=5)
            project=json.loads((out/'project.json').read_text(encoding='utf-8'));device=project['devices'][0]
            self.assertEqual([l['layoutRequest']['kind'] for l in device['layouts']],['full-reflow','overlay'])
            package=tmp/'adapted.hwt';export_package(out/'project.json',device['profile']['id'],package);model=load_model(package)
            for layout in device['layouts']:
                actual={e['id']:e for e in Runtime(model,1080,720,layout['overrides']).snapshot({})}
                for row in layout['rows']:
                    uid=project['nodeOrder'][row[0]];transform=layout['transforms'][project['nodeComponents'][uid]]
                    self.assertAlmostEqual(actual[uid]['rect']['x'],row[1]*transform['sx']+transform['tx'],places=2)
                    self.assertAlmostEqual(actual[uid]['rect']['y'],row[2]*transform['sy']+transform['ty'],places=2)

    def test_four_strategies_and_fixed_component_invariants(self):
        project,device=cluster_fixture()
        class Recording(SemanticReflow):
            def __init__(self):self.requests=[]
            def layout_scene(self,components,source,target,context):
                self.requests.append((context['layoutRequest']['kind'],[c['id'] for c in components]))
                return super().layout_scene(components,source,target,context)
        algorithm=Recording();result=schedule_device(project,device,algorithm)
        self.assertEqual([l['layoutRequest']['kind'] for l in device['layouts']],['full-reflow','reuse','local-reflow','overlay','full-reflow'])
        baseline=device['layouts'][0]['transforms']
        self.assertEqual(device['layouts'][1]['transforms'],baseline)
        for cid in ('clock','footer'):self.assertEqual(device['layouts'][2]['transforms'][cid],baseline[cid])
        for cid in baseline:self.assertEqual(device['layouts'][3]['transforms'][cid],baseline[cid])
        self.assertIn(('local-reflow',['body']),algorithm.requests)
        self.assertIn(('overlay',['dialog']),algorithm.requests)
        self.assertEqual(result['summary']['algorithmCalls'],4)
        self.assertLess(result['summary']['submittedComponents'],result['summary']['naiveComponentRequests'])

    def test_live_runtime_preserves_concrete_counter_across_reused_layouts(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            out=Path(tmp)
            profiles={'profiles':[{'id':'phone','device':'Phone','resolutionPx':{'width':1080,'height':2160},'orientations':['portrait']}]}
            create_project(ROOT/'samples/scene-theme',SOURCE,profiles,out,max_states=12,max_depth=5)
            project=json.loads((out/'project.json').read_text(encoding='utf-8'))
            session=InteractiveSession(project,'phone-portrait')
            owner=next(n['id'] for n in project['model']['nodes'].values() if n['attributes'].get('name')=='switch')
            for _ in range(12):result=session.gesture({'owner':owner,'kind':'tap','gesture':'center','sample':[.5,.5,.5,.5]})
            self.assertEqual(session.runtime.state['count'],12)
            self.assertEqual(session.runtime.state['pressed'],0)
            self.assertEqual(result['layout']['layoutRequest']['kind'],'reuse')
            self.assertEqual(len(project['graph']['states']),2)
            session.back();self.assertEqual(session.runtime.state['count'],11)
            before=json.dumps(session.project,sort_keys=True);old_id=session.state_id
            with patch('multires_app.preview.interaction_server.schedule_device',side_effect=ValueError('plugin rejected layout')):
                with self.assertRaises(ValueError):session.gesture({'owner':owner,'kind':'tap','sample':[.5]*4})
            self.assertEqual(session.runtime.state['count'],11);self.assertEqual(session.state_id,old_id)
            self.assertEqual(json.dumps(session.project,sort_keys=True),before)


if __name__=='__main__':unittest.main()
