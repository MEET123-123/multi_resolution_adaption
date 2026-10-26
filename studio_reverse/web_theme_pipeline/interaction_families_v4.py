"""Diverse, interaction-graph-driven HWT dataset generation.
Source backgrounds from verified local Commons catalog. All layout labels are synthetic.
"""
from __future__ import annotations
import argparse, collections, copy, hashlib, io, json, os, random, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from PIL import Image, ImageDraw, ImageFont
import theme_primitives as base
from multires_app.engine.scene_engine import Evaluator

# Independent interaction structures. Each entry is (event, action, changed variable, expression, rect, visibility)
R = lambda x,y,w,h:(x,y,w,h)
FAMILIES = {
 'carousel': [
  ('forward','up','frame','ifelse(eq(#frame,3),0,#frame+1)',R(810,1130,230,145),'1'),
  ('backward','up','frame','ifelse(eq(#frame,0),3,#frame-1)',R(40,1130,230,145),'1')],
 'nested_panels': [
  ('open','up','layer','1',R(370,770,320,125),'eq(#layer,0)'),
  ('deeper','up','layer','ifelse(eq(#layer,1),2,3)',R(680,1130,260,130),'ge(#layer,1)'),
  ('back','up','layer','max(0,#layer-1)',R(130,1580,260,130),'ge(#layer,1)')],
 'modal_stack': [
  ('open_modal','up','modal','1',R(370,800,350,145),'eq(#modal,0)'),
  ('open_child','up','modal','2',R(550,1370,370,130),'eq(#modal,1)'),
  ('close_modal','up','modal','max(0,#modal-1)',R(120,1600,330,135),'ge(#modal,1)')],
 'swipe_carousel': [
  ('tap_next','up','slide','ifelse(eq(#slide,2),0,#slide+1)',R(760,1230,275,140),'1'),
  ('tap_back','up','slide','ifelse(eq(#slide,0),2,#slide-1)',R(55,1230,275,140),'1')],
 'motion_control': [
  ('play','up','motion','1',R(65,1250,260,125),'eq(#motion,0)'),
  ('pause','up','motion','2',R(390,1250,260,125),'eq(#motion,1)'),
  ('resume','up','motion','1',R(390,1250,260,125),'eq(#motion,2)'),
  ('restart','up','motion','0',R(720,1250,260,125),'ge(#motion,1)')],
 'trigger_chain': [
  ('touch','up','touches','min(3,#touches+1)',R(390,1030,315,155),'1'),
  ('clear','up','touches','0',R(700,1490,260,125),'ge(#touches,1)')],
 'compound': [
  ('switch','double','mode','1-#mode',R(35,970,260,150),'1'),
  ('drawer','long','sheet','1-#sheet',R(395,740,280,150),'1'),
  ('rotate','up','card','ifelse(eq(#card,2),0,#card+1)',R(780,1260,245,145),'1'),
  ('close','up','sheet','0',R(95,1550,260,145),'eq(#sheet,1)')],
 'nested_tabs': [
  ('open','up','tabPanel','1',R(375,850,315,140),'eq(#tabPanel,0)'),
  ('tab','up','tab','1-#tab',R(720,1190,270,140),'eq(#tabPanel,1)'),
  ('expand','up','detail','1',R(365,1520,330,120),'eq(#tabPanel,1)'),
  ('collapse','up','detail','0',R(365,1520,330,120),'eq(#detail,1)'),
  ('close','up','tabPanel','0',R(65,1550,245,135),'eq(#tabPanel,1)')]
}
INITIAL={
 'carousel':{'frame':0},'nested_panels':{'layer':0},'modal_stack':{'modal':0},
 'swipe_carousel':{'slide':0},'motion_control':{'motion':0},
 'trigger_chain':{'touches':0,'ready':0},
 'compound':{'mode':0,'sheet':0,'card':0},
 'nested_tabs':{'tabPanel':0,'tab':0,'detail':0}
}
SHARED=('clock','date')
DEFAULT_STATES=18

def eval_expr(expr, state):
 return Evaluator(lambda name:state.get(name,0)).evaluate(expr)

def advance(family, state, event):
 action=next((a for a in FAMILIES[family] if a[0]==event),None)
 if not action or not eval_expr(action[5],state):return None
 nxt=dict(state)
 nxt[action[2]]=int(eval_expr(action[3],state))
 if family=='trigger_chain':
  nxt['ready']=int(nxt['touches']>=2)  # Mirrors Var-trigger callback in XML.
 if family=='nested_tabs' and event=='close':
  nxt.update(tab=0,detail=0)
 if family=='nested_tabs' and event=='tab':
  nxt['detail']=0
 return nxt

