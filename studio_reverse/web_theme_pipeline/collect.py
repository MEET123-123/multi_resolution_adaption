"""Find openly licensed image resources through Wikimedia Commons official Action API.
Produces *asset* records, not claims that these are pre-existing phone themes.
Only downloads a bounded thumbnail after license metadata filtering.
"""
from __future__ import annotations
import argparse, html, hashlib, json, random, re, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from PIL import Image
from io import BytesIO

SEARCHES=[
 "mountain landscape","sea horizon","night sky stars","aurora borealis",
 "abstract art color","watercolor abstract","botanical flowers","japanese garden",
 "desert landscape","autumn forest","city skyline night","urban architecture",
 "clouds sunset","galaxy nebula","geometric abstract","macro nature",
 "minimalist architecture","lake reflection","snow mountain","tropical beach",
 "abstract texture","forest trees","underwater coral","vintage illustration",
 "national park landscape","colorful minerals","digital art landscape",
 "ocean waves","northern lights","space earth NASA","night city photography",
 "flower macro","satellite earth","bird wildlife photography","architecture facade",
 "starry sky milky way","fall foliage","waterfall","river landscape",
 "crystal mineral","sand dunes","meadow flowers","rock formation",
 "modern building","abstract painting","ice glacier","rainforest","winter trees",
 "seaside cliff","ocean aerial"
]
ALLOWED=re.compile(r'^(?:CC0(?: 1\\.0)?|Public domain|CC BY [0-9.]+|CC BY-SA [0-9.]+)$',re.I)
USER_AGENT="MultiResThemeResearch/0.2 (https://github.com/topics/lockscreen; educational design research) Python-urllib"
BASE="https://commons.wikimedia.org/w/api.php"
def clean(s):
    s=html.unescape(re.sub(r'<[^>]+>',' ',str(s or '')))
    return ' '.join(s.split())[:380]
