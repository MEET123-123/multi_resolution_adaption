"""Advisory perceptual near-duplicate audit across preview-corpus theme splits.

This does NOT mark training examples as approved or infer license rights.
"""
from __future__ import annotations
import argparse
import collections
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps

def dhash(image: Image.Image):
    gray=ImageOps.grayscale(image).resize((17, 16),Image.Resampling.LANCZOS)
    pixels=np.asarray(gray,dtype=np.int16)
    return sum(int(b)<<i for i,b in enumerate((pixels[:,1:]>pixels[:,:-1]).ravel()))

def color_digest(image: Image.Image):
    thumb=ImageOps.fit(image.convert('RGB'),(16,16),Image.Resampling.LANCZOS)
    arr=np.asarray(thumb,dtype=np.float32)
    return tuple(float(x) for x in np.mean(arr,axis=(0,1)))

def main(root: Path,threshold:int=16):
    root=root.resolve()
    report=root/'reports'
    theme_rows=[json.loads(x) for x in (report/'themes.jsonl').read_text(encoding='utf8').splitlines()]
    samples=[]
    for t in theme_rows:
        files=sorted((root/'sources').glob(t['themeId']+'--*.jpg'))
        if not files:
            continue
        with Image.open(files[0]) as image:
            samples.append((t['themeId'],t['split'],dhash(image),color_digest(image)))
    cross=[];same=[];closest=[]
    for i in range(len(samples)):
        for j in range(i+1,len(samples)):
            a,b=samples[i],samples[j]
            dist=(a[2]^b[2]).bit_count()
            color_dist=sum(abs(x-y) for x,y in zip(a[3],b[3]))/3
            # dhash compares structure; color delta helps suppress poster false positives.
            if dist<=threshold and color_dist<35:
                match={"themes":[a[0],b[0]],"splits":[a[1],b[1]],"dhashDistance":dist,
                       "meanColorDelta":round(color_dist,2),"severity":"review"}
                (cross if a[1]!=b[1] else same).append(match)
    summary={
        "schema":"preview-near-duplicate-advisory/1",
        "comparedThemes":len(samples),
        "dhashBits":256,
        "distanceThreshold":threshold,
        "maxMeanColorDelta":35,
        "crossSplitCandidates":sorted(cross,key=lambda x:x['dhashDistance']),
        "sameSplitCandidates":sorted(same,key=lambda x:x['dhashDistance']),
        "crossSplitCount":len(cross),
        "sameSplitCount":len(same),
        "notes":"Perceptual hash is an advisory check. False positives and missed crop variants are possible. Human review required."
    }
    (report/'near_duplicates.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({"comparedThemes":len(samples),"crossSplitCount":len(cross),"sameSplitCount":len(same),
                      "crossSplitExamples":summary["crossSplitCandidates"][:6]},ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--threshold',type=int,default=16)
    a=p.parse_args();main(Path(a.dataset),a.threshold)
