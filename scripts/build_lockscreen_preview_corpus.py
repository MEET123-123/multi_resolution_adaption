r"""Build a traceable LOCKSCREEN-PREVIEW *candidate* dataset from collected theme previews.

The input collection is NOT HWT/XML. Store screenshots are observations, not
native-state ground truth. All layout pairs produced here are synthetic weak
supervision, with unknown third-party usage rights until independently reviewed.

Usage:
 .venv\Scripts\python.exe scripts\build_lockscreen_preview_corpus.py ^
     --input datasets --output artifacts\learning\lockscreen-previews-pilot-01 ^
     --max-themes 8 --max-previews 1 --devices phone,square,tablet ^
     --variants cover,pad,layered
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import random
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "studio_reverse" / "web_theme_pipeline"))
from theme_primitives import DEV

DEVICE_MAP = {name: (w, h) for name, w, h in DEV}
VARIANTS = ("cover", "pad", "layered")
MAX_IMAGE_BYTES = 25 * 1024 * 1024


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_asset(folder: Path, rel: str) -> Path | None:
    if not isinstance(rel, str) or not rel or "\\" in rel or "\x00" in rel:
        return None
    root = folder.resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        return None
    if path.stat().st_size > MAX_IMAGE_BYTES:
        return None
    return path


def classify_metadata(meta, folder: Path, max_previews: int):
    """Preview states are gallery images, NOT proven interaction transitions."""
    observed = sorted((folder / "scene-checks").glob("*.png"))
    candidates = []
    errors = []
    assets = [a for a in meta.get("assets", []) if a.get("kind") == "image"]
    for index, asset in enumerate(assets):
        if len(candidates) >= max_previews:
            break
        path = safe_asset(folder, asset.get("file"))
        if path is None:
            errors.append("invalid image asset: " + str(asset.get("file")))
            continue
        try:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                width, height = im.size
            if width < 320 or height < 600 or height / width < 1.25:
                errors.append("not portrait preview: " + path.name)
                continue
            candidates.append({
                "path": path, "previewIndex": index + 1,
                "width": width, "height": height,
                "sha256": hash_file(path),
            })
        except (OSError, ValueError) as ex:
            errors.append("image decode failed: " + path.name + ": " + str(ex)[:90])
    return candidates, observed, errors


def collect(root: Path, max_themes: int, max_previews: int):
    themes = []
    failures = []
    for folder in sorted(x for x in root.iterdir() if x.is_dir()):
        meta_path = folder / "metadata.json"
        if not meta_path.is_file():
            failures.append({"folder": folder.name, "reason": "metadata missing"})
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            previews, evidence, errs = classify_metadata(meta, folder, max_previews)
        except (ValueError, OSError, TypeError) as ex:
            failures.append({"folder": folder.name, "reason": "metadata invalid: " + str(ex)[:150]})
            continue
        if not previews:
            failures.append({"folder": folder.name, "reason": "no valid portrait previews", "details": errs})
            continue
        name = str(meta.get("name") or folder.name)
        theme_id = str(meta.get("masterId") or folder.name)
        themes.append({
            "id": theme_id, "name": name, "folder": folder, "meta": meta,
            "previews": previews, "evidence": evidence, "warnings": errs,
        })
        if max_themes and len(themes) >= max_themes:
            break
    return themes, failures


class DisjointSets:
    def __init__(self, count):
        self.parent = list(range(count))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[b] = a


def split_themes(themes):
    """Group exact duplicate assets across themes and keep them on one split."""
    dsu = DisjointSets(len(themes))
    seen = {}
    for i, item in enumerate(themes):
        for source in item["previews"]:
            digest = source["sha256"]
            if digest in seen:
                dsu.union(i, seen[digest])
            else:
                seen[digest] = i
    groups = collections.defaultdict(list)
    for i, item in enumerate(themes):
        groups[dsu.find(i)].append(item["id"])
    components = sorted(groups.values(), key=lambda ids: hashlib.sha256(("|".join(sorted(ids))).encode()).hexdigest())
    assigned = {}
    # Deterministic component-held-out split, only when enough independent
    # themes exist. A tiny pilot may have no representative test set.
    n = len(components)
    ntest = max(1, round(n * .10)) if n >= 3 else 0
    nval = max(1, round(n * .10)) if n >= 3 else 0
    for j, ids in enumerate(components):
        split = "test" if j < ntest else "validation" if j < ntest + nval else "train"
        assigned.update({i: split for i in ids})
    return assigned, [ids for ids in components if len(ids) > 1]


def grabcut_candidate(image: Image.Image):
    """Conservative OpenCV GrabCut candidate, NEVER semantic ground truth.

    Returns (transparent crop, grayscale mask, bbox, confidence-like diagnostics)
    or None. Reject improbable masks, do not hallucinate a foreground.
    """
    rgb = np.asarray(image.convert("RGB"))
    ih, iw = rgb.shape[:2]
    scale = min(1.0, 640 / max(iw, ih))
    sw, sh = max(2, round(iw * scale)), max(2, round(ih * scale))
    small = cv2.resize(rgb, (sw, sh))
    bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
    # Central subject prior; many artwork previews also contain text/overlays.
    rect = (round(sw * .14), round(sh * .17), round(sw * .72), round(sh * .67))
    mask = np.zeros((sh, sw), np.uint8)
    bg, fg = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(bgr, mask, rect, bg, fg, 3, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    m = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    largest = np.where(labels == idx, 255, 0).astype(np.uint8)
    area_fraction = float(np.count_nonzero(largest)) / (sw * sh)
    if not .025 <= area_fraction <= .52:
        return None
    x, y, w, h = [int(v) for v in stats[idx, :4]]
    if w < 20 or h < 30 or x <= 1 or y <= 1 or x+w >= sw-1 or y+h >= sh-1:
        return None
    mask_full = cv2.resize(largest, (iw, ih), interpolation=cv2.INTER_NEAREST)
    mask_full = cv2.GaussianBlur(mask_full, (5, 5), 0)
    ys, xs = np.nonzero(mask_full > 25)
    if not len(xs):
        return None
    bx0, by0, bx1, by1 = int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1
    rgba = image.convert("RGBA")
    rgba.putalpha(Image.fromarray(mask_full))
    crop = rgba.crop((bx0, by0, bx1, by1))
    return crop, Image.fromarray(mask_full), [bx0, by0, bx1-bx0, by1-by0], {
        "method": "grabcut-central-prior", "semanticVerified": False,
        "areaFraction": round(area_fraction, 4), "status": "candidate-review-required",
    }


def cover(image, size):
    return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS,
                        centering=(.5, .5))


def pad(image, size):
    width, height = size
    bg = cover(image, size).filter(ImageFilter.GaussianBlur(radius=max(8, min(size)//80)))
    front = ImageOps.contain(image.convert("RGB"), size, Image.Resampling.LANCZOS)
    bg.paste(front, ((width-front.width)//2, (height-front.height)//2))
    return bg, [int((width-front.width)//2), int((height-front.height)//2),front.width,front.height]


def layered(image, cutout, size):
    """A visibly different *synthetic* candidate, not a clean native adaptation."""
    if cutout is None:
        return None
    sprite, _mask, bbox, diagnostics = cutout
    width, height = size
    result = cover(image, size)
    # Distinct placement rule across portrait/landscape while respecting aspect.
    landscape = width / height > 1.15
    target_width = int(width * (.34 if landscape else .52))
    ratio = min(target_width / sprite.width, (height * .65) / sprite.height)
    new_size = (max(1, round(sprite.width * ratio)), max(1, round(sprite.height * ratio)))
    fg = sprite.resize(new_size, Image.Resampling.LANCZOS)
    px = round(width * (.61 if landscape else .48) - fg.width/2)
    py = round(height * (.49 if landscape else .60) - fg.height/2)
    px = max(0, min(width-fg.width, px))
    py = max(0, min(height-fg.height, py))
    result.paste(fg, (px, py), fg)
    return result, [px, py, fg.width, fg.height]


def bounds_ok(box, canvas):
    x, y, w, h = box
    return w > 0 and h > 0 and x >= 0 and y >= 0 and x+w <= canvas[0]+1 and y+h <= canvas[1]+1


def generate(theme, split, input_root, output, devices, variants, max_pairs, counter):
    pairs, cutouts = [], []
    tid = theme["id"]
    for entry in theme["previews"]:
        sid = "p" + str(entry["previewIndex"]).zfill(2)
        source = entry["path"]
        with Image.open(source) as frame:
            image = frame.convert("RGB")
        sw, sh = image.size
        source_out = output / "sources" / f"{tid}--{sid}.jpg"
        source_out.parent.mkdir(parents=True, exist_ok=True)
        image.save(source_out, quality=88, optimize=True)
        cutout = grabcut_candidate(image)
        if cutout is not None:
            sprite, mask, bbox, diagnostics = cutout
            folder = output / "assets" / tid / sid
            folder.mkdir(parents=True, exist_ok=True)
            sprite_path = folder / "foreground_candidate.png"
            mask_path = folder / "candidate_mask.png"
            sprite.save(sprite_path)
            mask.save(mask_path)
            cutouts.append({"theme":tid,"state":sid,"asset":str(sprite_path.relative_to(output)).replace("\\","/"),
                            "mask":str(mask_path.relative_to(output)).replace("\\","/"),
                            "sourceBox":bbox,**diagnostics})
        for dev in devices:
            width, height = DEVICE_MAP[dev]
            for variant in variants:
                if counter[0] >= max_pairs:
                    return pairs, cutouts
                region = None
                if variant == "cover":
                    target = cover(image, (width, height))
                    region = [0, 0, width, height]
                elif variant == "pad":
                    target, region = pad(image, (width, height))
                elif variant == "layered":
                    layer = layered(image, cutout, (width, height))
                    if layer is None:
                        continue
                    target, region = layer
                else:
                    raise ValueError("unknown variant")
                path = output / "targets" / (f"{tid}--{sid}--{dev}--{variant}.jpg")
                path.parent.mkdir(parents=True, exist_ok=True)
                target.save(path, quality=85, optimize=True)
                with Image.open(path) as check:
                    decoded = check.size == (width, height)
                passed = bool(decoded and bounds_ok(region, (width,height)))
                row = {
                    "themeId":tid, "themeName":theme["name"],"previewState":sid,
                    "stateEvidence":"storefront gallery preview only; not verified runtime state",
                    "split":split, "device":dev, "variant":variant,
                    "sourceImage":str(source_out.relative_to(output)).replace("\\","/"),
                    "targetImage":str(path.relative_to(output)).replace("\\","/"),
                    "sourceDimensions":[sw,sh],"targetDimensions":[width,height],
                    "sourceContentSha256":entry["sha256"],
                    "targetLayout":{"canvas":[width,height],"region":[int(v) for v in region],
                                    "kind":"whole-preview" if variant!="layered" else "candidate-foreground"},
                    "sourceLayout":{"canvas":[sw,sh],"region":[0,0,sw,sh],"kind":"whole-preview"},
                    "cutoutCandidate": bool(cutout is not None),
                    "provenance":{
                        "origin":"OPPO/HeyTap storefront preview (user-collected)",
                        "metadata":str((theme["folder"]/"metadata.json").relative_to(input_root)).replace("\\","/"),
                        "originalAsset":str(source.relative_to(input_root)).replace("\\","/"),
                        "shareUrl":theme["meta"].get("shareUrl"),
                        "rights":"unknown / review required",
                        "label":"deterministic synthetic baseline; not human designer target",
                    },
                    "qualityGate":{"decoded":decoded,"bboxInside":bounds_ok(region,(width,height)),
                                   "staticPassed":passed,"semanticSegmentationVerified":False,
                                   "interactionReplayVerified":False,"nativeDeviceVerified":False,
                                   "trainApproved":False},
                }
                pairs.append(row)
                counter[0] += 1
    return pairs, cutouts


def pipeline(args):
    source=Path(args.input).resolve()
    output=Path(args.output).resolve()
    if not source.is_dir():
        raise ValueError("Input directory missing: "+str(source))
    if output.exists():
        raise ValueError("Output already exists; refusing overwrite: "+str(output))
    if output.is_relative_to(source):
        raise ValueError("Output must not be inside the source collection")
    devices = [x.strip() for x in args.devices.split(",") if x.strip()]
    variants = [x.strip() for x in args.variants.split(",") if x.strip()]
    if not devices or set(devices)-set(DEVICE_MAP) or not variants or set(variants)-set(VARIANTS):
        raise ValueError("Unknown device or variant")
    if args.max_themes < 0 or args.max_previews < 1 or args.max_pairs < 1:
        raise ValueError("Invalid limits")
    if args.max_themes==0 and args.max_pairs<1000:
        raise ValueError("Full scan requested but max-pairs too low; specify explicit budget")
    themes, errors = collect(source,args.max_themes,args.max_previews)
    if not themes:
        raise ValueError("No eligible preview themes")
    splits, overlaps = split_themes(themes)
    output.mkdir(parents=True,exist_ok=False)
    pair_file=output/"reports"/"pairs.jsonl"
    theme_file=output/"reports"/"themes.jsonl"
    cutout_file=output/"reports"/"cutouts.jsonl"
    for p in (pair_file,theme_file,cutout_file):
        p.parent.mkdir(parents=True,exist_ok=True)
    pair_counts, cutout_counts = collections.Counter(), collections.Counter()
    counter=[0]
    with pair_file.open("w",encoding="utf8") as pairs_fp, \
         theme_file.open("w",encoding="utf8") as themes_fp, \
         cutout_file.open("w",encoding="utf8") as cutouts_fp:
        for idx, theme in enumerate(themes):
            split=splits[theme["id"]]
            entry={
                "themeId":theme["id"],"themeName":theme["name"],"split":split,
                "sourceFolder":str(theme["folder"].relative_to(source)).replace("\\","/"),
                "previewCount":len(theme["previews"]),"sceneEvidenceCount":len(theme["evidence"]),
                "observedSwipes":{"left":theme["meta"].get("leftSwipeCount"),
                                  "right":theme["meta"].get("rightSwipeCount")},
                "rawSceneEvidence":[str(x.relative_to(source)).replace("\\","/")
                                    for x in theme["evidence"]],
                "sceneStateVerified":False,
                "licenseReviewed":False,
                "warnings":theme["warnings"]
            }
            themes_fp.write(json.dumps(entry,ensure_ascii=False)+"\n")
            rows, candidates=generate(theme,split,source,output,devices,variants,args.max_pairs,counter)
            for row in rows:
                pairs_fp.write(json.dumps(row,ensure_ascii=False)+"\n")
                pair_counts[row["split"]]+=1
            for row in candidates:
                cutouts_fp.write(json.dumps(row,ensure_ascii=False)+"\n")
                cutout_counts["candidate"]+=1
            print(f"THEME {idx+1}/{len(themes)} {theme['id']}: {len(rows)} pairs, {len(candidates)} cutout candidates",flush=True)
            if counter[0]>=args.max_pairs:
                break
    # All split datasets intentionally remain candidate-only until rights,
    # source semantics and layout supervision are reviewed.
    manifest={
        "schema":"preview-lockscreen-weak-corpus/1",
        "source":str(source),"themesSelected":len(themes),
        "themesProcessed":idx+1,"pairs":counter[0],
        "pairSplits":dict(pair_counts),"duplicateThemeGroups":overlaps,
        "cutoutCandidates":cutout_counts["candidate"],"excluded":errors,
        "devices":devices,"variants":variants,
        "sftApproved":False,"trainReady":False,
        "pixelGeneration":"Pillow cover/blurred padding/GrabCut candidate overlay",
        "semanticSegmentationModelUsed":False,
        "screenshotPolicy":"scene-checks are interaction observations, NEVER target labels",
        "rightsPolicy":"third-party provenance retained; rights unknown; no redistribution or train approval",
        "nativeThemeVerified":False
    }
    write_json(output/"reports"/"manifest.json",manifest)
    lines=[
        "# Preview-derived lockscreen corpus (weak supervision only)",
        f"- Themes selected: {len(themes)}; processed: {idx+1}",
        f"- Generated pairs: {counter[0]}; split: {dict(pair_counts)}",
        f"- GrabCut foreground candidates: {cutout_counts['candidate']}",
        "- No actual HWT/XML, native gesture, semantic segmentation or design correctness claims.",
        "- Source interaction screenshots stay as evidence, not training labels.",
        "- Do not train/redistribute until source rights and labels are reviewed.",
    ]
    (output/"reports"/"SUMMARY.md").write_text("\n".join(lines)+"\n",encoding="utf8")
    print(json.dumps({"output":str(output),"pairs":counter[0],
                      "themes":idx+1,"splitCounts":dict(pair_counts),
                      "cutoutCandidates":cutout_counts["candidate"]},ensure_ascii=False,indent=2),flush=True)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",default="datasets")
    parser.add_argument("--output",required=True)
    parser.add_argument("--max-themes",type=int,default=8,help="0 = all themes")
    parser.add_argument("--max-previews",type=int,default=1)
    parser.add_argument("--devices",default="phone,square,tablet")
    parser.add_argument("--variants",default="cover,pad,layered")
    parser.add_argument("--max-pairs",type=int,default=1000)
    args=parser.parse_args()
    pipeline(args)

if __name__=="__main__":
    main()
