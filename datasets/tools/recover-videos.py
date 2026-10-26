import collector_core as c
import re,time,json

def spans():
 try:
  paths=c.adb('shell',"find /sdcard/Android/data/com.heytap.themestore/files/download_cache -type f -name '*.v3.exo'")
  result={}
  for path in paths.splitlines():
   match=re.search(r'/([0-9]+)\.[0-9]+\.([0-9]+)\.v3\.exo$',path)
   if match:
    key=int(match[1]);result[key]=max(result.get(key,0),int(match[2]))
  return result
 except:return {}
def mapping():
 raw=c.adb('exec-out','cat',c.REMOTE,binary=True)
 return {int.from_bytes(raw[m.start()-6:m.start()-2],'big'):m.group().decode() for m in re.finditer(rb'https?://[^\x00-\x20]+?\.mp4',raw)}
normalize=lambda text:re.sub(r'\s+','',text or '').replace('全局','').casefold()
queue=[r for r in c.records if r.get('scenesChecked') and not r.get('videoUrls')]
for r in queue:
 try:
  before=spans();before_urls=c.videos();mid=r['masterId']
  c.adb('shell','am','start','-W','-a','android.intent.action.VIEW','-d',f'"oaps://theme/detail?from=h5&rtp=theme&id={mid}&region=CN"')
  nodes=c.ui();node=c.find(nodes,':id/res_name')
  if node is None or normalize(node.get('text'))!=normalize(r['name']):raise RuntimeError('Preview title not confirmed')
  time.sleep(1);after=spans();urls=mapping()
  touched={key for key,stamp in after.items() if stamp>before.get(key,0)}
  current={urls[key] for key in touched if key in urls}
  current|=set(urls.values())-before_urls
  r['videoRecoveryAttempted']=True
  if current:
   r['videoUrls']=sorted(current);r['videoNote']='Original URLs matched by cache span access during confirmed theme preview'
   c.download(r);print('VIDEO RECOVERED '+mid+': '+str(len(current)),flush=True)
  else:print('NO ADDITIONAL VIDEO '+mid,flush=True)
  c.save()
 except Exception as e:r['videoRecoveryError']=repr(e);c.save();print('RECOVERY FAILED '+r['masterId']+': '+str(e),flush=True)
c.POOL.shutdown();c.save();print('VIDEO RECOVERY FINISHED',flush=True)