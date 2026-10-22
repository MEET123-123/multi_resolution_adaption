"""Live preview of a lockscreen HWT: import, gestures, animations.

The preview does not precompute states. It keeps one Runtime alive, lets the
browser drive the animation clock, and only calls back into Python when the
finger actually produces an engine event. That keeps 60fps motion out of the
interpreter while still executing the theme's own Trigger/VariableCommand graph.
"""
from __future__ import annotations

import copy
import datetime
import json
import math
import mimetypes
import posixpath
import re
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from multires_app.engine.scene_engine import Runtime, extract_assets, load_model, resolve_asset
from multires_app.interaction.interaction_semantics import hit_test, initialize, perform_action

ANIM_TAGS = {'PositionAnimation': 'pos', 'AlphaAnimation': 'alpha', 'SizeAnimation': 'size',
             'SourcesAnimation': 'frames'}
GESTURES = {'tap', 'swipe', 'long-press', 'double-tap'}


def canvas_size(model, source):
    """Source logical canvas: script attributes first, packaged preview second."""
    root = model.get('root') or {}
    width = root.get('screenWidth')
    height = root.get('screenHeight')
    if width and height:
        return float(width), float(height)
    from PIL import Image
    import io
    import zipfile
    size = None
    if Path(source).is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if 'preview_unlock_0' not in name.lower():
                    continue
                try:
                    with Image.open(io.BytesIO(archive.read(name))) as image:
                        size = image.size
                    break
                except Exception:
                    continue
    width = float(width or 1080)
    height = float(height or (width * size[1] / size[0] if size else width * 2))
    return width, height


def frame_list(model, node):
    return [model['nodes'][child]['attributes'] for child in node['children']]


def tracks_for(model, runtime, uid, assets=None):
    """Animation keyframes resolved at the current state, for client-side playback."""
    tracks = []
    for child in model['nodes'][uid]['children']:
        node = model['nodes'][child]
        kind = ANIM_TAGS.get(node['tag'])
        if not kind:
            continue
        attributes = node['attributes']
        raw = frame_list(model, node)
        if not raw:
            continue
        keyframes = []
        for item in raw:
            entry = {'t': float(runtime.value(item.get('time', 0), 0, child + ':time'))}
            if kind == 'pos':
                entry['x'] = float(runtime.value(item.get('x', 0), 0, child + ':x'))
                entry['y'] = float(runtime.value(item.get('y', 0), 0, child + ':y'))
            elif kind == 'alpha':
                entry['a'] = float(runtime.value(item.get('a', 255), 255, child + ':a'))
            elif kind == 'size':
                entry['w'] = float(runtime.value(item.get('w', 0), 0, child + ':w'))
                entry['h'] = float(runtime.value(item.get('h', 0), 0, child + ':h'))
            else:
                entry['src'] = item.get('src', '')
                entry['url'] = (assets or {}).get(resolve_asset(model, entry['src']), {}).get('url')
                entry['x'] = float(runtime.value(item.get('x', 0), 0, child + ':x'))
                entry['y'] = float(runtime.value(item.get('y', 0), 0, child + ':y'))
            keyframes.append(entry)
        keyframes.sort(key=lambda item: item['t'])
        duration = keyframes[-1]['t']
        repeat = attributes.get('repeat')
        loop = attributes.get('loop', 'false' if repeat == '1' else 'true').lower() != 'false'
        tracks.append({'kind': kind, 'delay': float(runtime.value(attributes.get('delay', 0), 0, child + ':delay')),
                       'duration': duration, 'loop': loop, 'keyframes': keyframes})
    return tracks


def rect_of(attributes, runtime, prefix=''):
    """StartPoint/EndPoint rects may use negative sizes and expressions."""
    def number(key, default=0.0):
        return float(runtime.value(attributes.get(key, default), default, prefix + ':' + key))
    x, y = number('x'), number('y')
    w, h = abs(number('w')), abs(number('h'))
    return {'x': min(x, x + number('w')), 'y': min(y, y + number('h')), 'w': w, 'h': h}


