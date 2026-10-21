import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from multires_app.preview.live_preview import LiveSession, serve_live  # noqa: E402


class LivePreviewTests(unittest.TestCase):
    def session(self, directory):
        return LiveSession(ROOT / 'samples/scene-theme', Path(directory), filename='scene-theme')

    def test_plan_exposes_canvas_elements_and_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.session(tmp)
            plan = session.plan()
            self.assertGreater(plan['canvas']['width'], 0)
            self.assertGreater(plan['canvas']['height'], 0)
            self.assertTrue(plan['elements'])
            for element in plan['elements']:
                self.assertIn('rect', element)
                self.assertGreater(element['rect']['w'], 0)
                self.assertGreater(element['rect']['h'], 0)
                self.assertNotEqual(element['tag'], 'Group')
                self.assertIsInstance(element['tracks'], list)
            json.dumps(plan, allow_nan=False)

    def test_tap_executes_trigger_and_bumps_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.session(tmp)
            before = session.plan()
            button = next(e for e in before['elements'] if e['tag'] == 'Button')
            x = button['rect']['x'] + button['rect']['w'] / 2
            y = button['rect']['y'] + button['rect']['h'] / 2
            result = session.gesture({'kind': 'tap', 'x': x, 'y': y, 'toX': x, 'toY': y, 'durationMs': 80})
            self.assertTrue(result['hit'], result.get('message'))
            self.assertTrue(result['executed'])
            self.assertGreater(result['plan']['revision'], before['revision'])

    def test_miss_returns_reason_without_changing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.session(tmp)
            before = session.plan()
            result = session.gesture({'kind': 'tap', 'x': -50, 'y': -50, 'toX': -50, 'toY': -50})
            self.assertFalse(result['hit'])
            self.assertEqual(result['plan']['revision'], before['revision'])

    def test_reset_restores_initial_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.session(tmp)
            first = session.plan()
            session.gesture({'kind': 'tap', 'x': 10, 'y': 10, 'toX': 10, 'toY': 10})
            session.reset()
            after = session.plan()
            self.assertGreater(after['revision'], first['revision'])
            self.assertEqual(len(after['history']), 0)

    def test_invalid_gesture_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.session(tmp)
            with self.assertRaises(ValueError):
                session.gesture({'kind': 'pinch', 'x': 1, 'y': 1})


