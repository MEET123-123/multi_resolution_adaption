"""Regenerate the 10k synthetic lockscreen layout-pair dataset locally.
All artwork/targets are procedural, NOT designer-verified or official device layouts.
"""
from __future__ import annotations
import argparse, collections, hashlib, io, json, math, random, zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont

SCHEMA="multires-layout/1"
SYSTEM=('你是锁屏主题布局规划器。图片是源屏幕，g 编号对应输入组件组。'
        '只返回 JSON：{"schema":"multires-layout/1","groups":{"g0":[x,y,w,h]}}。'
        '坐标相对目标 safeRegion 归一化到 0..1000，格式是 xywh。'
        '必须返回所有组件组；保持组内资源比例、居中、贴边、相对顺序与公共组件。'
        '源屏外组可以保留屏外。不要修改交互、可见性、动画或脚本。')
TARGETS=[("phone-portrait",1080,2400),("phone-tall",1080,2520),
         ("fold-square",2200,2200),("fold-wide",2480,1860),
         ("tablet-landscape",2560,1600),("tablet-portrait",1600,2560)]
ROLES=["background","clock","date","weather","headline","flashlight","camera"]
SOURCE={"background":[0,0,1000,1000],"clock":[225,166,550,118],
        "date":[285,295,430,52],"weather":[315,395,370,65],
        "headline":[190,711,620,96],"flashlight":[36,885,100,64],"camera":[864,885,100,64]}
COLORS=[((16,25,56),(85,87,145)),((8,50,70),(17,139,144)),
        ((43,28,65),(145,81,120)),((30,47,38),(78,119,95)),
        ((29,43,68),(95,150,180)),((63,30,35),(174,95,89)),
        ((20,34,41),(97,129,116)),((54,38,74),(117,83,151))]
