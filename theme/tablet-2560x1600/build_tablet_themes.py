"""Build two native HarmonyOS 4.x tablet small themes, without changing inputs.

Layout is authored once as an XML scene; offline previews render that same XML.
Image generation is used only for sea-master.png. UI assets are code-drawn.
"""
from pathlib import Path
from collections import Counter
import argparse, copy, hashlib, io, json, math, re, sys, zipfile
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE.parents[1]))
from multires_app.engine.scene_engine import Evaluator
from multires_app.interaction.tablet_interactions import install_state, bind_gestures, defaults_actions
DESIGNS=json.loads((BASE/'tablet-layouts.json').read_text(encoding='utf-8'))
DOG_DIMS={}
PYTHEMES=['比邻熊 新拟态','线条小狗 夏日出逃']
PREFIX='unlock/lockscreen/'
FONT='C:/Windows/Fonts/msyh.ttc'
NUMFONT='C:/Windows/Fonts/segoeuil.ttf'
SOURCES={
 'tablet':'https://developer.huawei.com/consumer/cn/doc/content/tablet-themes-specification-0000001078779076',
 'engine':'https://developer.huawei.com/consumer/cn/doc/distribution/content/themes-engine-update-0000001055348462',
 'notes':'https://developer.huawei.com/consumer/cn/doc/content/themes-engine-notes-0000001055508442',
}

def encxml(root):
    ET.indent(root,space='  ')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)

def imagebytes(im,fmt='PNG'):
    b=io.BytesIO(); im.save(b,fmt,**({'quality':94,'subsampling':0} if fmt=='JPEG' else {})); return b.getvalue()

def native_ui():
    out={}
    for mode,bg,shadow,highlight in [(0,'#dfe7eb','#b7c3cc','#ffffff'),(1,'#222b36','#121820','#3a4858')]:
        out[f'tablet/bg_{mode}.jpg']=imagebytes(Image.new('RGB',(8,8),bg),'JPEG')
        for label,wh,r in [('card',(512,512),64),('button',(520,132),60)]:
            w,h=wh
            canvas=Image.new('RGBA',(w,h))
            for shift,c in [(10,shadow),(-10,highlight)]:
                layer=Image.new('RGBA',(w,h));d=ImageDraw.Draw(layer)
                d.rounded_rectangle((28+shift,28+shift,w-28+shift,h-28+shift),radius=r,fill=c)
                canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(14)))
            d=ImageDraw.Draw(canvas)
            d.rounded_rectangle((28,28,w-28,h-28),radius=r,fill=bg)
            out[f'tablet/{label}_{mode}.png']=imagebytes(canvas)
        # Analog dial and needles: all graphic geometry is generated in code.
        dial=Image.new('RGBA',(360,360));d=ImageDraw.Draw(dial)
        d.ellipse((5,5,355,355),fill='#1d2935' if mode==0 else '#17202b',outline='#405164',width=3)
        for i in range(60):
            a=math.radians(i*6);r1=151 if i%5==0 else 161;r2=168
            d.line((180+math.sin(a)*r1,180-math.cos(a)*r1,180+math.sin(a)*r2,180-math.cos(a)*r2),fill='#99aab4',width=4 if i%5==0 else 2)
        out[f'tablet/dial_{mode}.png']=imagebytes(dial)
    for name,length,width,color in [('hour',100,10,'#edf5f9'),('minute',145,7,'#edf5f9'),('second',156,3,'#72cde0')]:
        im=Image.new('RGBA',(360,360));d=ImageDraw.Draw(im)
        d.line((180,180+20,180,180-length),fill=color,width=width)
        d.ellipse((172,172,188,188),fill=color)
        out[f'tablet/{name}.png']=imagebytes(im)
    # A translucent button for the sea theme.
    im=Image.new('RGBA',(520,132));d=ImageDraw.Draw(im)
    d.rounded_rectangle((3,3,517,129),radius=63,fill=(8,63,79,155),outline=(255,255,255,140),width=3)
    out['tablet/sea-button.png']=imagebytes(im)
    # Battery bar, displayed through native dynamic width.
    for label,col in [('bar','#77c7cd'),('track','#afc1c8')]:
        out[f'tablet/{label}.png']=imagebytes(Image.new('RGBA',(4,4),col))
    return out

