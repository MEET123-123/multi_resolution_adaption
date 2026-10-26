"""Multi-state interactive themes built from locally downloaded licensed Commons backgrounds.
Weak supervised pairs; XML replay and native validity NOT assumed.
"""
import argparse,copy,io,json,hashlib,random,zipfile
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from xml.etree import ElementTree as E
from theme_primitives import DEV, BOX, font, cover, template, panels, position, state_position, conditional_xy, target_xml, draw_scene

STATES=[('idle',0,0,0,0,[]),('page-1',1,0,0,0,['next']),('page-2',2,0,0,0,['next','next']),
 ('weather',0,1,0,0,['weather']),('drawer',0,0,1,0,['long']),('night',0,0,0,1,['double']),
 ('weather-night',0,1,0,1,['double','weather']),('drawer-page',1,0,1,0,['next','long']),
 ('weather-drawer',0,1,1,0,['weather','long']),('page-night',2,0,0,1,['next','next','double'])]
def digest(x):return hashlib.sha256(str(x).encode()).hexdigest()


def create(catalog,out,limit):
 catalogue=Path(catalog).resolve();out=Path(out).resolve()
 if out.exists():raise ValueError('Output exists; use a new directory')
 content=json.loads(catalogue.read_text(encoding='utf8'))
 records=[r for r in content if r.get('localPath') and r.get('license') and (catalogue.parent/r['localPath']).is_file()][:limit]
 if not records:raise ValueError('No licensed downloaded backgrounds')
 for sub in ('themes','adapted','images','screens','sft','reports'):(out/sub).mkdir(parents=True,exist_ok=True)
 templates=template();extras=panels()
 counts={'train':0,'validation':0,'test':0}
 sft={k:(out/'sft'/(k+'.jsonl')).open('w',encoding='utf8') for k in counts}
 pairs=(out/'reports'/'pairs.jsonl').open('w',encoding='utf8')
 try:
  for i,r in enumerate(records):
   theme='commons-'+str(r['id'])
   bg=cover(Image.open(catalogue.parent/r['localPath']),(1080,2400))
   source=out/'images'/(theme+'.jpg');bg.save(source,'JPEG',quality=82)
   meta=json.dumps({k:r.get(k) for k in ('id','page','license','licenseUrl','artist','credit')},ensure_ascii=False)
   def package(p,xml,bgdata):
    with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED) as z:
     z.writestr('unlock/lockscreen/manifest.xml',E.tostring(xml,encoding='utf-8',xml_declaration=True))
     z.writestr('unlock/lockscreen/background.jpg',bgdata)
     for name,data in extras.items():z.writestr('unlock/lockscreen/'+name,data)
     z.writestr('source-attribution.json',meta)
   package(out/'themes'/(theme+'.hwt'),templates,source.read_bytes())
   pick=int(digest(r['id'])[:8],16)%10;split='test' if pick==9 else 'validation' if pick==8 else 'train'
   for dev in DEV:
    dname,w,h=dev;root=target_xml(templates,dev)
    b=io.BytesIO();cover(bg,(w,h)).save(b,'JPEG',quality=81)
    package(out/'adapted'/(theme+'--'+dname+'.hwt'),root,b.getvalue())
    answer={'schema':'multires-layout/1','groups':{'g'+str(j):[round(1000*v[0]/w,3),round(1000*v[1]/h,3),round(1000*v[2]/w,3),round(1000*v[3]/h,3)]
              for j,v in enumerate(position(name,dev) for name in BOX)}}
    groups=[{'id':'g'+str(j),'roles':[name],'sourceBox':[round(1000*v[0]/1080,3),round(1000*v[1]/2400,3),round(1000*v[2]/1080,3),round(1000*v[3]/2400,3)]} for j,(name,v) in enumerate(BOX.items())]
    for st in STATES:
     sn,page,weather,drawer,night,trace=st
     answer={'schema':'multires-layout/1','groups':{
      'g'+str(j):[round(1000*v[0]/w,3),round(1000*v[1]/h,3),round(1000*v[2]/w,3),round(1000*v[3]/h,3)]
      for j,v in enumerate(state_position(name,dev,st) for name in BOX)}}
     target=out/'screens'/(theme+'--'+dname+'--'+sn+'.jpg');draw_scene(bg,dev,st).save(target,'JPEG',quality=79)
     req={'schema':'multires-layout/1','theme':theme,'sourceViewport':{'width':1080,'height':2400},
       'targetViewport':{'width':w,'height':h},'safeRegion':{'x':0,'y':0,'w':w,'h':h},
       'groups':groups,'interactionState':{'name':sn,'variables':{'page':page,'weather':weather,'drawer':drawer,'night':night},'trace':trace}}
     sample={'messages':[{'role':'system','content':'锁屏多状态布局规划任务，只返回含全部 g 组件组的 xywh JSON。'},
       {'role':'user','content':'<image>\n源主题、状态与目标设备：'+json.dumps(req,ensure_ascii=False)},
       {'role':'assistant','content':json.dumps(answer,ensure_ascii=False)}],
       'images':[str(source)]}
     sft[split].write(json.dumps(sample,ensure_ascii=False)+'\n');counts[split]+=1
     pairs.write(json.dumps({'theme':theme,'state':sn,'trace':trace,'variables':req['interactionState']['variables'],
       'targetDevice':dname,'sourceHwt':'themes/'+theme+'.hwt','targetHwt':'adapted/'+theme+'--'+dname+'.hwt',
       'sourceImage':'images/'+theme+'.jpg','targetImage':'screens/'+target.name,'answer':answer,
       'split':split,'origin':'synthetic-rule','interactionVerified':False,'nativeValidated':False,
       'license':r.get('license'),'sourcePage':r.get('page')},ensure_ascii=False)+'\n')
   if (i+1)%10==0:print('built',i+1,'interactive themes',flush=True)
 finally:
  pairs.close()
  for f in sft.values():f.close()
 m={'themes':len(records),'statesPerTheme':len(STATES),'devices':len(DEV),'pairs':sum(counts.values()),
    'splits':counts,'includeWeak':True,'labelOrigin':'synthetic rules','interactionVerified':False,
    'nativeValidated':False,'realDesignerGroundTruth':False}
 for f in (out/'sft'/'manifest.json',out/'reports'/'manifest.json'):f.write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(m,ensure_ascii=False),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser()
 p.add_argument('--catalog',default='artifacts/learning/web-themes-1000/catalog.json')
 p.add_argument('--out',default='artifacts/learning/web-multistate-v1')
 p.add_argument('--themes',type=int,default=1000)
 a=p.parse_args();create(a.catalog,a.out,a.themes)
