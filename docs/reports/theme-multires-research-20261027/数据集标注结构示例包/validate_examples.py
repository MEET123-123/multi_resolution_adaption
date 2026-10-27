"""Read-only checks for this synthetic pack; no production import or training."""
from pathlib import Path
import hashlib
import json
import math

root = Path(__file__).resolve().parent
def read(rel):
    return json.loads((root / rel).read_text(encoding='utf-8'))
def require(condition, message):
    if not condition:
        raise ValueError(message)
def digest(obj):
    return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()
def file_ref(rel):
    target = (root / rel).resolve()
    require(target.is_relative_to(root), 'Reference outside pack: '+rel)
    require(target.is_file(), 'Missing referenced file: '+rel)
manifest = read('manifest.json')
require(manifest['demo_only'] is True and manifest['training_eligible'] is False,
    'Pack must remain explicitly synthetic and ineligible for training')
for path in root.rglob('*.json'):
    json.loads(path.read_text(encoding='utf-8'))
samples = [json.loads(line) for line in (root/'samples.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
index = {s['id']:s for s in samples}
require(len(index)==len(samples), 'Duplicate sample id')
family_splits = {}
for s in samples:
    family_splits.setdefault(s['family'],set()).add(s['split'])
    require(s['label']['status']=='weak', 'Example must not become reviewed truth')
    require(s['requestKey']==digest(s['request']), 'requestKey mismatch')
    known={g['id'] for g in s['request']['groups']}
    answer=s['label']['answer']
    require(set(answer)=={'schema','groups'} and answer['schema']=='multires-layout/1','Invalid answer schema')
    require(set(answer['groups'])==known==set(s['groupMap']), 'Group ID mismatch')
    for box in answer['groups'].values():
        require(len(box)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in box),'Invalid xywh')
        require(min(box[2:])>0 and max(abs(v) for v in box)<=8000,'Unbounded box')
    for rel in [s['sourceImage'],s['targetImage'],s['annotations'],s['labels']['script']]:
        file_ref(rel)
    annotation=read(s['annotations'])
    nodes={e['source_node_id'] for e in annotation['elementLevel']['elements']}
    require(set(s['elementIndex'].values())<=nodes,'Missing source node identity')
    require(all(e['id'] in s['elementIndex'] and e['group'] in known for e in s['inputLayout']['elementLevel']), 'Invalid element alias')
require(all(len(v)==1 for v in family_splits.values()),'Family crosses splits')
template=read('templates/review.template.json')
for review in template['reviews']:
    s=index[review['id']]
    require(review['requestKey']==s['requestKey'],'Stale template requestKey')
    edits=review['annotationEdits']
    require(set(edits)<= {'elementSemantics','groupSemantics','sceneIntent'},'Unsupported annotationEdits field')
    require(set(edits.get('elementSemantics',{}))<=set(s['elementIndex']),'Unknown element alias')
    require(set(edits.get('groupSemantics',{}))<=set(s['groupMap']),'Unknown group alias')
trajectory=read('trajectories/demo_t001.json')
require(all(step['observed'] is None and step['native_evidence'] is None for step in trajectory['steps']), 'Unverified example must not contain fabricated observed results')
print(json.dumps({'format_checks':'passed','samples':len(samples),'families':len(family_splits),
    'actual_human_reviews':0,'actual_native_checks':0,'training_eligible':False},ensure_ascii=False))