def states(family,limit=DEFAULT_STATES):
 order=list(INITIAL[family]); initial=INITIAL[family]
 start=tuple(initial[k] for k in order)
 queue=collections.deque([(dict(initial),[])])
 seen={start};out=[]
 while queue and len(out)<limit:
  state,trace=queue.popleft()
  out.append({'id':'s'+str(len(out)).zfill(2),'variables':state,'trace':trace})
  for action in FAMILIES[family]:
   nxt=advance(family,state,action[0])
   if nxt is None:continue
   key=tuple(nxt[k] for k in order)
   if key in seen:continue
   seen.add(key);queue.append((nxt,trace+[action[0]]))
 return out

def positions(family,dev,state):
 _,w,h=dev;landscape=w/h>1.15
 if landscape:
  normal={'hero':(.56,.23,.39,.51),'status':(.56,.76,.39,.13),'panel':(.52,.19,.45,.65),
          'badge':(.10,.67,.35,.11),'control':(.52,.82,.42,.13)}
 else:
  normal={'hero':(.13,.43,.74,.26),'status':(.17,.74,.66,.09),'panel':(.10,.39,.80,.43),
          'badge':(.16,.33,.68,.07),'control':(.15,.86,.70,.08)}
 result={k:tuple(round(q*r) for q,r in zip(v,(w,h,w,h))) for k,v in normal.items()}
 vals=state
 dial=max(vals.get('modal',0),vals.get('layer',0),vals.get('tabPanel',0))
 if dial:
  b=list(result['hero']);b[0]+=round(w*.035);b[1]-=round(h*.05);result['hero']=tuple(b)
  b=list(result['panel']);b[1]-=round(h*min(.06,dial*.02));result['panel']=tuple(b)
 if vals.get('frame',0) or vals.get('slide',0) or vals.get('card',0):
  n=sum(vals.get(k,0) for k in ('frame','slide','card'))
  b=list(result['hero']);b[0]+=round(w*.012*n);result['hero']=tuple(b)
 if vals.get('sheet',0):
  b=list(result['status']);b[1]-=round(h*.08);result['status']=tuple(b)
 if vals.get('motion',0)==2:
  b=list(result['badge']);b[1]+=round(h*.025);result['badge']=tuple(b)
 return result

def add_button(parent, event, action, variable, expression, rectangle, visibility):
 attrs=dict(name='v4-'+event,x=str(rectangle[0]),y=str(rectangle[1]),w=str(rectangle[2]),h=str(rectangle[3]),visibility=visibility)
 button=ET.SubElement(parent,'Button',attrs)
 trigger=ET.SubElement(button,'Trigger',action=action)
 ET.SubElement(trigger,'VariableCommand',name=variable,expression=expression)

