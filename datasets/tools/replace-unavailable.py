import json,re,shutil
from pathlib import Path
root=Path(r'G:\codex_workspace\theme-previews').resolve()
records=json.loads((root/'manifest.json').read_text('utf8'))
used={r['masterId'] for r in records};names={r['name'] for r in records}
archive=root/'unavailable-public';archive.mkdir(exist_ok=True)
for f in archive.glob('*/metadata.json'):
 try:used.add(str(json.loads(f.read_text('utf8'))['masterId']))
 except:pass
candidates=[]
for line in (root/'candidates.jsonl').read_text('utf8').splitlines():
 try:d=json.loads(line)
 except:continue
 if str(d['masterId']) not in used and d['name'] not in names and len(d.get('hdPicUrl',[]))>=4 and 'Fold' not in d['name']:
  candidates.append(d);used.add(str(d['masterId']));names.add(d['name'])
candidates.sort(key=lambda d:(int(d['masterId'])>=12500000,-int(d['masterId'])))
replaced=[]
for i,r in enumerate(records):
 if r.get('scenesChecked'):continue
 if not candidates:break
 d=candidates.pop(0);mid=str(d['masterId']);name=d['name']
 src=(root/r['folder']).resolve();dst=(archive/r['folder']).resolve()
 if not src.is_relative_to(root) or not dst.is_relative_to(root):raise RuntimeError('Archive paths escape output directory')
 if src.exists() and not dst.exists():src.rename(dst)
 folder=f"{i+1:03}_{re.sub(r'[<>:\"/\\|?*\x00-\x1f]','_',name)}_{mid}"
 records[i]={'masterId':mid,'name':name,'shareUrl':f'https://theme-h5-static.heytap.com/sp/theme/storeStatic/themeDetailShare/themedetail/index.html?masterId={mid}&region=CN&type=0','folder':folder,'imageUrls':d['hdPicUrl'],'videoUrls':[],'status':'pending','detail':d,'source':'Public theme sharing detail','replacesUnavailableTheme':r['masterId']}
 replaced.append({'old':r['masterId'],'new':mid,'reason':r.get('devicePreviewError','Preview unavailable')})
(root/'manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),'utf8')
with (root/'inspection/replacements.jsonl').open('a',encoding='utf8') as out:
 for r in replaced:out.write(json.dumps(r,ensure_ascii=False)+'\n')
print('Replaced unavailable themes:',len(replaced),'Spare eligible themes:',len(candidates))