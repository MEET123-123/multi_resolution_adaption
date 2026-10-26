"""Validate complete interaction paths on source/target HWTs and render browser feedback.
Fail-closed synthetic SFT gate. Not a Huawei-native renderer or designer-truth metric.
"""
import argparse,collections,json,sys,tempfile
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[2]/'src'),str(Path(__file__).resolve().parent)]
from multires_app.preview.live_preview import LiveSession
from multires_app.preview.live_geometry import slider_geometry
from playwright.sync_api import sync_playwright
import interaction_families_v4 as g
from browser_runtime_render import render

def records(path):
 with (path/'reports'/'pairs.jsonl').open(encoding='utf8') as fp:
  for line in fp:yield json.loads(line)

def replay(session,family,trace,device,expected):
 """Hit the XML's actual visible control, but also validate adapted hitbox geometry.

 A gesture replay must not silently pass an unscaled/old target HWT by
 clicking its stale source-coordinate control.
 """
 session.reset();problems=[]
 for event in trace:
  rule=next(x for x in g.FAMILIES[family] if x[0]==event)
  _,action,_,_,(x,y,w,h),_=rule
  kind={'up':'tap','long':'long-press','double':'double-tap'}[action]
  sx,sy=device[1]/1080,device[2]/2400
  expected_rect=[x*sx,y*sy,w*sx,h*sy]
  target='v4-'+event
  try:
   visible=session.runtime.snapshot(session.assets,visible_only=True)
   candidates=[e for e in visible if e['tag']=='Button'
               and session.model['nodes'][e['id']]['attributes'].get('name')==target]
   if len(candidates)!=1:
    problems.append({'event':event,'reason':'control missing/ambiguous','found':len(candidates)})
    break
   rect=candidates[0]['rect']
   actual_rect=[float(rect[key]) for key in ('x','y','w','h')]
   if any(abs(a-b)>max(2.0,abs(b)*.005) for a,b in zip(actual_rect,expected_rect)):
    problems.append({'event':event,'reason':'target hitbox not adapted',
                     'actualRect':[round(v,2) for v in actual_rect],
                     'expectedRect':[round(v,2) for v in expected_rect]})
   px=actual_rect[0]+actual_rect[2]/2
   py=actual_rect[1]+actual_rect[3]/2
   result=session.gesture({'kind':kind,'x':px,'y':py,'toX':px,'toY':py,
                           'durationMs':680 if kind=='long-press' else 80})
   uid=result.get('owner')
   actual=session.model['nodes'].get(uid,{}).get('attributes',{}).get('name') if uid else None
   if not result.get('hit') or actual!=target:
    problems.append({'event':event,'reason':'wrong hit','actual':actual,'expected':target})
    break
  except Exception as exc:problems.append({'event':event,'error':str(exc)[:250]});break
 actual={v:int(round(float(session.runtime.lookup(v)))) for v in g.INITIAL[family]}
 if actual!=expected:problems.append({'state mismatch':{'actual':actual,'expected':expected}})
 return session.plan(),problems