def construct(family):
 root=base.template()
 for var,value in INITIAL[family].items():
  ET.SubElement(root,'Var',name=var,expression=str(value))
 if family=='trigger_chain':
  ready=next(x for x in root if x.tag=='Var' and x.get('name')=='ready')
  ready.set('expression','ge(#touches,2)')
  trig=ET.SubElement(ready,'Trigger')
  ET.SubElement(trig,'VariableCommand',name='ready',expression='ge(#touches,2)')
 for action in FAMILIES[family]:
  add_button(root,*action)
 if family=='nested_tabs':
  # True nested XML group hierarchy, with visibility conditions.
  top=ET.SubElement(root,'Group',name='v4-main-panel',visibility='eq(#tabPanel,1)')
  child=ET.SubElement(top,'Group',name='v4-child-panel',visibility='eq(#detail,1)')
  ET.SubElement(child,'Text',name='v4-nested-content',text='NESTED DETAILS',size='50',
                color='#ffffff',x='250',y='1560',w='530',h='100')
 if family=='swipe_carousel':
  slider=ET.SubElement(root,'Slider',name='v4-swipe',bounceInitSpeed='1900',bounceAcceleration='800')
  start=ET.SubElement(slider,'StartPoint',x='380',y='1150',w='185',h='150')
  ET.SubElement(ET.SubElement(start,'NormalState'),'Image',x='0',y='0',src='knob.png')
  stop=ET.SubElement(slider,'EndPoint',x='840',y='1150',w='185',h='150')
  trig=ET.SubElement(stop,'Trigger',action='up')
  ET.SubElement(trig,'VariableCommand',name='slide',expression='ifelse(eq(#slide,2),0,#slide+1)')
  path=ET.SubElement(stop,'Path',x='0',y='0')
  ET.SubElement(path,'Position',x='380',y='1150')
  ET.SubElement(path,'Position',x='840',y='1150')
 if family=='motion_control':
  sprite=ET.SubElement(root,'Image',name='v4-motion-sprite',x='250',y='1010',
                       w='250',h='250',src='sprite.png',visibility='eq(#motion,1)')
  anim=ET.SubElement(sprite,'PositionAnimation',loop='true')
  for t,x,y in [(0,250,1010),(350,520,1090),(700,250,1010)]:
   ET.SubElement(anim,'Position',time=str(t),x=str(x),y=str(y))
  alpha=ET.SubElement(sprite,'AlphaAnimation',loop='true')
  for t,a in [(0,255),(350,130),(700,255)]:
   ET.SubElement(alpha,'Alpha',time=str(t),a=str(a))
 # Include visually distinct scenes, driven by actual variables.
 for role,visibility,text in [('hero','1','INTERACTIVE'),
                               ('status','1',family.replace('_',' ').upper()),
                               ('panel', 'ge(#'+list(INITIAL[family])[0]+',1)','OPEN PANEL'),
                               ('badge','1','GESTURES')]:
  rect=(130,880,820,450) if role=='hero' else (190,1540,690,105)
  ET.SubElement(root,'Text',{'name':'v4-'+role,'x':str(rect[0]),'y':str(rect[1]),
       'w':str(rect[2]),'h':str(rect[3]),'size':'49' if role!='hero' else '75',
       'text':text,'color':'#ffffff','visibility':visibility})
 return root

def adapt(root,dev,family):
 tree=base.target_xml(root,dev)
 for node in tree.iter():
  n=node.get('name','')
  # Scale interaction hitboxes together with visual elements on every target device.
  if n.startswith('v4-') and node.tag=='Button' and all(k in node.attrib for k in 'xywh'):
   sx,sy=dev[1]/1080,dev[2]/2400
   for key,scale in [('x',sx),('y',sy),('w',sx),('h',sy)]:
    node.set(key,str(round(float(node.get(key))*scale)))
  if n in SHARED:
   for key,v in zip('xywh',base.position(n,dev)):node.set(key,str(v))
  if n.startswith('v4-') and n[3:] in ('hero','status','panel','badge'):
   box=positions(family,dev,INITIAL[family])[n[3:]]
   for key,v in zip('xywh',box):node.set(key,str(v))
   for j,key in enumerate(('x','y')):
    expr=str(box[j])
    for var in INITIAL[family]:
     for val in range(1,4):
      fake=dict(INITIAL[family]);fake[var]=val
      shifted=positions(family,dev,fake)[n[3:]][j]
      if shifted!=box[j]:
       expr='ifelse(eq(#'+var+','+str(val)+'),'+str(shifted)+','+expr+')'
    node.set(key,expr)
 return tree

