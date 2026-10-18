from pathlib import Path
import zipfile, io, json
from PIL import Image, ImageDraw, ImageFont

BASE=Path(__file__).resolve().parent
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',18)
for name in ['比邻熊 新拟态','线条小狗 夏日出逃']:
    z=zipfile.ZipFile(BASE.parent/(name+'.hwt'))
    rows=[]
    for p in z.namelist():
        if p.startswith('unlock/lockscreen/') and p.lower().endswith(('.jpg','.png')):
            im=Image.open(io.BytesIO(z.read(p)))
            rows.append((p,im.size))
    (BASE/name/'asset-list.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    selected=[p for p in z.namelist() if p in ['preview/preview_unlock_0.jpg','preview/preview_unlock_1.jpg','preview/preview_unlock_2.jpg','wallpaper/home_wallpaper_0.jpg','wallpaper/unlock_wallpaper_0.jpg']]
    if name.startswith('比邻'):
        selected += ['unlock/lockscreen/'+p for p in ['zy/wp_0.jpg','zy/wp_1.jpg','zy/a_0.png','zy/a_1.png','ani2/a_0.png','ani2/a_49.png','kpsm1.png']]
    else:
        selected += ['unlock/lockscreen/'+p for p in ['wal.jpg','dx/dx16_0.jpg','dx/dx16_1.jpg','dx/dx14_0.png','dx/dx14_1.png']]
    selected=[p for p in selected if p in z.namelist()]
    sheet=Image.new('RGB',(320*4,420*((len(selected)+3)//4)), '#d9d9df')
    d=ImageDraw.Draw(sheet)
    for i,p in enumerate(selected):
        im=Image.open(io.BytesIO(z.read(p))).convert('RGBA')
        dims=im.size
        im.thumbnail((300,360))
        x=(i%4)*320+(320-im.width)//2;y=(i//4)*420+12
        sheet.paste(im,(x,y),im)
        d.text(((i%4)*320+8,(i//4)*420+375),p.replace('unlock/lockscreen/',''),font=font,fill='#151820')
        d.text(((i%4)*320+8,(i//4)*420+398),str(dims),font=font,fill='#151820')
    sheet.save(BASE/name/'source-contact-sheet.jpg')
print('Source contact sheets generated')