class Scene:
    def __init__(self,w,h,theme):
        self.w,self.h,self.theme=w,h,theme
        self.root=ET.Element('Lockscreen',version='1',frameRate='30',screenWidth=str(w),vibrate='false')
        self.boxes=[];self.owner_count=0
        install_state(self.root,theme)
        # Large background swipe region first; local controls are added later.
        self.swipe=self.touch_button('background',0,0,'#screen_width','#screen_height',hold=[('focus','1-#focus')])

    def elem(self,tag,**a):
        return ET.SubElement(self.root,tag,{k:(format(v,'.6g') if isinstance(v,float) else str(v)) for k,v in a.items() if v is not None})

    def img(self,src,x,y,w,h,**a):
        return self.elem('Image',src=src,x=x,y=y,w=w,h=h,**a)

    def text(self,text,x,y,size,color,**a):
        a.setdefault('alignV','top')
        return self.elem('Text',text=text,x=x,y=y,size=size,color=color,**a)

    def themed_text(self,text,x,y,size,**a):
        for p,c in [(0,'#243a46'),(1,'#d5e5f4')]:
            self.text(text,x,y,size,c,visibility=f'eq(#palette,{p})',**a)

    def clock(self,x,y,size,color,fmt='HH:mm',**a):
        return self.elem('DateTime',x=x,y=y,size=size,color=color,format=fmt,alignV='top',**a)

    def themed_clock(self,x,y,size,fmt='HH:mm',**a):
        for p,c in [(0,'#243a46'),(1,'#d5e5f4')]:
            self.clock(x,y,size,c,fmt,visibility=f'eq(#palette,{p})',**a)

    def group_since(self,start,visibility):
        children=list(self.root)[start:]
        for child in children:self.root.remove(child)
        group=ET.SubElement(self.root,'Group',visibility=visibility)
        group.extend(children)

    def touch_button(self,name,x,y,w,h,**gestures):
        button=self.elem('Button',name=name,x=x,y=y,w=w,h=h)
        bind_gestures(button,self.owner_count,**gestures);self.owner_count+=1
        return button

    def button(self,label,x,y,w,h,variable=None,mod=2,sea=False,name=None,labels=None,visibility='1',tap=None,hold=()):
        start=len(self.root)
        if sea:
            self.img('tablet/sea-button.png',x,y,w,h)
        else:
            for p in range(2):self.img(f'tablet/button_{p}.png',x,y,w,h,visibility=f'eq(#palette,{p})')
        for caption,condition in (labels or [(label,'1')]):
            for p,color in ([(0,'#ffffff')] if sea else [(0,'#243a46'),(1,'#d5e5f4')]):
                self.text(caption,x+w/2,y+h/2,round(h*.27),color,align='center',alignV='center',visibility=condition if sea else f'({condition})*eq(#palette,{p})')
        if tap is None:tap=[(variable,f'(#{variable}+1)%{mod}')]
        self.touch_button(name or variable,x,y,w,h,tap=tap,hold=hold)
        self.group_since(start,visibility)
        self.boxes.append({'role':'button','label':label,'box':[x,y,w,h],'visibility':visibility})

    def character_control(self,name,box,variable,count,reaction):
        x,y,w,h=box;pad=self.w*.0125
        self.touch_button(name,x-pad,y-pad*1.8,w+pad*2,h+pad*2.8,
                          tap=[(reaction,'#time')],hold=[('focus','1-#focus')],
                          left=[(variable,f'(#{variable}+1)%{count}')],
                          right=[(variable,f'(#{variable}+{count}-1)%{count}')])

    def enhancements(self,layout):
        x,y,w,h=layout['focusControl']
        self.button('简洁模式',x,y,w,h,sea=self.theme=='dog',name='focus-toggle',
                    labels=[('简洁模式','eq(#focus,0)'),('返回完整模式','eq(#focus,1)')],
                    tap=[('focus','1-#focus')],hold=defaults_actions(self.theme))
        for p,c in ([(0,'#ffffff')] if self.theme=='dog' else [(0,'#51727d'),(1,'#9fc3da')]):
            self.text('低电量 · 动画已暂停',x+w/2,y+h+12,22,c,align='center',
                      visibility='eq(#low_power,1)*gt(#motion,0)*eq(#focus,0)'+('' if self.theme=='dog' else f'*eq(#palette,{p})'))

    def card(self,x,y,wh):
        for p in range(2):self.img(f'tablet/card_{p}.png',x,y,wh,wh,visibility=f'eq(#palette,{p})')
        self.boxes.append({'role':'card','box':[x,y,wh,wh]})