def sha(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def rng(*parts):
    return random.Random(int(sha(parts)[:16],16))
def clamp(v,a,b):return min(b,max(a,v))
def box(values):return [round(float(v),3) for v in values]
def stem(i):return f"original-{i:03d}"
def source_layout(theme):
    d={k:v.copy() for k,v in SOURCE.items()}
    r=rng("source",theme)
    for key in ROLES[1:-2]:
        d[key][0]=round(clamp(d[key][0]+r.uniform(-45,45),0,1000-d[key][2]),3)
        d[key][1]=round(clamp(d[key][1]+r.uniform(-55,55),0,1000-d[key][3]),3)
    return d
def target_layout(theme,target):
    name,w,h=target
    ratio=w/h
    r=rng("label",theme,name)
    if ratio>=1.35:
        result={"background":[0,0,1000,1000],"clock":[90,253,440,165],
                "date":[105,432,405,58],"weather":[115,518,370,64],
                "headline":[582,356,345,130],"flashlight":[37,858,87,90],"camera":[875,858,87,90]}
    elif ratio>=.96:
        result={"background":[0,0,1000,1000],"clock":[176,201,648,153],
                "date":[278,378,444,58],"weather":[311,471,378,78],
                "headline":[238,695,524,95],"flashlight":[37,865,92,73],"camera":[870,865,92,73]}
    else:
        result={"background":[0,0,1000,1000],"clock":[216,160,568,125],
                "date":[289,305,422,55],"weather":[315,414,370,70],
                "headline":[200,700,600,115],"flashlight":[38,881,98,70],"camera":[864,881,98,70]}
    if theme%4==1:
        result["headline"][1]-=75
        result["weather"][1]+=40
    elif theme%4==2:
        result["clock"][1]+=55
        result["date"][1]+=45
    elif theme%4==3:
        result["headline"][0]+=35
        result["headline"][2]-=70
    for k in ROLES[1:]:
        b=result[k]
        b[0]=clamp(b[0]+r.uniform(-13,13),0,1000-b[2])
        b[1]=clamp(b[1]+r.uniform(-14,14),0,1000-b[3])
    return {k:box(v) for k,v in result.items()}
def damage(ref,theme,target,variant):
    r=rng("perturb",theme,target[0],variant)
    mode=variant%6
    result={}
    for i,k in enumerate(ROLES):
        b=ref[k].copy()
        if k=="background":result[f"g{i}"]=b;continue
        if mode==0: dx,dy=r.uniform(-3,3),r.uniform(-3,3)
        elif mode==1:dx,dy=r.uniform(-110,110),r.uniform(-90,90)
        elif mode==2:dx,dy=r.uniform(-180,180),r.uniform(-75,75)
        elif mode==3:dx,dy=r.uniform(-35,35),r.uniform(-200,200)
        elif mode==4:dx,dy=r.uniform(-135,135),r.uniform(-155,155)
        else:dx,dy=r.uniform(-30,30),r.uniform(-30,30)
        if mode in (2,4):
            s=r.uniform(.82,1.18)
            b[2]=clamp(b[2]*s,30,1000); b[3]=clamp(b[3]*s,20,1000)
        b[0]=clamp(b[0]+dx,-80,1020-b[2])
        b[1]=clamp(b[1]+dy,-80,1020-b[3])
        result[f"g{i}"]=box(b)
    return result
def gradient(theme):
    w,h=540,1200
    pal=COLORS[theme%8]
    img=Image.new("RGB",(w,h));draw=ImageDraw.Draw(img)
    for y in range(h):
        t=(y/(h-1))**(.7+(theme%5)*.2)
        c=tuple(int(pal[0][j]*(1-t)+pal[1][j]*t) for j in range(3))
        draw.line((0,y,w,y),fill=c)
    r=rng("art",theme)
    for j in range(10+theme%11):
        x=r.randrange(-200,700);y=r.randrange(-220,1420);rad=r.randint(70,310)
        c=tuple(min(245,v+12+r.randrange(20)) for v in pal[(j+theme)%2])
        draw.ellipse((x-rad,y-rad,x+rad,y+rad),outline=c,width=r.randint(2,9))
    for _ in range(34):
        x=r.randrange(w);y=r.randrange(h);s=r.randrange(1,4)
        draw.ellipse((x-s,y-s,x+s,y+s),fill=(215,215,227))
    return img
def icon(kind):
    im=Image.new("RGBA",(72,72),(0,0,0,0));d=ImageDraw.Draw(im)
    if kind=="camera":
        d.rounded_rectangle((9,20,62,56),radius=8,outline="white",width=4)
        d.ellipse((28,28,47,47),outline="white",width=4)
        d.rectangle((20,15,36,21),fill="white")
    else:
        d.rounded_rectangle((25,8,47,48),radius=5,fill="white")
        d.polygon(((28,45),(20,64),(42,45)),fill="white")
    return im
def font(size):
    for path in ("C:/Windows/Fonts/arial.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:return ImageFont.truetype(path,size)
        except OSError:pass
    return ImageFont.load_default()
def make_theme(root,theme):
    n=stem(theme); bg=gradient(theme);src=source_layout(theme)
    art=bg.convert("RGBA")
    d=ImageDraw.Draw(art)
    for k,text,size in [("clock","10:21",74),("date","FRI 09 OCT",25),
                         ("weather","22 C SUNNY",24),("headline",["AURORA","SOFT SPACE","MORNING"][theme%3],35)]:
        b=src[k];d.text((round(b[0]*.54),round(b[1]*1.2)),text,font=font(size),fill="white")
    icons={k:icon(k) for k in ("flashlight","camera")}
    for k,img in icons.items():
        b=src[k];art.alpha_composite(img,(round(b[0]*.54),round(b[1]*1.2)))
    art.convert("RGB").save(root/"images"/(n+".jpg"),quality=81,optimize=True)
    xml=['<Lockscreen screenWidth="1080" screenHeight="2400">',
         '<Image name="background" x="0" y="0" w="1080" h="2400" src="background.png"/>']
    for k,text,size in (("clock","10:21",120),("date","FRI - 09 OCT",39),
                        ("weather","22 C SUNNY",48),("headline","AURORA",62)):
        b=src[k];p=[round(b[0]*1.08),round(b[1]*2.4),round(b[2]*1.08),round(b[3]*2.4)]
        xml.append(f'<Text name="{k}" x="{p[0]}" y="{p[1]}" w="{p[2]}" h="{p[3]}" size="{size}" text="{escape(text)}" color="#ffffff"/>')
    for k in icons:
        b=src[k];p=[round(b[0]*1.08),round(b[1]*2.4),round(b[2]*1.08),round(b[3]*2.4)]
        xml.append(f'<Image name="{k}" x="{p[0]}" y="{p[1]}" w="{p[2]}" h="{p[3]}" src="{k}.png"/>')
    xml.append("</Lockscreen>")
    lock="unlock/lockscreen/"
    with zipfile.ZipFile(root/"themes"/(n+".hwt"),"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr(lock+"manifest.xml","\n".join(xml))
        buf=io.BytesIO();bg.save(buf,format="PNG");z.writestr(lock+"background.png",buf.getvalue())
        for k,img in icons.items():
            buf=io.BytesIO();img.save(buf,format="PNG")
            z.writestr(lock+k+".png",buf.getvalue())
        z.writestr("description.xml","<theme><title>"+n+"</title></theme>")
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--out",default="artifacts/learning/synthetic-10k-v1")
    ap.add_argument("--count",type=int,default=10000)
    ap.add_argument("--themes",type=int,default=64)
    args=ap.parse_args()
    root=Path(args.out).resolve()
    if root.exists():raise SystemExit("目标已存在，避免覆盖："+str(root))
    if args.themes<16 or args.themes%8:raise SystemExit("themes 必须为 8 的倍数且 >=16")
    for folder in ("themes","images","dataset","sft"):
        (root/folder).mkdir(parents=True,exist_ok=True)
    for t in range(args.themes):
        make_theme(root,t)
        if (t+1)%16==0:print("主题包",t+1,flush=True)
    files={s:(root/"sft"/(s+".jsonl")).open("w",encoding="utf8") for s in ("train","validation","test")}
    counters=collections.Counter();unique=set();preview=[]
    with (root/"dataset"/"pairs.jsonl").open("w",encoding="utf8") as pairs:
        try:
            for idx in range(args.count):
                theme=idx%args.themes
                target=TARGETS[(idx//args.themes)%len(TARGETS)]
                variant=idx//(args.themes*len(TARGETS))
                source=source_layout(theme)
                truth=target_layout(theme,target)
                current=damage(truth,theme,target,variant)
                group_list=[{"id":f"g{i}","sourceBox":box(source[k]),"roles":[k],
                              "memberCount":1,"container":"rigid","anchor":"center",
                              "fixed":k=="background","sourceHints":[{"tag":"Image" if k in ("background","flashlight","camera") else "Text","attributes":{"name":k}}]}
                             for i,k in enumerate(ROLES)]
                request={"schema":SCHEMA,"theme":"synthetic:"+stem(theme),
                         "sourceViewport":{"width":1080,"height":2400},
                         "safeRegion":{"x":0,"y":0,"w":target[1],"h":target[2]},
                         "targetViewport":{"width":target[1],"height":target[2]},
                         "requestKind":"full-reflow",
                         "mediaPolicy":"reference-frame affine; preserve timeline",
                         "groups":group_list}
                layout={"schema":SCHEMA,"groups":current,
                        "element_coordinate_system":"source_canvas_normalized_1000",
                        "group_coordinate_system":"target_safe_region_normalized_1000",
                        "elementLevel":[{"id":f"e{i}","group":f"g{i}","semantic":k,"sourceBox":source[k]} for i,k in enumerate(ROLES)],
                        "semanticLevel":[{"id":f"g{i}","roles":[k]} for i,k in enumerate(ROLES)],
                        "compositionLevel":{"kind":"full-reflow","mediaPolicy":"reference-frame affine; preserve timeline"}}
                answer={"schema":SCHEMA,"groups":{f"g{i}":truth[k] for i,k in enumerate(ROLES)}}
                key=sha([request["theme"],target[0],current])
                if key in unique:raise RuntimeError("重复输入 "+key)
                unique.add(key)
                split=("test" if theme%8==7 else "validation" if theme%8==6 else "train")
                sample={"id":key[:24],"family":f"synthetic-style-{theme%8}","split":split,
                        "themeId":stem(theme),"target":{"id":target[0],"width":target[1],"height":target[2]},
                        "image":"../images/"+stem(theme)+".jpg","package":"../themes/"+stem(theme)+".hwt",
                        "request":request,"layoutJson":layout,"answer":answer,
                        "provenance":{"kind":"procedural-synthetic","supervision":"deterministic layout teacher",
                                      "realDesignerGroundTruth":False,"sourceAssetLicense":"original-generated",
                                      "targetLayoutIndependentlyHumanVerified":False}}
                pairs.write(json.dumps(sample,ensure_ascii=False,separators=(",",":"))+"\n")
                prompt="<image>\n首图是源场景；若有第二图则是当前目标布局。请输出修改后的目标布局 JSON。输入：\n"+json.dumps({"task":"edit-group-layout","constraints":request,"layoutJson":layout},ensure_ascii=False,separators=(",",":"))
                example={"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":prompt},
                                      {"role":"assistant","content":json.dumps(answer,ensure_ascii=False,separators=(",",":"))}],
                         "images":[str((root/"images"/(stem(theme)+".jpg")).resolve())]}
                files[split].write(json.dumps(example,ensure_ascii=False,separators=(",",":"))+"\n")
                counters[split]+=1
                if len(preview)<5:preview.append(sample)
                if (idx+1)%2000==0:print("样本",idx+1,flush=True)
        finally:
            for f in files.values():f.close()
    manifest={"schema":"multires-synthetic-layout-pairs/1","total":args.count,
              "splitCounts":dict(counters),"uniqueThemes":args.themes,
              "sourceFamilies":8,"targetProfiles":[t[0] for t in TARGETS],
              "uniquePairs":len(unique),"teacherTargetLayouts":args.themes*len(TARGETS),
              "supervision":"synthetic-rule-designed weak labels, not real designer or native device references",
              "splitPolicy":"held-out original style family (not scene-level random)",
              "images":"original generated artwork; inputs do not include targets",
              "license":"original generated assets; no external theme assets used"}
    for path in (root/"dataset"/"manifest.json",root/"sft"/"manifest.json"):
        path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf8")
    (root/"dataset"/"samples-preview.json").write_text(json.dumps(preview,ensure_ascii=False,indent=2),encoding="utf8")
    print(json.dumps(manifest,ensure_ascii=False,indent=2),flush=True)
if __name__=="__main__":
    main()
