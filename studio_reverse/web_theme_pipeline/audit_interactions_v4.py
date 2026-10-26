"""Audit v4 generated data: distinct state graphs, shared anchors, XML, and exact trigger replay.
Interaction replay is the local legacy interpreter, not proof of HarmonyOS 5/6 support.
"""
from __future__ import annotations
import argparse,collections,json,sys,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import interaction_families_v4 as v4
from multires_app.engine.scene_engine import load_model,Runtime
from multires_app.interaction.interaction_semantics import initialize

def event_by_name(model,name):
 for t in model.get('triggers',[]):
  owner=model['nodes'].get(t.get('owner'),{})
  if owner.get('attributes',{}).get('name')=='v4-'+name:
   return t
 return None

def inspect_theme(path,family):
 model=load_model(path)
 runtime=initialize(model,1080,2400)
 problem=[];checks=0
 for st in v4.states(family):
  runtime=initialize(model,1080,2400)
  for event in st['trace']:
   trig=event_by_name(model,event)
   if trig is None:problem.append({'state':st['id'],'event':event,'reason':'missing XML trigger'});break
   try:
    runtime.execute(trig)
    runtime.cache.clear()
   except Exception as exc:
    problem.append({'state':st['id'],'event':event,'reason':str(exc)[:200]});break
  vals={name:int(round(float(runtime.lookup(name)))) for name in v4.INITIAL[family]}
  checks+=1
  if vals!=st['variables']:problem.append({'state':st['id'],'expected':st['variables'],'got':vals})
 return {'family':family,'checks':checks,'passed':not problem,'errors':problem[:15],
         'triggers':len(model.get('triggers',[]))}

def audit(folder):
 root=Path(folder);manifest=json.loads((root/'reports'/'manifest.json').read_text(encoding='utf8'))
 counts=collections.Counter();families={};samples={};shared={};seen=set();errors=[]
 with (root/'reports'/'pairs.jsonl').open(encoding='utf8') as fp:
  for line in fp:
   p=json.loads(line);identity=(p['theme'],p['device'],p['state'])
   if identity in seen:errors.append('duplicate '+str(identity))
   seen.add(identity)
   counts[p['split']]+=1;families[p['theme']]=p['family']
   samples.setdefault((p['theme'],p['device']),set()).add(p['state'])
   fixed=tuple(tuple(p['answer']['groups'][g]) for g in p['request']['sharedLockedGroups'])
   basekey=(p['theme'],p['device'])
   if basekey in shared and shared[basekey]!=fixed:errors.append('cross-state anchor drift: '+str(basekey))
   shared[basekey]=fixed
   path=root/p['sourceImage'];target=root/p['targetImage']
   if not path.is_file() or not target.is_file():errors.append('missing screenshot: '+str(identity))
 if sum(counts.values())!=manifest['pairs']:errors.append('row-count mismatch')
 verified=[]
 for theme,family in families.items():
  path=root/'themes'/(theme+'.hwt')
  if not zipfile.is_zipfile(path):errors.append('invalid HWT: '+str(path));continue
  with zipfile.ZipFile(path) as z:
   if z.testzip() is not None:errors.append('corrupted archive '+str(path))
   if 'unlock/lockscreen/manifest.xml' not in z.namelist():errors.append('missing manifest '+str(path))
  try:verified.append({'theme':theme,**inspect_theme(path,family)})
  except Exception as exc:verified.append({'theme':theme,'passed':False,'errors':[str(exc)[:250]]})
  n=len(v4.states(family))
  for dev in v4.base.DEV:
   if len(samples.get((theme,dev[0]),set()))!=n:errors.append('missing states '+theme+' '+dev[0])
 local_pass=sum(bool(v.get('passed')) for v in verified)
 output={'directory':str(root),'pairs':sum(counts.values()),'splits':dict(counts),'themes':len(families),
         'families':dict(collections.Counter(families.values())),
         'localReplayPassed':local_pass,'localReplayAttempted':len(verified),
         'modelTrainingEligible':not errors and local_pass==len(verified),
         'warnings':['XML exact-trigger replay does not simulate pointer targeting or true native animations',
                     'All geometry labels are deterministic weak teacher; no human-designed target labels'],
         'errors':errors[:50],'replays':verified}
 (root/'reports'/'audit-v4.json').write_text(json.dumps(output,indent=2,ensure_ascii=False),encoding='utf8')
 print(json.dumps({k:v for k,v in output.items() if k!='replays'},ensure_ascii=False,indent=2))
 return output

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--dataset',required=True)
 a=p.parse_args();result=audit(a.dataset)
 if not result['modelTrainingEligible']:sys.exit(1)
