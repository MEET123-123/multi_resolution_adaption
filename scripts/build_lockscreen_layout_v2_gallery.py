"""Create a side-by-side, offline manual review gallery for layout-authored v2."""
from __future__ import annotations
import argparse
import collections
import html
import json
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

def read_jsonl(path):
    return [json.loads(x) for x in path.read_text(encoding="utf8").splitlines() if x.strip()]

def generate(root,limit=20):
    root=root.resolve()
    report=root/"reports"
    rows=read_jsonl(report/"pairs.jsonl")
    themes=read_jsonl(report/"themes.jsonl")
    rejects=read_jsonl(report/"rejected.jsonl")
    by_theme=collections.defaultdict(dict)
    for x in rows:by_theme[x["themeId"]][(x["style"],x["device"])]=x
    cards=[];sheet=[]
    for t in themes:
        tid=t["themeId"]
        match=by_theme[tid]
        default_style="editorial" if ("editorial","tablet") in match else sorted(match)[0][0]
        representative=[match.get((default_style,v)) for v in ("phone","square","tablet","fold-wide")]
        representative=[x for x in representative if x]
        status=collections.Counter(x["qualityGate"]["autoDisposition"] for x in match.values())
        review="reject" if status["reject"] else "manual_review" if status["manual_review"] else "keep_candidate"
        pictures=[]
        for row in representative:
            r="../"+row["targetSynthetic"]
            label=row["device"]+" / "+row["style"]
            pictures.append('<figure><a target="_blank" href="'+html.escape(r,quote=True)+'"><img loading="lazy" src="'+html.escape(r,quote=True)+'"></a><figcaption>'+html.escape(label)+'</figcaption></figure>')
        cards.append('<article data-split="'+html.escape(t["split"])+'" data-status="'+review+'"><h2>'+
             html.escape(tid)+" · "+html.escape(t.get("themeName") or "")+
             '</h2><p>Split: '+html.escape(t["split"])+" · "+review+
             ' · 仅程序合成/未批准训练</p><div class="images">'+''.join(pictures)+'</div></article>')
        if len(sheet)<limit and representative:sheet.append((tid,representative))
    rej='<ul>'+''.join('<li>'+html.escape(x["themeId"])+" — "+html.escape(x.get("reason","unknown"))+"</li>" for x in rejects)+'</ul>'
    css="""*{box-sizing:border-box}body{max-width:1550px;margin:0 auto;padding:18px 22px;background:#0f1624;color:#edf3fc;font:14px system-ui}
header{position:sticky;top:0;background:#0f1624ec;padding:8px 0 15px;z-index:5}
h1{font-size:21px}h2{font-size:14px;overflow-wrap:anywhere}p,figcaption{color:#afc1da}input,select{padding:8px;background:#17253d;border:1px solid #54627c;color:white;border-radius:5px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:14px}
article{border-radius:12px;border:1px solid #3a4760;background:#17233a;padding:12px;min-width:0}
.images{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}figure{margin:0}figure img{width:100%;height:260px;object-fit:contain;background:#080c16}figcaption{text-align:center;font-size:11px;margin-top:6px}
details{margin:10px 0;padding:10px;border:1px solid #536079;border-radius:8px}article[hidden]{display:none}"""
    js="""const query=document.querySelector('#query'),split=document.querySelector('#split'),status=document.querySelector('#status');
function filter(){for(const e of document.querySelectorAll('article')){e.hidden=(query.value&&!e.textContent.toLowerCase().includes(query.value.toLowerCase()))||(split.value!='all'&&e.dataset.split!==split.value)||(status.value!='all'&&e.dataset.status!==status.value)}}
for(const x of [query,split,status])x.addEventListener('input',filter);"""
    page='''<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Layout authored v2 · Manual review</title><style>'''+css+'''</style></head>
<body><header><h1>锁屏布局数据 v2 · 主题审查图库</h1>
<p>独立生成源图和目标图，图层坐标可验证；主体抠图仍是候选，不能替代人工验收、授权或设备实测。</p>
<input id="query" placeholder="主题名称 / ID">
<select id="split"><option value="all">全部划分</option><option value="train">train</option><option value="validation">validation</option><option value="test">test</option></select>
<select id="status"><option value="all">全部质量状态</option><option value="keep_candidate">可保留候选</option><option value="manual_review">待复核</option><option value="reject">拒绝</option></select>
<details><summary>未进入生成的主题：'''+str(len(rejects))+''' 套（点击展开）</summary>'''+rej+'''</details></header><main>'''+''.join(cards)+'''</main><script>'''+js+'''</script></body></html>'''
    (report/"gallery.html").write_text(page,encoding="utf8")
    if sheet:
        fw,fh=1250,320
        canvas=Image.new("RGB",(fw,48+len(sheet)*fh),(14,19,34))
        dr=ImageDraw.Draw(canvas)
        dr.text((9,14),"phone    |    square    |    tablet    |    fold wide    (layout-authored synthetic)",fill="white")
        for i,(tid,items) in enumerate(sheet):
            y=48+i*fh
            dr.text((9,y+4),tid,fill="white")
            for j,row in enumerate(items):
                with Image.open(root/row["targetSynthetic"]) as im:
                    thumb=ImageOps.contain(im.convert("RGB"),(297,282),Image.Resampling.LANCZOS)
                    canvas.paste(thumb,(j*310+(297-thumb.width)//2,y+31))
        canvas.save(report/"review_montage.jpg",quality=84,optimize=True)
    print(json.dumps({"themesInGallery":len(themes),"rejectedThemes":len(rejects),
       "html":str(report/"gallery.html"),
       "montage":str(report/"review_montage.jpg")},indent=2,ensure_ascii=False))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--dataset",required=True)
    p.add_argument("--montage-count",type=int,default=16)
    a=p.parse_args()
    generate(Path(a.dataset),a.montage_count)
