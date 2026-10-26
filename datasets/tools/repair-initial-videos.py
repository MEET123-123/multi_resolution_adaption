import json,subprocess,re,urllib.request
from pathlib import Path
root=Path(r'G:\codex_workspace\theme-previews')
raw=subprocess.check_output([r'G:\codex_workspace\android-adb\platform-tools\adb.exe','-s','8ea99c1e','exec-out','cat','/sdcard/Android/data/com.heytap.themestore/files/download_cache/cached_content_index.exi'])
urls={int.from_bytes(raw[m.start()-6:m.start()-2],'big'):m.group().decode() for m in re.finditer(rb'https?://[^\x00-\x20]+?\.mp4',raw)}
records=json.loads((root/'manifest.json').read_text('utf8'))
for index,r in enumerate(records,1):
 if index not in urls:continue
 url=urls[index];r['videoUrls']=[url];r['videoNote']='Matched using observed sequential cache insertion during individual theme visits'
 folder=root/r['folder'];data=urllib.request.urlopen(url,timeout=35).read()
 assert b'ftyp' in data[:64]
 (folder/'video_01.mp4').write_bytes(data)
 r.setdefault('assets',[]).append({'kind':'video','url':url,'file':'video_01.mp4','bytes':len(data),'status':'downloaded'})
 (folder/'metadata.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),'utf8')
 print('Video recovered for '+r['masterId'])
(root/'manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),'utf8')