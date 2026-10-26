"""Browser screenshot from the existing LiveSession XML interpretation (not HarmonyOS-native)."""
from pathlib import Path
from urllib.parse import urlsplit,unquote
import base64,html,mimetypes

def asset(url,root):
 """Resolve only local assets exported by LiveSession; reject unsafe URL paths.

 LiveSession.plan() emits assets/... while HTTP routes may emit /assets/....
 Both are valid relative to the session output directory.
 """
 parsed=urlsplit(str(url))
 if parsed.scheme or parsed.netloc:return None
 p=unquote(parsed.path)
 if '\\' in p or any(ord(ch)<32 for ch in p):return None
 if p.startswith('/assets/'):
  rel=p[1:]
 elif p.startswith('assets/'):
  rel=p
 else:return None
 from pathlib import PurePosixPath
 parts=PurePosixPath(rel).parts
 if not parts or parts[0]!='assets' or any(part in ('','.','..') for part in parts):return None
 root=Path(root).resolve()
 f=root.joinpath(*parts).resolve()
 if not f.is_relative_to(root) or not f.is_file() or f.stat().st_size>20_000_000:return None
 if f.suffix.lower() not in ('.jpg','.jpeg','.png','.webp','.gif','.svg'):return None
 return 'data:'+(mimetypes.guess_type(f.name)[0] or 'image/png')+';base64,'+base64.b64encode(f.read_bytes()).decode()

def render(page,plan,root,out):
 w=float(plan['canvas']['width']);h=float(plan['canvas']['height'])
 if not (64<=w<=8000 and 64<=h<=8000):raise ValueError('canvas invalid')
 scale=min(1,900/w);parts=[];errors=[]
 for j,e in enumerate(plan.get('elements',[])):
  r=e['rect'];x,y,bw,bh=[float(r[k]) for k in ('x','y','w','h')]
  if bw<=0 or bh<=0:continue
  st=f'position:absolute;left:{x}px;top:{y}px;width:{bw}px;height:{bh}px;opacity:{float(e.get("alpha",1))};z-index:{j};pointer-events:none;'
  if e.get('url'):
   u=asset(e['url'],root)
   if u:parts.append(f'<img src="{u}" style="{st}"/>')
   else:errors.append({'element':j,'reason':'asset missing'})
  elif e.get('kind')=='video':errors.append({'element':j,'reason':'video unsupported'})
  elif e.get('text'):
   col=str(e.get('color') or '#ffffff')
   if not col.startswith(('#','rgb')):col='#ffffff'
   size=max(4,float(e.get('size') or 16))
   parts.append(f'<div style="{st}white-space:pre;overflow:hidden;font-family:Arial;font-size:{size}px;color:{html.escape(col)}">{html.escape(str(e["text"]))}</div>')
 body='<!doctype html><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:black;overflow:hidden}#screen{position:relative;overflow:hidden;transform-origin:0 0;transform:scale('+str(scale)+');width:'+str(w)+'px;height:'+str(h)+'px}</style><div id="screen">'+''.join(parts)+'</div>'
 # Keep the browser-produced image but report unsupported assets instead of declaring it correct.
 page.set_viewport_size({'width':max(1,round(w*scale)),'height':max(1,round(h*scale))})
 page.set_content(body,wait_until='load')
 page.screenshot(path=str(out),animations='disabled')
 return {'issues':errors,'elementsRendered':len(parts),'canvas':[w,h]}
