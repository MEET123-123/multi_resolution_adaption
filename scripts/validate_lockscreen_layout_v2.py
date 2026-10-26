"""Validate a procedurally authored lockscreen geometry dataset, export candidates.

This gate checks hard invariants only. It is NOT a substitute for visual or
copyright review. Never emits trainApproved=true.
"""
import argparse
import collections
import json
from pathlib import Path
from PIL import Image

def read_jsonl(path):
    if not path.is_file():raise FileNotFoundError(path)
    return [json.loads(s) for s in path.read_text(encoding="utf8").splitlines() if s.strip()]

def write_json(path,item):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding="utf8")

def safe(root,rel):
    if not isinstance(rel,str) or "\\" in rel or not rel: raise ValueError("unsafe path "+repr(rel))
    path=(root/rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError("missing or escaping: "+rel)
    return path

def intersection(a,b):
    ax,ay,aw,ah=a;bx,by,bw,bh=b
    return max(0,min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))

def check_layout(layout,errors,key,which):
    dims=layout.get("canvas",{})
    w,h=dims.get("width"),dims.get("height")
    if not all(isinstance(x,int) and x>0 for x in (w,h)):
        errors.append(f"{key} invalid canvas {which}")
        return
    roles={}
    for item in layout.get("elements",[]):
        role=item.get("id");box=item.get("box")
        if role in roles:errors.append(f"{key} duplicated role {role}");continue
        roles[role]=box
        if not isinstance(box,list) or len(box)!=4:
            errors.append(f"{key} bad box {which}/{role}");continue
        x,y,bw,bh=box
        if x<0 or y<0 or bw<=0 or bh<=0 or x+bw>w or y+bh>h:
            errors.append(f"{key} outside {which}/{role}")
    if set(roles)!={"clock","date","hero","eyebrow","caption"}:
        errors.append(f"{key} missing semantic roles {which}")
        return
    for name in ("clock","date","eyebrow","caption"):
        if intersection(roles[name],roles["hero"])>0:
            errors.append(f"{key} hero/text collision {which} {name}")
    for a,b in (("clock","date"),("eyebrow","caption")):
        if intersection(roles[a],roles[b])>0:
            errors.append(f"{key} text collision {which} {a}/{b}")

