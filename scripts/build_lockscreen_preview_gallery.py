"""Create a browsable HTML QA gallery and visual contact sheet for the preview corpus."""
from __future__ import annotations

import argparse
import collections
import html
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


def load_jsonl(path):
    with path.open(encoding="utf8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def generate(root, montage_count=12):
    root = Path(root).resolve()
    reports = root / "reports"
    if not (reports / "manifest.json").is_file():
        raise FileNotFoundError("Manifest is missing; finish generation first")
    themes = load_jsonl(reports / "themes.jsonl")
    pairs = load_jsonl(reports / "pairs.jsonl")
    index = {}
    for item in pairs:
        key = item["themeId"], item["previewState"], item["device"], item["variant"]
        index[key] = item
    cards = []
    visual_rows = []
    for theme in themes:
        tid = theme["themeId"]
        states = sorted(set(x[1] for x in index if x[0] == tid))
        if not states:
            continue
        state = states[0]
        chosen = []
        for device, variant in (("phone", "cover"), ("square", "cover"),
                                ("tablet", "pad"), ("tablet", "cover")):
            item = index.get((tid,state,device,variant))
            if item:
                chosen.append(item)
        figures = []
        for item in chosen:
            url = "../" + item["targetImage"]
            label = f'{item["device"]} / {item["variant"]}'
            figures.append(
                '<figure><a target="_blank" href="' + html.escape(url,quote=True) +
                '"><img loading="lazy" src="' + html.escape(url,quote=True) +
                '" alt="' + html.escape(label,quote=True) + '"></a><figcaption>' +
                html.escape(label) + '</figcaption></figure>'
            )
        cards.append(
            '<article data-split="' + html.escape(theme["split"],quote=True) + '">'
            '<h2>' + html.escape(tid) + ' · ' + html.escape(theme["themeName"]) + '</h2>'
            '<p>' + html.escape(theme["split"]) + ' · 商店预览合成 · 不代表真实锁屏运行结果</p>'
            '<div class="imgs">' + ''.join(figures) + '</div></article>'
        )
        if len(visual_rows) < montage_count:
            visual_rows.append((tid,chosen))
    css = """*{box-sizing:border-box}body{margin:0 auto;padding:22px;max-width:1560px;color:#f3f6ff;background:#0c1322;font:14px system-ui}
header{position:sticky;top:0;background:#0c1322f5;padding-bottom:12px;z-index:2}
h1{font-size:22px}p{color:#a2b4d1}input,select{padding:10px;border-radius:7px;border:1px solid #53627a;background:#182640;color:#fff;margin-right:8px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(450px,1fr));gap:16px}
article{padding:16px;border:1px solid #364862;border-radius:12px;background:#15223a;min-width:0}
article[hidden]{display:none}h2{font-size:15px;overflow-wrap:anywhere}
.imgs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px}
figure{margin:0;min-width:0}img{height:280px;width:100%;object-fit:contain;background:#05080e;border-radius:5px}
figcaption{font-size:12px;color:#b6d0ec;margin-top:6px;text-align:center}
"""
    js = """const query=document.getElementById('query'),split=document.getElementById('split');
function filter(){for(const e of document.querySelectorAll('article')){e.hidden=(query.value&&!e.textContent.toLowerCase().includes(query.value.toLowerCase()))||(split.value!='all'&&e.dataset.split!==split.value)}}
query.addEventListener('input',filter);split.addEventListener('change',filter);"""
    page = (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<title>Lockscreen preview QA gallery</title><style>'+css+'</style></head>'
        '<body><header><h1>100 套主题 · 多设备预览人工审查</h1>'
        '<p>这些是第三方商店预览的算法变体，非 HWT 真实状态，非设计师真值，授权和视觉质量待审。</p>'
        '<input id="query" placeholder="按 ID/名称筛选"><select id="split">'
        '<option value="all">全部</option><option value="train">train</option>'
        '<option value="validation">validation</option><option value="test">test</option>'
        '</select></header><main>'+''.join(cards)+'</main><script>'+js+'</script></body></html>'
    )
    (reports / "gallery.html").write_text(page,encoding="utf8")
    if visual_rows:
        image = Image.new("RGB",(1460,42+len(visual_rows)*306),"#0c1322")
        draw = ImageDraw.Draw(image)
        draw.text((12,12),"PHONE / COVER         SQUARE / COVER         TABLET / PAD         TABLET / COVER",fill="#e4ebfa")
        for i,(tid,items) in enumerate(visual_rows):
            top=42+306*i
            draw.text((12,top),str(tid),fill="#ffffff")
            for j,item in enumerate(items):
                with Image.open(root/item["targetImage"]) as frame:
                    thumb=ImageOps.contain(frame.convert("RGB"),(345,272),Image.Resampling.LANCZOS)
                    image.paste(thumb,(j*365+int((345-thumb.width)/2),top+26))
        image.save(reports/"review_montage.jpg",quality=78,optimize=True)
    return {"html":str(reports/"gallery.html"),"themes":len(cards),
            "montageSampleCount":len(visual_rows)}


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--dataset",required=True)
    ap.add_argument("--montage-count",type=int,default=12)
    args=ap.parse_args()
    print(json.dumps(generate(args.dataset,args.montage_count),ensure_ascii=False,indent=2))
