"""Create a self-contained SVG/HTML preview for layout plans."""

from __future__ import annotations

import html
import json
import base64
import mimetypes
from pathlib import Path
from typing import Any


def build_preview(layout_plan: dict[str, Any], out: Path, source_image: Path | None = None) -> None:
    devices = [
        device for device in layout_plan.get("devices", []) if device.get("status") == "ready"
    ]
    if not devices:
        raise ValueError("布局计划没有可预览的设备 profile")
    payload = {
        "algorithm": layout_plan.get("algorithm"),
        "sourceProfile": layout_plan.get("sourceProfile"),
        "adaptationNote": layout_plan.get("adaptationNote"),
        "devices": devices,
    }
    if source_image and devices[0].get("elements"):
        # Every profile shares the same source metadata and scene graph. Keep
        # that once, and store only per-profile geometry so real-theme previews
        # stay small even when a theme has thousands of analyzed elements.
        payload["elements"] = [
            {k: v for k, v in element.items() if k != "rect"} for element in devices[0]["elements"]
        ]
        payload["scenes"] = devices[0].get("scenes", [])
        compact_devices = []
        for device in devices:
            row = {
                "profile": device["profile"],
                "status": device["status"],
                "warnings": device.get("warnings", []),
            }
            if device.get("elements"):
                row["rects"] = [element.get("rect", {}) for element in device["elements"]]
            compact_devices.append(row)
        payload["devices"] = compact_devices
    if source_image:
        mime = mimetypes.guess_type(source_image.name)[0] or "image/jpeg"
        encoded = base64.b64encode(source_image.read_bytes()).decode("ascii")
        payload["sourcePreviewData"] = f"data:{mime};base64,{encoded}"
    payload_text = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape("鸿蒙锁屏多设备布局预览")
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{color-scheme:dark;--bg:#101319;--panel:#191e27;--line:#303847;--muted:#9da9b9;--text:#f2f5fa;--accent:#8be0c2}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,"Microsoft YaHei",sans-serif}}
header{{padding:18px 22px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:20px}}
h1{{margin:0;font-size:18px}}.meta,.muted{{color:var(--muted)}}main{{display:grid;grid-template-columns:minmax(300px,1fr) 340px;gap:16px;padding:16px;min-height:calc(100vh - 65px)}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0}}.stage{{display:flex;align-items:center;justify-content:center;min-height:65vh;overflow:auto;background:radial-gradient(ellipse at center,#222b39,#121720 70%);border-radius:9px;padding:18px}}
svg{{display:block;max-width:min(100%,520px);max-height:72vh;width:auto;height:auto;box-shadow:0 16px 48px #0009;border-radius:18px;background:#17212e}}
.controls{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}}select,button{{background:#222a36;border:1px solid #3b4658;color:var(--text);border-radius:7px;padding:8px 10px;font:inherit}}label{{color:var(--muted)}}button{{cursor:pointer}}button:hover{{border-color:var(--accent)}}aside h2{{font-size:14px;margin:4px 0 9px}}.scene-list{{display:grid;gap:8px;max-height:44vh;overflow:auto}}.scene{{background:#202733;border:1px solid #343e4d;border-radius:8px;padding:9px;cursor:pointer}}.scene.active{{border-color:var(--accent)}}.scene small{{display:block;color:var(--muted);margin-top:3px}}.legend{{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px;color:var(--muted);font-size:12px}}.dot{{display:inline-block;width:9px;height:9px;border-radius:50%;background:#7ce0ba;margin-right:5px}}.dot.hit{{background:#ffc36c}}.status{{margin-top:10px;color:var(--accent);min-height:22px}}.details{{margin-top:16px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);overflow-wrap:anywhere}}
@media(max-width:850px){{main{{grid-template-columns:1fr}}.stage{{min-height:50vh}}svg{{max-height:65vh}}}}
</style></head><body><header><h1>鸿蒙锁屏多设备布局预览</h1><div class="meta">算法：{html.escape(str(layout_plan.get("algorithm", "unknown")))} · {html.escape(str(layout_plan.get("adaptationNote") or "布局预览"))}</div></header>
<main><section class="panel"><div class="controls"><label for="device">目标设备/状态</label><select id="device"></select><label for="fit">图片适配</label><select id="fit"><option value="xMidYMid meet">完整显示</option><option value="xMidYMid slice">铺满画布</option><option value="none">拉伸</option></select><label><input id="nodes" type="checkbox"> 显示节点参考</label><label><input id="hits" type="checkbox" checked> 显示交互热区</label></div><div class="stage"><svg id="screen" role="img" aria-label="锁屏布局"></svg></div><div class="legend"><span><i class="dot"></i>主题元素/参考节点</span><span><i class="dot hit"></i>可交互热区</span></div><div id="status" class="status"></div></section>
<aside class="panel"><h2>场景分层</h2><div id="scenes" class="scene-list"></div><div id="details" class="details">选择目标设备或场景查看布局信息。</div></aside></main>
<script>
const DATA={payload_text};
const deviceSelect=document.getElementById('device'), svg=document.getElementById('screen'), scenesEl=document.getElementById('scenes'), details=document.getElementById('details'), statusEl=document.getElementById('status'), hitToggle=document.getElementById('hits'), nodeToggle=document.getElementById('nodes'), fitSelect=document.getElementById('fit');
const NS='http://www.w3.org/2000/svg';let current=null,activeScene=null;
const make=(tag,attrs={{}},text='')=>{{const n=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(text)n.textContent=text;return n}};
const val=(obj,key,fallback=0)=>{{const v=obj?.[key];if(typeof v==='number'&&Number.isFinite(v))return v;if(typeof v==='string'&&/^\\s*-?\\d+(?:\\.\\d+)?\\s*$/.test(v))return Number(v);return fallback}};
function draw(){{
 current=DATA.devices[Number(deviceSelect.value)||0];if(!current)return;const p=current.profile,r=p.resolutionPx;svg.replaceChildren();svg.setAttribute('viewBox',`0 0 ${{r.width}} ${{r.height}}`);svg.setAttribute('width',r.width);svg.setAttribute('height',r.height);
 svg.append(make('rect',{{x:0,y:0,width:r.width,height:r.height,fill:'#172434'}}));
 const bg=make('rect',{{x:0,y:0,width:r.width,height:r.height,fill:'url(#bg)'}});const defs=make('defs');const grad=make('linearGradient',{{id:'bg',x1:'0',y1:'0',x2:'1',y2:'1'}});grad.append(make('stop',{{offset:'0%','stop-color':'#283b53'}}),make('stop',{{offset:'100%','stop-color':'#111923'}}));defs.append(grad);svg.append(defs,bg);
 if(DATA.sourcePreviewData)svg.append(make('image',{{x:0,y:0,width:r.width,height:r.height,href:DATA.sourcePreviewData,preserveAspectRatio:fitSelect.value}}));
  const elements=nodeToggle.checked?(current.elements||(current.rects?DATA.elements.map((e,i)=>({{...e,rect:current.rects[i]||{{}}}})):DATA.elements)||[]):[];for(const e of elements){{const a=e.attributes||{{}},q=e.rect&&Object.keys(e.rect).length?e.rect:e.sourceRect||{{}},x=val(q,'x'),y=val(q,'y'),w=val(q,'w',val(q,'width',0)),h=val(q,'h',val(q,'height',0));if(w<=0||h<=0)continue;const tag=e.tag;const color=tag==='Button'?'#ffc36c':tag==='Text'||tag==='Time'||tag==='Date'?'#f4f3ed':'#8be0c2';
   const group=make('g',{{'data-key':e.key,tabindex:tag==='Button'?'0':'-1'}});let shape,extraLabel=null;
   if(tag==='Text'||tag==='Time'||tag==='Date'){{shape=make('text',{{x:x+w/2,y:y+h/2,'text-anchor':'middle','dominant-baseline':'middle',fill:color,'font-size':Math.max(20,Math.min(h*.72,Math.min(r.width,r.height)*.09)),'font-family':'sans-serif'}},tag==='Time'?'09:41':tag==='Date'?'10月13日 星期二':(a.text||a.format||a.name||'文字'))}}
   else{{shape=make('rect',{{x,y,width:w,height:h,rx:tag==='Button'?Math.min(20,h/5):8,fill:tag==='Button'?'#ffc36c22':tag==='Image'?'#8be0c222':'#ffffff12',stroke:color,'stroke-width':Math.max(2,Math.min(r.width,r.height)/600)}});extraLabel=make('text',{{x:x+w/2,y:y+h/2,'text-anchor':'middle','dominant-baseline':'middle',fill:'#f1f5f9','font-size':Math.max(14,Math.min(30,h*.18))}},tag==='Image'?'图片 · '+(a.src||a.name||'mock asset'):tag)}}
   if(tag==='Button'){{group.dataset.scenes=(e.sceneRefs||[]).join(',');group.addEventListener('click',()=>{{statusEl.textContent=`模拟点击：${{a.name||e.key}}；关联场景：${{(e.sceneRefs||DATA.scenes||[]).filter(s=>(e.sceneRefs||[]).includes(s.id)).map(s=>s.id).join(', ')||'未识别'}}`;const scene=(current.scenes||DATA.scenes||[]).find(s=>(e.sceneRefs||[]).includes(s.id));if(scene)showScene(scene.id)}})}}
   group.append(shape);if(extraLabel)group.append(extraLabel);svg.append(group);
   if(tag==='Button'&&hitToggle.checked){{svg.append(make('rect',{{x,y,width:w,height:h,rx:6,fill:'none',stroke:'#ffc36c',"stroke-width":Math.max(3,Math.min(r.width,r.height)/400),"stroke-dasharray":"12 8","pointer-events":"none"}}))}}
 }}
 renderScenes();details.textContent=`${{p.device}} · ${{p.displayState}} · ${{p.orientation}} · ${{r.width}} × ${{r.height}} px。${{DATA.sourcePreviewData?'源主题预览图适配映射；不等同于鸿蒙引擎实机布局。':'坐标由演示算法按源画布分别缩放 X/Y，仅用于打通流程。'}}${{p.note?' '+p.note:''}}`;
}}
function showScene(id){{activeScene=id;for(const el of scenesEl.children)el.classList.toggle('active',el.dataset.id===id);const s=(current.scenes||DATA.scenes||[]).find(v=>v.id===id);if(!s)return;details.textContent=`${{(s.layers||[s.layer]).join(' → ')}} / ${{s.action}} · 控件：${{s.owner}} · 命令：${{(s.commands||[]).map(c=>c.tag).join(', ')||'无'}}`;}}
function renderScenes(){{scenesEl.replaceChildren();for(const s of (current.scenes||DATA.scenes||[])){{const card=document.createElement('div');card.className='scene'+(s.id===activeScene?' active':'');card.dataset.id=s.id;const title=document.createElement('b');title.textContent=(s.layers||[s.layer]).join(' → ')+' · '+s.action;const meta=document.createElement('small');meta.textContent=s.owner+' → '+((s.commands||[]).map(c=>c.tag).join(', ')||'状态/静态');card.append(title,meta);card.onclick=()=>showScene(s.id);scenesEl.append(card)}}}}
for(const [i,d] of DATA.devices.entries()){{const o=document.createElement('option');o.value=i;o.textContent=`${{d.profile.device}} · ${{d.profile.displayState}} · ${{d.profile.orientation}}`;deviceSelect.append(o)}}
deviceSelect.onchange=()=>{{activeScene=null;draw()}};hitToggle.onchange=draw;nodeToggle.onchange=draw;fitSelect.onchange=draw;draw();
</script></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(document, encoding="utf-8")
