"""Shared tools for legacy and V4 multistate theme experiments.
Do not confuse this experimental XML generator with the official Huawei runtime.
"""
import copy
import io
from PIL import Image, ImageDraw, ImageFont
from xml.etree import ElementTree as E

DEV=[('phone',1080,2400),('tall',1080,2520),('square',2200,2200),('fold-wide',2480,1860),('tablet',2560,1600),('tablet-portrait',1600,2560)]

BOX={'background':(0,0,1080,2400),'night-overlay':(0,0,1080,2400),
 'clock':(265,285,550,175),'date':(350,485,380,80),'headline':(120,1450,840,145),
 'page-caption':(150,1660,780,135),'gallery':(175,1120,730,410),
 'weather':(285,900,510,140),'weather-panel':(105,775,870,515),
 'drawer-panel':(80,1420,920,600),'next':(825,1890,200,160),
 'prev':(55,1890,200,160),'weather-tap':(285,865,510,195),
 'drawer-hold':(335,565,410,165),'drawer-close':(340,1780,400,165),
 'night-double':(45,1170,260,155)}

def font(size):
 for x in ('C:/Windows/Fonts/arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
  try:return ImageFont.truetype(x,size)
  except OSError:pass
 return ImageFont.load_default()

def cover(image,size):
 im=image.convert('RGB');w,h=size;ow,oh=im.size
 if ow/oh>w/h:
  nw=int(oh*w/h);im=im.crop(((ow-nw)//2,0,(ow+nw)//2,oh))
 else:
  nh=int(ow*h/w);im=im.crop((0,(oh-nh)//2,ow,(oh+nh)//2))
 return im.resize(size,Image.Resampling.LANCZOS)

def template():
 r=E.Element('Lockscreen',screenWidth='1080',screenHeight='2400',version='1')
 for n in ('page','weather','drawer','night'):E.SubElement(r,'Var',name=n,expression='0')
 def element(tag,name,**kwargs):
  box=BOX[name];return E.SubElement(r,tag,{**{'name':name,**dict(zip('xywh',map(str,box)))},**kwargs})
 element('Image','background',src='background.jpg')
 element('Image','night-overlay',src='night.png',visibility='eq(#night,1)')
 for role,text,size,visible in [('clock','10:21',155,None),('date','FRI / OCT',50,None),
  ('headline','WELCOME',75,'eq(#page,0)'),('page-caption','EXPLORE',67,'eq(#page,1)'),
  ('weather','22 C / CLEAR',58,None)]:
  attrs={'text':text,'size':str(size),'color':'#ffffff'}
  if visible:attrs['visibility']=visible
  element('Text',role,**attrs)
 for role,src,visible in [('gallery','gallery.png','eq(#page,2)'),
  ('weather-panel','weather.png','eq(#weather,1)'),
  ('drawer-panel','drawer.png','eq(#drawer,1)')]:element('Image',role,src=src,visibility=visible)
 def button(name,action,var,expression):
  b=element('Button',name)
  trig=E.SubElement(b,'Trigger',action=action)
  E.SubElement(trig,'VariableCommand',name=var,expression=expression)
 button('next','up','page','ifelse(eq(#page,2),0,#page+1)')
 button('prev','up','page','ifelse(eq(#page,0),2,#page-1)')
 button('weather-tap','up','weather','1-#weather')
 button('drawer-hold','long','drawer','1-#drawer')
 button('drawer-close','up','drawer','0')
 button('night-double','double','night','1-#night')
 slider=E.SubElement(r,'Slider',{'name':'slideUnlock','bounceInitSpeed':'2000','bounceAcceleration':'1000'})
 E.SubElement(slider,'StartPoint',{'x':'440','y':'2190','w':'200','h':'90'})
 end=E.SubElement(slider,'EndPoint',{'x':'440','y':'1770','w':'200','h':'90'})
 tr=E.SubElement(end,'Trigger',{'action':'up'});E.SubElement(tr,'VariableCommand',{'name':'page','expression':'0'})
 path=E.SubElement(end,'Path',{'x':'0','y':'0'})
 for y in (2190,1770):E.SubElement(path,'Position',{'x':'440','y':str(y)})
 return r

def panels():
 output={}
 for name,sz in [('night.png',(1080,2400)),('weather.png',(870,515)),('drawer.png',(920,600)),('gallery.png',(730,410))]:
  im=Image.new('RGBA',sz);d=ImageDraw.Draw(im);w,h=sz
  if name=='night.png':d.rectangle((0,0,w,h),fill=(6,14,34,115))
  else:
   d.rounded_rectangle((4,4,w-4,h-4),radius=40,fill=(16,37,67,230),outline=(196,233,246),width=4)
   d.text((70,85),{'weather.png':'WEATHER / CLEAR','drawer.png':'QUICK SETTINGS','gallery.png':'GALLERY / 02'}[name],font=font(51),fill='white')
  b=io.BytesIO();im.save(b,'PNG');output[name]=b.getvalue()
 return output

def position(name,dev):
 _,w,h=dev;x,y,bw,bh=BOX[name]
 if name in ('background','night-overlay'):return (0,0,w,h)
 if w/h>1.15:
  slot={'clock':(.08,.22,.38,.18),'date':(.09,.42,.38,.07),
  'headline':(.56,.36,.37,.12),'page-caption':(.53,.47,.42,.12),
  'gallery':(.54,.24,.42,.47),'weather':(.09,.62,.38,.1),
  'weather-panel':(.52,.22,.44,.55),'drawer-panel':(.50,.22,.47,.65),
  'next':(.88,.82,.09,.13),'prev':(.03,.82,.09,.13),
  'weather-tap':(.09,.59,.41,.17),'drawer-hold':(.12,.44,.37,.14),
  'drawer-close':(.61,.72,.18,.14),'night-double':(.04,.69,.12,.17)}
  if name in slot:return tuple(round(a*b) for a,b in zip(slot[name],(w,h,w,h)))
 return (round(x*w/1080),round(y*h/2400),round(bw*w/1080),round(bh*h/2400))

def state_position(name,dev,st):
 # Drawer precedence over weather when both are open.
 b=list(position(name,dev))
 if st is None:return tuple(b)
 _,page,weather,drawer,night,_=st
 w,h=dev[1:]
 if name in ('clock','date'):
  if drawer:b[1]-=round(h*.07)
  elif weather:
   b[0]-=round(w*.035)
   b[1]-=round(h*.025)
 elif name=='weather' and weather:
  b[0]-=round(w*.035);b[1]-=round(h*.055)
 elif name=='page-caption' and page==1:b[0]+=round(w*.04)
 elif name=='headline' and night:b[1]-=round(h*.02)
 return tuple(b)

def conditional_xy(el,name,dev):
 # Mirrors state_position in generated XML, not a precomputed screenshot.
 baseline=position(name,dev)
 for j,key in enumerate(('x','y')):
  expr=str(baseline[j])
  for var,idx in reversed((('drawer',3),('weather',2),('page',1),('night',4))):
   state=['probe',0,0,0,0,[]];state[idx]=1
   shifted=state_position(name,dev,tuple(state))[j]
   if shifted!=baseline[j] or (name in ('clock','date') and var=='drawer'):
    expr=f'ifelse(eq(#{var},1),{shifted},{expr})'
  el.set(key,expr)

def target_xml(src,dev):
 r=copy.deepcopy(src);r.set('screenWidth',str(dev[1]));r.set('screenHeight',str(dev[2]))
 for el in r.iter():
  if el.get('name') in BOX and all(k in el.attrib for k in 'xywh'):
   for k,v in zip('xywh',position(el.get('name'),dev)):el.set(k,str(v))
   conditional_xy(el,el.get('name'),dev)
  if el.tag in ('StartPoint','EndPoint','Position'):
   for key,f in [('x',dev[1]/1080),('y',dev[2]/2400),('w',dev[1]/1080),('h',dev[2]/2400)]:
    if el.get(key):el.set(key,str(round(int(el.get(key))*f)))
  if el.tag=='Text' and el.get('size'):el.set('size',str(round(int(el.get('size'))*min(dev[1]/1080,dev[2]/2400))))
 return r

def draw_scene(bg,dev,state):
 im=cover(bg,dev[1:]).convert('RGBA');d=ImageDraw.Draw(im)
 if state[4]:d.rectangle((0,0,dev[1],dev[2]),fill=(5,13,30,95))
 for name,text,active,sz in [('clock','10:21',True,125),('date','FRI / OCT',True,46),
  ('headline','WELCOME',state[1]==0,67),('page-caption','EXPLORE',state[1]==1,56),
  ('weather','22 C / CLEAR',True,52)]:
  if active:
   x,y,_,_=state_position(name,dev,state)
   d.text((x,y),text,font=font(max(18,int(sz*min(dev[1]/1080,dev[2]/2400)))),fill='white')
 for name,on,text in [('gallery',state[1]==2,'GALLERY / 02'),('weather-panel',state[2],'WEATHER / CLEAR'),('drawer-panel',state[3],'QUICK SETTINGS')]:
  if on:
   x,y,w,h=state_position(name,dev,state)
   d.rounded_rectangle((x,y,x+w,y+h),radius=max(9,w//22),fill=(16,38,68,230),outline='white',width=4)
   d.text((x+w//12,y+h//5),text,fill='white',font=font(max(20,w//20)))
 return im.convert('RGB')

