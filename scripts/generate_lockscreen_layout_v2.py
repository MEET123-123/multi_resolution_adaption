r"""Build a layout-supervised, procedurally authored theme-inspired corpus.

This is NOT native HWT adaptation and NOT a diffusion model. It creates a
synthetic PHONE source AND responsive targets from the SAME separated layers.
Labels contain the exact rendered positions of clock/date/hero/chips and may
be useful for geometry/reflow experiments only after rights/visual review.

Reuses traceable GrabCut *candidates* from V1 and never mistakes them for
a verified human segmentation. Does not overwrite V1.

Usage:
 .venv\Scripts\python.exe scripts\generate_lockscreen_layout_v2.py ^
 --input datasets ^
 --candidate-dataset artifacts\learning\lockscreen-previews-100themes-20261024 ^
 --output artifacts\learning\lockscreen-layout-authored-v2-pilot ^
 --max-themes 12 --devices phone,square,fold-wide,tablet,tablet-portrait,tall
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

DEVICES={
    "phone":(1080,2400), "tall":(1080,2520),
    "square":(2200,2200), "fold-wide":(2480,1860),
    "tablet":(2560,1600), "tablet-portrait":(1600,2560),
}
STYLES=("editorial","poster")
FONT_REGULAR=Path("C:/Windows/Fonts/segoeui.ttf")
FONT_BOLD=Path("C:/Windows/Fonts/segoeuib.ttf")

def load_rows(path):
    if not path.is_file(): return []
    return [json.loads(s) for s in path.read_text(encoding="utf8").splitlines() if s.strip()]

def make_font(size,bold=False):
    fp=FONT_BOLD if bold else FONT_REGULAR
    return ImageFont.truetype(str(fp),max(12,round(size)))

def json_dump(path,item):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(item,indent=2,ensure_ascii=False),encoding="utf8")

def rel(root,p):
    return p.relative_to(root).as_posix()

def clamp(n,lo,hi): return max(lo,min(hi,n))

def palette(source):
    with Image.open(source) as im:
        arr=np.asarray(im.convert("RGB").resize((48,96),Image.Resampling.LANCZOS),np.float32)
    h,w=arr.shape[:2]
    border=np.concatenate([arr[:h//8].reshape(-1,3),arr[-h//8:].reshape(-1,3),
                           arr[:, :w//8].reshape(-1,3),arr[:,-w//8:].reshape(-1,3)])
    background=np.median(border,axis=0)
    accent=np.percentile(arr.reshape(-1,3),76,axis=0)
    dark=float(np.mean(background))<118
    base=background * .70+np.array([12,18,30] if dark else [242,242,244])*.30
    secondary=accent*.64+base*.36
    lum=np.mean(base)
    foreground=(243,247,253) if lum<135 else (31,41,56)
    soft=(196,219,239) if lum<135 else (95,104,119)
    return tuple(int(x) for x in base),tuple(int(x) for x in secondary),foreground,soft

def bg_image(size,colors,seed):
    """Procedurally authored gradient/wisps; no AI model or source wallpaper."""
    w,h=size;bw,bh=320,max(240,round(320*h/w))
    yy,xx=np.mgrid[0:bh,0:bw].astype(np.float32)
    x=xx/bw;y=yy/bh
    base=np.asarray(colors[0],np.float32);accent=np.asarray(colors[1],np.float32)
    mix=np.clip(.11+.24*y+.12*np.sin(x*3.14)**2,0,1)
    arr=base[None,None,:]*(1-mix[:,:,None])+accent[None,None,:]*mix[:,:,None]
    rng=np.random.default_rng(seed)
    glows=[(.76,.35,.33,.31),(.22,.85,.4,.3),(.40,.16,.19,.23)]
    for gx,gy,rx,ry in glows:
        d=((x-gx)/rx)**2+((y-gy)/ry)**2
        alpha=np.exp(-d*2).astype(np.float32)*.15
        arr=arr*(1-alpha[:,:,None])+accent[None,None,:]*alpha[:,:,None]
    noise=rng.normal(0,1.5,(bh,bw,1))
    arr=np.uint8(np.clip(arr+noise,0,255))
    out=Image.fromarray(arr,"RGB").resize(size,Image.Resampling.BICUBIC)
    # Few abstract glows are deliberately behind the subject; reproducible.
    draw=ImageDraw.Draw(out,"RGBA")
    for i in range(6):
        radius=round(min(w,h)*(.03+.02*rng.random()))
        cx=round(w*rng.uniform(.1,.9));cy=round(h*rng.uniform(.16,.9))
        col=(*colors[1],round(20+15*rng.random()))
        draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius),fill=col)
    return out

def refine_mask(source_image,mask_path):
    """Refine cached GrabCut candidate with soft edges; no semantic guarantees."""
    with Image.open(mask_path) as m:
        mask=np.asarray(m.convert("L"),dtype=np.uint8)
    with Image.open(source_image) as frame:
        iw,ih=frame.size
    if mask.shape!=(ih,iw):
        mask=np.asarray(Image.fromarray(mask).resize((iw,ih),Image.Resampling.NEAREST))
    # Suppress isolated speckles and retain the dominant subject region.
    import cv2
    binary=(mask>76).astype(np.uint8)
    n,labels,stats,_=cv2.connectedComponentsWithStats(binary,8)
    if n<=1: return None,{"reason":"no foreground component"}
    comp=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
    dominant=(labels==comp).astype(np.uint8)*255
    dominant=cv2.morphologyEx(dominant,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    alpha=cv2.GaussianBlur(dominant,(5,5),0)
    ys,xs=np.nonzero(alpha>10)
    if not len(xs):return None,{"reason":"empty refined alpha"}
    x0,y0,x1,y1=int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)
    fraction=float(np.count_nonzero(dominant))/(iw*ih)
    width_fraction=(x1-x0)/iw
    height_fraction=(y1-y0)/ih
    bbox=(x0,y0,x1-x0,y1-y0)
    # Geometry-only QA. Too small/large or touching an image boundary often
    # indicates a decoration or a failed GrabCut mask.
    if fraction<.045 or fraction>.39 or width_fraction<.18 or height_fraction<.18:
        return None,{"reason":"implausible foreground geometry","areaFraction":round(fraction,4),"bbox":bbox}
    if x0<3 or y0<3 or x1>=iw-2 or y1>=ih-2:
        return None,{"reason":"foreground touches source boundary","bbox":bbox}
    with Image.open(source_image) as im:
        rgba=im.convert("RGBA")
    rgba.putalpha(Image.fromarray(alpha))
    crop=rgba.crop((x0,y0,x1,y1))
    return crop,{
        "method":"grabcut-candidate-connected-component-edge-refinement",
        "bbox":bbox,"areaFraction":round(fraction,4),
        "semanticVerified":False,"status":"segmentation-candidate",
    }

def text_bounds(draw,text,pos,font):
    # Pillow textbbox returns exact painted bbox including glyph offset.
    x,y=pos
    b=draw.textbbox((x,y),text,font=font,anchor="lt")
    return [int(b[0]),int(b[1]),int(b[2]-b[0]),int(b[3]-b[1])]

def layout(w,h,style,hero_aspect):
    landscape=w/h>1.16
    square=.92<w/h<=1.16
    minside=min(w,h)
    if landscape:
        # Two-column editorial grid: clock and chips on the left, hero right.
        topx=int(w*.08)
        clocky=int(h*(.29 if style=="editorial" else .18))
        cfont=int(minside*(.132 if style=="editorial" else .115))
        dfont=int(minside*.034)
        hero_area=[round(w*.55),round(h*.12),round(w*.38),round(h*.74)]
        badge=(int(w*.08),int(h*.70))
        label=(int(w*.08),int(h*.78))
        datey=clocky+round(cfont*1.12)
    elif square:
        topx=int(w*.09)
        clocky=int(h*(.125 if style=="editorial" else .10))
        cfont=int(minside*.100)
        dfont=int(minside*.029)
        hero_area=[round(w*.50),round(h*.33),round(w*.44),round(h*.53)]
        badge=(int(w*.10),int(h*.73))
        label=(int(w*.10),int(h*.83))
        datey=clocky+round(cfont*1.13)
    else:
        topx=int(w*(.10 if style=="editorial" else .13))
        clocky=int(h*(.14 if style=="editorial" else .10))
        cfont=int(w*(.145 if style=="editorial" else .175))
        dfont=int(w*.037)
        hero_area=[round(w*(.13 if style=="editorial" else .20)),
                   round(h*(.335 if style=="editorial" else .36)),
                   round(w*(.74 if style=="editorial" else .67)),
                   round(h*.430)]
        badge=(int(w*.10),int(h*.825))
        label=(int(w*.10),int(h*.875))
        datey=clocky+round(cfont*1.2)
    # image itself keeps aspect ratio; reserve hero box but store exact sprite box
    _,_,aw,ah=hero_area
    scale=min(aw/max(1,hero_aspect[0]),ah/max(1,hero_aspect[1]))
    sw,sh=round(hero_aspect[0]*scale),round(hero_aspect[1]*scale)
    hx=hero_area[0]+(aw-sw)//2
    hy=hero_area[1]+(ah-sh)//2
    return {
       "landscape":landscape,"clockPos":(topx,clocky),
       "clockSize":cfont,"datePos":(topx,datey),"dateSize":dfont,
       "hero":[hx,hy,sw,sh],"heroArea":hero_area,
       "badgePos":badge,"labelPos":label,
       "labelSize":int(minside*(.028 if landscape else .030)),
       "badgeSize":int(minside*(.033 if landscape else .035)),
    }

def rect_intersection(a,b):
    ax,ay,aw,ah=a;bx,by,bw,bh=b
    return max(0,min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))

def in_bounds(box,w,h):
    x,y,bw,bh=box
    return x>=0 and y>=0 and bw>0 and bh>0 and x+bw<=w and y+bh<=h

def draw_scene(dim,style,hero,pal,seed):
    w,h=dim
    image=bg_image((w,h),pal,seed)
    dr=ImageDraw.Draw(image,"RGBA")
    lay=layout(w,h,style,hero.size)
    fg=pal[2];soft=pal[3]
    # Transparent chips & bands, authored elements placed independently.
    clock="10:21"
    date="FRIDAY · OCT 09"
    clock_font=make_font(lay["clockSize"],True)
    date_font=make_font(lay["dateSize"])
    datebox=text_bounds(dr,date,lay["datePos"],date_font)
    clockbox=text_bounds(dr,clock,lay["clockPos"],clock_font)
    # small atmospheric heading / brand line, not copied from original theme
    badge="STILL MOMENTS"
    label="LOCKSCREEN  /  2026"
    bf=make_font(lay["badgeSize"],True);lf=make_font(lay["labelSize"])
    badgebox=text_bounds(dr,badge,lay["badgePos"],bf)
    labelbox=text_bounds(dr,label,lay["labelPos"],lf)
    # Adjustable transparency ensures text readable against generated bg.
    for box in (clockbox,datebox,badgebox,labelbox):
        x,y,bw,bh=box
        dr.rounded_rectangle((x-12,y-8,x+bw+12,y+bh+8),radius=16,fill=(0,0,0,19) if sum(fg)>370 else (255,255,255,24))
    dr.text(lay["clockPos"],clock,font=clock_font,fill=fg,anchor="lt")
    dr.text(lay["datePos"],date,font=date_font,fill=soft,anchor="lt")
    dr.text(lay["badgePos"],badge,font=bf,fill=fg,anchor="lt")
    dr.text(lay["labelPos"],label,font=lf,fill=soft,anchor="lt")
    # Hero and optional soft offset silhouette plate.
    hx,hy,hw,hh=lay["hero"]
    sprite=hero.resize((hw,hh),Image.Resampling.LANCZOS)
    halo=Image.new("RGBA",(w,h),(0,0,0,0))
    halo.paste(sprite,(hx,hy),sprite)
    # diffuse edge behind hero rather than double-paste original subject
    glow=Image.new("RGBA",(w,h),(0,0,0,0))
    glow_dr=ImageDraw.Draw(glow)
    glow_dr.ellipse((hx-hw*.1,hy-hh*.07,hx+hw*1.1,hy+hh*1.1),fill=(*pal[1],38))
    glow=glow.filter(ImageFilter.GaussianBlur(round(min(w,h)*.024)))
    image=Image.alpha_composite(image.convert("RGBA"),glow)
    image.alpha_composite(halo)
    items=[
      {"id":"clock","type":"text","text":clock,"box":clockbox,"z":3,"anchor":"top-or-left"},
      {"id":"date","type":"text","text":date,"box":datebox,"z":3,"anchor":"under-clock"},
      {"id":"hero","type":"image","asset":"foreground_candidate.png","box":[hx,hy,hw,hh],"z":2,"anchor":"center-or-right"},
      {"id":"eyebrow","type":"text","text":badge,"box":badgebox,"z":3,"anchor":"bottom-or-left"},
      {"id":"caption","type":"text","text":label,"box":labelbox,"z":3,"anchor":"bottom-or-left"},
    ]
    # hero may overlap decoration/glow, but clock/date must not overlap hero;
    # badges/text also should not overlay hero in these template variants.
    forbidden=[]
    for item in items:
        if not in_bounds(item["box"],w,h): forbidden.append("out-of-bounds:"+item["id"])
    for a in ("clock","date","eyebrow","caption"):
        box=next(x["box"] for x in items if x["id"]==a)
        hero_box=next(x["box"] for x in items if x["id"]=="hero")
        if rect_intersection(box,hero_box)>0: forbidden.append("hero-overlap:"+a)
    for a,b in (("clock","date"),("eyebrow","caption")):
        ba=next(x["box"] for x in items if x["id"]==a)
        bb=next(x["box"] for x in items if x["id"]==b)
        if rect_intersection(ba,bb)>0: forbidden.append("text-overlap:"+a+"/"+b)
    return image.convert("RGB"),{
      "canvas":{"width":w,"height":h},"elements":items,
      "style":style,"landscape":lay["landscape"],
    },forbidden

def run(args):
    src=Path(args.input).resolve()
    candidates=Path(args.candidate_dataset).resolve()
    out=Path(args.output).resolve()
    if not src.is_dir():raise ValueError("missing input directory")
    if not candidates.is_dir():raise ValueError("missing V1 candidate directory")
    if out.exists():raise ValueError("new output directory required; refuse overwrite: "+str(out))
    devices=[x.strip() for x in args.devices.split(",")]
    if not devices or set(devices)-set(DEVICES):raise ValueError("invalid device")
    if args.max_themes<1:raise ValueError("max themes must be positive")
    ts=load_rows(candidates/"reports"/"themes.jsonl")[:args.max_themes]
    cuts={ (c["theme"],c["state"]):c for c in load_rows(candidates/"reports"/"cutouts.jsonl")}
    if not ts:raise ValueError("no themes in source manifest")
    out.mkdir(parents=True,exist_ok=False)
    (out/"reports").mkdir()
    streams={k:(out/"reports"/(k+".jsonl")).open("w",encoding="utf8") for k in ("pairs","themes","rejected","assets")}
    pairs=collections.Counter();dispositions=collections.Counter();rejected=[]
    nprocessed=0
    try:
        for ti,t in enumerate(ts):
            tid=t["themeId"];state="p01";key=(tid,state)
            src_file=candidates/"sources"/(tid+"--"+state+".jpg")
            cut=cuts.get(key)
            if not src_file.is_file() or cut is None:
                result={"themeId":tid,"reason":"source missing or no GrabCut candidate"}
                streams["rejected"].write(json.dumps(result,ensure_ascii=False)+"\n")
                rejected.append(result)
                continue
            crop,quality=refine_mask(src_file,candidates/cut["mask"])
            if crop is None:
                result={"themeId":tid,**quality}
                streams["rejected"].write(json.dumps(result,ensure_ascii=False)+"\n")
                rejected.append(result)
                continue
            sub=out/"assets"/tid
            sub.mkdir(parents=True)
            crop.save(sub/"foreground_candidate.png")
            pal=palette(src_file)
            seed=int(hashlib.sha256(tid.encode()).hexdigest()[:8],16)
            assetrow={"themeId":tid,"asset":rel(out,sub/"foreground_candidate.png"),
                      "sourcePreview":str(src_file),"originalThemeId":tid,"palette":pal[:2],**quality}
            streams["assets"].write(json.dumps(assetrow,ensure_ascii=False)+"\n")
            theme_row={"themeId":tid,"themeName":t.get("themeName"),"split":t["split"],
                       "sourcePreview":str(src_file),"sourceType":"user-collected store preview",
                       "cutoutType":"heuristic-alpha","semanticSegmentationVerified":False,
                       "rightsReviewed":False}
            streams["themes"].write(json.dumps(theme_row,ensure_ascii=False)+"\n")
            nprocessed+=1
            for style in STYLES[:args.styles]:
                source_path=out/"source-synthetic"/(tid+"--"+style+".jpg")
                source_path.parent.mkdir(parents=True,exist_ok=True)
                simage,slayout,sissues=draw_scene(DEVICES["phone"],style,crop,pal,seed+style.count("o"))
                simage.save(source_path,quality=89)
                for dev in devices:
                    target_path=out/"target-synthetic"/(tid+"--"+style+"--"+dev+".jpg")
                    target_path.parent.mkdir(parents=True,exist_ok=True)
                    target,t_layout,issues=draw_scene(DEVICES[dev],style,crop,pal,seed+style.count("o"))
                    target.save(target_path,quality=89)
                    allissues=list(sorted(set(sissues+issues)))
                    fraction=quality.get("areaFraction",0)
                    # Sample score based ONLY on inspectable geometry and rough
                    # alpha/size indicators. Never auto-authorize model training.
                    confidence=round(max(0,1-min(abs(fraction-.20)*1.6,.36)),3)
                    disposition=("reject" if allissues else
                                 "manual_review" if confidence<.78 or fraction<.07 else
                                 "keep_candidate")
                    row={"schema":"layout-authored-synthetic/2",
                         "themeId":tid,"style":style,"device":dev,"split":t["split"],
                         "sourceSynthetic":rel(out,source_path),"targetSynthetic":rel(out,target_path),
                         "originalPreview":str(src_file),
                         "foregroundAsset":rel(out,sub/"foreground_candidate.png"),
                         "sourceLayout":slayout,"targetLayout":t_layout,
                         "generation":"procedural-gradient+alpha-candidate+responsive-grid",
                         "targetIsHumanGroundTruth":False,
                         "sourceIsOriginalThemeScreenshot":False,
                         "qualityGate":{"geometryIssues":allissues,"segmentationConfidenceHeuristic":confidence,
                                        "autoDisposition":disposition,
                                        "visualReviewVerified":False,"licenseReviewed":False,
                                        "nativeDeviceVerified":False,"trainApproved":False}}
                    streams["pairs"].write(json.dumps(row,ensure_ascii=False)+"\n")
                    pairs[t["split"]]+=1
                    dispositions[disposition]+=1
            print("THEME",ti+1,"/",len(ts),tid,"rows",len(devices)*args.styles,flush=True)
    finally:
        for f in streams.values():f.close()
    manifest={"schema":"layout-authored-synthetic/2","input":str(src),"candidateDataset":str(candidates),
       "selectedThemes":len(ts),"themesWithForeground":nprocessed,"rejectedThemes":len(rejected),
       "pairCount":sum(pairs.values()),"splitCounts":dict(pairs),
       "autoDispositionCounts":dict(dispositions),"devices":devices,
       "styles":STYLES[:args.styles],"nativeVerified":False,"trainApproved":False,
       "scope":"procedurally authored layouts using third-party preview-derived alpha candidates",
       "methodNote":"Both synthetic source and target are generated from same layer components. NOT native target ground truth. Text boxes rendered as independent layers.",
       "segmenter":"refined prior GrabCut mask, not semantic model",
       "visualReviewRequired":True,"rightsReviewRequired":True}
    json_dump(out/"reports"/"manifest.json",manifest)
    print(json.dumps(manifest,ensure_ascii=False,indent=2),flush=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",default="datasets")
    p.add_argument("--candidate-dataset",default="artifacts/learning/lockscreen-previews-100themes-20261024")
    p.add_argument("--output",required=True)
    p.add_argument("--max-themes",type=int,default=12)
    p.add_argument("--styles",type=int,choices=(1,2),default=2)
    p.add_argument("--devices",default="phone,tall,square,fold-wide,tablet,tablet-portrait")
    run(p.parse_args())

if __name__=="__main__":main()