def inside(rect, point):
    return rect['w'] > 0 and rect['h'] > 0 and rect['x'] <= point[0] <= rect['x'] + rect['w'] \
        and rect['y'] <= point[1] <= rect['y'] + rect['h']


def subtree_ids(model, uid):
    """Every descendant of uid, in document order."""
    found, stack = [], list(reversed(model['nodes'][uid]['children']))
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(reversed(model['nodes'][current]['children']))
    return found


def slider_geometry(model, runtime, owner):
    """StartPoint anchor, drag axis, travel distance and EndPoint targets.

    Two families exist in shipping themes and they need different treatment:

    ``track``  StartPoint/EndPoint are static (or screen-relative), so the handle
               travels a fixed distance and ``#name.move_x/move_y/progress``
               describe the drag. The preview drives those variables.
    ``touch``  StartPoint/EndPoint are written in ``#touch_*``, i.e. the theme
               recomputes the track from the live finger. Faking a travel
               distance would fight the theme, so the preview instead feeds the
               real touch coordinates and fires the EndPoint's own ``up``
               Trigger on release.
    """
    node = model['nodes'][owner]
    if node['tag'] not in {'Slider', 'Unlocker'}:
        return None
    children = [model['nodes'][child] for child in node['children']]
    starts = [child for child in children if child['tag'] == 'StartPoint']
    ends = [child for child in children if child['tag'] == 'EndPoint']
    if not starts or not ends:
        return None
    start = starts[0]
    touch_driven = any('touch_' in str(value) for child in (starts + ends) for value in child['attributes'].values())
    anchor = rect_of(start['attributes'], runtime, start['id'])
    targets = [rect_of(end['attributes'], runtime, end['id']) for end in ends]
    # Path lives inside EndPoint and defines the allowed travel direction.
    points = []
    for end in ends:
        path = next((model['nodes'][child] for child in end['children'] if model['nodes'][child]['tag'] == 'Path'), None)
        if not path:
            continue
        base_x = float(runtime.value(path['attributes'].get('x', 0), 0, path['id'] + ':x'))
        base_y = float(runtime.value(path['attributes'].get('y', 0), 0, path['id'] + ':y'))
        for child in path['children']:
            item = model['nodes'][child]
            if item['tag'] != 'Position':
                continue
            points.append((base_x + float(runtime.value(item['attributes'].get('x', 0), 0, child + ':x')),
                           base_y + float(runtime.value(item['attributes'].get('y', 0), 0, child + ':y'))))
        if len(points) >= 2:
            break
    centre = (anchor['x'] + anchor['w'] / 2, anchor['y'] + anchor['h'] / 2)
    if len(points) < 2:
        target = targets[0]
        target_centre = (target['x'] + target['w'] / 2, target['y'] + target['h'] / 2)
        if touch_driven:
            points = [centre, target_centre]
        else:
            # Travel is measured from the StartPoint origin to the closest edge of
            # the EndPoint box, which is what the theme's own geometry implies.
            origin = (anchor['x'], anchor['y'])
            nearest = (min(max(origin[0], target['x']), target['x'] + target['w']),
                       min(max(origin[1], target['y']), target['y'] + target['h']))
            if math.hypot(nearest[0] - origin[0], nearest[1] - origin[1]) < 1e-6:
                nearest = target_centre
            points = [origin, nearest]
    dx, dy = points[-1][0] - points[0][0], points[-1][1] - points[0][1]
    length = math.hypot(dx, dy)
    direction = (dx / length, dy / length) if length > 1e-6 else (0.0, -1.0)
    screen = (runtime.system.get('screen_width', 1080), runtime.system.get('screen_height', 2160))
    if length < 1e-6 or (touch_driven and length < 32):
        # A touch-driven track has no static travel; use the screen extent so the
        # progress readout stays meaningful.
        length = max(screen)
    # A touch-driven track has no static grab area, so the whole screen is the
    # target; a static track exposes the StartPoint box.
    hit = ({'x': 0.0, 'y': 0.0, 'w': screen[0], 'h': screen[1]} if touch_driven else anchor)
    return {'name': node['attributes'].get('name') or 'slider', 'anchor': (anchor['x'], anchor['y']),
            'hit': hit, 'direction': direction, 'length': length, 'targets': targets,
            'mode': 'touch' if touch_driven else 'track',
            'endTriggers': [trigger['id'] for trigger in model['triggers']
                            if trigger['owner'] in {end['id'] for end in ends}],
            'bounce': (float(runtime.value(node['attributes'].get('bounceInitSpeed', 0), 0, owner + ':speed')),
                       float(runtime.value(node['attributes'].get('bounceAcceleration', 0), 0, owner + ':accel'))),
            'startPoint': start['id']}


