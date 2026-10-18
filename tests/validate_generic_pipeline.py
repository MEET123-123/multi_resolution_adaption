"""Reproducible offline real-package checks; does not claim native compatibility."""
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from multires_app.pipeline.scene_pipeline import export_package
from multires_app.tools.batch_convert import validate
from multires_app.preview.interaction_server import InteractiveSession


def check(directory):
    p=json.loads((directory/'project.json').read_text(encoding='utf-8'))
    source=Path(p['source']);digest=hashlib.sha256(source.read_bytes()).hexdigest()
    package=directory/'packages/tablet-landscape.hwt'
    export_package(directory/'project.json','tablet-landscape',package)
    structural=validate(directory,[package]);gestures=[]
    # One actual outgoing witness per sampled state, with both surfaces checked.
    for state in p['graph']['states']:
        candidates=[e for e in p['graph']['transitions'] if e['from']==state['id'] and e.get('to')]
        if not candidates:continue
        edge=next((e for e in candidates if e['to']!=state['id']),candidates[0])
        session=InteractiveSession(p,'tablet-landscape',state['id'])
        before=copy.deepcopy(session.runtime.state);source_before=copy.deepcopy(session.source_runtime.state)
        action={'owner':edge['owner'],'kind':edge['action'],'sample':edge['sample'],'gesture':edge['gesture'],'surface':'source'}
        try:
            r=session.gesture(action)
            outcomes=r['comparison'];session.back()
            gestures.append({'state':state['id'],'owner':edge['owner'],'kind':edge['action'],
                             'sourceValid':outcomes['sourceOutcome']['valid'],'targetValid':outcomes['targetOutcome']['valid'],
                             'undoRestored':session.runtime.state==before and session.source_runtime.state==source_before,
                             'warnings':outcomes['warnings']})
        except (ValueError,AssertionError) as exc:gestures.append({'state':state['id'],'error':str(exc)})
    result={'sourceSha256':digest,'sourceUnchanged':hashlib.sha256(source.read_bytes()).hexdigest()==digest,
            'structural':structural,'gestures':gestures,'gestureReviewCount':sum(bool(g.get('error')) or not g.get('targetValid') or not g.get('undoRestored') for g in gestures),
            'bindings':p['sceneAnalysis']['bindings'],'nativeDeviceTest':False,
            'scope':'Sampled geometry, archive integrity, one gesture per expanded state, history restore. Unexpanded branches and native effects remain unverified.'}
    (directory/'pipeline-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    directory=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'theme/generic-pipeline-demo'
    for slug in ['bear','dog']:
        result=check(directory/slug)
        print(json.dumps({'theme':slug,'geometryFailures':result['structural']['failures'],'gestures':len(result['gestures']),
                          'gestureReviewCount':result['gestureReviewCount'],'sourceUnchanged':result['sourceUnchanged']},ensure_ascii=False),flush=True)