def preview(bg,dev,family,state):
 img=base.draw_scene(bg,dev,('idle',0,0,0,0,[])).convert('RGBA')
 draw=ImageDraw.Draw(img)
 p=positions(family,dev,state)
 for name,text in [('hero','INTERACTIVE'),('badge',family.upper()),('status',json.dumps(state,sort_keys=True)[:28])]:
  x,y,w,h=p[name]
  draw.text((x,y),text,fill='white',font=base.font(max(22,min(w//12,72))))
 if any(int(v)>0 for v in state.values()):
  x,y,w,h=p['panel']
  draw.rounded_rectangle((x,y,x+w,y+h),radius=max(12,w//18),fill=(17,41,64,220),outline='#e5eef8',width=4)
  draw.text((x+w//13,y+h//5),'OPEN / '+family.upper(),fill='white',font=base.font(max(20,w//24)))
 return img.convert('RGB')

def package(path,xml,images,record):
 with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
  z.writestr('unlock/lockscreen/manifest.xml',ET.tostring(xml,encoding='utf-8',xml_declaration=True))
  for name,blob in images.items():z.writestr('unlock/lockscreen/'+name,blob)
  z.writestr('provenance.json',json.dumps({'page':record.get('page'),'license':record.get('license'),
              'licenseUrl':record.get('licenseUrl'),'artist':record.get('artist'),
              'origin':'Wikimedia Commons image + original XML interaction graph'},
               ensure_ascii=False,indent=2))
def png(im):
 out=io.BytesIO();im.save(out,'PNG');return out.getvalue()

def assign_splits(rows):
 """Deterministic family-stratified, theme-disjoint train/validation/test.

 With >=3 different source images for a family, reserve at least one
 theme for each held-out split. Smaller pilots cannot fully stratify;
 preserve the historical hash policy and report their coverage.
 """
 assignments={}
 families=list(FAMILIES)
 by_family=collections.defaultdict(list)
 for index,item in enumerate(rows):
  by_family[families[index%len(families)]].append(item['id'])
 for family,ids in by_family.items():
  ids=sorted(ids,key=lambda theme_id:hashlib.sha256(theme_id.encode()).hexdigest())
  n=len(ids)
  if n>=3:
   test_n=max(1,round(n*.1))
   val_n=max(1,round(n*.1))
   if test_n+val_n>=n:
    test_n=val_n=1
   for i,theme_id in enumerate(ids):
    assignments[theme_id]='test' if i<test_n else 'validation' if i<test_n+val_n else 'train'
  else:
   for theme_id in ids:
    bucket=int(hashlib.sha256(theme_id.encode()).hexdigest()[:8],16)%10
    assignments[theme_id]='test' if bucket==9 else 'validation' if bucket==8 else 'train'
 return assignments

def build(catalog,out,limit=24,max_states=DEFAULT_STATES):
 catalog=Path(catalog).resolve();out=Path(out).resolve()
 if out.exists():raise ValueError('Use a fresh output directory: '+str(out))
 rows=json.loads(catalog.read_text(encoding='utf8'))
 rows=[r for r in rows if r.get('localPath') and (catalog.parent/r['localPath']).is_file()]
 rows=rows[:limit]
 if not rows:raise ValueError('No downloaded licensed source images')
 for folder in ('themes','adapted','source-states','target-states','sft','reports'): (out/folder).mkdir(parents=True,exist_ok=True)
 streams={s:(out/'sft'/(s+'.jsonl')).open('w',encoding='utf8') for s in ('train','validation','test')}
 pairs=(out/'reports'/'pairs.jsonl').open('w',encoding='utf8')
 counts=collections.Counter();familycounts=collections.Counter()
 extra=base.panels()
 knob=Image.new('RGBA',(128,128),(0,0,0,0));d=ImageDraw.Draw(knob);d.ellipse((10,10,118,118),fill='#ffffff',outline='#254d78',width=6)
 extra['knob.png']=png(knob)
 extra['sprite.png']=png(knob)
 try:
  for index,r in enumerate(rows):
   name='web-'+r['id']; family=list(FAMILIES)[index%len(FAMILIES)]
   tree=construct(family); graph=states(family,max_states)
   bg=base.cover(Image.open(catalog.parent/r['localPath']),(1080,2400))
   b=io.BytesIO();bg.save(b,'JPEG',quality=80)
   images={'background.jpg':b.getvalue(),**extra}
   package(out/'themes'/(name+'.hwt'),tree,images,r)
   split='test' if int(hashlib.sha256(r['id'].encode()).hexdigest()[:8],16)%10==9 else 'validation' if int(hashlib.sha256(r['id'].encode()).hexdigest()[:8],16)%10==8 else 'train'
   for state in graph:
    source=out/'source-states'/(name+'--'+state['id']+'.jpg')
    preview(bg,base.DEV[0],family,state['variables']).save(source,'JPEG',quality=76)
   for dev in base.DEV:
    target_xml=adapt(tree,dev,family)
    target_bg=base.cover(bg,dev[1:]);b=io.BytesIO();target_bg.save(b,'JPEG',quality=79)
    package(out/'adapted'/(name+'--'+dev[0]+'.hwt'),target_xml,{'background.jpg':b.getvalue(),**extra},r)
    for state in graph:
     target=out/'target-states'/(name+'--'+dev[0]+'--'+state['id']+'.jpg')
     preview(bg,dev,family,state['variables']).save(target,'JPEG',quality=75)
     groups=[]
     answer={'schema':'multires-layout/1','groups':{}}
     for j,role in enumerate(('hero','status','panel','badge')):
      source_rect=positions(family,base.DEV[0],state['variables'])[role]
      tr=positions(family,dev,state['variables'])[role]
      def norm(rect,w,h):
       return [round(rect[0]*1000/w,3),round(rect[1]*1000/h,3),
               round(rect[2]*1000/w,3),round(rect[3]*1000/h,3)]
      groups.append({'id':'g'+str(j),'roles':[role],'sourceBox':norm(source_rect,1080,2400),
                     'fixed':False})
      answer['groups']['g'+str(j)]=norm(tr,dev[1],dev[2])
     # Fixed cross-state common elements retain identical target boxes.
     for role in SHARED:
      j=len(groups);sr=base.position(role,base.DEV[0]);tr=base.position(role,dev)
      groups.append({'id':'g'+str(j),'roles':[role],
        'sourceBox':[round(sr[0]/1.08,3),round(sr[1]/2.4,3),round(sr[2]/1.08,3),round(sr[3]/2.4,3)],'fixed':True})
      answer['groups']['g'+str(j)]=[round(tr[0]*1000/dev[1],3),round(tr[1]*1000/dev[2],3),round(tr[2]*1000/dev[1],3),round(tr[3]*1000/dev[2],3)]
     request={'schema':'multires-layout/1','theme':name,'sourceViewport':{'width':1080,'height':2400},
       'safeRegion':{'x':0,'y':0,'w':dev[1],'h':dev[2]},'targetViewport':{'width':dev[1],'height':dev[2]},
       'groups':groups,'interaction':{'family':family,'variables':state['variables'],'path':state['trace'],
                                  'state':state['id'],'graphStateCount':len(graph)},
       'sharedLockedGroups':['g4','g5']}
     entry={'messages':[{'role':'system','content':'你是多交互锁屏的跨设备布局规划器。必须保持公共时钟和日期组件跨状态不变，并返回完整 XYWH 组 JSON。'},
             {'role':'user','content':'<image>\n状态截图与布局约束：'+json.dumps(request,ensure_ascii=False,separators=(',',':'))},
             {'role':'assistant','content':json.dumps(answer,ensure_ascii=False,separators=(',',':'))}],
            'images':[str(source)]}
     streams[split].write(json.dumps(entry,ensure_ascii=False)+'\n')
     pairs.write(json.dumps({'theme':name,'family':family,'state':state['id'],'stateVariables':state['variables'],
       'trace':state['trace'],'sourceTheme':'themes/'+name+'.hwt','adaptedTheme':'adapted/'+name+'--'+dev[0]+'.hwt',
       'sourceImage':str(source.relative_to(out)).replace('\\','/'),'targetImage':str(target.relative_to(out)).replace('\\','/'),
       'device':dev[0],'split':split,'request':request,'answer':answer,'labelProvenance':'deterministic-rule',
       'renderProvenance':'Pillow simulation, not native engine','interactionReplayVerified':False,
       'license':r['license'],'sourcePage':r.get('page')},ensure_ascii=False)+'\n')
     counts[split]+=1;familycounts[family]+=1
   if (index+1)%8==0:print('PACKAGED_THEMES',index+1,flush=True)
 finally:
  pairs.close()
  for fp in streams.values():fp.close()
 summary={'schema':'multires-interaction-families/4','themes':len(rows),'families':dict(familycounts),
          'counts':dict(counts),'pairs':sum(counts.values()),'targets':[d[0] for d in base.DEV],
          'trainReady':False,'includeWeak':True,'supervision':'synthetic-rule',
          'labelWarning':'Not designer truth. Interaction replay and frame rendering not verified.',
          'splitPolicy':'hash of source media page id; all states/devices of same theme share split'}
 for path in (out/'reports'/'manifest.json',out/'sft'/'manifest.json'):
  path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
 print('MANIFEST',json.dumps(summary,ensure_ascii=False),flush=True)
 return summary

if __name__=='__main__':
 parser=argparse.ArgumentParser()
 parser.add_argument('--catalog',default='artifacts/learning/web-themes-1000/catalog.json')
 parser.add_argument('--out',default='artifacts/learning/interactive-families-v4')
 parser.add_argument('--themes',type=int,default=24)
 parser.add_argument('--max-states',type=int,default=18)
 args=parser.parse_args()
 build(args.catalog,args.out,args.themes,args.max_states)