def make_bear(w,h):
    s=Scene(w,h,'bear'); land=w>h
    for p in range(2):s.img(f'tablet/bg_{p}.jpg',0,0,w,h,visibility=f'eq(#palette,{p})',isBackground='true')
    layout=DESIGNS['bear']['landscape' if land else 'portrait']
    if layout['canvas']!=[w,h]:raise ValueError('Template canvas does not match bear target')
    clock,date,bear=layout['clock'],layout['date'],layout['character']
    cards,cw=layout['cards'],layout['cardSize']
    by,bw,bx=layout['buttons']['y'],layout['buttons']['width'],layout['buttons']['x']
    s.themed_clock(*clock)
    s.themed_clock(*date,fmt='M月d日 EEEE')
    start=len(s.root)
    s.themed_text('一起，把日子过得柔软。',date[0],date[1]+100,32)
    s.themed_text('B E A R   /   D A I L Y',date[0],date[1]+166,23)
    s.group_since(start,'eq(#focus,0)')
    for i,(folder,count) in enumerate([('ani3',58),('ani2',50),('ani1',50),('ani4',50),('ani5',49)]):
        s.img(folder+'/a.png',bear[0],f'{bear[1]}+sin(#time*#animation_rate/1600)*{8 if land else 10}*#effective_motion-24*#reaction',bear[2],bear[3],srcid=f'ifelse(#effective_motion,int(#time*#animation_rate/100)%{count},0)',visibility=f'eq(#look,{i})')
    s.boxes.append({'role':'character','box':list(bear)})
    s.character_control('character-bear',bear,'look',5,'react_at')
    start=len(s.root)
    for x,y in cards:s.card(x,y,cw)
    x,y=cards[0];d=cw*.69;dx=x+(cw-d)/2;dy=y+(cw-d)/2
    for p in range(2):s.img(f'tablet/dial_{p}.png',dx,dy,d,d,visibility=f'eq(#palette,{p})')
    for hand,rot in [('hour','#hour12*30+#minute/2'),('minute','#minute*6'),('second','#second*6')]:
        s.img(f'tablet/{hand}.png',dx,dy,d,d,pivotX=d/2,pivotY=d/2,rotation=rot)
    x,y=cards[1]
    s.themed_text('平板电量',x+cw*.18,y+cw*.18,32)
    for p,c in [(0,'#243a46'),(1,'#d5e5f4')]:
        s.elem('Text',x=x+cw*.18,y=y+cw*.32,size=92,color=c,format='%d%%',paras='#battery_level',visibility=f'eq(#palette,{p})',alignV='top')
    s.img('tablet/track.png',x+cw*.18,y+cw*.71,cw*.64,8)
    s.img('tablet/bar.png',x+cw*.18,y+cw*.71,f'{cw*.64}*#battery_level/100',8)
    for p,c in [(0,'#51727d'),(1,'#9fc3da')]:
        s.text('正在充电',x+cw*.18,y+cw*.78,23,c,visibility=f'(eq(#battery_state,1)+eq(#battery_state,3))*eq(#palette,{p})')
    x,y=cards[2]
    s.themed_clock(x+cw*.18,y+cw*.16,37,fmt='EEEE')
    s.themed_clock(x+cw*.18,y+cw*.29,124,fmt='dd')
    s.themed_clock(x+cw*.18,y+cw*.72,27,fmt='yyyy / MM')
    x,y=cards[3]
    s.themed_text('今日心情',x+cw*.18,y+cw*.18,32)
    s.themed_text('慢慢来',x+cw*.18,y+cw*.37,67)
    s.themed_text('每一刻，都值得喜欢。',x+cw*.18,y+cw*.70,25)
    s.group_since(start,'eq(#focus,0)')
    s.button('主题外观',bx[0],by,bw,108,'palette_mode',3,name='appearance',visibility='eq(#focus,0)',
             labels=[('外观：浅色','eq(#palette_mode,0)'),('外观：深色','eq(#palette_mode,1)'),('外观：自动','eq(#palette_mode,2)')])
    s.button('轻触换装',bx[1],by,bw,108,'look',5,name='costume',visibility='eq(#focus,0)')
    s.button('动画节奏',bx[2],by,bw,108,'motion',3,name='motion',visibility='eq(#focus,0)',
             labels=[('动画：暂停','eq(#motion,0)'),('动画：标准','eq(#motion,1)'),('动画：舒缓','eq(#motion,2)')])
    s.enhancements(layout)
    s.themed_text('上滑解锁',w/2,h-40,25,align='center')
    return s