class SliderDragTests(unittest.TestCase):
    """A Slider is either a fixed track or a track the theme rebuilds per frame."""

    def track_session(self, directory):
        return LiveSession(ROOT / 'samples/slider-theme', Path(directory), filename='slider-theme')

    def touch_session(self, directory):
        return LiveSession(ROOT / 'samples/touch-slider-theme', Path(directory), filename='touch-slider-theme')

    def test_press_reports_slider_and_owning_button(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.track_session(tmp)
            result = session.press({'x': 540, 'y': 1600})
            self.assertIsNotNone(result['slider'])
            self.assertIsNotNone(result['button'])

    def test_drag_start_reports_geometry_for_client_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.track_session(tmp)
            started = session.drag_phase({'phase': 'start', 'x': 540, 'y': 1600})
            self.assertTrue(started['active'], started.get('message'))
            self.assertEqual(started['mode'], 'track')
            self.assertAlmostEqual(started['length'], 540.0)
            self.assertEqual(started['direction'], [0.0, -1.0])
            self.assertEqual(started['chained'], {'down': 0, 'up': 1})

    def test_progress_tracks_finger_and_clamps_at_the_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.track_session(tmp)
            session.drag_phase({'phase': 'start', 'x': 540, 'y': 1600})
            half = session.drag_phase({'phase': 'move', 'x': 540, 'y': 1330})
            self.assertAlmostEqual(half['progress'], 0.5, places=3)
            self.assertFalse(half['reached'])
            self.assertAlmostEqual(half['offset'][1], -270.0, places=3)
            past = session.drag_phase({'phase': 'move', 'x': 540, 'y': 200})
            self.assertEqual(past['progress'], 1.0)
            self.assertTrue(past['reached'])
            self.assertAlmostEqual(past['offset'][1], -540.0, places=3)

    def test_reaching_end_fires_chained_touch_button_and_unlocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.track_session(tmp)
            session.drag_phase({'phase': 'start', 'x': 540, 'y': 1600})
            session.drag_phase({'phase': 'move', 'x': 540, 'y': 700})
            ended = session.drag_phase({'phase': 'end', 'x': 540, 'y': 700})
            self.assertTrue(ended['reached'])
            self.assertTrue(ended['unlocked'])
            self.assertGreaterEqual(ended['executed'], 1)
            self.assertEqual(ended['plan']['variables']['unlocked'], 1)
            self.assertEqual(ended['plan']['drag'], None)
            self.assertEqual(session.history[-1]['kind'], 'drag')

    def test_release_below_end_settles_without_unlocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.track_session(tmp)
            session.drag_phase({'phase': 'start', 'x': 540, 'y': 1600})
            session.drag_phase({'phase': 'move', 'x': 540, 'y': 1500})
            ended = session.drag_phase({'phase': 'end', 'x': 540, 'y': 1500})
            self.assertFalse(ended['reached'])
            self.assertFalse(ended['unlocked'])
            self.assertFalse(ended['rebounding'])
            # The handle stays where the finger left it, like a real Slider.
            self.assertAlmostEqual(session.runtime.state['slide_move.move_y'], -100.0, places=3)

    def test_move_without_start_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.track_session(tmp)
            result = session.drag_phase({'phase': 'move', 'x': 10, 'y': 10})
            self.assertFalse(result['active'])
            self.assertIn('message', result)

    def test_drag_start_without_slider_explains_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = LiveSession(ROOT / 'samples/scene-theme', Path(tmp), filename='scene-theme')
            result = session.drag_phase({'phase': 'start', 'x': 400, 'y': 1000})
            self.assertFalse(result['active'])
            self.assertIn('滑动控件', result['message'])

    def test_invalid_phase_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.track_session(tmp)
            session.drag_phase({'phase': 'start', 'x': 540, 'y': 1600})
            with self.assertRaises(ValueError):
                session.drag_phase({'phase': 'hover', 'x': 1, 'y': 1})

    def test_touch_driven_slider_defers_the_decision_to_the_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.touch_session(tmp)
            started = session.drag_phase({'phase': 'start', 'x': 900, 'y': 1000})
            self.assertTrue(started['active'], started.get('message'))
            self.assertEqual(started['mode'], 'touch')
            session.drag_phase({'phase': 'move', 'x': 600, 'y': 1000})
            ended = session.drag_phase({'phase': 'end', 'x': 600, 'y': 1000})
            # No static track exists, so the preview reports the theme's own outcome.
            self.assertIsNone(ended['reached'])
            self.assertGreaterEqual(ended['executed'], 1)
            self.assertEqual(ended['plan']['variables']['page'], 1)

    def test_reset_clears_drag_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.track_session(tmp)
            session.drag_phase({'phase': 'start', 'x': 540, 'y': 1600})
            session.drag_phase({'phase': 'move', 'x': 540, 'y': 1200})
            session.drag_phase({'phase': 'end', 'x': 540, 'y': 1200})
            self.assertIn('slide_move.move_y', session.runtime.state)
            session.reset()
            self.assertNotIn('slide_move.move_y', session.runtime.state)
            self.assertIsNone(session.plan()['drag'])
            self.assertFalse(session.plan()['unlocked'])


class LiveDragHTTPTests(unittest.TestCase):
    """The browser drives a drag over HTTP; the endpoints must agree with the session."""

    def test_press_and_drag_round_trip(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as tmp:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / 'multires.py'), 'live', str(ROOT / 'samples/slider-theme'),
                 '--port', '0', '--out', str(Path(tmp) / 'preview')],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                url = json.loads(process.stdout.readline())['url']
                with urlopen(url, timeout=10) as response:
                    page = response.read().decode()
                config = json.loads(re.search(r'const LIVE_CONFIG=(.*?);', page).group(1))
                self.assertIn('dragbar', page)
                self.assertIn('beginDrag', page)

                def post(operation, body, token=config['token']):
                    request = Request(url + 'api/' + operation, data=json.dumps(body).encode(),
                                      headers={'Content-Type': 'application/json', 'X-Live-Token': token})
                    with urlopen(request, timeout=10) as response:
                        return json.load(response)

                probe = post('press', {'x': 540, 'y': 1600})
                self.assertIsNotNone(probe['slider'])
                started = post('drag', {'phase': 'start', 'x': 540, 'y': 1600, 'owner': probe['slider']})
                self.assertTrue(started['active'])
                self.assertAlmostEqual(started['length'], 540.0)
                half = post('drag', {'phase': 'move', 'x': 540, 'y': 1330})
                self.assertAlmostEqual(half['progress'], 0.5, places=3)
                self.assertEqual(half['plan']['drag']['mode'], 'track')
                ended = post('drag', {'phase': 'end', 'x': 540, 'y': 700})
                self.assertTrue(ended['unlocked'])
                self.assertIsNone(ended['plan']['drag'])
                with self.assertRaises(HTTPError) as error:
                    post('drag', {'phase': 'start', 'x': 540, 'y': 1600}, 'wrong')
                self.assertEqual(error.exception.code, 403)
            finally:
                process.terminate()
                process.wait(timeout=10)
                process.stdout.close()
                process.stderr.close()


if __name__ == '__main__':
    unittest.main()
