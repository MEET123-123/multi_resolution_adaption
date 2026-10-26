import subprocess,time,sys,re,json,io,xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0,r'G:\codex_workspace\theme-previews\tools\python-libs')
import zxingcpp
from PIL import Image
root=Path(r'G:\codex_workspace\theme-previews\inspection/scenes');root.mkdir(exist_ok=True)
adb=r'G:\codex_workspace\android-adb\platform-tools\adb.exe'
def run(*a):return subprocess.check_output([adb,'-s','8ea99c1e',*map(str,a)])
def xml():
 run('shell','uiautomator','dump','/sdcard/Download/theme-window.xml');return ET.fromstring(run('exec-out','cat','/sdcard/Download/theme-window.xml'))
shot=run('exec-out','screencap','-p');print([x.text for x in zxingcpp.read_barcodes(Image.open(io.BytesIO(shot)))])
for i in range(3):
 run('shell','input','keyevent','4');time.sleep(.3);x=xml()
 if any(n.get('resource-id','').endswith(':id/res_name') for n in x.iter('node')):break
for i,d in enumerate(['initial','left','left','right','right']):
 if d=='left':run('shell','input','swipe',620,700,100,700,400)
 if d=='right':run('shell','input','swipe',100,700,620,700,400)
 time.sleep(.8)
 shot=run('exec-out','screencap','-p');(root/f'{i:02}_{d}.png').write_bytes(shot)
 raw=run('exec-out','cat','/sdcard/Android/data/com.heytap.themestore/files/download_cache/cached_content_index.exi')
 print(d, re.findall(rb'https?://[^\x00-\x20]+?\.mp4',raw))
x=xml();print([(n.get('text'),n.get('resource-id')) for n in x.iter('node') if n.get('text')])