def make_dog(w,h):
    s=Scene(w,h,'dog');land=w>h
    s.img('tablet/sea.jpg',0,0,w,h,scaleType='center_crop',isBackground='true')
    layout=DESIGNS['dog']['landscape' if land else 'portrait']
    if layout['canvas']!=[w,h]:raise ValueError('Template canvas does not match dog target')
    (cx,cy,cs),date=layout['clock'],layout['date']
    dogs,decor=layout['characters'],layout['decorations']
    by,bx,bw=layout['buttons']['y'],layout['buttons']['x'],layout['buttons']['width']
    s.clock(cx,cy,cs,'#ffffff')
    s.clock(*date,'#ffffff',fmt='M月d日 EEEE')
    s.text('H A P P Y   E V E R Y D A Y',date[0],date[1]+100,26,'#ffffff')
    for p,(x,y,targetw) in enumerate(dogs):
        variable='look_a' if p==0 else 'look_b';suffix='_a' if p==0 else '_b'
        folders=[6,7,8,5] if p==0 else [2,3,4,1]
        for look,n in enumerate(folders):
            iw,ih=DOG_DIMS[n]
            s.img(f'dx/dx{n}.png',f'{x}+sin(#time*#animation_rate/2100+{p})*12*#effective_motion',f'{y}+sin(#time*#animation_rate/1400+{p})*18*#effective_motion-28*#reaction{suffix}',targetw,round(targetw*ih/iw),srcid='ifelse(#effective_motion,int(#time*#animation_rate/120)%20+1,1)',visibility=f'eq(#{variable},{look})')
        maxh=max(targetw*DOG_DIMS[n][1]/DOG_DIMS[n][0] for n in folders)
        s.boxes.append({'role':'character','box':[x-12,y-18,targetw+24,maxh+36]})
        s.character_control('character-white' if p==0 else 'character-brown',(x,y,targetw,maxh),variable,4,'react'+suffix+'_at')
    start=len(s.root)
    for x,y,ww,n in decor:
        iw,ih=DOG_DIMS[n];s.img(f'dx/dx{n}.png',x,f'{y}+sin(#time*#animation_rate/1800)*10*#effective_motion',ww,round(ww*ih/iw),srcid='ifelse(#effective_motion,int(#time*#animation_rate/120)%20+1,1)')
    s.group_since(start,'eq(#focus,0)')
    s.button('一起换装',bx[0],by,bw,110,sea=True,name='costume',visibility='eq(#focus,0)',tap=[('look_a','(#look_a+1)%4'),('look_b','(#look_b+1)%4')])
    s.button('动画节奏',bx[1],by,bw,110,'motion',3,sea=True,name='motion',visibility='eq(#focus,0)',
             labels=[('动画：暂停','eq(#motion,0)'),('动画：标准','eq(#motion,1)'),('动画：舒缓','eq(#motion,2)')])
    s.enhancements(layout)
    s.text('上滑解锁',w/2,h-80,27,'#ffffff',align='center')
    return s

def evaluate(expr,state):
    return Evaluator(lambda name:state[name]).evaluate(expr)


class SceneState(dict):
    def __init__(self,scene,values):
        super().__init__(values)
        self.definitions={e.get('name'):e.get('expression') for e in scene.root.findall('Var')}
    def __missing__(self,name):
        if name not in self.definitions:raise KeyError(name)
        value=evaluate(self.definitions[name],self);self[name]=value;return value


def leaves(root,state,ox=0,oy=0):
    for e in root:
        if not evaluate(e.get('visibility','1'),state):continue
        if e.tag=='Group':
            yield from leaves(e,state,ox+evaluate(e.get('x','0'),state),oy+evaluate(e.get('y','0'),state))
        elif e.tag in ('Image','Text','DateTime'):yield e,ox,oy

