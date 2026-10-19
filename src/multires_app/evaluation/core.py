"""Versioned observations and a failure-isolating evaluation plugin boundary."""
from dataclasses import asdict, dataclass, field
import hashlib
import importlib
import json
import math
import re
from pathlib import Path
from typing import Protocol, Iterable

SCHEMA = 'multires-feedback/1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
    temporary.replace(path)


@dataclass
class Observation:
    rule: str
    status: str  # pass, fail, review, unknown, not_applicable
    severity: str = 'P1'
    confidence: float = 1.
    device: str = ''
    state: str = ''
    members: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    axis: str = ''
    residual: float = 0.
    stage: str = 'layout'
    message: str = ''

    def validate(self):
        if not isinstance(self.rule, str) or not self.rule or not isinstance(self.members, list) or not all(isinstance(c, str) for c in self.members):
            raise ValueError('rule and string member list required')
        if self.status not in {'pass', 'fail', 'review', 'unknown', 'not_applicable'}:
            raise ValueError('invalid observation status')
        if self.severity not in {'P0', 'P1', 'P2'} or not 0 <= self.confidence <= 1:
            raise ValueError('invalid severity/confidence')
        if not math.isfinite(self.residual) or self.residual < 0:
            raise ValueError('invalid normalized residual')
        json.dumps(asdict(self), allow_nan=False)


def validate_record(row):
    fields = Observation.__dataclass_fields__
    Observation(**{k: v for k, v in row.items() if k in fields}).validate()
    if row.get('schema') != SCHEMA or not re.fullmatch(r'[0-9a-f]{24}', row.get('id', '')):
        raise ValueError('unsupported schema or invalid case id')
    if not isinstance(row.get('trace'), list) or not isinstance(row.get('plugin'), dict) or not row['plugin'].get('version'):
        raise ValueError('trace/plugin provenance missing')
    for path in row.get('artifacts', {}).values():
        if not isinstance(path, str) or path.startswith(('/', '\\')) or ':' in path or '..' in path.replace('\\', '/').split('/'):
            raise ValueError('artifact path must be contained and relative')
    json.dumps(row, allow_nan=False)


def validate_options(options):
    if not isinstance(options, dict):
        raise ValueError('evaluation config must be an object')
    json.dumps(options, allow_nan=False)
    for name, low, high in [('evidenceBudget', 0, 1000), ('replayTraces', 0, 100),
                            ('replaySteps', 1, 100), ('passSamples', 0, 100),
                            ('responsiveSteps', 3, 64), ('responsiveBudget', 3, 64),
                            ('responsiveStates', 1, 16), ('responsiveDevices', 1, 8)]:
        value = options.get(name, low)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(name + ' is outside its evaluation budget')
    for name, low, high in [('centerTolerance', 0., .05), ('responsiveFraction', .001, .3)]:
        value = options.get(name, .001 if name == 'centerTolerance' else .08)
        if not isinstance(value, (float, int)) or not low <= value <= high:
            raise ValueError('invalid ' + name)
    if any(axis not in {'width', 'height'} for axis in options.get('responsiveAxes', ['width', 'height'])):
        raise ValueError('responsiveAxes must contain width/height')


class Evaluator(Protocol):
    name: str
    version: str

    def evaluate(self, context: 'Context') -> Iterable[Observation]: ...


class Refiner(Protocol):
    name: str
    version: str

    def propose(self, project: dict, observations: list[dict], options: dict) -> dict: ...


@dataclass
class Context:
    project: dict
    path: Path
    options: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)

    @property
    def identity(self):
        if 'identity' not in self.cache:
            p = self.project
            self.cache['identity'] = digest({'model': p['model'], 'sourceProfile': p['sourceProfile'],
                'sourceDigest': self.cache.get('sourceDigest'),
                'sourceLayouts': p.get('sourceDevice', {}).get('layouts'),
                'assets': {k: {a: v for a, v in m.items() if a not in {'url', 'visualBounds', 'visualBoundsPolicy'}}
                           for k, m in p.get('assets', {}).items()},
                'traces': [(s['id'], s.get('sourceTrace', s.get('trace'))) for s in p['graph']['states']]})
        return self.cache['identity']

    def geometry(self, layout):
        from multires_app.layout.visual_geometry import component_visuals
        key = ('geometry', id(layout))
        if key not in self.cache:
            self.cache[key] = component_visuals(self.project, layout)
        return self.cache[key]


def load_plugin(spec):
    """Explicit trusted Python extensions only; never load names from theme XML."""
    module, separator, attribute = spec.partition(':')
    if not separator:
        raise ValueError('plugin must be module:factory')
    result = getattr(importlib.import_module(module), attribute)()
    if not isinstance(result.name, str) or not isinstance(result.version, str):
        raise ValueError('plugin name/version required')
    return result


def evaluate(context, plugins):
    # Cached preview geometry is identity keyed. An evaluation boundary must
    # not reuse it after in-place edits or a previous algorithm candidate.
    from multires_app.layout.visual_geometry import _GEOMETRY_CACHE
    _GEOMETRY_CACHE.clear()
    records = []
    states = {s['id']: s for s in context.project['graph']['states']}
    roles = {c['id']: c.get('role', 'content') for c in context.project['components']}
    for plugin in plugins:
        try:
            # Isolate partial results too: a crashing evaluator cannot emit a
            # partial set of apparently passing checks.
            observations = list(plugin.evaluate(context))
            for observation in observations:
                observation.validate()
        except Exception as exc:
            observations = [Observation('SYSTEM.PLUGIN_ERROR', 'unknown', 'P0', stage='evaluation',
                            evidence={'plugin': plugin.name, 'error': str(exc)}, message='评价器失败，未判为通过')]
        for observation in observations:
            row = asdict(observation)
            trace = states.get(row['state'], {}).get('sourceTrace', states.get(row['state'], {}).get('trace', []))
            row.update(schema=SCHEMA, project=context.path.parent.parent.name,
                       inputIdentity=context.identity, plugin={'name': plugin.name, 'version': plugin.version},
                       trace=trace, phase='stable', timeMs=next((l.get('timeMs') for d in context.project['devices']
                           for l in d['layouts'] if l['state'] == row['state']), None))
            row['issueType'] = ('B0' if row['rule'].startswith(('INPUT.', 'EXPORT.')) else
                'B5' if row['stage'] == 'interaction' else 'B6' if row['stage'] == 'aesthetic' else
                'B4' if row['rule'] == 'GEO.FIXED_COMPONENT' else 'B1' if row['rule'] in
                {'GEO.TRANSFORM', 'GEO.CONTAINMENT'} else 'B9' if row['status'] == 'unknown' else 'B2')
            row['rootCauseStatus'] = 'suspected'
            row['nativeValidation'] = 'needs_native_validation'
            key = [context.identity, row['device'], row['state'], digest(trace), row['rule'],
                   sorted(row['members']), row['axis'], plugin.name]
            row['id'] = digest(key)[:24]
            target = next((d for d in context.project['devices'] if d['profile']['id'] == row['device']), {})
            vp = target.get('viewport', {'width': 1, 'height': 1})
            aspect = round(vp['width'] / max(1, vp['height']) * 4) / 4
            row['cluster'] = digest([row['rule'], row['axis'], aspect,
                sorted(roles.get(c, 'content') for c in row['members']),
                row['evidence'].get('direction', ''), row['stage']])[:16]
            records.append(row)
    ids = [r['id'] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate evaluation keys; plugins must identify members/axis/state')
    return records
