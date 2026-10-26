import urllib.request,concurrent.futures,json,time,threading
from pathlib import Path
root=Path(r'G:\codex_workspace\theme-previews')
stop=threading.Event();found=[];checked=0

def fetch(mid):
 if stop.is_set():return None
 try:
  req=urllib.request.Request(f'https://theme-html-cn.heytapmobi.com/theme/resource/info/jsonp?masterId={mid}&region=CN&callback=jsonpDetail',headers={'User-Agent':'Mozilla/5.0'})
  with urllib.request.urlopen(req,timeout=12) as r:raw=r.read().decode()
  d=json.loads(raw[raw.index('(')+1:raw.rfind(')')])
  if d and d.get('appType')==0 and len(d.get('hdPicUrl',[]))>=4 and 'Fold' not in d.get('name',''):return d
 except:pass
 finally:time.sleep(.08)
 return None
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 pending={};it=iter(range(13473877,13461877,-2))
 for _ in range(3):
  mid=next(it);pending[pool.submit(fetch,mid)]=mid
 while pending and len(found)<70:
  ready,_=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
  for f in ready:
   pending.pop(f);checked+=1;d=f.result()
   if d:
    found.append(d)
    with (root/'candidates.jsonl').open('a',encoding='utf8') as out:out.write(json.dumps(d,ensure_ascii=False)+'\n')
    if len(found)%10==0:print(f'Public theme details: {len(found)}; pages checked: {checked}',flush=True)
   if len(found)>=70:stop.set();break
   try:
    mid=next(it);pending[pool.submit(fetch,mid)]=mid
   except StopIteration:pass
 stop.set()
 print(f'Discovery finished: {len(found)} themes',flush=True)