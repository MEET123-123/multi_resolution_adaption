import os,sys,subprocess,time,re,json,io,urllib.request,urllib.parse,concurrent.futures,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(r'G:\codex_workspace\theme-previews')
sys.path.insert(0,str(ROOT/'tools/python-libs'))
import zxingcpp
from PIL import Image
ADB=r'G:\codex_workspace\android-adb\platform-tools\adb.exe'
REMOTE='/sdcard/Android/data/com.heytap.themestore/files/download_cache/cached_content_index.exi'
sys.stdout.reconfigure(encoding='utf-8')
POOL=concurrent.futures.ThreadPoolExecutor(max_workers=4)
records=[]
if (ROOT/'manifest.json').exists(): records=json.loads((ROOT/'manifest.json').read_text('utf8'))
seen_ids={str(r['masterId']) for r in records}
visited={r['name'] for r in records}

def adb(*args,binary=False):
 p=subprocess.run([ADB,'-s','8ea99c1e',*map(str,args)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=35)
 if p.returncode: raise RuntimeError(p.stderr.decode('utf8','replace'))
 return p.stdout if binary else p.stdout.decode('utf8','replace')
def tap(x,y): adb('shell','input','tap',int(x),int(y));time.sleep(.35)
def back(): adb('shell','input','keyevent','4');time.sleep(.35)
def ui():
 adb('shell','uiautomator','dump','/sdcard/Download/theme-window.xml')
 return list(ET.fromstring(adb('exec-out','cat','/sdcard/Download/theme-window.xml')).iter('node'))
def find(nodes,suffix): return next((n for n in nodes if n.get('resource-id','').endswith(suffix)),None)
def center(n,dy=0):
 a=list(map(int,re.findall(r'\d+',n.get('bounds',''))));return ((a[0]+a[2])//2,(a[1]+a[3])//2+dy)
def videos():
 try:
  raw=adb('exec-out','cat',REMOTE,binary=True)
  return set(x.decode() for x in re.findall(rb'https?://[^\x00-\x20]+?\.mp4(?:\?[^\x00-\x20]*)?',raw))
 except: return set()
def get(url):
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
 with urllib.request.urlopen(req,timeout=35) as r:return r.read()
def detail(mid):
 raw=get('https://theme-html-cn.heytapmobi.com/theme/resource/info/jsonp?'+urllib.parse.urlencode({'masterId':mid,'region':'CN','callback':'jsonpDetail'})).decode()
 return json.loads(raw[raw.index('(')+1:raw.rfind(')')])
def save():
 p=ROOT/'manifest.json.tmp';p.write_text(json.dumps(records,ensure_ascii=False,indent=2),'utf8');p.replace(ROOT/'manifest.json')
def download(r):
 folder=ROOT/r['folder'];folder.mkdir(parents=True,exist_ok=True)
 assets=[]
 for kind,urls in [('image',r['imageUrls']),('video',r['videoUrls'])]:
  for i,url in enumerate(urls,1):
   ext='.mp4' if kind=='video' else ('.webp' if '.webp' in url else Path(urllib.parse.urlparse(url).path).suffix or '.jpg')
   name=f'{kind}_{i:02}{ext}'
   a={'kind':kind,'url':url,'file':name}
   try:
    data=get(url)
    if kind=='image':
     im=Image.open(io.BytesIO(data));im.verify();a['width'],a['height']=Image.open(io.BytesIO(data)).size
    elif b'ftyp' not in data[:64]:raise ValueError('Not an MP4 file')
    (folder/name).write_bytes(data);a['bytes']=len(data);a['status']='downloaded'
   except Exception as e:a['status']='failed';a['error']=str(e)
   assets.append(a)
 r['assets']=assets;r['status']='complete' if all(a['status']=='downloaded' for a in assets) else 'partial'
 (folder/'metadata.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),'utf8')
 print(f"DOWNLOADED {r['name']}: {sum(a['status']=='downloaded' for a in assets)} assets",flush=True)
 return r
futures=[]
def collect(nodes=None):
 nodes=nodes or ui(); name_node=find(nodes,':id/res_name')
 if name_node is None:raise RuntimeError('Not on theme preview')
 name=name_node.get('text');before=current_before[0]
 tap(*center(name_node));nodes=ui()
 share=find(nodes,':id/share_layout')
 if share is None:share=find(nodes,':id/share_title_tv')
 if share is None:raise RuntimeError('Share control not found')
 tap(*center(share));url=None;png=None
 for attempt in range(5):
  time.sleep(.7);png=adb('exec-out','screencap','-p',binary=True)
  results=zxingcpp.read_barcodes(Image.open(io.BytesIO(png)))
  url=next((x.text for x in results if 'masterId=' in x.text),None)
  if url:break
 if not url:raise RuntimeError('Share QR unavailable')
 mid=urllib.parse.parse_qs(urllib.parse.urlparse(url).query)['masterId'][0]
 d=detail(mid)
 if d.get('appType')!=0:raise RuntimeError('Resource is not a theme')
 name=d.get('name') or name
 existing=next((r for r in records if r['masterId']==mid),None)
 if existing is not None:r=existing
 else:
  folder=f"{len(records)+1:03}_{re.sub(r'[<>:\"/\\|?*\x00-\x1f]','_',name)}_{mid}"
  r={'masterId':mid,'name':name,'shareUrl':url,'folder':folder,'imageUrls':d.get('hdPicUrl',[]),'videoUrls':[],'status':'downloading','detail':d}
  records.append(r);seen_ids.add(mid)
 folder=ROOT/r['folder'];folder.mkdir(exist_ok=True);(folder/'share-evidence.png').write_bytes(png)
 # Return from share card to the preview, without entering trial/purchase controls.
 for _ in range(3):
  back();nodes=ui()
  if find(nodes,':id/res_name') is not None:break
 if find(nodes,':id/res_name') is None:raise RuntimeError('Could not return to preview')
 scene_dir=folder/'scene-checks';scene_dir.mkdir(exist_ok=True)
 (scene_dir/'00_initial.png').write_bytes(adb('exec-out','screencap','-p',binary=True))
 steps=min(len(r['imageUrls'])+1,9)
 for i in range(steps):
  adb('shell','input','swipe',620,700,100,700,320);time.sleep(.5)
  (scene_dir/f'{i+1:02}_left.png').write_bytes(adb('exec-out','screencap','-p',binary=True))
 for i in range(steps):
  adb('shell','input','swipe',100,700,620,700,320);time.sleep(.3)
 (scene_dir/'99_right.png').write_bytes(adb('exec-out','screencap','-p',binary=True))
 after=videos();v=sorted(after-before)
 r['videoUrls']=sorted(set(r.get('videoUrls',[]))|set(v))
 r['videoNote']='New cache entries observed while this theme was open' if r['videoUrls'] else 'No new video URL observed; may require revisit'
 r['scenesChecked']=True;r['leftSwipeCount']=steps;r['rightSwipeCount']=steps
 visited.add(name);save();futures.append(POOL.submit(download,r))
 print(f"COLLECTED {len(records)}/100 {name} ({mid}), images={len(r['imageUrls'])}, videos={len(r['videoUrls'])}, multi-scene checked",flush=True)
 back();nodes=ui()
 if find(nodes,':id/name') is not None:return nodes
 for _ in range(3):
  back();nodes=ui()
  if find(nodes,':id/name') is not None:return nodes
 raise RuntimeError('Could not return to theme catalog')
last_video=[set()]
current_before=[videos()]
try:
 nodes=ui()
 # Resume safely if a share card is currently open.
 if find(nodes,':id/res_name') is None and find(nodes,':id/name') is None:
  for _ in range(4):
   back();nodes=ui()
   if find(nodes,':id/res_name') is not None or find(nodes,':id/name') is not None:break
 if find(nodes,':id/res_name') is not None:nodes=collect(nodes)
 no_new=0;tab=0
 while len(records)<100:
  candidates=[]
  for n in nodes:
   if n.get('resource-id','').endswith(':id/name') and n.get('text') and n.get('text') not in visited:
    x,y=center(n)
    if 330<y<1320 and 30<x<690:candidates.append(n)
  if candidates:
   n=candidates[0];name=n.get('text');visited.add(name)
   current_before[0]=videos()
   tap(*center(n,-160));opened=ui()
   if find(opened,':id/res_name') is None:
    tap(*center(n,-80));opened=ui()
   if find(opened,':id/res_name') is None:
    print('SKIP unable to open '+name,flush=True);nodes=opened;continue
   nodes=collect(opened);no_new=0
  else:
   signature=[n.get('text') for n in nodes if n.get('resource-id','').endswith(':id/name')]
   adb('shell','input','swipe',360,1250,360,480,420);time.sleep(.5);nodes=ui()
   current=[n.get('text') for n in nodes if n.get('resource-id','').endswith(':id/name')]
   no_new=no_new+1 if signature==current else 0
   if no_new>=2:
    tab+=1
    if tab>4:raise RuntimeError('Reached the end of all ranking lists')
    tap(80+136*tab,215);nodes=ui();no_new=0
  for f in futures:
   if f.done():f.result()
  save()
except Exception as e:
 print('STOPPED: '+repr(e),flush=True)
finally:
 for f in futures:
  try:f.result()
  except Exception as e:print('DOWNLOAD ERROR '+repr(e),flush=True)
 save();POOL.shutdown()
 print('FINISHED count='+str(len(records)),flush=True)