"""Audit shared panel/clock sizing and concrete open/close gestures."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.preview.interaction_server import InteractiveSession
from multires_app.layout.anchor_validation import validate_export


def check(path):
    p=json.loads(path.read_text(encoding='utf-8'));states={s['id']:s for s in p['graph']['states']}
    families=p.get('resourceScaleFamilies',[]);panels={c for f in families for c in f['panels']}
    errors=[];checks=0;opened=0;records=[]
    initial=next((e for e in p['graph']['transitions'] if e['to'] and
                  not any(c in p['devices'][0]['layouts'][int(e['from'][6:])]['transforms'] for c in panels) and
                  any(c in p['devices'][0]['layouts'][int(e['to'][6:])]['transforms'] for c in panels)),None)
    if not families or not initial:raise ValueError('no shared panel family or executable opening edge')
    closing=next((e for e in p['graph']['transitions'] if e['from']==initial['to'] and e['to'] and
                  not any(c in p['devices'][0]['layouts'][int(e['to'][6:])]['transforms'] for c in panels)),None)
    if not closing:raise ValueError('no executable panel closing edge')
    for d in p['devices']:
        validate_export(p,d)
        for l in d['layouts']:
            for cid,scale in d.get('sharedResourceScales',{}).items():
                if cid not in l['transforms']:continue
                checks+=1
                if any(abs(l['transforms'][cid][axis]-scale)>1e-7 for axis in ('sx','sy')):
                    errors.append({'target':d['profile']['id'],'state':l['state'],'component':cid,'reason':'scale drift'})
        steps=[]
        try:
            session=InteractiveSession(p,d['profile']['id'],initial['from'])
            for edge in (initial,closing):
                result=session.gesture({'owner':edge['owner'],'kind':edge['action'],'sample':edge['sample'],'gesture':edge['gesture']})
                comparison=result['comparison']
                steps.append({'gesture':edge['gesture'],'sourceValid':comparison['sourceOutcome']['valid'],
                              'targetValid':comparison['targetOutcome']['valid'],'processFrames':len(result['processFrames']),
                              'sourceControls':{n:session.source_runtime.lookup(n) for n in edge['changes']},
                              'targetControls':{n:session.runtime.lookup(n) for n in edge['changes']}})
            passed=all(s['sourceValid'] and s['targetValid'] and s['sourceControls']==s['targetControls'] for s in steps)
            opened+=1
            if not passed:errors.append({'target':d['profile']['id'],'reason':'source/target gesture mismatch'})
            records.append({'target':d['profile']['id'],'passed':passed,'steps':steps,'scaleFamilies':d['resourceScaleFamilies']})
        except Exception as exc:
            errors.append({'target':d['profile']['id'],'reason':str(exc)});records.append({'target':d['profile']['id'],'passed':False,'steps':steps})
        print(json.dumps({'target':d['profile']['id'],'passed':records[-1]['passed']}),flush=True)
    report={'project':path.parent.parent.name,'devices':len(p['devices']),'states':sum(len(d['layouts']) for d in p['devices']),
            'sharedScaleChecks':checks,'openCloseDevices':opened,'failures':len(errors),'examples':errors[:20],'records':records,
            'scope':'cached states and concrete source/target panel open/close gestures; not native device validation'}
    (path.parent/'panel-scale-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if errors:raise SystemExit(1)


if __name__=='__main__':check(Path(sys.argv[1]))
