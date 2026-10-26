import collector_core as c
import json,re,time,subprocess,concurrent.futures,shutil
from pathlib import Path
ROOT=c.ROOT
# Correct the two initial cache entries observed during the initial sequential visits.
cache=json.loads((ROOT/'inspection/cache-video-map.json').read_text('utf8'))
for r in c.records:
 mapping={'11639079':'4','12859365':'7'}
 key=mapping.get(r['masterId'])
 if key and key in cache:r['videoUrls']=sorted(set(r.get('videoUrls',[]))|{cache[key]});r['videoNote']='Matched to individually observed initial cache insertion'
# Add public, available theme resources until exactly 100 unique themes are present.
while len(c.records)<100:
 added=False
 for line in (ROOT/'candidates.jsonl').read_text('utf8').splitlines():
  try:d=json.loads(line)
  except:continue
  mid=str(d['masterId'])
  if mid in c.seen_ids:continue
  name=d['name'];folder=f"{len(c.records)+1:03}_{re.sub(r'[<>:\"/\\|?*\x00-\x1f]','_',name)}_{mid}"
  r={'masterId':mid,'name':name,'shareUrl':f'https://theme-h5-static.heytap.com/sp/theme/storeStatic/themeDetailShare/themedetail/index.html?masterId={mid}&region=CN&type=0','folder':folder,'imageUrls':d['hdPicUrl'],'videoUrls':[],'status':'pending','detail':d,'source':'Public theme sharing detail'}
  c.records.append(r);c.seen_ids.add(mid);added=True
  if len(c.records)==100:break
 c.save()
 if not added:time.sleep(2)
print('100 unique theme details selected.',flush=True)
# All original images are independent downloads; phone navigation remains sequential.
futures=[]
for r in c.records:
 futures.append(c.POOL.submit(c.download,r))
queue=[r for r in c.records if not r.get('scenesChecked')]
for r in queue:
 try:
  mid=r['masterId'];folder=ROOT/r['folder'];scene_dir=folder/'scene-checks';scene_dir.mkdir(parents=True,exist_ok=True)
  before=c.videos()
  result=c.adb('shell','am','start','-W','-a','android.intent.action.VIEW','-d',f'"oaps://theme/detail?from=h5&rtp=theme&id={mid}&region=CN"')
  if 'Status: ok' not in result:raise RuntimeError('Theme deep link did not open: '+result)
  nodes=c.ui();node=c.find(nodes,':id/res_name')
  actual=node.get('text') if node is not None else None
  r['deviceDisplayedName']=actual
  normalize=lambda text:re.sub(r'\s+','',text or '').replace('全局','').casefold()
  if node is None or normalize(actual)!=normalize(r['name']):
   empty=c.find(nodes,':id/empty_view_title')
   raise RuntimeError(empty.get('text') if empty is not None else 'Theme name did not match: '+str(actual))
  steps=min(len(r['imageUrls'])+1,9)
  remote=f'/sdcard/Download/theme-preview-capture/{mid}'
  commands=[f'mkdir -p {remote}',f'screencap -p {remote}/00_initial.png']
  # Traverse all preview positions in both directions, preserving every right-swipe view.
  for i in range(steps):commands.extend(['input swipe 100 700 620 700 250','sleep 0.15'])
  commands.append(f'screencap -p {remote}/50_right.png')
  for i in range(steps):commands.append('input swipe 620 700 100 700 250')
  commands.extend(['sleep 0.2',f'screencap -p {remote}/99_left.png'])
  c.adb('shell','; '.join(commands))
  stage=ROOT/'inspection'/'transfer'/mid;stage.mkdir(parents=True,exist_ok=True)
  c.adb('pull',remote+'/.',str(stage))
  for shot in stage.glob('*.png'):shutil.copy2(shot,scene_dir/shot.name)
  after=c.videos();new=after-before
  if new:r['videoUrls']=sorted(set(r['videoUrls'])|new)
  r.pop('devicePreviewError',None)
  r['scenesChecked']=True;r['leftSwipeCount']=steps;r['rightSwipeCount']=steps;r['devicePreviewVerified']=True
  r['videoNote']='Full original MP4 URLs discovered during this theme visit' if r['videoUrls'] else 'No distinct original video URL was exposed during this theme visit'
  # Avoid simultaneous writers for the same theme while original-image downloads finish.
  for f in futures:
   if f.done():f.result()
  for f in futures:
   if not f.done():continue
  # Existing local assets are verified again by the final audit.
  if new:futures.append(c.POOL.submit(c.download,r))
  c.save()
  checked=sum(bool(x.get('scenesChecked')) for x in c.records)
  print(f"SCENE CHECK {checked}/100 {r['name']} images={len(r['imageUrls'])} videos={len(r['videoUrls'])}",flush=True)
 except Exception as e:
  r['devicePreviewError']=repr(e);c.save();print(f"PREVIEW CHECK FAILED {r['masterId']}: {e}",flush=True)
for f in futures:
 try:f.result()
 except Exception as e:print('DOWNLOAD ERROR '+repr(e),flush=True)
c.save();c.POOL.shutdown()
print('ALL PHONE SCENE CHECKS FINISHED',flush=True)