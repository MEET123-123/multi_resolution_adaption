import subprocess,json,time
from pathlib import Path
root=Path(r'G:\codex_workspace\theme-previews');tools=root/'tools'
python=r'C:\Users\mj\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
for iteration in range(1,8):
 records=json.loads((root/'manifest.json').read_text('utf8'));checked=sum(bool(r.get('scenesChecked')) for r in records)
 print(f'CONFIRMED AVAILABLE THEMES: {checked}/100',flush=True)
 if checked==100:break
 before={r['masterId'] for r in records}
 subprocess.run([python,'-u',str(tools/'replace-unavailable.py')],check=True)
 after=json.loads((root/'manifest.json').read_text('utf8'))
 if {r['masterId'] for r in after}==before:
  # Discover another bounded batch of public theme details only if the spare pool is exhausted.
  template=(tools/'discover-more.py').read_text('utf8')
  start=13461875-(iteration-1)*12000
  template=template.replace('range(13473877,13461877,-2)',f'range({start},{start-12000},-2)')
  script=tools/f'discover-extra-{iteration}.py';script.write_text(template,'utf8')
  subprocess.run([python,'-u',str(script)],check=True)
  subprocess.run([python,'-u',str(tools/'replace-unavailable.py')],check=True)
 subprocess.run([python,'-u',str(tools/'collect-direct.py')],check=True)
else:raise RuntimeError('Replacement passes exhausted')
records=json.loads((root/'manifest.json').read_text('utf8'))
assert len(records)==100 and all(r.get('scenesChecked') for r in records)
print('100 AVAILABLE THEMES VERIFIED. Starting video cache recovery.',flush=True)
subprocess.run([python,'-u',str(tools/'recover-videos.py')],check=True)
subprocess.run([python,'-u',str(tools/'final-audit.py')],check=True)
subprocess.run([python,'-u',str(tools/'build-index.py')],check=True)
p=root/'预览索引.html';text=p.read_text('utf8').replace('视频和手机左右切换检查仍在补齐；可稍后刷新本页。','全部文件已校验；独立视频取得情况及手机左右切换记录见“下载说明.md”。');p.write_text(text,'utf8')
with (root/'下载说明.md').open('a',encoding='utf8') as out:out.write('\n未适配或区域不可用主题的已下载图片保存在 unavailable-public 子目录，不计入主清单的 100 个主题。\n')
print('DOWNLOAD AND AUDIT COMPLETED',flush=True)