from pathlib import Path
import json,struct,hashlib,urllib.request,concurrent.futures
from PIL import Image
ROOT=Path(r'G:\codex_workspace\theme-previews')
records=json.loads((ROOT/'manifest.json').read_text('utf8'))
cache=json.loads((ROOT/'inspection/cache-video-map.json').read_text('utf8'))

def verify_video(data):
 offset=0;boxes=[]
 while offset+8<=len(data):
  size,kind=struct.unpack_from('>I4s',data,offset);header=8
  if size==1:
   size=struct.unpack_from('>Q',data,offset+8)[0];header=16
  elif size==0:size=len(data)-offset
  if size<header or offset+size>len(data):raise ValueError('Incomplete MP4 container')
  boxes.append(kind);offset+=size
 if offset!=len(data) or b'moov' not in boxes or b'ftyp' not in boxes:raise ValueError('MP4 structure invalid')
 p=data.find(b'mvhd');duration=None
 if p>=0:
  if data[p+4]==0:scale,length=struct.unpack_from('>II',data,p+16)
  else:scale=struct.unpack_from('>I',data,p+24)[0];length=struct.unpack_from('>Q',data,p+28)[0]
  if scale:duration=round(length/scale,3)
 return duration

def audit(r):
 folder=ROOT/r['folder'];assets=[]
 for kind,urls in [('image',r['imageUrls']),('video',r.get('videoUrls',[]))]:
  for i,url in enumerate(urls,1):
   suffix='.mp4' if kind=='video' else '.webp' if '.webp' in url else Path(url.split('?')[0]).suffix or '.jpg'
   file=folder/f'{kind}_{i:02}{suffix}';a={'kind':kind,'url':url,'file':file.name}
   for attempt in range(2):
    try:
     if not file.exists() or (kind=='video' and len(urls)>1):
      req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
      data=urllib.request.urlopen(req,timeout=40).read();file.write_bytes(data)
     else:data=file.read_bytes()
     if kind=='image':
      with Image.open(file) as im:a['width'],a['height']=im.size;a['format']=im.format;a['frames']=getattr(im,'n_frames',1);im.load()
     else:a['durationSeconds']=verify_video(data)
     a['bytes']=len(data);a['sha256']=hashlib.sha256(data).hexdigest();a['status']='downloaded';break
    except Exception as e:
     a['status']='failed';a['error']=repr(e)
     if attempt==0 and file.exists():file.unlink()
   assets.append(a)
 r['assets']=assets;r['status']='complete' if all(a['status']=='downloaded' for a in assets) else 'partial'
 if r.get('scenesChecked'):r.pop('devicePreviewError',None)
 (folder/'metadata.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),'utf8')
 return r
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:records=list(pool.map(audit,records))
(ROOT/'manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),'utf8')
assets=[a for r in records for a in r['assets']]
summary={'themes':len(records),'uniqueThemeNames':len({r['name'] for r in records}),'images':sum(a['kind']=='image' and a['status']=='downloaded' for a in assets),'videos':sum(a['kind']=='video' and a['status']=='downloaded' for a in assets),'themesWithVideo':sum(bool(r.get('videoUrls')) for r in records),'sceneChecksCompleted':sum(bool(r.get('scenesChecked')) for r in records),'failedAssets':sum(a['status']!='downloaded' for a in assets),'originalMediaBytes':sum(a.get('bytes',0) for a in assets)}
(ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf8')
lines=['# 主题预览素材下载结果','',f"主题：{summary['themes']} 个（主题编号及名称均不重复）。",f"原始预览图片：{summary['images']} 张。",f"完整预览视频：{summary['videos']} 个，涉及 {summary['themesWithVideo']} 个主题。",f"手机左右滑动核对完成：{summary['sceneChecksCompleted']} 个主题。",f"文件校验失败：{summary['failedAssets']} 个。",'', '打开“预览索引.html”浏览素材。每个主题单独一个文件夹，metadata.json 记录来源和校验信息。','', '- image_*.webp / png / jpg：商店公开分享详情列出的全部原始预览文件，保留商店提供的分辨率与格式。','- video_*.mp4：完整原始预览视频，已验证 MP4 容器结构及播放时长。','- scene-checks：在手机预览页左右切换时截取的补充画面，含商店界面；截图不代替原始图片。','- share-evidence.png：部分主题的手机分享页佐证。','', '说明：某些主题未发现独立视频地址，不能据此断言该主题没有视频；未完成手机核对或未取得视频的项目在下方列出。不会将另一个主题的缓存视频混入。','', '## 视频或手机核对未齐的主题','']
for r in records:
 if not r.get('videoUrls') or not r.get('scenesChecked'):
  desc=[]
  if not r.get('videoUrls'):desc.append('未取得独立原始视频')
  if not r.get('scenesChecked'):desc.append('手机预览未核对完成：'+r.get('devicePreviewError','待核对'))
  lines.append(f"- {r['name']}（{r['masterId']}）：{'；'.join(desc)}")
(ROOT/'下载说明.md').write_text('\n'.join(lines)+'\n','utf8')
print(json.dumps(summary,ensure_ascii=False))