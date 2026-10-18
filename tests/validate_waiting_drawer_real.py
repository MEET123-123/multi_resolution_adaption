"""Targeted Half-Island diary drawer: cached geometry and real open/close."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from multires_app.layout.anchor_validation import validate_export
from multires_app.preview.interaction_server import InteractiveSession
from multires_app.pipeline.scene_clusters import transformed_box


def check(path):
    p=json.loads(path.read_text(encoding='utf8'))
    uid=next(u for u,n in p['model']['nodes'].items() if n['attributes'].get('src')=='app_p.png')
    index=p['nodeOrder'].index(uid);cid=p['nodeComponents'][uid]
    sources={l['state']:l for l in p['sourceDevice']['layouts']}
    opening=next(e for e in p['graph']['transitions'] if e['to'] and e.get('changes',{}).get('set_go')==1 and e.get('changes',{}).get('p_set')==1)
    closing=next(e for e in p['graph']['transitions'] if e['from']==opening['to'] and e.get('changes',{}).get('set_go2')==1)
    errors=[];records=[];closed=opened=0
    def panel(layout):
        r=next(r for r in layout['rows'] if r[0]==index)
        return transformed_box(dict(zip(('x','y','w','h'),r[1:5])),layout.get('transforms',{}).get(cid,dict(sx=1,sy=1,tx=0,ty=0)))
    for device in p['devices']:
        validate_export(p,device)
        for layout in device['layouts']:
            source=panel(sources[layout['state']]);target=panel(layout)
            if source['y']+source['h']<=.01:
                closed+=1
                if target['y']+target['h']>.01:errors.append({'state':layout['state'],'reason':'closed panel peeks inside'})
            elif source['y']>=-.01 and source['y']+source['h']<=p['sourceDevice']['viewport']['height']+.01:
                opened+=1
                if target['y']<-.01 or target['y']+target['h']>device['viewport']['height']+.01:errors.append({'state':layout['state'],'reason':'open panel outside canvas'})
        session=InteractiveSession(p,device['profile']['id'],opening['from']);steps=[]
        for edge in (opening,closing):
            response=session.gesture({'surface':'source','owner':edge['owner'],'kind':edge['action'],'sample':edge['sample'],'gesture':edge['gesture']})
            cmp=response['comparison'];layout=response['layout'];box=panel(layout)
            passed=cmp['sourceOutcome']['valid'] and cmp['targetOutcome']['valid'] and not cmp['warnings']
            passed=passed and all(session.runtime.lookup(k)==session.source_runtime.lookup(k) for k in ('set_go','set_go2','p_set'))
            passed=passed and (box['y']+box['h']>.01 if edge is opening else box['y']+box['h']<=.01)
            steps.append({'action':'open' if edge is opening else 'close','passed':passed,'targetPanel':box})
            if not passed:errors.append({'target':device['profile']['id'],'reason':'live open/close mismatch','comparison':cmp})
        records.append({'target':device['profile']['id'],'steps':steps})
        print(json.dumps({'target':device['profile']['id'],'passed':all(s['passed'] for s in steps)}),flush=True)
    report={'devices':len(p['devices']),'cachedLayouts':sum(len(d['layouts']) for d in p['devices']),
            'closedPanelChecks':closed,'openPanelChecks':opened,'liveOpenCloseTransactions':len(records)*2,
            'failures':len(errors),'examples':errors[:10],'records':records,'nativeDeviceTest':False}
    (path.parent/'drawer-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    if errors:raise SystemExit(1)


if __name__=='__main__':check(Path(sys.argv[1]))
