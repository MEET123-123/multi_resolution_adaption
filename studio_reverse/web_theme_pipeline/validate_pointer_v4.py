"""Pointer-level source-device replay of generated interactive themes.
Tests hit regions and gestures, not only calling XML trigger IDs.
"""
import argparse, json, sys, tempfile
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[2]/'src'),str(Path(__file__).resolve().parent)]
import interaction_families_v4 as v4
from multires_app.preview.live_preview import LiveSession

def run(root):
 root=Path(root);pairs={}
 for line in (root/'reports'/'pairs.jsonl').open(encoding='utf8'):
  row=json.loads(line)
  pairs[row['theme']]=row['family']
 output=[]
 for theme,family in pairs.items():
  theme_file=root/'themes'/(theme+'.hwt')
  with tempfile.TemporaryDirectory() as tmp:
   ses=LiveSession(theme_file,Path(tmp))
   cases=[]
   for state in v4.states(family):
    ses.reset();issues=[]
    for event in state['trace']:
     rule=next(r for r in v4.FAMILIES[family] if r[0]==event)
     _,event_action,_,_,rect,_=rule
     kind={'up':'tap','double':'double-tap','long':'long-press'}[event_action]
     x=rect[0]+rect[2]/2;y=rect[1]+rect[3]/2
     try:
      result=ses.gesture({'kind':kind,'x':x,'y':y,'toX':x,'toY':y,
                          'durationMs':680 if kind=='long-press' else 75})
      owner=result.get('owner')
      actual_owner=ses.model['nodes'][owner]['attributes'].get('name') if owner else None
      if not result.get('hit') or actual_owner!='v4-'+event:
       issues.append({'event':event,'hit':result.get('hit'),'owner':actual_owner,
                      'message':result.get('message')})
     except Exception as exc:
      issues.append({'event':event,'error':str(exc)[:200]})
    actual={v:int(round(float(ses.runtime.lookup(v)))) for v in v4.INITIAL[family]}
    if actual!=state['variables']:
     issues.append({'stateMismatch':True,'actual':actual,'expected':state['variables']})
    cases.append({'state':state['id'],'path':state['trace'],'passed':not issues,'issues':issues})
   output.append({'theme':theme,'family':family,'passed':sum(c['passed'] for c in cases),
                  'total':len(cases),'cases':cases})
 summary={'testedThemes':len(output),'passedThemes':sum(a['passed']==a['total'] for a in output),
          'testedStatePaths':sum(a['total'] for a in output),
          'passedStatePaths':sum(a['passed'] for a in output)}
 (root/'reports'/'pointer-replay-v4.json').write_text(json.dumps({'summary':summary,'themes':output},
                                          ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(summary,ensure_ascii=False))
 for a in output:
  if a['passed']!=a['total']:
   print(a['family'],a['passed'],'/',a['total'])
   for c in a['cases']:
    if not c['passed']:print(' ',c['state'],str(c['issues'])[:600])
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--dataset',required=True)
 a=p.parse_args();run(a.dataset)