def pick_owner(visible, x, y, model, viewport, gesture=None):
    """hit_test only ranks Buttons; Slider/Unlocker also own Triggers."""
    owner = hit_test(visible, x, y, model=model, viewport=viewport, gesture=gesture)
    if owner:
        return owner
    for element in reversed(visible):
        rect = element['rect']
        if element['tag'] in {'Slider', 'Unlocker'} and element['visible'] and rect['w'] > 0 and rect['h'] > 0 \
                and rect['x'] <= x <= rect['x'] + rect['w'] and rect['y'] <= y <= rect['y'] + rect['h']:
            return element['id']
    return None


class LiveSession:
    def __init__(self, source: Path, output: Path, filename=None):
        self.source = Path(source)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.name = filename or self.source.name
        self.revision = 0
        self.lock = threading.Lock()
        self.issues = {}
        self.history = []
        self.load()

    def load(self):
        self.model = load_model(self.source)
        self.width, self.height = canvas_size(self.model, self.source)
        self.assets = extract_assets(self.source, self.model, self.output)
        self.reset()

    def reset(self):
        self.runtime = initialize(self.model, self.width, self.height)
        self.base_time = self.runtime.time_ms
        self.epoch = time.monotonic()
        self.issues = dict(self.runtime.issues)
        self.history = []
        self.drag = None
        self.unlocked = False
        self.revision += 1

    def advance(self):
        """Wall clock drives the engine clock so loops and clocks stay live."""
        elapsed = (time.monotonic() - self.epoch) * 1000
        self.runtime.time_ms = self.base_time + elapsed
        now = datetime.datetime.now()
        hour24, minute, second = now.hour, now.minute, now.second
        self.runtime.system.update(time=self.runtime.time_ms, hour=hour24 % 12 or 12, hour24=hour24,
                                   minute=minute, second=second, year=now.year, month=now.month,
                                   date=now.day, day_of_week=(now.weekday() + 1) % 7,
                                   **{'system.time.hour1': hour24 // 10, 'system.time.hour2': hour24 % 10,
                                      'system.time.min1': minute // 10, 'system.time.min2': minute % 10,
                                      'system.time.sec1': second // 10, 'system.time.sec2': second % 10})
        for key in ('system.time.hour1', 'system.time.hour2', 'system.time.min1', 'system.time.min2'):
            self.issues.pop('input:' + key, None)
        self.runtime.cache.clear()
        self.step_rebound()

    # ---- Slider / Unlocker drag -------------------------------------------------
    def slider_at(self, x, y, visible=None):
        """Grab area is the StartPoint; the track rect is the fallback.

        Touch-driven sliders claim the whole screen, so a static track always
        wins when both are under the finger.
        """
        if visible is None:
            visible = self.runtime.snapshot(self.assets, visible_only=True, slider_states=True)
        rects = {item['id']: item['rect'] for item in visible}
        found = []
        for uid, node in self.model['nodes'].items():
            if node['tag'] not in {'Slider', 'Unlocker'}:
                continue
            try:
                geometry = slider_geometry(self.model, self.runtime, uid)
            except Exception:
                continue
            if not geometry:
                continue
            if inside(geometry['hit'], (x, y)) or (rects.get(uid) and inside(rects[uid], (x, y))):
                found.append((geometry['mode'] != 'track', uid))
        return min(found)[1] if found else None

    def apply_drag(self):
        if not self.drag or self.drag['mode'] != 'track':
            return
        name = self.drag['name']
        ox, oy = self.drag['offset']
        self.runtime.state[name + '.move_x'] = ox
        self.runtime.state[name + '.move_y'] = oy
        self.runtime.state[name + '.progress'] = self.drag['progress']
        self.runtime.cache.clear()

    def settle_drag(self, name, offset=(0.0, 0.0), progress=0.0):
        """Clear the injected move_* variables when a drag ends or snaps back."""
        self.runtime.state[name + '.move_x'] = offset[0]
        self.runtime.state[name + '.move_y'] = offset[1]
        self.runtime.state[name + '.progress'] = progress
        self.runtime.cache.clear()

    def drag_offset_at(self, x, y):
        drag = self.drag
        dx, dy = x - drag['press'][0], y - drag['press'][1]
        along = dx * drag['direction'][0] + dy * drag['direction'][1]
        along = max(0.0, min(drag['length'], along))
        drag['progress'] = round(along / drag['length'], 5)
        drag['offset'] = (drag['direction'][0] * along, drag['direction'][1] * along)
        anchor = drag['anchor']
        point = (anchor[0] + drag['offset'][0], anchor[1] + drag['offset'][1])
        drag['reached'] = any(inside(rect, point) for rect in drag['targets'])
        return drag['offset']

    def step_rebound(self):
        drag = self.drag
        if not drag or not drag.get('released'):
            return
        v0, acceleration = drag['bounce']
        elapsed = time.monotonic() - drag['released']
        travelled = v0 * elapsed + 0.5 * acceleration * elapsed * elapsed
        remaining = drag['released_length'] - travelled
        if remaining <= 0.5 or elapsed > 4:
            self.settle_drag(drag['name'])
            self.drag = None
            return
        direction = drag['direction']
        drag['offset'] = (direction[0] * remaining, direction[1] * remaining)
        drag['progress'] = round(remaining / drag['length'], 5)
        self.apply_drag()

    def press(self, body):
        self.advance()
        x, y = float(body.get('x', 0)), float(body.get('y', 0))
        visible = self.runtime.snapshot(self.assets, visible_only=True, slider_states=True)
        button = hit_test(visible, x, y, model=self.model, viewport={'width': self.width, 'height': self.height})
        return {'button': button, 'slider': self.slider_at(x, y, visible), 'point': [x, y]}

    def press_chains(self, x, y, visible):
        """down/up Triggers of every visible Button under the point.

        Themes in this family stack full-screen Buttons and split the gesture
        between them: one arms the slide on ``down``, another decides the unlock
        from ``#touch_begin_y - #touch_y`` on ``up``. Hit testing keeps only the
        topmost, so the preview dispatches to all of them, matching what the
        platform does for a touch that lands on overlapping Buttons.
        """
        owners = {item['id'] for item in visible
                  if item['tag'] == 'Button' and item['visible']
                  and item['rect']['w'] > 0 and item['rect']['h'] > 0
                  and item['rect']['x'] <= x <= item['rect']['x'] + item['rect']['w']
                  and item['rect']['y'] <= y <= item['rect']['y'] + item['rect']['h']}
        found = {'down': [], 'up': []}
        for trigger in self.model['triggers']:
            if trigger['owner'] in owners and trigger['action'] in {'down', 'up'}:
                found[trigger['action']].append(trigger['id'])
        return found

    def drag_phase(self, body):
        phase = body.get('phase')
        x, y = float(body.get('x', 0)), float(body.get('y', 0))
        if phase == 'start':
            self.advance()
            visible = self.runtime.snapshot(self.assets, visible_only=True, slider_states=True)
            owner = body.get('owner') or self.slider_at(x, y, visible)
            if not owner:
                return {'active': False, 'message': '该位置没有滑动控件', 'plan': self.plan()}
            geometry = slider_geometry(self.model, self.runtime, owner)
            if not geometry:
                return {'active': False, 'message': '滑动控件缺少 StartPoint/EndPoint', 'plan': self.plan()}
            chained = self.press_chains(x, y, visible)
            self.runtime.system.update(touch_begin_x=x, touch_begin_y=y, touch_x=x, touch_y=y)
            self.drag = {**geometry, 'owner': owner, 'press': (x, y), 'offset': (0.0, 0.0),
                         'progress': 0.0, 'reached': False, 'released': None, 'released_length': 0.0,
                         'follow': subtree_ids(self.model, geometry['startPoint']), 'chained': chained}
            for trigger_id in chained['down']:
                trigger = next((item for item in self.model['triggers'] if item['id'] == trigger_id), None)
                if trigger:
                    self.runtime.execute(trigger, {'touch_begin_x': x, 'touch_begin_y': y,
                                                   'touch_x': x, 'touch_y': y})
            self.apply_drag()
            self.advance()
            return {'active': True, 'owner': owner, 'name': geometry['name'], 'mode': geometry['mode'],
                    'length': round(geometry['length'], 3), 'direction': list(geometry['direction']),
                    'anchor': list(geometry['anchor']), 'follow': self.drag['follow'],
                    'chained': {'down': len(chained['down']), 'up': len(chained['up'])},
                    'plan': self.plan()}
        if not self.drag:
            return {'active': False, 'message': '没有进行中的拖动', 'plan': self.plan()}
        if phase == 'move':
            self.advance()
            self.runtime.system.update(touch_x=x, touch_y=y)
            if self.drag['mode'] == 'track':
                self.drag_offset_at(x, y)
            else:
                self.drag['offset'] = (x - self.drag['press'][0], y - self.drag['press'][1])
                self.drag['progress'] = round(
                    max(0.0, min(self.drag['length'], math.hypot(*self.drag['offset']))) / self.drag['length'], 5)
            self.apply_drag()
            return {'active': True, 'name': self.drag['name'], 'mode': self.drag['mode'],
                    'reached': self.drag['reached'], 'progress': self.drag['progress'],
                    'offset': [round(v, 3) for v in self.drag['offset']], 'plan': self.plan()}
        if phase == 'end':
            self.advance()
            drag = self.drag
            self.runtime.system.update(touch_x=x, touch_y=y)
            if drag['mode'] == 'track':
                self.drag_offset_at(x, y)
            else:
                self.drag['offset'] = (x - drag['press'][0], y - drag['press'][1])
                self.drag['progress'] = round(
                    max(0.0, min(drag['length'], math.hypot(*drag['offset']))) / drag['length'], 5)
            reached = drag['reached']
            v0, acceleration = drag['bounce']
            tag = self.model['nodes'][drag['owner']]['tag']
            rebounding = False
            executed = 0
            touch = {'touch_begin_x': drag['press'][0], 'touch_begin_y': drag['press'][1],
                     'touch_x': x, 'touch_y': y}
            self.runtime.events = []
            chained = drag['chained']['up'] + (drag['endTriggers'] if drag['mode'] == 'touch' else [])
            if chained:
                before = {name: self.runtime.lookup(name) for name in self.model['variables']}
                for trigger_id in chained:
                    trigger = next((item for item in self.model['triggers'] if item['id'] == trigger_id), None)
                    if not trigger or trigger['action'] != 'up':
                        continue
                    self.runtime.execute(trigger, touch)
                    executed += 1
                self.runtime.settle(before)
                if drag['mode'] == 'touch':
                    # No static track, so the only honest signal is whether the
                    # theme actually changed state.
                    reached = sum(1 for name, value in before.items()
                                  if self.runtime.lookup(name) != value) > 0
            if drag['mode'] == 'touch':
                self.drag = None
            elif reached and tag == 'Unlocker':
                self.unlocked = True
                self.settle_drag(drag['name'], drag['offset'], drag['progress'])
                self.drag = None
                executed += 1
            elif reached and v0 > 0:
                # A page slider returns to its start after firing the target.
                self.settle_drag(drag['name'])
                self.drag = None
                executed += 1
            elif not reached and v0 > 0:
                drag['released'] = time.monotonic()
                drag['released_length'] = math.hypot(*drag['offset'])
                rebounding = True
            else:
                self.settle_drag(drag['name'], drag['offset'], drag['progress'])
                self.drag = None
            if any(event['tag'] == 'ExternCommand' and event['attributes'].get('command') == 'unlock'
                   for event in self.runtime.events):
                self.unlocked = True
            self.history.append({'kind': 'drag', 'owner': drag['name'],
                                 'executed': executed,
                                 'at': datetime.datetime.now().strftime('%H:%M:%S')})
            self.revision += 1
            return {'active': False, 'reached': reached if drag['mode'] == 'track' else None,
                    'rebounding': rebounding, 'mode': drag['mode'], 'unlocked': self.unlocked,
                    'executed': executed, 'plan': self.plan()}
        raise ValueError('无效拖动阶段')

    def offset_for(self, uid):
        """StartPoint children follow the handle, per the Slider spec."""
        if not self.drag or self.drag['mode'] != 'track':
            return None
        start = self.drag['startPoint']
        node = self.model['nodes'].get(uid)
        while node is not None:
            if node['id'] == start:
                return self.drag['offset']
            node = self.model['nodes'].get(node['parent']) if node['parent'] else None
        return None

    def plan(self):
        self.advance()
        elements = []
        for item in self.runtime.snapshot(self.assets, visible_only=True, slider_states=True):
            if not item['visible'] or item['tag'] == 'Group':
                continue
            rect = item['rect']
            if rect['w'] <= 0 or rect['h'] <= 0:
                continue
            offset = self.offset_for(item['id'])
            if offset:
                rect = {**rect, 'x': rect['x'] + offset[0], 'y': rect['y'] + offset[1]}
            asset = item.get('asset')
            meta = self.assets.get(asset) if asset else None
            tracks = tracks_for(self.model, self.runtime, item['id'], self.assets)
            elements.append({
                'id': item['id'], 'tag': item['tag'], 'rect': {k: round(v, 3) for k, v in rect.items()},
                'alpha': round(item['alpha'], 4), 'text': item.get('text') or '', 'size': item.get('size') or 0,
                'color': item.get('color') or '#ffffff', 'name': item.get('name') or item['tag'],
                'url': meta['url'] if meta else None, 'kind': meta['kind'] if meta else ('text' if item['tag'] in {'Text', 'Time', 'Date', 'DateTime', 'Number'} else None),
                'tracks': tracks,
            })
        controls = [t for t in self.model['triggers'] if t['action'] in {'down', 'up', 'move', 'long', 'tap', 'click', 'double'}]
        owners = sorted({t['owner'] for t in controls})
        self.issues.update(self.runtime.issues)
        drag = None
        if self.drag:
            drag = {'active': True, 'name': self.drag['name'], 'mode': self.drag['mode'],
                    'progress': self.drag['progress'], 'reached': self.drag['reached'],
                    'rebounding': bool(self.drag.get('released')),
                    'offset': [round(v, 3) for v in self.drag['offset']],
                    'direction': list(self.drag['direction']), 'length': round(self.drag['length'], 3),
                    'follow': self.drag['follow']}
        return {
            'revision': self.revision, 'name': self.name, 'source': str(self.source),
            'canvas': {'width': self.width, 'height': self.height}, 'timeMs': self.runtime.time_ms,
            'elements': elements, 'interactive': owners, 'drag': drag, 'unlocked': self.unlocked,
            'variables': {name: self.runtime.lookup(name) for name in list(self.model['variables'])[:40]},
            'diagnostics': [{'key': k, 'message': v} for k, v in list(self.issues.items())[:60]],
            'counts': {'elements': len(elements), 'triggers': len(self.model['triggers']),
                       'controls': len(controls), 'variables': len(self.model['variables']),
                       'assets': len(self.assets)},
            'history': self.history[-12:],
        }

    def gesture(self, body):
        self.advance()
        kind = body.get('kind')
        if kind not in GESTURES:
            raise ValueError('无效手势类型')
        x = float(body.get('x', 0))
        y = float(body.get('y', 0))
        to_x = float(body.get('toX', x))
        to_y = float(body.get('toY', y))
        duration = float(body.get('durationMs', 0) or 0)
        visible = self.runtime.snapshot(self.assets, visible_only=True)
        dx, dy = to_x - x, to_y - y
        direction = ('left' if dx < 0 else 'right') if abs(dx) > abs(dy) else ('up' if dy < 0 else 'down')
        owner = pick_owner(visible, x, y, self.model,
                           {'width': self.width, 'height': self.height},
                           gesture=direction if kind == 'swipe' else None)
        if not owner:
            return {'hit': False, 'message': '该位置没有可响应控件', 'plan': self.plan()}
        rect = next(item['rect'] for item in visible if item['id'] == owner)
        sample = [(to_x - rect['x']) / max(rect['w'], 1e-6), (to_y - rect['y']) / max(rect['h'], 1e-6),
                  (x - rect['x']) / max(rect['w'], 1e-6), (y - rect['y']) / max(rect['h'], 1e-6)]
        action = {'owner': owner, 'kind': kind, 'sample': sample, 'durationMs': duration}
        before = copy.deepcopy(self.runtime.state)
        outcome = perform_action(self.runtime, action, self.assets, verify_hit=True)
        if not outcome.get('valid'):
            self.runtime.state = before
            self.runtime.cache.clear()
            return {'hit': False, 'owner': owner, 'message': outcome.get('reason', '手势未产生引擎事件'), 'plan': self.plan()}
        self.issues.update(self.runtime.issues)
        self.base_time = self.runtime.time_ms
        self.epoch = time.monotonic()
        self.revision += 1
        self.history.append({'kind': kind, 'owner': self.model['nodes'][owner]['attributes'].get('name') or self.model['nodes'][owner]['tag'],
                             'executed': len(outcome.get('executed') or []), 'at': datetime.datetime.now().strftime('%H:%M:%S')})
        return {'hit': True, 'owner': owner, 'executed': outcome.get('executed'),
                'timeline': outcome.get('timeline'), 'plan': self.plan()}


def serve_live(source=None, port=8767, output=Path('preview-live'), allow_import=True, vlm_config=None):
    source = Path(source).resolve() if source else None
    output = Path(output).resolve()
    token = secrets.token_urlsafe(32)
    state = {'session': LiveSession(source, output / uuid.uuid4().hex[:8]) if source else None}
    from multires_app.preview.reconstruction_live import ReconstructionJobs
    reconstruction = ReconstructionJobs(output,vlm_config)
    guard = threading.Lock()
    page = Path(__file__).with_name('live_preview.html').read_text(encoding='utf-8')
    script = Path(__file__).with_name('live_preview.js').read_text(encoding='utf-8')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def send_json(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path in {'/', '/index.html'}:
                config = json.dumps({'token': token, 'api': '/api/', 'import': allow_import})
                data = page.replace('__LIVE_CONFIG__', config).replace('__SCRIPT__', script).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            if path.startswith('/assets/'):
                session = state['session']
                if session is None:
                    self.send_error(404)
                    return
                target = (session.output / path.lstrip('/')).resolve()
                if not target.is_relative_to(session.output) or not target.is_file():
                    self.send_error(404)
                    return
                data = target.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'private, max-age=3600')
                self.end_headers()
                self.wfile.write(data)
                return
            if path.startswith('/reconstruction/'):
                target=reconstruction.artifact(path)
                if target is None:
                    self.send_error(404);return
                data=target.read_bytes();self.send_response(200)
                self.send_header('Content-Type',mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers()
                if self.command!='HEAD':self.wfile.write(data)
                return
            self.send_error(404)

        do_HEAD = do_GET

        def do_POST(self):
            if self.headers.get('X-Live-Token') != token:
                self.send_json(403, {'error': 'invalid token'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                self.send_json(400, {'error': 'invalid length'})
                return
            operation = urlsplit(self.path).path
            if operation == '/api/import':
                if not allow_import:
                    self.send_json(403, {'error': '导入已禁用'})
                    return
                if not 0 < length <= 300 * 1024 * 1024:
                    self.send_json(413, {'error': '文件为空或超过 300MB'})
                    return
                name = Path(unquote(self.headers.get('X-Filename', 'theme.hwt'))).name
                if not name.lower().endswith(('.hwt', '.zip')):
                    self.send_json(400, {'error': '只支持 .hwt / .zip'})
                    return
                target = output / 'imports' / f'{uuid.uuid4().hex[:8]}-{name}'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.rfile.read(length))
                try:
                    with guard:
                        state['session'] = LiveSession(target, output / uuid.uuid4().hex[:8], filename=name)
                        state.pop('original_session',None)
                        payload = state['session'].plan()
                except Exception as exc:
                    target.unlink(missing_ok=True)
                    self.send_json(400, {'error': f'主题解析失败：{exc}'})
                    return
                self.send_json(200, payload)
                return
            if length > 1024 * 1024:
                self.send_json(413, {'error': 'request too large'})
                return
            try:
                body = json.loads(self.rfile.read(length) or b'{}')
            except ValueError:
                self.send_json(400, {'error': 'invalid json'})
                return
            if not isinstance(body,dict):
                self.send_json(400,{'error':'请求必须为 JSON 对象'});return
            session = state['session']
            if operation.startswith('/api/reconstruction/'):
                try:
                    with guard:
                        action=operation.rsplit('/',1)[-1]
                        if action=='inspect':result=reconstruction.inspect(body.get('project',''),state.get('original_session',session))
                        elif action=='analyse':result=reconstruction.analyse(state.get('original_session',session))
                        elif action=='start':result=reconstruction.start(body,state.get('original_session',session))
                        elif action=='status':result=reconstruction.status(body.get('id',''))
                        elif action=='activate':
                            result=reconstruction.activate(body.get('id',''))
                            reconstruction.inspect(result['project'],state.get('original_session',session))
                            candidate=LiveSession(Path(result['package']),output/uuid.uuid4().hex[:8])
                            candidate.width=result['viewport']['width'];candidate.height=result['viewport']['height'];candidate.reset()
                            if session is not None:state.setdefault('original_session',session)
                            state['session']=candidate;result=candidate.plan()
                        elif action=='restore':
                            original=state.pop('original_session',None)
                            if original is None:raise ValueError('当前没有待返回的原主题')
                            state['session']=original;result=original.plan()
                        else:raise ValueError('未知重构操作')
                    self.send_json(200,result)
                except (ValueError,KeyError,OSError) as exc:self.send_json(400,{'error':str(exc)[:700]})
                except Exception:self.send_json(500,{'error':'布局重构请求失败，请查看任务报告'})
                return
            if session is None:
                self.send_json(409, {'error': '尚未导入主题'})
                return
            try:
                with guard:
                    if operation == '/api/gesture':
                        result = session.gesture(body)
                    elif operation == '/api/press':
                        result = session.press(body)
                    elif operation == '/api/drag':
                        result = session.drag_phase(body)
                    elif operation == '/api/plan':
                        result = session.plan()
                    elif operation == '/api/reset':
                        session.reset()
                        result = session.plan()
                    else:
                        self.send_error(404)
                        return
                self.send_json(200, result)
            except (ValueError, KeyError) as exc:
                self.send_json(400, {'error': str(exc)})
            except Exception as exc:
                self.send_json(500, {'error': '预览执行失败：' + str(exc)})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}/',
                      'theme': str(source) if source else None}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
