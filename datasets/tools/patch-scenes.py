from pathlib import Path
p=Path(r'G:\codex_workspace\theme-previews\tools\collect.py')
s=p.read_text('utf8')
a=s.index('def collect(');b=s.index('last_video=[set()]',a)
new=r'''def collect(nodes=None):
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
'''
s=s[:a]+new+s[b:]
p.write_text(s,'utf8')