def get(url,timeout=32):
    for attempt in range(4):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":USER_AGENT,"Accept":"application/json,image/*"})
            with urllib.request.urlopen(req,timeout=timeout) as response:
                return response.read(3_000_000),response.headers.get('Content-Type','')
        except urllib.error.HTTPError as e:
            if e.code not in (429,500,502,503,504):raise
            retry=e.headers.get("Retry-After")
            wait=min(80,float(retry)) if retry and retry.isdecimal() else 2**attempt*3
            time.sleep(wait)
        except (TimeoutError,urllib.error.URLError):
            if attempt==3:raise
            time.sleep(3+attempt*3)
    raise RuntimeError("HTTP retries exhausted")
def query(q,cont=None,limit=100):
    params={"action":"query","generator":"search","gsrsearch":q,
            "gsrnamespace":6,"gsrlimit":min(100,limit),"prop":"imageinfo",
            "iiprop":"url|size|mime|extmetadata","iiurlwidth":720,"format":"json","formatversion":2}
    if cont:params.update(cont)
    raw,_=get(BASE+"?"+urllib.parse.urlencode(params))
    return json.loads(raw)
def candidate(page,q):
    item=page.get("imageinfo",[None])[0]
    if not item:return None
    meta=item.get("extmetadata") or {}
    lic=clean((meta.get("LicenseShortName") or {}).get("value"))
    if not ALLOWED.fullmatch(lic):return None
    url=item.get("thumburl") or ""
    host=urllib.parse.urlparse(url).hostname
    if host not in ("upload.wikimedia.org","thumb.wikimedia.org","commons.wikimedia.org"):return None
    width,height=item.get("width",0),item.get("height",0)
    if width<700 or height<500:return None
    if item.get("mime","").lower() not in ("image/jpeg","image/png","image/webp"):return None
    return {"id":str(page["pageid"]),"title":page.get("title","")[:180],
            "query":q,"license":lic,
            "licenseUrl":clean((meta.get("LicenseUrl") or {}).get("value")),
            "artist":clean((meta.get("Artist") or {}).get("value")),
            "credit":clean((meta.get("Credit") or {}).get("value")),
            "description":clean((meta.get("ImageDescription") or {}).get("value")),
            "page":"https://commons.wikimedia.org/wiki/"+urllib.parse.quote(page["title"].replace(" ","_")),
            "imageUrl":url,"width":width,"height":height}
def atomic_json(path, obj):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8')
    temp.replace(path)
def collect(out,desired,discover_only=False):
    out=Path(out).resolve()
    (out/"assets").mkdir(parents=True,exist_ok=True)
    cat=out/"catalog.json"
    rows=json.loads(cat.read_text(encoding="utf8")) if cat.exists() else []
    seen={x["id"] for x in rows}; hashes={x.get("sha256") for x in rows if x.get("sha256")}
    print("existing",len(rows),flush=True)
    # Additional candidates amortize failed downloads and irrelevant license metadata.
    for q in SEARCHES:
        if len(rows)>=desired:break
        cont=None
        for page_no in range(4):
            try:r=query(q,cont)
            except Exception as e:
                print("query-failed",q,type(e).__name__,str(e)[:120],flush=True);break
            pages=r.get("query",{}).get("pages",[])
            for page in pages:
                c=candidate(page,q)
                if c is None or c["id"] in seen:continue
                seen.add(c["id"])
                rows.append(c)
            atomic_json(cat,rows)
            print("searched",q,page_no+1,"found",len(rows),flush=True)
            if len(rows)>=desired:break
            cont=r.get("continue")
            if not cont:break
            time.sleep(.25)
    if discover_only:
        print("DISCOVERY_DONE",len(rows),flush=True)
        return
    successes=0;issues=[]
    for i,row in enumerate(rows):
        filename=out/"assets"/(row["id"]+".jpg")
        if filename.is_file() and filename.stat().st_size>2000:
            successes+=1;continue
        if successes>=desired:break
        try:
            binary,_=get(row["imageUrl"],timeout=40)
            with Image.open(BytesIO(binary)) as orig:
                im=orig.convert("RGB")
                if im.width<250 or im.height<250:raise ValueError("small image")
                # Keep useful aspect ratio and remove irrelevant metadata.
                im.thumbnail((720,1280),Image.Resampling.LANCZOS)
                tmp=filename.with_suffix(".tmp")
                im.save(tmp,"JPEG",quality=82,optimize=True)
                tmp.replace(filename)
            digest=hashlib.sha256(filename.read_bytes()).hexdigest()
            if digest in hashes:
                filename.unlink()
                raise ValueError("duplicate asset bytes")
            hashes.add(digest);row["sha256"]=digest
            row["localPath"]="assets/"+filename.name
            successes+=1
        except Exception as e:
            row["downloadError"]=type(e).__name__+": "+str(e)[:100]
            issues.append(row["id"])
        if (i+1)%30==0:
            atomic_json(cat,rows)
            print("downloaded",successes,"attempted",i+1,"candidates",len(rows),flush=True)
        # Bounded requests, single-threaded and respectful of Wikimedia CDN.
        time.sleep(.08)
    atomic_json(cat,rows)
    selected=[x for x in rows if x.get("localPath") and (out/x["localPath"]).is_file()]
    atomic_json(out/"inventory.json",{"requested":desired,"foundMetadata":len(rows),"downloaded":len(selected),"failed":len(issues),
                                    "source":"Wikimedia Commons official API","complete":len(selected)>=desired,
                                    "note":"These are openly licensed background media, NOT 1000 pre-existing phone themes.",
                                    "licenseDistribution":{k:sum(r["license"]==k for r in selected) for k in sorted({r["license"] for r in selected})}})
    print("COMPLETE",len(selected),"/",desired,flush=True)
if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--out",default="artifacts/learning/web-themes-1000")
    p.add_argument("--count",type=int,default=1000)
    p.add_argument("--discover-only",action="store_true")
    a=p.parse_args()
    collect(a.out,a.count,a.discover_only)
