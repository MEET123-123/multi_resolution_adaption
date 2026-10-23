"""Build portable algorithm-supervised pairs from existing real theme caches.

No human-review records are fabricated. Raw elements stay in evidence files;
SFT sees compact semantic groups and source facts only.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import html
import json
import math
import platform
import shutil
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(REPO / 'src'))
if (ROOT / 'code' / 'src').is_dir(): sys.path.insert(0, str(ROOT / 'code' / 'src'))
TARGETS = ['tablet-mock-16-10-portrait', 'tablet-mock-16-10-landscape', 'dual-fold-mock-inner-portrait']


def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    temp.replace(path)
def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()
def digest(value): return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def jsonl(path, rows):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf8') as stream:
        for row in rows: stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
def rows(path):
    with Path(path).open(encoding='utf8') as stream:
        for line in stream:
            if line.strip(): yield json.loads(line)


def compact_input(request):
    """A source-only whitelist: never inspect the target answer."""
    clean = {k: copy.deepcopy(request[k]) for k in ('schema', 'sourceViewport', 'safeRegion', 'targetViewport', 'requestKind', 'mediaPolicy')}
    clean['groups'] = []
    for group in request['groups']:
        item = {k: copy.deepcopy(group[k]) for k in ('id', 'sourceBox', 'roles', 'memberCount', 'container', 'anchor', 'centerAxes', 'edges', 'fixed')}
        item['roles'] = sorted({str(r) for r in item['roles'] if r}) or ['unknown']
        clean['groups'].append(item)
    layout = {'schema': request['schema'], 'groups': {g['id']: g['sourceBox'] for g in clean['groups']},
              'group_coordinate_system': 'source_canvas_normalized_1000', 'task': 'source-to-target-layout'}
    return clean, layout


def template_tokens(model):
    values = []
    for node in model['nodes'].values():
        attrs = {k: v for k, v in node.get('attributes', {}).items() if k not in {'color', 'colorFilter', 'text', 'name', 'id'}}
        values.append(json.dumps([node['tag'], attrs], sort_keys=True, ensure_ascii=False))
    return {hashlib.sha256('\n'.join(values[i:i+4]).encode()).hexdigest() for i in range(max(1, len(values)-3))}


def worker(project_path, out, state_limit=10):
    from multires_app.engine.scene_engine import load_model
    from multires_app.tools.corpus_adapt import script_signature
    from multires_app.pipeline.representative_states import select_states
    from multires_app.pipeline.scene_clusters import schedule_device
    from multires_app.learning.algorithm import RecordingAnchor
    from multires_app.learning.dataset import actual_boxes
    from multires_app.learning.contract import normalized, validate_answer, SCHEMA
    from multires_app.learning.annotations import extract
    from multires_app.learning.render import render
    from multires_app.evaluation.checks import GeometryEvaluator, ContractEvaluator
    from multires_app.evaluation.core import Context
    project_path, out = Path(project_path).resolve(), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    original_sha = sha(project_path); p = read(project_path); source = Path(p['source'])
    source_sha = sha(source)
    native = load_model(source, p['model']['file'])
    if script_signature(native) != script_signature(p['model']): raise ValueError('cached script differs from source package')
    del native
    meta = {'project': str(project_path), 'projectSha256': original_sha, 'source': str(source), 'sourceSha256': source_sha,
            'manifest': p['model']['file'], 'templateTokens': sorted(template_tokens(p['model'])),
            'scriptSignature': script_signature(p['model']), 'status': 'building', 'pairs': 0, 'issues': [], 'sampling': []}
    save(out / 'worker.json', meta)
    devices = [d for d in p['devices'] if d['profile']['id'] in TARGETS]
    if not devices: raise ValueError('cache has no requested devices')
    selected = set()
    for device in devices:
        sample = select_states(p, device, state_limit); meta['sampling'].append({'device': device['profile']['id'], **sample})
        chosen = set(sample['states']); device['layouts'] = [l for l in device['layouts'] if l['state'] in chosen]; selected |= chosen
    p['sourceDevice']['layouts'] = [l for l in p['sourceDevice']['layouts'] if l['state'] in selected]
    states = {s['id']: s for s in p['graph']['states']}; sources = {l['state']: l for l in p['sourceDevice']['layouts']}
    p['devices'] = devices
    # Verify cached bitmap bytes used in sampled source states against HWT.
    asset_ids = {p['assetOrder'][r[6]] for l in p['sourceDevice']['layouts'] for r in l['rows'] if r[6] >= 0 and r[5] > 0}
    verified = 0
    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
        for aid in sorted(asset_ids):
            asset = p['assets'].get(aid, {}); url = unquote(urlsplit(asset.get('url', '')).path)
            cached = (project_path.parent / url).resolve()
            if not cached.is_relative_to((project_path.parent / 'assets').resolve()): raise ValueError('invalid cached asset path')
            if aid in names and cached.is_file():
                expected = hashlib.sha256(archive.read(aid)).hexdigest()
                if sha(cached) != expected: raise ValueError('cached bitmap differs: ' + aid)
                verified += 1
    meta['verifiedAssetFiles'] = verified
    collected, source_renders, signatures = [], {}, set()
    for device in devices:
        did = device['profile']['id']; p['devices'] = [device]
        try: schedule_device(p, device, RecordingAnchor())
        except Exception as exc:
            meta['issues'].append({'device': did, 'reason': 'solver', 'error': str(exc)[:500]}); continue
        checks = defaultdict(list); ctx = Context(p, project_path)
        for plugin in (GeometryEvaluator(), ContractEvaluator()):
            try:
                for observation in plugin.evaluate(ctx):
                    checks[observation.state].append({k: getattr(observation, k) for k in ('rule', 'status', 'severity', 'members', 'residual')})
            except Exception as exc:
                meta['issues'].append({'device': did, 'reason': 'evaluator', 'error': str(exc)[:500]})
                for layout in device['layouts']: checks[layout['state']].append({'rule': 'CHECK.ERROR', 'status': 'unknown'})
        for layout in device['layouts']:
            sid = layout['state']; proposal = layout.get('adaptationPlan', {}).get('layoutProposal')
            if not proposal or not proposal['request']['groups'] or proposal['key'] in signatures: continue
            if layout.get('layoutRequest', {}).get('kind') == 'reuse': continue
            signatures.add(proposal['key']); request = proposal['request']; plan = layout['adaptationPlan']
            identity = digest([source_sha, proposal['key']])[:24]
            try:
                bounds = actual_boxes(p, layout, plan['groups'])
                answer = {'schema': SCHEMA, 'groups': {alias: normalized(bounds[gid], request['safeRegion']) for alias, gid in proposal['mapping'].items()}}
                validate_answer(answer, request)
                if sid not in source_renders:
                    name = 'images/' + digest([source_sha, sid])[:24] + '-source.png'
                    source_renders[sid] = (name, render(p, p['sourceDevice'], sources[sid], project_path.parent / 'assets', out / name))
                source_image, source_render = source_renders[sid]
                target_image = 'images/' + identity + '-target.png'
                target_render = render(p, device, layout, project_path.parent / 'assets', out / target_image)
                annotation = extract(p, sources[sid], plan, states[sid])
                save(out / 'evidence' / (identity + '.json'), {'annotations': annotation,
                     'request': request, 'groupMap': proposal['mapping'], 'checks': checks[sid],
                     'state': states[sid], 'targetTransforms': layout['transforms'], 'targetViewport': device['viewport']})
                clean, inputs = compact_input(request)
                record = {'id': identity, 'requestKey': proposal['key'], 'sourceSha256': source_sha,
                          'sourceName': source.name, 'manifest': p['model']['file'], 'projectSha256': original_sha,
                          'device': did, 'state': sid, 'sourceImage': source_image, 'targetImage': target_image,
                          'rendering': {'source': source_render, 'target': target_render}, 'request': clean,
                          'inputLayout': inputs, 'label': {'status': 'weak', 'origin': 'anchor constraint solver; machine generated; no human review', 'answer': answer},
                          'checks': checks[sid], 'evidence': 'evidence/' + identity + '.json',
                          'groupCount': len(request['groups']), 'teacherVersion': 'RecordingAnchor/anchor-theme-layout-v1'}
                collected.append(record)
            except Exception as exc: meta['issues'].append({'device': did, 'state': sid, 'reason': 'pair', 'error': str(exc)[:500]})
        jsonl(out / 'pairs.jsonl', collected); save(out / 'worker.json', meta)
    if sha(project_path) != original_sha or sha(source) != source_sha: raise RuntimeError('input changed during build')
    meta.update(status='complete', pairs=len(collected)); jsonl(out / 'pairs.jsonl', collected); save(out / 'worker.json', meta)
    return {'source': source.name, 'pairs': len(collected), 'issues': len(meta['issues'])}


def families(metadata):
    parent = list(range(len(metadata)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, a in enumerate(metadata):
        for j, b in enumerate(metadata[:i]):
            ta, tb = set(a['templateTokens']), set(b['templateTokens'])
            similarity = len(ta & tb) / max(1, len(ta | tb))
            if a['sourceSha256'] == b['sourceSha256'] or a['scriptSignature'] == b['scriptSignature'] or similarity >= .88:
                parent[find(i)] = find(j)
    members = defaultdict(list)
    for i, item in enumerate(metadata): members[find(i)].append(item['sourceSha256'])
    return {item['sourceSha256']: 'family-' + digest(sorted(set(members[find(i)])))[:12] for i, item in enumerate(metadata)}


def split_families(names):
    ordered = sorted(set(names), key=lambda s: digest(['theme-layout-split-v1', s]))
    if len(ordered) < 3: raise ValueError('need at least three usable families for held-out splits')
    validation = max(1, round(len(ordered) * .15)); test = max(1, round(len(ordered) * .15))
    return {family: 'test' if i < test else 'validation' if i < test + validation else 'train' for i, family in enumerate(ordered)}


def exclusion(pair):
    if pair['groupCount'] > 16: return 'group_budget_over_16; requires local task decomposition'
    if any(v['status'] == 'incomplete' for v in pair['rendering'].values()): return 'incomplete_render'
    if any(c['status'] in {'fail', 'unknown', 'review'} for c in pair['checks']): return 'geometry_or_contract_check'
    if not pair['checks']: return 'no_geometry_evidence'
    return None


def finalize(run):
    from PIL import Image, ImageDraw
    from multires_app.learning.contract import SYSTEM, prompt, validate_answer
    run = Path(run); output = run / 'server-bundle'
    if output.exists(): raise ValueError('server bundle exists; preserve it and use a new run/version')
    workers = [path for path in sorted((run / 'workers').glob('*/worker.json')) if read(path).get('status') == 'complete']
    metadata = [read(path) for path in workers]; family_map = families(metadata)
    candidates, excluded = [], []
    for path in workers:
        for pair in rows(path.parent / 'pairs.jsonl'):
            pair['family'] = family_map[pair['sourceSha256']]; pair['_folder'] = path.parent
            reason = exclusion(pair)
            if reason:
                excluded.append({'id': pair['id'], 'source': pair['sourceName'], 'device': pair['device'], 'reason': reason,
                                 'rendering': pair['rendering'], 'checks': pair['checks']})
            else: candidates.append(pair)
    split_map = split_families(p['family'] for p in candidates)
    output.mkdir(parents=True); examples = defaultdict(list); indexes = []; pairs = []; seen = set(); counts = Counter()
    for pair in candidates:
        # Exact compact request + source image dedup, never scene-name padding.
        content_key = digest([sha(pair['_folder'] / pair['sourceImage']), pair['request']])
        if content_key in seen:
            excluded.append({'id': pair['id'], 'source': pair['sourceName'], 'reason': 'duplicate_source_and_request'}); continue
        seen.add(content_key); pair['split'] = split_map[pair['family']]
        validate_answer(pair['label']['answer'], pair['request'])
        for field in ('sourceImage', 'targetImage', 'evidence'):
            src = pair['_folder'] / pair[field]
            dst = output / pair[field]; dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists(): shutil.copy2(src, dst)
        example = {'messages': [{'role': 'system', 'content': SYSTEM},
                   {'role': 'user', 'content': prompt(pair['request'], pair['inputLayout'])},
                   {'role': 'assistant', 'content': json.dumps(pair['label']['answer'], ensure_ascii=False, separators=(',', ':'))}],
                   'images': [pair['sourceImage']]}
        examples[pair['split']].append(example)
        indexes.append({'id': pair['id'], 'family': pair['family'], 'split': pair['split'], 'origin': 'weak-teacher', 'sourceSha256': pair['sourceSha256'], 'task': 'source-to-target-layout'})
        pair.pop('_folder'); pairs.append(pair); counts[pair['split']] += 1
    for split in ('train', 'validation', 'test'): jsonl(output / (split + '.jsonl'), examples[split])
    jsonl(output / 'sample-index.jsonl', indexes); jsonl(output / 'pairs.jsonl', pairs); jsonl(output / 'samples.jsonl', pairs)
    jsonl(run / 'excluded.jsonl', excluded)
    sources = [{k: meta[k] for k in ('source', 'sourceSha256', 'project', 'projectSha256', 'manifest', 'pairs', 'issues', 'sampling', 'verifiedAssetFiles')} for meta in metadata]
    save(run / 'source-provenance.json', sources)
    manifest = {'schema': 'theme-layout-sft/1', 'counts': dict(counts), 'samples': len(pairs), 'includeWeak': True,
                'includeSynthetic': False, 'allowIncomplete': False, 'inputImages': 'source', 'imagePathPolicy': 'relative to bundle root; run training from this directory',
                'familySplits': split_map, 'sourcePackages': len({p['sourceSha256'] for p in pairs}), 'families': len(split_map),
                'groupLimit': 16, 'groupCounts': dict(Counter(p['groupCount'] for p in pairs)), 'excluded': dict(Counter(p['reason'] for p in excluded)),
                'supervision': 'algorithmic distillation; no human certification; not native paired ground truth', 'nativeValidation': 'not_measured',
                'familyPolicy': 'same source SHA / same script / >=0.88 template shingle similarity; conservative automatic grouping',
                'builderSha256': sha(__file__), 'python': platform.python_version(), 'targetDevices': TARGETS,
                'inputPolicy': 'source-only whitelist; no target coordinates, no target reference image, no full element list'}
    save(output / 'manifest.json', manifest)
    # Browse every accepted pair; references are for audit only, never SFT inputs.
    cards = []
    for pair in pairs:
        cards.append('<section><h3>' + html.escape(pair['sourceName'] + ' / ' + pair['device']) + '</h3><p>' + html.escape(pair['split'] + ' · ' + str(pair['groupCount']) + ' groups · machine teacher') + '</p><div>' + ''.join('<img loading="lazy" src="' + pair[k] + '">' for k in ('sourceImage', 'targetImage')) + '</div></section>')
    (output / 'gallery.html').write_text('<!doctype html><meta charset="utf-8"><title>真实主题布局信息对</title><style>body{font:15px system-ui;background:#eef2f6;margin:24px}section{background:white;padding:16px;margin:12px;border-radius:12px}section div{display:flex;gap:18px}img{max-width:46%;max-height:500px;object-fit:contain}</style><h1>源图 → 算法适配目标</h1><p>自动监督，未冒充人工标注。图像与几何检查通过；真机与设计质量另行评估。</p>' + ''.join(cards), encoding='utf8')
    tiles = []
    preview_counts = Counter()
    preview_pairs = []
    for pair in pairs:
        if preview_counts[pair['sourceSha256']] < 2:
            preview_pairs.append(pair); preview_counts[pair['sourceSha256']] += 1
    for pair in preview_pairs[:24]:
        tile = Image.new('RGB', (500, 300), 'white'); draw = ImageDraw.Draw(tile)
        draw.text((8, 5), pair['id'][:8] + ' / ' + pair['split'] + ' / ' + str(pair['groupCount']) + ' groups', fill='black')
        for i, key in enumerate(('sourceImage', 'targetImage')):
            with Image.open(output / pair[key]) as image:
                image.thumbnail((240, 270)); tile.paste(image, (i * 250 + (250-image.width)//2, 25))
        tiles.append(tile)
    contact = Image.new('RGB', (1000, math.ceil(len(tiles)/2)*300), '#dddddd')
    for i, tile in enumerate(tiles): contact.paste(tile, ((i % 2)*500, (i//2)*300))
    contact.save(output / 'contact-sheet.jpg')
    copy_runtime(output)
    return manifest


def copy_runtime(output):
    code = output / 'code'; code.mkdir()
    for source, destination in ((REPO / 'src', code / 'src'),):
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for filename in ('requirements-ml.txt', 'requirements-core.txt', 'pyproject.toml', 'README.md', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(REPO / filename, code / filename)
    for folder in ('configs',): shutil.copytree(REPO / folder, code / folder)
    shutil.copy2(__file__, output / 'build_training_pairs.py')
    shutil.copy2(ROOT / 'preflight_tokens.py', output / 'preflight_tokens.py')
    script = '''#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/code/src${PYTHONPATH:+:$PYTHONPATH}"
MODEL_PATH="${MODEL_PATH:?Set MODEL_PATH to the model directory or model id}"
GPU_COUNT="${GPU_COUNT:-1}"
OUT="${OUT:-$PWD/adapter-run-1}"
torchrun --standalone --nproc_per_node="$GPU_COUNT" --module multires_app.learning.qwen_runner train \\
 --model "$MODEL_PATH" --sft "$PWD" --out "$OUT" --allow-weak \\
 --max-steps "${MAX_STEPS:-100}" --rank 16 --gradient-accumulation 8 \\
 --max-pixels 200704 --max-length 8192 --learning-rate 0.0001 "$@"
'''
    (output / 'train_server.sh').write_text(script, encoding='utf8', newline='\n')
    (output / 'README.md').write_text('''# Real-theme layout distillation bundle

Source packages stay on the local machine. This bundle contains relative-path
images, compact source-only inputs, generated target geometry, evidence, and
runtime source code. Targets are algorithmic weak labels, not human/native gold.
Validation/test measure agreement with a held-out-family teacher, not independent
design quality. No labels are silently promoted to human-reviewed status.

On the server, install a CUDA-compatible PyTorch and the pinned ML/core
requirements in code/. The Windows requirements may need a Linux CUDA wheel
selection; do not install a Windows torch wheel on Linux. Then:

```bash
python build_training_pairs.py verify --run .
MODEL_PATH=/path/to/model GPU_COUNT=2 bash train_server.sh
```

The training script changes cwd to this folder, resolving all relative image
paths. Pass --precision fp16 for GPUs without BF16 support. MAX_STEPS/OUT can be
set by environment. The 100-step default is an engineering starting budget,
not a claim of convergence. No model training was run on the local machine.
Gallery target images are references only; SFT uses source images exclusively.
Only fixed semantic-group affine layouts are encoded; topology changes and
scenes needing more than 16 root groups stay outside this version.

After training, infer on the held-out families (samples.jsonl is the inference
index; it is not loaded as the user prompt, and labels are never sent to the model):

```bash
export PYTHONPATH="$PWD/code/src${PYTHONPATH:+:$PYTHONPATH}"
python -m multires_app.learning.qwen_runner infer --model "$MODEL_PATH" \\
 --adapter "$PWD/adapter-run-1" --dataset "$PWD" --split test --limit 1000 \\
 --max-pixels 200704 --max-length 8192 --max-new-tokens 2048 --out lora-predictions.json
python build_training_pairs.py score --run . --predictions lora-predictions.json
```

Repeat inference without --adapter to a different output path for the base
model comparison. Score reports teacher agreement and JSON validity only.
The current trainer reads train.jsonl; validation/test are for separate evaluation,
not automatic early stopping. Run the processor preflight again when changing
the model/tokenizer: python preflight_tokens.py --bundle . --processor-model /path/to/model --out token-preflight-server.json
''', encoding='utf8')


def verify(bundle):
    from PIL import Image
    from multires_app.learning.contract import validate_answer
    bundle = Path(bundle).resolve(); manifest = read(bundle / 'manifest.json'); pairs = {p['id']: p for p in rows(bundle / 'pairs.jsonl')}
    index = list(rows(bundle / 'sample-index.jsonl')); errors = []; counted = Counter(); family_splits = {}; source_splits = {}
    if len(index) != len(pairs) or len({item['id'] for item in index}) != len(index): errors.append('duplicate or incomplete sample index')
    if list(rows(bundle / 'samples.jsonl')) != list(pairs.values()): errors.append('inference index differs from pair evidence')
    for split in ('train', 'validation', 'test'):
        examples = list(rows(bundle / (split + '.jsonl'))); selected = [i for i in index if i['split'] == split]
        if len(examples) != len(selected): errors.append(split + ': index length mismatch')
        for item, example in zip(selected, examples):
            pair = pairs[item['id']]
            try:
                family, source = item['family'], item['sourceSha256']
                if family in family_splits and family_splits[family] != split: raise ValueError('family leakage')
                if source in source_splits and source_splits[source] != split: raise ValueError('source leakage')
                family_splits[family] = split; source_splits[source] = split
                expected_request, expected_layout = compact_input(pair['request'])
                if expected_layout != pair['inputLayout'] or expected_request != pair['request']: raise ValueError('non-whitelisted input')
                from multires_app.learning.contract import prompt
                if example['messages'][1]['content'] != prompt(expected_request, expected_layout): raise ValueError('SFT input differs from source-only whitelist')
                answer = json.loads(example['messages'][2]['content']); validate_answer(answer, expected_request)
                if answer != pair['label']['answer']: raise ValueError('answer mismatch')
                if example['images'] != [pair['sourceImage']] or len(example['images']) != example['messages'][1]['content'].count('<image>'): raise ValueError('image or target leakage')
                for name in (pair['sourceImage'], pair['targetImage'], pair['evidence']):
                    path = (bundle / name).resolve()
                    if not path.is_relative_to(bundle) or not path.is_file(): raise ValueError('nonportable/missing asset')
                    if path.suffix == '.png':
                        with Image.open(path) as image: image.verify()
                if exclusion(pair): raise ValueError('quality gate violated')
                counted[split] += 1
            except Exception as exc: errors.append({'id': item['id'], 'error': str(exc)})
    if dict(counted) != manifest['counts']: errors.append('manifest count mismatch')
    integrity = bundle / 'file-sha256.json'
    if integrity.exists():
        for name, expected in read(integrity).items():
            path = (bundle / name).resolve()
            if not path.is_relative_to(bundle) or not path.is_file() or sha(path) != expected:
                errors.append('file checksum mismatch: ' + name)
    if not all(counted[s] for s in ('train', 'validation', 'test')): errors.append('empty split')
    result = {'passed': not errors, 'counts': dict(counted), 'errors': errors, 'imagesPortable': not errors,
              'humanReviewed': False, 'nativeValidation': 'not_measured', 'teacherAgreementOnly': True,
              'fileIntegrityManifest': integrity.exists()}
    save(bundle / 'verification.json', result); return result


def score(bundle, predictions_path, split='test'):
    from multires_app.learning.contract import validate_answer
    predictions = read(predictions_path); selected = [p for p in rows(Path(bundle) / 'pairs.jsonl') if p['split'] == split]
    results = []; valid = 0
    for pair in selected:
        prediction = predictions.get(pair['requestKey'], {})
        try:
            if prediction.get('status') != 'valid': raise ValueError('missing or invalid prediction')
            answer = validate_answer(prediction['answer'], pair['request'])
            deltas = [abs(value - pair['label']['answer']['groups'][gid][i]) for gid, box in answer['groups'].items() for i, value in enumerate(box)]
            results.append({'id': pair['id'], 'valid': True, 'mae1000': sum(deltas)/len(deltas)}); valid += 1
        except (ValueError, KeyError, TypeError) as exc: results.append({'id': pair['id'], 'valid': False, 'error': str(exc)})
    output = {'split': split, 'samples': len(selected), 'valid': valid, 'validRate': valid/len(selected) if selected else None,
              'teacherMAE1000': sum(r['mae1000'] for r in results if r['valid'])/valid if valid else None,
              'scope': 'raw proposal agreement with generated teacher only; not human/native design quality or solver execution', 'cases': results}
    save(Path(predictions_path).with_suffix('.score.json'), output)
    return {k: value for k, value in output.items() if k != 'cases'}


def build(run, limit=100, jobs=2):
    run = Path(run).resolve(); run.mkdir(parents=True, exist_ok=True)
    paths = sorted((REPO / 'theme' / 'adaptation').glob('*/scenes/project.json'), key=lambda p: p.stat().st_size)[:limit]
    outcomes = []
    def launch(path):
        tag = path.parent.parent.name; out = run / 'workers' / tag; report = out / 'worker.json'
        if report.exists() and read(report).get('status') == 'complete': return {'cache': tag, 'status': 'resumed', 'pairs': read(report)['pairs']}
        out.mkdir(parents=True, exist_ok=True)
        with (out / 'build.log').open('w', encoding='utf8') as log:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), 'worker', '--project', str(path), '--run', str(out)], stdout=log, stderr=subprocess.STDOUT, timeout=900)
        return {'cache': tag, 'status': 'complete' if process.returncode == 0 else 'failed', 'exitCode': process.returncode,
                'pairs': read(report).get('pairs', 0) if report.exists() else 0}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        tasks = {pool.submit(launch, path): path for path in paths}
        for task in as_completed(tasks):
            try: result = task.result()
            except Exception as exc: result = {'cache': tasks[task].parent.parent.name, 'status': 'failed', 'error': str(exc)}
            outcomes.append(result); save(run / 'build-status.json', {'projects': outcomes, 'total': len(paths), 'finished': len(outcomes)})
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return {'projects': len(outcomes), 'status': dict(Counter(r['status'] for r in outcomes))}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('command', choices=['build', 'worker', 'finalize', 'verify', 'bundle', 'score'])
    parser.add_argument('--run', default=str(ROOT / 'runs' / 'auto-pairs-v1')); parser.add_argument('--project'); parser.add_argument('--limit', type=int, default=100); parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--predictions'); parser.add_argument('--split', choices=['train','validation','test'], default='test')
    args = parser.parse_args(); run = Path(args.run).resolve()
    if args.command == 'build': result = build(run, args.limit, args.jobs)
    elif args.command == 'worker': result = worker(args.project, run)
    elif args.command == 'finalize': result = finalize(run)
    elif args.command == 'verify': result = verify(run)
    elif args.command == 'score': result = score(run, args.predictions, args.split)
    else:
        target = run / 'server-bundle'; result = verify(target)
        if not result['passed']: raise ValueError('verification failed; archive not produced')
        files = [p for p in target.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'
                 and p.name not in {'file-sha256.json', 'verification.json'}]
        save(target / 'file-sha256.json', {p.relative_to(target).as_posix(): sha(p) for p in sorted(files)})
        if not verify(target)['passed']: raise ValueError('file integrity verification failed')
        archive = run / 'theme-layout-server-bundle.zip'
        if archive.exists(): raise ValueError('archive exists; preserve immutable output')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
            for path in sorted(files + [target / 'file-sha256.json', target / 'verification.json']):
                package.write(path, path.relative_to(target).as_posix())
        result = {'archive': str(archive), 'sha256': sha(archive), 'bytes': archive.stat().st_size}; save(run / 'archive.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 1 if result.get('passed') is False else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf8')
    raise SystemExit(main())
