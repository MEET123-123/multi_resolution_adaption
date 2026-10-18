import copy
import json
import tempfile
import unittest
from pathlib import Path

from multires_app.interaction.interaction_ir import compile_interactions,InteractionMachine,expression
from multires_app.engine.scene_engine import load_model,ExpressionError,Runtime
from multires_app.engine.engine_capabilities import capability_report

ROOT=Path(__file__).resolve().parents[1]


class InteractionIRTests(unittest.TestCase):
    def setUp(self):
        self.model=load_model(ROOT/'samples/generic-interactions')
        self.ir=compile_interactions(self.model)
        self.machine=InteractionMachine(json.loads(json.dumps(self.ir)))
        self.ids={n['attributes']['name']:uid for uid,n in self.model['nodes'].items() if n['tag']=='Button'}

    def tap(self,name):return self.machine.dispatch('pointer.up',self.ids[name])

    def detail(self):
        for _ in range(3):self.tap('next')

    def test_continuous_click_nested_return_preserves_choice_and_history_branches(self):
        self.tap('next');self.tap('next');self.assertEqual(self.machine.lookup('page'),0)
        self.tap('next');self.assertEqual(self.machine.lookup('page'),1);self.assertEqual(self.machine.lookup('reminder'),1)
        self.tap('settings');self.tap('choose');self.tap('back')
        self.assertEqual(self.machine.lookup('page'),1);self.assertEqual(self.machine.lookup('selectionLabel'),'Summer')
        self.machine.undo();self.assertEqual(self.machine.lookup('page'),2)
        self.tap('choose');self.assertEqual(self.machine.lookup('choice'),0)
        self.assertEqual(len(self.machine.history.records),8)
        restored=InteractionMachine.restore(self.ir,json.loads(json.dumps(self.machine.save())))
        self.assertEqual(restored.snapshot(),self.machine.snapshot())

    def test_delayed_action_can_be_restored_and_undo_requeues_timer(self):
        self.detail();self.tap('settings');self.tap('later');self.machine.advance(499)
        self.assertEqual(self.machine.lookup('choice'),0)
        self.machine=InteractionMachine.restore(self.ir,json.loads(json.dumps(self.machine.save())))
        self.machine.advance(1);self.assertEqual(self.machine.lookup('choice'),1)
        self.machine.undo();self.assertEqual(self.machine.lookup('choice'),0)
        self.assertEqual(len(self.machine.queue),1)

    def test_ir_matches_legacy_immediate_semantics_and_hidden_buttons_do_not_fire(self):
        legacy=Runtime(self.model,1080,2160)
        for name in ['next','next','next','settings','choose','back','home']:
            before={k:legacy.lookup(k) for k in self.model['variables']}
            for t in self.model['triggers']:
                if t['owner']==self.ids[name]:legacy.execute(t)
            legacy.settle(before);self.tap(name)
            for k in ['clicks','page','choice','returnPage','reminder']:self.assertEqual(self.machine.lookup(k),legacy.lookup(k),k)
        self.tap('choose');self.assertEqual(self.machine.lookup('choice'),1)

    def test_error_is_atomic_and_owner_required(self):
        bad=copy.deepcopy(self.ir);rule=next(r for r in bad['rules'] if r['owner']==self.ids['next'])
        rule['actions'][1]['guards']=[];rule['actions'][1]['value']=expression('1/0')
        m=InteractionMachine(bad);before=m.snapshot()
        with self.assertRaises((ExpressionError,ZeroDivisionError)):m.dispatch('pointer.up',self.ids['next'])
        self.assertEqual(m.snapshot(),before);self.assertEqual(len(m.history.records),1)
        with self.assertRaises(ValueError):m.dispatch('pointer.up')

    def test_native_effect_and_unsupported_loop_are_explicit_and_version_gated(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            path=Path(tmp)/'lockscreen';path.mkdir()
            (path/'manifest.xml').write_text('<Lockscreen><Var name="x" expression="0"/><Button name="a"><Trigger action="double"><EmotionCommand value="0"/><IntentCommand package="android.app"/><CycleCommand><VariableCommand name="x" expression="#x+1"/></CycleCommand></Trigger></Button></Lockscreen>')
            model=load_model(Path(tmp));ir=compile_interactions(model,'harmonyos-5');m=InteractionMachine(ir)
            rule=ir['rules'][0]
            with self.assertRaises(ExpressionError):m.dispatch('gesture.double',rule['owner'])
            self.assertEqual(m.lookup('x'),0);self.assertEqual(m.effects,[])
            legacy=Runtime(model,1080,2160);legacy.execute(model['triggers'][0])
            self.assertEqual(legacy.lookup('x'),0);self.assertTrue(any('原生命令容器' in issue for issue in legacy.issues.values()))
            reports=[capability_report(model,t) for t in ['harmonyos-5','harmonyos-6']]
            self.assertIn('version-gated',reports[0]['summary']);self.assertNotIn('version-gated',reports[1]['summary'])
            self.assertIn('requires-platform-mapping',reports[0]['summary'])
            rule['actions']=rule['actions'][:2];m=InteractionMachine(ir);m.dispatch('gesture.double',rule['owner'])
            self.assertEqual(len(m.effects),2);self.assertTrue(all(not e['executed'] for e in m.effects))