def render(scene,assets,state):
    state=dict(state)
    if scene.theme=='dog' and 'look' in state:state.update(look_a=state['look'],look_b=state['look'])
    st=SceneState(scene,{'screen_width':scene.w,'screen_height':scene.h,'time':0,'hour24':8,'hour12':8,'minute':8,'second':24,'battery_level':86,'battery_state':0,**state})
    canvas=Image.new('RGBA',(scene.w,scene.h))
    for e,ox,oy in leaves(scene.root,st):
        a=e.attrib
        if 'visibility' in a and not evaluate(a['visibility'],st):continue
        x,y=ox+float(evaluate(a.get('x','0'),st)),oy+float(evaluate(a.get('y','0'),st))
        if e.tag=='Image':
            src=a['src']
            if 'srcid' in a:src=re.sub(r'(\.[^.]+)$','_'+str(int(evaluate(a['srcid'],st)))+r'\1',src)
            im=Image.open(io.BytesIO(assets[PREFIX+src])).convert('RGBA')
            w,h=int(round(evaluate(a['w'],st))),int(round(evaluate(a['h'],st)))
            if w<=0 or h<=0:continue
            im=ImageOps.fit(im,(w,h),method=Image.Resampling.LANCZOS) if a.get('scaleType')=='center_crop' else im.resize((w,h),Image.Resampling.LANCZOS)
            if 'rotation' in a:im=im.rotate(-evaluate(a['rotation'],st),resample=Image.Resampling.BICUBIC)
            canvas.alpha_composite(im,(round(x),round(y)))
        else:
            if e.tag=='DateTime':
                text={'HH:mm':f"{st['hour24']:02d}:{st['minute']:02d}",'M月d日 EEEE':'10月16日 星期五','EEEE':'星期五','dd':'16','yyyy / MM':'2026 / 10'}[a['format']]
            elif 'format' in a:text=a['format']%evaluate(a['paras'],st)
            else:text=a['text']
            font=ImageFont.truetype(NUMFONT if (text.replace(':','').replace('%','').isdigit()) else FONT,round(float(a['size'])))
            draw=ImageDraw.Draw(canvas);bbox=draw.textbbox((0,0),text,font=font);tw,th=bbox[2]-bbox[0],bbox[3]-bbox[1]
            if a.get('align')=='center':x-=tw/2
            if a.get('alignV')=='center':y-=th/2
            draw.text((round(x)-bbox[0],round(y)-bbox[1]),text,font=font,fill=a['color'])
    return canvas.convert('RGB')

