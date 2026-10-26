from pathlib import Path
import json,html,urllib.parse
root=Path(r'G:\codex_workspace\theme-previews')
records=json.loads((root/'manifest.json').read_text('utf8'))
parts=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>主题预览素材 · 100 个主题</title><style>body{font:16px system-ui;margin:32px;background:#f6f7f9;color:#202124}article{background:white;padding:24px;margin:24px 0;border-radius:16px}h1{margin-bottom:8px}.media{display:flex;gap:12px;overflow:auto;padding:12px 0}img,video{height:360px;max-width:90vw;object-fit:contain;border-radius:8px;background:#eee}a{color:#215bc6}.note{color:#666;font-size:14px}</style><h1>100 个主题的预览素材</h1><p>图片为商店公开分享页提供的原始预览文件。视频和手机左右切换检查仍在补齐；可稍后刷新本页。</p>']
for i,r in enumerate(records,1):
 folder=r['folder'];prefix=urllib.parse.quote(folder)+'/'
 parts.append(f'<article><h2>{i:03} · {html.escape(r["name"])}</h2><p class="note">主题编号：{r["masterId"]} · <a href="{html.escape(r["shareUrl"])}">商店分享页</a> · <a href="{prefix}metadata.json">素材清单</a></p><div class="media">')
 for p in sorted((root/folder).glob('image_*')):parts.append(f'<a href="{prefix+urllib.parse.quote(p.name)}"><img loading="lazy" src="{prefix+urllib.parse.quote(p.name)}" alt="{html.escape(p.name)}"></a>')
 for p in sorted((root/folder).glob('video_*.mp4')):parts.append(f'<video controls preload="none" src="{prefix+urllib.parse.quote(p.name)}"></video>')
 parts.append('</div></article>')
parts.append('</html>');(root/'预览索引.html').write_text(''.join(parts),'utf8')
print('Local media index created.')