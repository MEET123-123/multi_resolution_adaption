import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from multires_app.interaction.interaction_history import InteractionHistory, state_changes, apply_changes
from multires_app.preview.interaction_server import InteractiveSession
from multires_app.pipeline.scene_pipeline import create_project
from samples.interaction_state_demo import initial_state, reduce_event, run_demo

ROOT = Path(__file__).resolve().parents[1]


class InteractionHistoryTests(unittest.TestCase):
    def test_reversible_changes_distinguish_missing_null_and_nested_lists(self):
        before = {'nullable': None, 'deleted': 5, 'nested': {'a/b~': [1, 2]}, 'typed': 1}
        after = {'added': None, 'nested': {'a/b~': [3]}, 'typed': True}
        changes = state_changes(before, after)
        self.assertEqual(apply_changes(before, changes), after)
        self.assertEqual(apply_changes(after, changes, reverse=True), before)
        deleted = next(c for c in changes if c['path'] == ['deleted'])
        self.assertEqual(deleted['after'], {'exists': False})
        with self.assertRaises(ValueError):
            apply_changes({}, changes)

    def test_more_than_fifty_events_round_trip_and_branch_after_undo(self):
        history = InteractionHistory({'count': 0}, checkpoint_every=7)
        for count in range(1, 81):
            history.commit({'type': 'tap'}, {'count': count})
        restored = InteractionHistory.from_dict(json.loads(json.dumps(history.to_dict())))
        self.assertEqual(restored.current, {'count': 80})
        for _ in range(80):
            restored.undo()
        self.assertEqual(restored.current, {'count': 0})
        restored.checkout('event-000079')
        alternate = restored.commit({'type': 'tap', 'value': 100}, {'count': 100})
        restored.checkout('event-000079')
        self.assertEqual({n['id'] for n in restored.children()}, {'event-000080', alternate['id']})
        self.assertEqual(restored.checkout('event-000080')['count'], 80)
        self.assertEqual(restored.checkout(alternate['id'])['count'], 100)

    def test_sequence_nested_return_and_undo_have_different_meanings(self):
        history, summary = run_demo()
        self.assertEqual([s['route'] for s in summary['steps']], ['home', 'home', 'detail', 'settings', 'settings', 'detail', 'home'])
        self.assertEqual(history.current['variables'], {'persistent': {'motion': 0}, 'session': {'tapCount': 3}})
        self.assertEqual(summary['undoAfterPageBack']['navigation'][-1]['route'], 'detail')
        self.assertEqual(summary['undoAfterPageBack']['variables']['persistent']['motion'], 0)
        self.assertEqual(len(summary['branchesAtSettings']), 2)
        with self.assertRaises(ValueError):
            reduce_event(initial_state(), {'type': 'set-motion', 'value': 0, 'timeMs': 100})

    def test_no_change_events_and_effects_survive_without_executing_effects(self):
        history = InteractionHistory({'screen': 'home'})
        node = history.commit({'type': 'tap'}, history.current, effects=[{'tag': 'ExternCommand', 'delivery': 'recorded-only'}])
        self.assertEqual(node['changes'], [])
        restored = InteractionHistory.from_dict(history.to_dict())
        restored.undo(); restored.checkout(node['id'])
        self.assertEqual(len(restored.records[-1]['effects']), 1)
        with self.assertRaises(ValueError):
            history.commit({'type': 'bad'}, {'x': float('nan')})
        self.assertEqual(len(history.records), 2)

    def test_live_commands_restore_and_export_with_original_branches(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as tmp:
            out = Path(tmp)
            source=out/'source.hwt'
            with zipfile.ZipFile(source,'w') as archive:
                for path in (ROOT/'samples/scene-theme').rglob('*'):
                    if path.is_file():archive.write(path,path.relative_to(ROOT/'samples/scene-theme').as_posix())
            create_project(source, {'resolutionPx': {'width': 1080, 'height': 2160}},
                           {'profiles': [{'id': 'phone', 'resolutionPx': {'width': 1080, 'height': 2160}, 'orientations': ['portrait']}]}, out,
                           max_states=8, max_depth=4)
            project = json.loads((out/'project.json').read_text(encoding='utf-8'))
            session = InteractiveSession(project, 'phone-portrait')
            owner = next(n['id'] for n in project['model']['nodes'].values() if n['attributes'].get('name') == 'switch')
            action = {'owner': owner, 'kind': 'tap', 'sample': [.5]*4}
            session.gesture(action)
            first = session.journal.cursor
            pressed = [c['valueChange'] for c in session.journal.records[-1]['commands'] if c.get('surface') == 'target' and c.get('valueChange', {}).get('name') == 'pressed']
            self.assertEqual([(c['before'], c['after']) for c in pressed], [(0, 1), (1, 0)])
            # The storage override is created on the first tap; later taps have no net pressed delta.
            session.gesture(action)
            self.assertNotIn('pressed', {c['path'][-1] for c in session.journal.records[-1]['changes'] if c['path']})
            second = session.journal.cursor
            session.back()
            self.assertEqual(session.journal.cursor, first)
            session.gesture(action)
            third = session.journal.cursor
            session.checkout(first)
            self.assertEqual({n['id'] for n in session.journal.children()}, {second, third})
            before = session.journal.to_dict()
            with patch('multires_app.preview.interaction_server.schedule_device', side_effect=ValueError('layout failed')):
                with self.assertRaises(ValueError):
                    session.gesture(action)
            self.assertEqual(session.journal.to_dict(), before)
            result = session.save(out/'saved')
            self.assertEqual(result['historyFile'], 'interaction-history.json')
            saved_project = json.loads((out/'saved/project.json').read_text(encoding='utf-8'))
            saved_history = json.loads((out/'saved/interaction-history.json').read_text(encoding='utf-8'))
            resumed = InteractiveSession(saved_project, 'phone-portrait')
            resumed.load_history(saved_history)
            resumed.checkout(second)
            self.assertEqual(resumed.runtime.lookup('count'), 2)
            resumed.checkout(first)
            resumed.gesture(action)
            self.assertEqual(len({s['id'] for s in resumed.project['graph']['states']}), len(resumed.project['graph']['states']))


if __name__ == '__main__':
    unittest.main()
