"""Read-only input/resource checks. Export independently audits behavior trees."""
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs
from .core import Observation


def file_sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


class InputEvaluator:
    name = 'input'
    version = '1.0'

    def evaluate(self, ctx):
        from multires_app.engine.scene_engine import load_model
        p = ctx.project
        source = Path(p['source'])
        if not source.exists():
            yield Observation('INPUT.SOURCE', 'fail', 'P0', stage='input', evidence={'reason': 'source missing'})
            return
        try:
            original = load_model(source, p['model']['file'])
            unchanged = original['nodes'] == p['model']['nodes']
            yield Observation('INPUT.SCRIPT', 'pass' if unchanged else 'fail', 'P0', stage='input',
                              evidence={'sourceNodesMatch': unchanged})
        except Exception as exc:
            yield Observation('INPUT.SCRIPT', 'fail', 'P0', stage='input', evidence={'error': str(exc)})
        root = ctx.path.parent.resolve()
        problems = []
        checked = 0
        for name, meta in p.get('assets', {}).items():
            url = urlsplit(meta.get('url', ''))
            path = (root / unquote(url.path)).resolve()
            if url.scheme or url.netloc or not path.is_relative_to(root / 'assets') or not path.is_file():
                problems.append({'asset': name, 'reason': 'missing/unsafe cached asset'})
                continue
            checked += 1
            expected = parse_qs(url.query).get('v', [None])[0]
            if expected and ctx.options.get('verifyAssetHashes', True) and not file_sha(path).startswith(expected):
                problems.append({'asset': name, 'reason': 'cached asset hash mismatch'})
        yield Observation('INPUT.ASSETS', 'fail' if problems else 'pass' if checked else 'unknown', 'P0',
                          stage='input', evidence={'checked': checked, 'problems': problems})
