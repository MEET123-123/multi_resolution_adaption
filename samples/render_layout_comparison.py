"""Render cached source/target geometry for visual QA without running a browser.

This is a static approximation, not a native engine or a browser screenshot.
Theme resources are read only; this writes comparison PNGs, not new assets.
"""
import json
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
FONT=Path('C:/Windows/Fonts/msyh.ttc')


@lru_cache(maxsize=128)
def font(size):
    return ImageFont.truetype(str(FONT),max(6,int(size)))


def draw_layout(project,directory,layout,viewport,output_size):
    scale=min(output_size[0]/viewport['width'],output_size[1]/viewport['height'])
    size=(round(viewport['width']*scale),round(viewport['height']*scale))
    canvas=Image.new('RGBA',size,'#14212b')
    for row in layout['rows']:
        index,x,y,w,h,alpha,asset_index,text,text_size=row[:9]
        uid=project['nodeOrder'][index];node=project['model']['nodes'][uid]
        if node['tag']=='Button':continue
        t=layout.get('transforms',{}).get(project['nodeComponents'].get(uid),{'sx':1,'sy':1,'tx':0,'ty':0})
        left=(x*t['sx']+t['tx'])*scale;top=(y*t['sy']+t['ty'])*scale
        width=round(w*t['sx']*scale);height=round(h*t['sy']*scale)
        if width<=0 or height<=0 or alpha<=0:continue
        asset=project['assetOrder'][asset_index] if asset_index>=0 else None
        path=directory/'assets'/asset if asset else None
        if path and path.is_file() and path.suffix.lower() not in {'.mp4','.webm'}:
            with Image.open(path) as original:
                bitmap=original.convert('RGBA').resize((width,height),Image.Resampling.LANCZOS)
            if alpha<1:bitmap.putalpha(bitmap.getchannel('A').point(lambda a:round(a*alpha)))
            canvas.alpha_composite(bitmap,(round(left),round(top)))
        elif node['tag'] in {'Text','Time','Date','DateTime','Number'} and text:
            color=node['attributes'].get('color','#ffffff')
            if len(color)==9 and color.startswith('#'):color='#'+color[3:]
            if not color.startswith('#'):color='#ffffff'
            ImageDraw.Draw(canvas).text((left,top),str(text),font=font(text_size*t['sx']*scale),fill=color)
    return canvas


def render(directory,state_index=0):
    project=json.loads((directory/'project.json').read_text(encoding='utf-8'))
    source=project['sourceDevice']
    near=next(d for d in project['devices'] if d['profile']['id']=='phone-similar-portrait')
    tablet=next(d for d in project['devices'] if d['profile']['id']=='tablet-landscape')
    result=Image.new('RGB',(2400,1160),'#ecf0f3');draw=ImageDraw.Draw(result)
    draw.text((36,20),'原布局 / 相近比例复用 / 原顺序两列',font=font(32),fill='#213341')
    for column,(title,device) in enumerate([('原始直板机',source),('相近比例：保留原构图',near),('2560×1600：逐行两列',tablet)]):
        x=column*800+30
        draw.text((x,80),title,font=font(26),fill='#213341')
        bitmap=draw_layout(project,directory,device['layouts'][state_index],device['viewport'],(740,930))
        result.paste(bitmap,(x+(740-bitmap.width)//2,130+(930-bitmap.height)//2))
    draw.text((36,1105),'离线采样画面；复杂图层需结合 adaptationReview 复核，非真机截图。',font=font(22),fill='#52636d')
    output=directory/('layout-comparison.png' if state_index==0 else f'layout-comparison-state-{state_index}.png');result.save(output)
    return output


if __name__=='__main__':
    for name in ('bear','dog'):print(render(ROOT/'theme/adaptive-code-demo'/name))