def fit_preview(im,size,bg):
    out=Image.new('RGB',size,bg);im=ImageOps.contain(im,size,Image.Resampling.LANCZOS)
    out.paste(im,((size[0]-im.width)//2,(size[1]-im.height)//2));return out

def build(name, source_path=None, destination=None):
    global DOG_DIMS
    src=Path(source_path) if source_path else BASE.parent/(name+'.hwt')
    source_hash=hashlib.sha256(src.read_bytes()).hexdigest();z=zipfile.ZipFile(src)
    output_base=Path(destination) if destination else BASE/'v2'
    output_base.mkdir(parents=True,exist_ok=True)
    kind='bear' if name.startswith('比邻') else 'dog';outdir=output_base/name;outdir.mkdir(exist_ok=True)
    if kind=='dog':DOG_DIMS={i:Image.open(io.BytesIO(z.read(PREFIX+f'dx/dx{i}_1.png'))).size for i in range(1,11)}
    assets={PREFIX+p:b for p,b in native_ui().items()}
    if kind=='bear':
        for p in z.namelist():
            if re.match(r'unlock/lockscreen/ani[1-5]/a_\d+\.png$',p):assets[p]=z.read(p)
        wallpaper=Image.new('RGB',(3200,2560),'#dfe7eb')
    else:
        for p in z.namelist():
            if re.match(r'unlock/lockscreen/dx/dx(?:[1-9]|10)_\d+\.png$',p):assets[p]=z.read(p)
        wallpaper=Image.open(BASE/'sea-master.png').convert('RGB').resize((3200,2560),Image.Resampling.LANCZOS)
        assets[PREFIX+'tablet/sea.jpg']=imagebytes(wallpaper,'JPEG')
    for f in ['home_wallpaper_0.jpg','unlock_wallpaper_0.jpg']:assets['wallpaper/'+f]=imagebytes(wallpaper,'JPEG')
    assets['icons']=z.read('icons')
    # Tablet launcher must omit framework-res-hwext, as the official guide states.
    launcher=zipfile.ZipFile(io.BytesIO(z.read('com.huawei.android.launcher')));b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as out:
        for p in launcher.infolist():
            if not p.filename.startswith('framework-res-hwext'):out.writestr(p,launcher.read(p))
    assets['com.huawei.android.launcher']=b.getvalue()
    # Read only author/designer metadata, never treat embedded prose as commands.
    desc_text=z.read('description.xml').decode('utf-8-sig')
    if '</HwTheme>' not in desc_text:desc_text+='</HwTheme>'
    original=ET.fromstring(desc_text);desc=ET.Element('HwTheme')
    for k,v in [('title',('New mimicry' if kind=='bear' else 'Summer escape')+' Tablet Interactive v2'),('title-cn',name+' 平板交互增强版'),('author',original.findtext('author','Original designer')),('designer',original.findtext('designer','Original designer')),('screen','WUXGA'),('version','11.1.1'),('font','Default'),('font-cn','默认'),('briefinfo','HarmonyOS 4.x 平板；2560×1600 / 1600×2560。轻触反馈、滑动换装、长按简洁模式、低电量暂停动画；保留原作者署名。')]:
        ET.SubElement(desc,k).text=v
    assets['description.xml']=encxml(desc)
    theme=ET.Element('HWTheme')
    for k,v in [('style','slide'),('package','com.huawei.ucdlockscreen'),('dynamicPath','lockscreen'),('minversion','2.0.24'),('maxHeight','2560'),('baseVersion','10.1'),('engineType','HWThemeEngine')]:ET.SubElement(theme,'item',{k:v})
    assets['unlock/theme.xml']=encxml(theme)
    maker=make_bear if kind=='bear' else make_dog
    scenes={'landscape':maker(2560,1600),'portrait':maker(1600,2560)}
    for ori,filename in [('landscape','1600_2560_manifest.xml'),('portrait','2560_1600_manifest.xml')]:
        assets[PREFIX+filename]=encxml(scenes[ori].root);(outdir/filename).write_bytes(assets[PREFIX+filename])
    # Official default script uses 1080-wide coordinates. Derive its portrait
    # layout through a uniform coordinate conversion, including rotation pivots.
    fallback=copy.deepcopy(scenes['portrait'].root);fallback.set('screenWidth','1080')
    ratio=1080/1600
    for e in fallback.iter():
        for attr in ['x','y','w','h','size','pivotX','pivotY']:
            if attr in e.attrib:
                val=e.get(attr)
                if val in ['#screen_width','#screen_height']:continue
                e.set(attr,f'({val})*{ratio}')
    assets[PREFIX+'manifest.xml']=encxml(fallback)
    (outdir/'manifest.xml').write_bytes(assets[PREFIX+'manifest.xml'])
    # Desktop preview uses actual retained themed icons on the new wallpaper.
    iz=zipfile.ZipFile(io.BytesIO(assets['icons']))
    iconpaths=[p for p in iz.namelist() if '/' not in p and p.endswith('.png') and not p.startswith(('icon_','sym_'))]
    preferred=['com.huawei.camera.png','com.android.settings.png','com.huawei.photos.png','com.android.calendar.png','com.huawei.browser.png','com.huawei.appmarket.png','com.huawei.music.png','com.huawei.filemanager.png']
    iconpaths=([p for p in preferred if p in iconpaths]+[p for p in iconpaths if p not in preferred])[:16]
    previews={};geometry={}
    for ori,s in scenes.items():
        im=render(s,assets,{})
        im.save(outdir/(ori+'-lockscreen.jpg'),quality=94)
        previews[ori]=im
        geometry[ori]=s.boxes
        home=ImageOps.fit(wallpaper,(s.w,s.h),method=Image.Resampling.LANCZOS)
        cols=8 if ori=='landscape' else 4;rows=2 if ori=='landscape' else 4
        size=135 if ori=='landscape' else 155
        for i,p in enumerate(iconpaths):
            icon=Image.open(io.BytesIO(iz.read(p))).convert('RGBA');icon.thumbnail((size,size),Image.Resampling.LANCZOS)
            xx=round((i%cols+1)*s.w/(cols+1)-icon.width/2);yy=round(s.h*.42+(i//cols)*(size+125))
            home.paste(icon,(xx,yy),icon)
        home.save(outdir/(ori+'-desktop.jpg'),quality=94)
        previewsize=(2160,1080) if ori=='landscape' else (1080,2160)
        tag='preview_land_' if ori=='landscape' else 'preview_'
        for category,img in [('unlock',im),('icons',home)]:assets[f'preview/{tag}{category}_0.jpg']=imagebytes(fit_preview(img,previewsize,'#dfe7eb' if kind=='bear' else '#138ba4'),'JPEG')
    for mode in range(1,5 if kind=='bear' else 4):
        im=render(scenes['landscape'],assets,{'look':mode,'palette_mode':mode%2 if kind=='bear' else 0})
        im.save(outdir/f'landscape-variant-{mode}.jpg',quality=92)
    if kind=='bear':
        dark=render(scenes['landscape'],assets,{'palette_mode':1});dark.save(outdir/'landscape-dark.jpg',quality=94)
        assets['preview/preview_land_unlock_1.jpg']=imagebytes(fit_preview(dark,(2160,1080),'#222b36'),'JPEG')
    assets['preview/cover.jpg']=imagebytes(fit_preview(previews['portrait'],(1080,1920),'#dfe7eb' if kind=='bear' else '#138ba4'),'JPEG')
    assets['preview/cover_land.jpg']=imagebytes(fit_preview(previews['landscape'],(1920,1080),'#dfe7eb' if kind=='bear' else '#138ba4'),'JPEG')
    for p in ['icon_small.jpg','icon_small_1.jpg','icon_thumbnail.png']:
        if 'preview/'+p in z.namelist():assets['preview/'+p]=z.read('preview/'+p)
    (outdir/'geometry.json').write_text(json.dumps(geometry,ensure_ascii=False,indent=2),encoding='utf-8')
    for orientation,scene in scenes.items():
        for suffix,state in [('focus',{'focus':1}),('low-power',{'battery_level':10}),('reaction',{'time':300,'react_at':0,'react_a_at':0,'react_b_at':0})]:
            render(scene,assets,state).save(outdir/f'{orientation}-{suffix}.jpg',quality=94)
    if kind=='dog':render(scenes['landscape'],assets,{'look_a':2,'look_b':1}).save(outdir/'landscape-independent.jpg',quality=94)
    outpath=output_base/(name+' 平板2560x1600 交互增强v2.hwt')
    if outpath.resolve()==src.resolve():raise ValueError('Output must not overwrite source')
    with zipfile.ZipFile(outpath,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as dst:
        for p,content in sorted(assets.items()):dst.writestr(p,content)
    report=validate(outpath,scenes,assets)
    report.update({'source':str(src),'sourceSha256':source_hash,'sourceUnchanged':source_hash==hashlib.sha256(src.read_bytes()).hexdigest(),'outputSha256':hashlib.sha256(outpath.read_bytes()).hexdigest(),'bytes':outpath.stat().st_size,'sources':SOURCES,'scope':'原主题角色素材与图标保留，动态锁屏按平板重新实现。手机大主题皮肤、AOD和桌面万象小组件未移植。','nativeDeviceTest':False,'preview':'根据导出的同一XML生成的离线设计预览；字体及原生渲染行为仍需实机核对。'})
    report['package']=str(outpath.resolve())
    (outdir/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(name,report['passed'],outpath.stat().st_size,report['errors'])
    return report

def validate(path,scenes,assets):
    from multires_app.tools.validate_tablet_theme import audit_package
    report=audit_package(path,advanced=True);errors=report['errors']
    checks=Counter(report['checks'])
    for ori,scene in scenes.items():
        for box in scene.boxes:
            x,y,w,h=box['box'];checks['layoutBounds']+=1
            if x<0 or y<0 or x+w>scene.w or y+h>scene.h:errors.append(f'{ori}: out of bounds {box}')
    report.update(passed=not errors,errors=list(dict.fromkeys(errors)),checks=dict(checks))
    return report


def write_gallery(destination,reports):
    data=[{'theme':Path(r['source']).stem,'filename':Path(r['package']).name,'passed':r['passed'],
           'cases':r['checks'].get('interactionScenarios',0)} for r in reports]
    template=(BASE/'interactive-gallery.html').read_text(encoding='utf-8')
    content=template.replace('__REPORT_JSON__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c'))
    (Path(destination)/'index.html').write_text(content,encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--design',type=Path,default=BASE/'tablet-layouts.json',help='Native tablet composition template')
    parser.add_argument('--out',type=Path,default=BASE/'v2',help='Output directory; v1 packages are retained')
    args=parser.parse_args()
    DESIGNS=json.loads(args.design.read_text(encoding='utf-8'))
    reports=[build(n,destination=args.out) for n in PYTHEMES]
    (args.out/'validation-summary.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    write_gallery(args.out,reports)
    if not all(r['passed'] for r in reports):raise SystemExit(1)
