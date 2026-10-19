"""Interaction-only reveal effects must not become initial layout content."""
import json
import tempfile
import unittest
from pathlib import Path

from multires_app.engine.scene_engine import Runtime, VISUAL, load_model
from multires_app.interaction.animation_semantics import scene_animation_names
from multires_app.interaction.interaction_semantics import initialize, perform_action, explore_human, analyze_interactions
from multires_app.media.media_layout import reference, layout_rows
from multires_app.pipeline.scene_pipeline import create_project, export_script, state_guard
from multires_app.pipeline.source_comparison import packed_snapshot
from multires_app.preview.interaction_server import InteractiveSession


class AnimationSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.manifest=self.root/'unlock/lockscreen/manifest.xml'
        self.manifest.parent.mkdir(parents=True)

    def model(self,field='x',name='reveal',reactive=False):
        pose={'x':f'x="#screen_width+20-(#screen_width-100)*#{name}"',
              'alpha':f'x="100" alpha="255*#{name}"',
              'visibility':f'x="100" visibility="gt(#{name},0.5)"'}[field]
        command=f'<Command target="{name}.animation" value="play"/>'
        reaction=f'<Var name="latch" expression="0"><Trigger>{command}</Trigger></Var>' if reactive else ''
        if reactive:command='<VariableCommand name="latch" expression="1"/>'
        self.manifest.write_text(f'''<Lockscreen screenWidth="1080">
        <Var name="{name}"><VariableAnimation repeat="1" delay="100">
        <AniFrame time="0" value="0"/><AniFrame time="400" value="1"/>
        </VariableAnimation></Var>{reaction}
        <Trigger action="init"><Command target="{name}.animation" value="stop"/></Trigger>
        <Rectangle name="art" {pose} y="300" w="100" h="80"/>
        <Button name="swipe" x="0" y="600" w="1080" h="800">
        <Trigger action="up">{command}</Trigger></Button></Lockscreen>''',encoding='utf8')
        return load_model(self.root)

    def action(self,model,direction='left'):
        samples={'left':[.1,.5,.9,.5],'right':[.9,.5,.1,.5],
                 'up':[.5,.1,.5,.9],'down':[.5,.9,.5,.1]}
        return {'owner':next(n['id'] for n in model['nodes'].values() if n['tag']=='Button'),
                'kind':'swipe','sample':samples[direction]}

    def project(self,model):
        return {'model':model,'assets':{},'assetOrder':[],
                'nodeOrder':[uid for uid,n in model['nodes'].items() if n['tag'] in VISUAL],
                'graph':{'semantics':{}},'nodeComponents':{}}

    def test_stopped_state_is_preserved_for_all_reveal_properties_and_directions(self):
        for field in ('x','alpha','visibility'):
            for direction in ('left','right','up','down'):
                with self.subTest(field=field,direction=direction):
                    name='renamed_'+field+'_'+direction
                    model=self.model(field,name);p=self.project(model)
                    rt=initialize(model,1080,2160)
                    uid=next(n['id'] for n in model['nodes'].values() if n['attributes'].get('name')=='art')
                    index=p['nodeOrder'].index(uid)
                    before=packed_snapshot(p,rt,'before')
                    self.assertEqual(rt.lookup(name),0)
                    self.assertIn(index,before['mediaLayout']['inactiveSceneNodeIndices'])
                    self.assertNotIn(index,[r[0] for r in layout_rows(p,before)])
                    self.assertTrue(perform_action(rt,self.action(model,direction),{})['valid'])
                    self.assertEqual(rt.lookup(name),1)
                    after=packed_snapshot(p,rt,'after')
                    self.assertNotIn(index,after['mediaLayout'].get('inactiveSceneNodeIndices',[]))
                    self.assertIn(index,[r[0] for r in layout_rows(p,after)])
                    self.assertIn(name,scene_animation_names(model))

    def test_reactive_animation_is_reached_through_a_user_assignment(self):
        model=self.model('alpha','through_reaction',reactive=True)
        self.assertIn('through_reaction',scene_animation_names(model))
        rt=initialize(model,1080,2160)
        self.assertTrue(perform_action(rt,self.action(model),{})['valid'])
        self.assertEqual(rt.lookup('through_reaction'),1)

    def test_animation_only_change_creates_a_distinct_human_state(self):
        model=self.model('alpha')
        graph=explore_human(model,{'resolutionPx':{'width':1080,'height':2160}},{},8,3)
        self.assertEqual(len(graph['states']),2)
        self.assertIn('reveal',graph['semantics']['behaviorControls'])
        self.assertTrue(any(s['behaviorValues']['reveal']==1 for s in graph['states']))

    def test_autonomous_transparent_intro_is_not_an_interaction_waiting_state(self):
        self.model('alpha')
        xml=self.manifest.read_text(encoding='utf8').replace('alpha="255*#reveal"','alpha="255*(1-#reveal)"')
        xml=xml.replace('w="100" h="80"/>','''w="100" h="80">
        <AlphaAnimation repeat="1"><Alpha time="0" a="0"/><Alpha time="500" a="255"/></AlphaAnimation>
        </Rectangle>''')
        self.manifest.write_text(xml,encoding='utf8');model=load_model(self.root);p=self.project(model)
        rt=initialize(model,1080,2160);ref=reference(rt,{},p['nodeOrder'],[])
        index=next(i for i,uid in enumerate(p['nodeOrder']) if model['nodes'][uid]['attributes'].get('name')=='art')
        self.assertNotIn(index,ref.get('inactiveSceneNodeIndices',[]))
        self.assertIn(index,[r[0] for r in ref['rows']])

    def test_an_autonomous_group_with_a_button_does_not_become_a_human_animation(self):
        self.manifest.write_text('''<Lockscreen><Var name="entry"><VariableAnimation repeat="1">
        <AniFrame time="0" value="1000"/><AniFrame time="500" value="0"/></VariableAnimation></Var>
        <Group y="#entry"><Rectangle w="100" h="100"/>
        <Button w="100" h="100"><Trigger action="up"><VariableCommand name="counter" expression="#counter+1"/>
        </Trigger></Button></Group></Lockscreen>''',encoding='utf8')
        model=load_model(self.root)
        self.assertNotIn('entry',scene_animation_names(model))
        graph=explore_human(model,{'resolutionPx':{'width':1080,'height':2160}},{},8,3)
        self.assertEqual(len(graph['states']),1)

    def test_play_starts_at_the_event_time_and_respects_delay(self):
        model=self.model();rt=initialize(model,1080,2160)
        trigger=next(t for t in model['triggers'] if t['action']=='up')
        rt.execute(trigger);start=rt.time_ms
        self.assertEqual(rt.state['__animations']['reveal']['started'],start)
        for elapsed,expected in ((0,0),(50,0),(100,0),(300,.5),(500,1)):
            rt.time_ms=start+elapsed;rt.system['time']=rt.time_ms;rt.cache.clear()
            self.assertAlmostEqual(rt.lookup('reveal'),expected)

    def test_native_export_and_media_preview_keep_the_current_human_scene(self):
        self.model()
        out=self.root/'out'
        create_project(self.root,{'resolutionPx':{'width':1080,'height':2160}},
                       {'profiles':[{'id':'wide','resolutionPx':{'width':1600,'height':1000},
                                     'baseOrientation':'landscape','orientations':['landscape']}]},
                       out,max_states=8,max_depth=3)
        p=json.loads((out/'project.json').read_text(encoding='utf8'));d=p['devices'][0]
        states={s['id']:s for s in p['graph']['states']}
        self.assertNotEqual(state_guard(p,states[d['layouts'][0]['state']],d),
                            state_guard(p,states[d['layouts'][1]['state']],d))
        adapted_root=self.root/'native';target=adapted_root/'unlock/lockscreen/manifest.xml'
        target.parent.mkdir(parents=True);export_script(p,d['profile']['id'],target)
        adapted=load_model(adapted_root)
        initial=initialize(adapted,1080,675)
        art=next(e for e in initial.snapshot({}) if e['name']=='art')
        self.assertGreaterEqual(art['rect']['x'],1080)
        session=InteractiveSession(p,d['profile']['id'])
        session.gesture(self.action(session.model))
        capture=session.capture_state()
        preview=session.media_preview(1200,200)
        uid=next(n['id'] for n in p['model']['nodes'].values() if n['attributes'].get('name')=='art')
        index=p['nodeOrder'].index(uid)
        positions=[next(r[1] for r in f['target']['rows'] if r[0]==index)
                   for f in preview['processFrames'][:-1]]
        self.assertTrue(all(x==positions[0] for x in positions))
        self.assertEqual(session.capture_state(),capture)

    def test_waiting_pose_stays_below_a_taller_target_canvas(self):
        self.model()
        xml=self.manifest.read_text(encoding='utf8').replace(
            'x="#screen_width+20-(#screen_width-100)*#reveal" y="300"',
            'x="100" y="#screen_height+20-(#screen_height-300)*#reveal"')
        xml=xml.replace('#screen_height','2160')
        self.manifest.write_text(xml,encoding='utf8');out=self.root/'tall'
        create_project(self.root,{'resolutionPx':{'width':1080,'height':2160}},
                       {'profiles':[{'id':'tall','resolutionPx':{'width':1080,'height':3200},
                                     'orientations':['portrait']}]},out,max_states=8,max_depth=3)
        p=json.loads((out/'project.json').read_text(encoding='utf8'));d=p['devices'][0]
        native_root=self.root/'native';path=native_root/'unlock/lockscreen/manifest.xml'
        path.parent.mkdir(parents=True);export_script(p,d['profile']['id'],path)
        rt=initialize(load_model(native_root),1080,3200)
        art=next(e for e in rt.snapshot({}) if e['name']=='art')
        self.assertGreaterEqual(art['rect']['y'],3200)

    def test_waiting_poses_share_one_native_affine_for_the_same_human_guard(self):
        from multires_app.media.media_layout import retain_waiting_components
        from multires_app.pipeline.scene_pipeline import compiled_transform
        def row(y):return [0,10,y,20,20,1,-1,'',0]
        model={'nodes':{'art':{'tag':'Rectangle','attributes':{},'children':[],'parent':None}},
               'variables':{},'controls':[],'triggers':[]}
        layouts=[{'state':sid,'controlValues':{},'rows':[row(y)],'transforms':{},
                  'mediaLayout':{'nodeIndices':[0],'rows':[],'inactiveSceneNodeIndices':[0]}}
                 for sid,y in (('a',240),('b',245))]
        p={'model':model,'nodeOrder':['art'],'nodeComponents':{'art':'component'},
           'graph':{'states':[{'id':'a'},{'id':'b'}],'semantics':{'behaviorControls':[]}},
           'sourceDevice':{'viewport':{'width':100,'height':200},
                           'layouts':[{'state':sid,'rows':[row(y)]} for sid,y in (('a',250),('b',260))]}}
        device={'profile':{'id':'tall'},'viewport':{'width':100,'height':300},'layouts':layouts}
        retain_waiting_components(p,device)
        self.assertEqual(layouts[0]['transforms'],layouts[1]['transforms'])
        self.assertEqual(compiled_transform(p,device,'component','ty'),'115')

    def test_continuous_navigation_keeps_clock_style_after_return_and_reentry(self):
        self.manifest.write_text('''<Lockscreen screenWidth="1080">
        <Var name="page" expression="0"/><Var name="style" expression="0"/>
        <Var name="destination" expression="0"/>
        <Var name="drawer"><VariableAnimation repeat="1"><AniFrame time="0" value="0"/>
        <AniFrame time="400" value="#destination"/></VariableAnimation></Var>
        <Trigger action="init"><Command target="drawer.animation" value="stop"/></Trigger>
        <Text name="clock" x="540" y="100" align="center" text="09:41" size="60+20*#style"/>
        <Button name="open" x="50" y="600" w="300" h="100" visibility="eq(#page,0)">
        <Trigger action="up"><VariableCommand name="page" expression="1"/>
        <VariableCommand name="destination" expression="1"/><Command target="drawer.animation" value="play"/></Trigger></Button>
        <Group x="#screen_width*(1-#drawer)">
        <Button name="enter-styles" x="50" y="600" w="300" h="100" visibility="eq(#page,1)">
        <Trigger action="up"><VariableCommand name="page" expression="2"/></Trigger></Button>
        <Button name="change-style" x="50" y="800" w="300" h="100" visibility="eq(#page,2)">
        <Trigger action="up"><VariableCommand name="style" expression="1-#style"/></Trigger></Button>
        <Button name="back" x="50" y="1000" w="300" h="100" visibility="eq(#page,2)">
        <Trigger action="up"><VariableCommand name="page" expression="1"/></Trigger></Button>
        <Button name="close" x="50" y="1000" w="300" h="100" visibility="eq(#page,1)">
        <Trigger action="up"><VariableCommand name="page" expression="0"/>
        <VariableCommand name="destination" expression="0"/><Command target="drawer.animation" value="play"/></Trigger></Button>
        </Group></Lockscreen>''',encoding='utf8')
        model=load_model(self.root);owners={n['attributes'].get('name'):n['id'] for n in model['nodes'].values() if n['tag']=='Button'}
        journey=['open','enter-styles','change-style','back','close','open','enter-styles']
        actions=[{'owner':owners[n],'kind':'tap','sample':[.5]*4} for n in journey]
        model['inputs']={'interaction':{'requiredTraces':[{'name':'choose-clock-and-return','actions':actions}]}}
        graph=explore_human(model,{'resolutionPx':{'width':1080,'height':2160}},{},32,8)
        self.assertTrue(graph['coverage']['requiredJourneys'][0]['passed'])
        edges=[e for e in graph['transitions'] if e.get('requiredJourney')]
        states={s['id']:s for s in graph['states']}
        self.assertEqual(states[edges[-1]['to']]['controlValues']['style'],1)
        self.assertEqual(states[edges[-1]['to']]['controlValues']['page'],2)
        self.assertNotEqual(edges[1]['to'],edges[-1]['to'])
        self.assertIn('style',analyze_interactions(model)['behaviorControls'])
        rt=initialize(model,1080,2160)
        for action in actions:self.assertTrue(perform_action(rt,action,{})['valid'])
        clock=next(e for e in rt.snapshot({}) if e['name']=='clock')
        self.assertEqual(clock['size'],80)
        # The complete journey must also survive state-conditioned XML
        # writeback, rather than working only in the source interpreter.
        inputs=self.root/'inputs.json';inputs.write_text(json.dumps(model['inputs']),encoding='utf8')
        out=self.root/'journey'
        create_project(self.root,{'resolutionPx':{'width':1080,'height':2160}},
                       {'profiles':[{'id':'target','resolutionPx':{'width':1600,'height':2560},
                                     'orientations':['portrait']}]},out,max_states=32,max_depth=8,inputs_file=inputs)
        p=json.loads((out/'project.json').read_text(encoding='utf8'));d=p['devices'][0]
        native=self.root/'native';path=native/'unlock/lockscreen/manifest.xml'
        path.parent.mkdir(parents=True);export_script(p,d['profile']['id'],path)
        rt=initialize(load_model(native),d['viewport']['width'],d['viewport']['height'])
        for action in actions:self.assertTrue(perform_action(rt,action,{})['valid'])
        self.assertEqual(rt.lookup('style'),1);self.assertEqual(rt.lookup('page'),2)


if __name__=='__main__':unittest.main()