def validate_slider_cycle(session):
 """Verify the actual track EndPoint command, short-release guard and wraparound.

 This covers discrete drag endpoints and state changes, NOT smoothness/timing
 of a native continuous animation.
 """
 issues=[]
 try:
  session.reset()
  nodes=[n for n in session.model['nodes'].values()
         if n['attributes'].get('name')=='v4-swipe']
  if len(nodes)!=1:return [{'reason':'slider missing/ambiguous','count':len(nodes)}]
  info=slider_geometry(session.model,session.runtime,nodes[0]['id'])
  if not info or info['mode']!='track':
   return [{'reason':'unsupported slider geometry','mode':info.get('mode') if info else None}]
  start=(info['anchor'][0]+info['hit']['w']*.5,
         info['anchor'][1]+info['hit']['h']*.5)
  end=(start[0]+info['direction'][0]*info['length']*1.1,
       start[1]+info['direction'][1]*info['length']*1.1)
  short=(start[0]+info['direction'][0]*info['length']*.1,
         start[1]+info['direction'][1]*info['length']*.1)
  def gesture(to):
   first=session.drag_phase({'phase':'start','x':start[0],'y':start[1]})
   if not first.get('active'):return None,None
   session.drag_phase({'phase':'move','x':to[0],'y':to[1]})
   return first,session.drag_phase({'phase':'end','x':to[0],'y':to[1]})
  first,finish=gesture(short)
  if not finish or finish.get('reached') or int(session.runtime.lookup('slide'))!=0:
   issues.append({'reason':'short drag incorrectly fired slider'})
  session.reset()
  for step,want in enumerate((1,2,0)):
   first,finish=gesture(end)
   got=int(round(float(session.runtime.lookup('slide'))))
   if not finish or not finish.get('reached') or got!=want:
    issues.append({'reason':'slider end-point cycle mismatch','step':step+1,
                   'expected':want,'actual':got,
                   'reached':finish.get('reached') if finish else None})
    break
 except Exception as exc:
  issues.append({'reason':'slider validation exception','error':str(exc)[:220]})
 return issues

def geometry(plan):
 problems=[];common={}
 w=plan['canvas']['width'];h=plan['canvas']['height']
 for e in plan.get('elements',[]):
  box=e.get('rect') or {}
  try:x,y,bw,bh=[float(box[k]) for k in ('x','y','w','h')]
  except Exception:problems.append('invalid component bbox');continue
  if bw<=0 or bh<=0:problems.append('invalid component dimensions')
  if e.get('name') in ('clock','date'):
   common[e['name']]=[round(x,3),round(y,3),round(bw,3),round(bh,3)]
 if set(common)!={'clock','date'}:problems.append('shared components missing')
 return problems,common

def choose_rows(rows,limit):
 """Deterministic smoke-test selection across splits, families and devices.

 Every user-provided split gets representation when the limit permits it.
 Full runs keep the original index order.
 """
 if limit is None:return rows
 if limit<1:raise ValueError('limit must be >= 1')
 if limit>=len(rows):return rows
 chosen=[];seen=set()
 splits=list(dict.fromkeys(row['split'] for row in rows))
 for split in splits[:limit]:
  idx=next(i for i,r in enumerate(rows) if r['split']==split)
  chosen.append(idx);seen.add(idx)
 families=collections.Counter(rows[i]['family'] for i in chosen)
 devices=collections.Counter(rows[i]['device'] for i in chosen)
 splitcounts=collections.Counter(rows[i]['split'] for i in chosen)
 while len(chosen)<limit:
  idx=min((i for i in range(len(rows)) if i not in seen),
          key=lambda i:(families[rows[i]['family']],
                        splitcounts[rows[i]['split']],
                        devices[rows[i]['device']],i))
  chosen.append(idx);seen.add(idx)
  families[rows[idx]['family']]+=1
  devices[rows[idx]['device']]+=1
  splitcounts[rows[idx]['split']]+=1
 return [rows[i] for i in chosen]

