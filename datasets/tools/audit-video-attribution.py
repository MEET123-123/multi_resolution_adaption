import sys,json,subprocess,concurrent.futures,math
from pathlib import Path
sys.path.insert(0,r'G:\codex_workspace\theme-previews\tools\python-libs')
import imageio_ffmpeg
from PIL import Image,ImageDraw,ImageFont
root=Path(r'G:\codex_workspace\theme-previews').resolve();records=json.loads((root/'manifest.json').read_text('utf8'))
r=next(r for r in records if r['masterId']=='13477591');wrong='https://theme.heytapimage.com/openplatform/share/202610/19/6eef9ffefd287810e9d99333f30e8ecf.mp4'
r['videoUrls']=[u for u in r['videoUrls'] if u!=wrong]
source=(root/r['folder']/'video_02.mp4').resolve();dest=(root/'inspection/unassigned-videos/13477591_previous-theme-copy.mp4').resolve()
assert source.is_relative_to(root) and dest.is_relative_to(root)
if source.exists():dest.parent.mkdir(exist_ok=True);source.rename(dest)
r['videoAttributionAudit']='Removed a duplicate belonging to the previous theme; verified remaining clip against preview images'
(root/'manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),'utf8')
ff=imageio_ffmpeg.get_ffmpeg_exe();targets=[r for r in records if r.get('videoUrls')];folder=root/'inspection/video-audit';folder.mkdir(exist_ok=True)
def frame(r):
 video=root/r['folder']/'video_01.mp4';out=folder/(r['masterId']+'.jpg')
 subprocess.run([ff,'-y','-loglevel','error','-ss','1.5','-i',str(video),'-frames:v','1','-vf','scale=128:-1',str(out)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 return r,out
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:items=list(pool.map(frame,targets))
font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',14)
for page in range(math.ceil(len(items)/12)):
 canvas=Image.new('RGB',(1080,990),'white');draw=ImageDraw.Draw(canvas)
 for j,(r,video) in enumerate(items[page*12:(page+1)*12]):
  x=(j%4)*270;y=(j//4)*330
  draw.text((x+3,y+2),f"{page*12+j+1:02} {r['masterId']} {r['name'][:10]}",font=font,fill='black')
  for k,p in enumerate([next((root/r['folder']).glob('image_01.*')),video]):
   im=Image.open(p).convert('RGB');im.thumbnail((128,290));canvas.paste(im,(x+k*134,y+28))
 canvas.save(folder/f'page-{page+1:02}.jpg',quality=88)
print('Attribution audit pages:',math.ceil(len(items)/12),'clips:',len(items))