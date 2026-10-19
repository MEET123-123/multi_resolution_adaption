"""Click-gated pages underneath independent vertical unlock handlers."""
import tempfile
import unittest
from pathlib import Path

from multires_app.interaction.interaction_semantics import explore_human, initialize, perform_action, hit_test, swipe_axes
from multires_app.engine.scene_engine import load_model


XML='''<Lockscreen screenWidth="1080">
<Var name="guide" expression="1"/><Var name="page" expression="0"/>
<Var name="horizontal" expression="#touch_begin_x-#touch_x"/>
<Group visibility="eq(#page,0)"><Rectangle x="10" y="10" w="100" h="100"/></Group>
<Group visibility="eq(#page,1)"><Rectangle x="400" y="10" w="100" h="100"/></Group>
<Button name="next" x="0" y="0" w="#screen_width" h="#screen_height" visibility="eq(#guide,0)*eq(#page,0)">
 <Trigger action="up"><VariableCommand name="page" expression="1" condition="ge(#horizontal,210)"/></Trigger>
</Button>
<Button name="previous" x="0" y="0" w="#screen_width" h="#screen_height" visibility="eq(#guide,0)*eq(#page,1)">
 <Trigger action="up"><VariableCommand name="page" expression="0" condition="lt(#horizontal,-210)"/></Trigger>
</Button>
<Button name="dismiss" x="850" y="228" w="180" h="180" visibility="eq(#guide,1)">
 <Trigger action="click"><VariableCommand name="guide" expression="0"/></Trigger>
</Button>
<Button name="unlock" x="0" y="0" w="#screen_width" h="#screen_height">
 <Trigger action="up"><ExternCommand command="unlock" condition="gt(abs(#touch_begin_y-#touch_y),250)"/></Trigger>
</Button></Lockscreen>'''


class SwipePageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        path=Path(self.tmp.name)/'unlock/lockscreen/manifest.xml'
        path.parent.mkdir(parents=True);path.write_text(XML,encoding='utf-8')
        self.model=load_model(Path(self.tmp.name))
        self.owners={n['attributes']['name']:n['id'] for n in self.model['nodes'].values() if n['tag']=='Button'}

    def action(self,name,kind='tap',sample=None):
        return {'owner':self.owners[name],'kind':kind,'sample':sample or [.5]*4}

    def test_click_closes_guide_and_swipes_reach_both_pages_and_return(self):
        rt=initialize(self.model,1080,2400)
        result=perform_action(rt,self.action('dismiss'),{})
        self.assertTrue(result['valid']);self.assertEqual(rt.lookup('guide'),0)
        self.assertEqual([t['event'] for t in result['timeline']],['click'])
        self.assertTrue(perform_action(rt,self.action('next','swipe',[.1,.5,.9,.5]),{})['valid'])
        self.assertEqual(rt.lookup('page'),1);self.assertFalse(rt.events)
        self.assertTrue(perform_action(rt,self.action('previous','swipe',[.9,.5,.1,.5]),{})['valid'])
        self.assertEqual(rt.lookup('page'),0)
        result=perform_action(rt,self.action('unlock','swipe',[.5,.1,.5,.9]),{})
        self.assertTrue(result['valid']);self.assertEqual(rt.events[-1]['attributes']['command'],'unlock')

    def test_swipe_does_not_emit_click(self):
        rt=initialize(self.model,1080,2400)
        perform_action(rt,self.action('dismiss','swipe',[.1,.5,.9,.5]),{})
        self.assertEqual(rt.lookup('guide'),1)

    def test_required_journey_precedes_exploration_budget_and_is_hit_tested(self):
        actions=[self.action('dismiss'),self.action('next','swipe',[.1,.5,.9,.5]),
                 self.action('previous','swipe',[.9,.5,.1,.5])]
        self.model['inputs']={'interaction':{'maxExplorationGestures':1,
            'requiredTraces':[{'name':'open-return','actions':actions}]}}
        graph=explore_human(self.model,{'resolutionPx':{'width':1080,'height':2400}},{},4,1)
        self.assertEqual(len(graph['states']),3)
        edges=[e for e in graph['transitions'] if e.get('requiredJourney')]
        self.assertEqual(len(edges),3)
        self.assertEqual(edges[-1]['to'],edges[0]['to'])
        self.assertTrue(graph['coverage']['requiredJourneys'][0]['passed'])
        actions[0]=self.action('next','swipe',[.1,.5,.9,.5])
        with self.assertRaisesRegex(ValueError,'button-not-visible'):
            explore_human(self.model,{'resolutionPx':{'width':1080,'height':2400}},{},4,1)

    def test_timestamp_page_pose_survives_media_first_frame_reference(self):
        from multires_app.media.media_layout import reference,page_animation_names
        from multires_app.engine.scene_engine import VISUAL
        xml='''<Lockscreen screenWidth="1080">
        <Var name="phase" expression="min(max(#time-#started,0),500)/500"/>
        <Var name="offset" expression="ifelse(eq(#page,1),1080-1080*#phase,1080*#phase)"/>
        <Group x="#offset"><Rectangle x="10" y="10" w="100" h="100"/></Group>
        <Button x="0" y="200" w="1080" h="200"><Trigger action="up">
        <VariableCommand name="started" expression="#time"/>
        <VariableCommand name="page" expression="1"/>
        </Trigger></Button></Lockscreen>'''
        (Path(self.tmp.name)/'unlock/lockscreen/manifest.xml').write_text(xml,encoding='utf-8')
        model=load_model(Path(self.tmp.name));rt=initialize(model,1080,2400)
        owner=next(n['id'] for n in model['nodes'].values() if n['tag']=='Button')
        perform_action(rt,{'owner':owner,'kind':'tap','sample':[.5]*4},{})
        self.assertEqual(rt.lookup('offset'),0)
        self.assertIn('offset',page_animation_names(model))
        order=[uid for uid,n in model['nodes'].items() if n['tag'] in VISUAL]
        ref=reference(rt,{},order,[])
        self.assertEqual(ref['rows'][0][1],10)
        self.assertFalse(ref.get('inactiveSceneNodeIndices'))

    def test_explorer_follows_guide_page_and_return_with_real_gestures(self):
        graph=explore_human(self.model,{'resolutionPx':{'width':1080,'height':2400}},{},16,5)
        self.assertEqual(len(graph['states']),3)
        self.assertFalse(graph['coverage']['truncated'])
        states={s['id']:s for s in graph['states']}
        forward=next(e for e in graph['transitions'] if e['gesture']=='left' and e['changes'].get('page')==1)
        self.assertEqual(states[forward['to']]['behaviorValues']['page'],1)
        self.assertTrue(any(e['from']==forward['to'] and e['gesture']=='right' and e['changes'].get('page')==0 for e in graph['transitions']))
        self.assertEqual(swipe_axes(self.model,self.owners['next']),['x'])

    def test_explicit_interception_and_strict_order_still_win(self):
        rt=initialize(self.model,1080,2400);rt.assign('guide',0)
        elements=rt.snapshot({},visible_only=True);vp={'width':1080,'height':2400}
        args=(elements,972,1200,self.model,vp)
        self.assertEqual(hit_test(*args,gesture='left'),self.owners['next'])
        self.model['nodes'][self.owners['unlock']]['attributes']['interceptTouch']='true'
        self.assertEqual(hit_test(*args,gesture='left'),self.owners['unlock'])
        self.model['nodes'][self.owners['unlock']]['attributes'].pop('interceptTouch')
        self.model['inputs']={'interaction':{'hitPolicy':'strict-z-order'}}
        self.assertEqual(hit_test(*args,gesture='left'),self.owners['unlock'])

    def test_animated_offscreen_page_is_not_reflowed_into_the_first_page(self):
        import json
        from multires_app.media.media_layout import page_animation_names
        from multires_app.pipeline.scene_pipeline import create_project,export_script,state_guard
        from multires_app.preview.interaction_server import InteractiveSession
        from multires_app.engine.scene_engine import Runtime
        xml=XML.replace('<Var name="page" expression="0"/>','''<Var name="page"><VariableAnimation repeat="1">
          <AniFrame time="0" value="#pageFrom"/><AniFrame time="360" value="#pageTo"/>
          </VariableAnimation></Var>''')
        xml=xml.replace('<Group visibility="eq(#page,1)">','<Group x="#screen_width*(1-#page)">')
        for value in (0,1):
            xml=xml.replace(f'<VariableCommand name="page" expression="{value}" condition="',f'<VariableCommand name="pageFrom" expression="{1-value}"/><VariableCommand name="pageTo" expression="{value}"/><Command target="page.animation" value="play" condition="')
        root=Path(self.tmp.name);lock=root/'unlock/lockscreen/manifest.xml';lock.write_text(xml,encoding='utf-8')
        out=root/'out'
        create_project(root,{'resolutionPx':{'width':1080,'height':2400}},
                       {'profiles':[{'id':'wide','resolutionPx':{'width':1600,'height':1000},'orientations':['landscape']}]},
                       out,max_states=20,max_depth=5)
        p=json.loads((out/'project.json').read_text(encoding='utf-8'));d=p['devices'][0]
        self.assertEqual(page_animation_names(p['model']),{'page'})
        self.assertIn('pageTo',p['graph']['semantics']['behaviorControls'])
        self.assertNotIn('horizontal',p['graph']['semantics']['guardVariables'])
        uid=next(n['id'] for n in p['model']['nodes'].values() if n['tag']=='Rectangle' and n['attributes']['x']=='400')
        cid=p['nodeComponents'][uid];index=p['nodeOrder'].index(uid)
        self.assertIn(index,d['layouts'][0]['mediaLayout']['inactiveSceneNodeIndices'])
        self.assertIn(cid,d['layouts'][0]['waitingComponents'])
        active=next(l for l in d['layouts'] if l['controlValues']['page']==1)
        self.assertIn(cid,active['transforms'])
        import copy
        from multires_app.media.media_layout import reference
        rt=Runtime(p['model'],1080,675,active['overrides'],time_ms=active['timeMs'])
        refs=[]
        for phase in (0,100,200,700,1600):
            probe=copy.deepcopy(rt);probe.time_ms=phase;probe.system['time']=phase;probe.cache.clear()
            for playback in probe.state.get('__animations',{}).values():playback['started']=0
            refs.append(reference(probe,{},p['nodeOrder'],p['assetOrder']))
        self.assertTrue(all(r==refs[0] for r in refs))
        states={s['id']:s for s in p['graph']['states']}
        self.assertNotEqual(state_guard(p,states[active['state']],d),state_guard(p,states['state-000'],d))
        target=root/'adapted/unlock/lockscreen/manifest.xml';target.parent.mkdir(parents=True)
        export_script(p,d['profile']['id'],target);adapted=load_model(root/'adapted')
        for layout in d['layouts']:
            rt=Runtime(adapted,1080,675,layout['overrides'],time_ms=layout['timeMs'])
            rect=next(e['rect'] for e in rt.snapshot({}) if e['id']==uid)
            if layout['controlValues']['page']==0:self.assertGreaterEqual(rect['x'],1080)
            else:self.assertLessEqual(rect['x']+rect['w'],1080)
        session=InteractiveSession(p,d['profile']['id'])
        session.gesture(self.action('dismiss'))
        session.gesture(self.action('next','swipe',[.1,.5,.9,.5]))
        self.assertEqual(session.runtime.lookup('page'),1)
        session.gesture(self.action('previous','swipe',[.9,.5,.1,.5]))
        self.assertEqual(session.runtime.lookup('page'),0)


if __name__=='__main__':unittest.main()