def run(dataset,out,limit=None):
 ds=Path(dataset).resolve();output=Path(out).resolve()
 if output.exists():raise ValueError('Refusing overwrite: '+str(output))
 all_rows=list(records(ds));checks=choose_rows(all_rows,limit)
 for d in ('source','target'): (output/d).mkdir(parents=True,exist_ok=False)
 accepted={};res=[];references={};counts=collections.Counter()
 with sync_playwright() as pw:
  # Existing Windows Edge; modify to plain chromium if unavailable.
  browser=pw.chromium.launch(channel='msedge',headless=True)
  page=browser.new_page()
  try:
   for j,row in enumerate(checks):
    theme=row['theme'];family=row['family'];dev=next(x for x in g.base.DEV if x[0]==row['device'])
    name='--'.join([theme,row['device'],row['state']])+'.png'
    record={'theme':theme,'family':family,'device':row['device'],'state':row['state'],
            'sourceErrors':[],'targetErrors':[],'renderIssues':[],'passed':False}
    try:
     with tempfile.TemporaryDirectory() as tmp:
      src=LiveSession(ds/row['sourceTheme'],Path(tmp)/'source')
      dst=LiveSession(ds/row['adaptedTheme'],Path(tmp)/'target')
      srcplan,record['sourceErrors']=replay(src,family,row['trace'],g.base.DEV[0],row['stateVariables'])
      dstplan,record['targetErrors']=replay(dst,family,row['trace'],dev,row['stateVariables'])
      problems,shared=geometry(dstplan);record['targetErrors'].extend(problems)
      key=(theme,row['device'])
      if key in references and references[key]!=shared:
       record['targetErrors'].append('cross-state common widget drift')
      references.setdefault(key,shared)
      source=output/'source'/name;target=output/'target'/name
      m1=render(page,srcplan,src.output,source)
      m2=render(page,dstplan,dst.output,target)
      record['renderIssues']=m1['issues']+m2['issues']
      if family=='motion_control':
       record['renderIssues'].append({'unverified':'animation pause/resume phase'})
      if family=='swipe_carousel':
       record['sourceErrors'].extend(validate_slider_cycle(src))
       record['targetErrors'].extend(validate_slider_cycle(dst))
       record['sliderCycleVerified']=not(record['sourceErrors'] or record['targetErrors'])
       record['warnings']=['slider endpoint cycle verified; continuous visual frame timing not verified']
      record['passed']=not(record['sourceErrors'] or record['targetErrors'] or record['renderIssues'])
      if record['passed']:accepted[(theme,row['device'],row['state'])]=source
    except Exception as exc:record['fatal']=type(exc).__name__+': '+str(exc)[:300]
    res.append(record);counts['pass' if record['passed'] else 'fail']+=1
    if (j+1)%10==0:print('QA',j+1,'passed',counts['pass'],'failed',counts['fail'],flush=True)
  finally:browser.close()
 with (output/'records.jsonl').open('w',encoding='utf8') as fp:
  for r in res:fp.write(json.dumps(r,ensure_ascii=False)+'\n')
 sftr=(output/'sft-gated');sftr.mkdir()
 source_sft={s:(ds/'sft'/(s+'.jsonl')).open(encoding='utf8') for s in ('train','validation','test')}
 target_sft={s:(sftr/(s+'.jsonl')).open('w',encoding='utf8') for s in source_sft}
 splitcounts=collections.Counter()
 try:
  for row in all_rows:
   split=row['split'];line=source_sft[split].readline()
   if not line:raise ValueError('SFT JSONL order mismatched pairs index')
   key=(row['theme'],row['device'],row['state'])
   if key not in accepted:continue
   ex=json.loads(line);ex['images']=[str(accepted[key])]
   target_sft[split].write(json.dumps(ex,ensure_ascii=False)+'\n')
   splitcounts[split]+=1
 finally:
  for fp in [*source_sft.values(),*target_sft.values()]:fp.close()
 selected_counts=collections.Counter(r['split'] for r in checks)
 complete=len(checks)==len(all_rows)
 weak_ready=(complete and counts['fail']==0
             and all(splitcounts[s]>0 for s in ('train','validation','test')))
 summary={'checked':len(checks),'passed':counts['pass'],'failed':counts['fail'],
          'selectedSplitCounts':dict(selected_counts),
          'sftPassCounts':{s:splitcounts[s] for s in ('train','validation','test')},
          'complete':complete,'selection':'stratified-smoke' if not complete else 'all',
          'weakSftReady':weak_ready,'trainReady':False,'syntheticRuleLabels':True,
          'nativeDeviceVerified':False,'renderMethod':'browser reconstruction of LiveSession plan'}
 (output/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
 (sftr/'manifest.json').write_text(json.dumps({'includeWeak':True,'approvedOnly':True,**summary},
                                                ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(summary,ensure_ascii=False,indent=2))
 return summary

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--dataset',required=True)
 p.add_argument('--out',required=True);p.add_argument('--limit',type=int)
 a=p.parse_args();run(a.dataset,a.out,a.limit)