def validate(root):
    root=root.resolve()
    reports=root/"reports"
    manifest=json.loads((reports/"manifest.json").read_text(encoding="utf8"))
    rows=read_jsonl(reports/"pairs.jsonl")
    themes=read_jsonl(reports/"themes.jsonl")
    assets=read_jsonl(reports/"assets.jsonl")
    rejected=read_jsonl(reports/"rejected.jsonl")
    errors=[]
    checked=set(); seen=set(); sha_splits={}
    splits=collections.Counter(); dispositions=collections.Counter()
    owners={t["themeId"]:t["split"] for t in themes}
    if len(owners)!=len(themes):errors.append("theme list contains duplicate theme IDs")
    if manifest.get("themesWithForeground")!=len(themes):errors.append("manifest count of processed themes mismatch")
    if manifest.get("pairCount")!=len(rows):errors.append("pair count mismatch")
    if manifest.get("rejectedThemes")!=len(rejected):errors.append("rejected themes count mismatch")
    if manifest.get("trainApproved") is not False:errors.append("manifest improperly claims training approval")
    for row in rows:
        key=(row["themeId"],row["style"],row["device"])
        if key in seen:errors.append(f"duplicate pair {key}")
        seen.add(key)
        if owners.get(row["themeId"])!=row["split"]:errors.append(f"split mismatch {key}")
        splits[row["split"]]+=1
        disposition=row["qualityGate"]["autoDisposition"]
        dispositions[disposition]+=1
        if row["qualityGate"].get("trainApproved") is not False:
            errors.append(f"training falsely approved {key}")
        if row.get("targetIsHumanGroundTruth") is not False:
            errors.append(f"human ground truth falsely claimed {key}")
        if row.get("sourceIsOriginalThemeScreenshot") is not False:
            errors.append(f"source screenshot falsely claimed {key}")
        for field,layout_name in (("sourceSynthetic","sourceLayout"),("targetSynthetic","targetLayout")):
            try:
                file=safe(root,row[field])
                if file not in checked:
                    with Image.open(file) as im:
                        layout=row[layout_name]["canvas"]
                        expected=(layout["width"],layout["height"])
                        if im.size!=expected:errors.append(f"dimensions mismatch {key} {field}: {im.size}!={expected}")
                        im.verify()
                    checked.add(file)
            except (OSError,KeyError,ValueError) as ex:
                errors.append(f"image/path fail {key} {field}: {ex}")
        check_layout(row["sourceLayout"],errors,key,"source")
        check_layout(row["targetLayout"],errors,key,"target")
        if row["qualityGate"]["geometryIssues"] and disposition=="keep_candidate":
            errors.append(f"keep candidate with geometry issues {key}")
        try:
            safe(root,row["foregroundAsset"])
        except ValueError as ex:errors.append(f"missing asset {key}: {ex}")
        if row["device"] in ("tablet","fold-wide"):
            def center_x(layout):
                el=next(x["box"] for x in layout["elements"] if x["id"]=="hero")
                return (el[0]+el[2]*.5)/layout["canvas"]["width"]
            shift=abs(center_x(row["sourceLayout"])-center_x(row["targetLayout"]))
            if shift<.08: errors.append(f"no meaningful horizontal hero reflow {key}: {round(shift,3)}")
    for theme in themes:
        path=Path(theme["sourcePreview"])
        if not path.is_file():errors.append("missing original preview "+str(path))
    for asset in assets:
        try:safe(root,asset["asset"])
        except ValueError as ex:errors.append(f"asset record invalid: {ex}")
    # Detect duplicated source image across disjoint splits based on immutable
    # source originals, not the generated output filenames.
    by_original=collections.defaultdict(set)
    for theme in themes:
        by_original[theme["sourcePreview"]].add(theme["split"])
    if any(len(x)>1 for x in by_original.values()):errors.append("source preview leaked between splits")
    derived={
      "schema":"layout-authored-synthetic-validation/2",
      "valid":not errors,"errorCount":len(errors),"errors":errors[:200],
      "themes":len(themes),"rejectedThemes":len(rejected),"pairs":len(rows),
      "imagesDecoded":len(checked),"splits":dict(splits),"autoDispositionCounts":dict(dispositions),
      "structureVerified":not errors,
      "subjectSemanticVerified":False,"aestheticQualityVerified":False,
      "rightsReviewed":False,"nativeThemeVerified":False,"trainingApproved":False}
    write_json(reports/"validation.json",derived)
    # Partition rows by candidate quality. No file labels these as released
    # training data; code consuming them must require human approval.
    (root/"sft-candidates").mkdir(exist_ok=True)
    folders={k:(root/"sft-candidates"/(k+".jsonl")).open("w",encoding="utf8") for k in ("train","validation","test")}
    try:
        for row in rows:
            if row["qualityGate"]["autoDisposition"]!="keep_candidate":continue
            entry={
                "themeId":row["themeId"],"style":row["style"],"device":row["device"],
                "split":row["split"],"source":row["sourceSynthetic"],"target":row["targetSynthetic"],
                "sourceLayout":row["sourceLayout"],"targetLayout":row["targetLayout"],
                "humanApproved":False,"trainApproved":False,
                "weakLabelType":"procedurally-authored-reflow",
                "segmentationUnverified":True}
            folders[row["split"]].write(json.dumps(entry,ensure_ascii=False)+"\n")
    finally:
        for f in folders.values():f.close()
    return derived

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--dataset",required=True)
    a=p.parse_args()
    result=validate(Path(a.dataset))
    print(json.dumps(result,indent=2,ensure_ascii=False))
    if not result["valid"]:raise SystemExit(1)
