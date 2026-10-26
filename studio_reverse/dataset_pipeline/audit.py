"""Verify local synthetic dataset counts, split leakage, HWT archive structure, images and SFT paths."""
import json, zipfile, collections, sys
from pathlib import Path

root=Path(sys.argv[1] if len(sys.argv)>1 else "artifacts/learning/synthetic-10k-v1").resolve()
manifest=json.loads((root/"dataset"/"manifest.json").read_text(encoding="utf8"))
counts=collections.Counter();families={};seen=set();targets=set();last=0
with (root/"dataset"/"pairs.jsonl").open(encoding="utf8") as f:
    for line in f:
        x=json.loads(line);counts[x["split"]]+=1;last+=1
        assert x["id"] not in seen, x["id"]
        seen.add(x["id"])
        family=x["family"];split=x["split"]
        assert family not in families or families[family]==split, "split leakage"
        families[family]=split
        targets.add(x["target"]["id"])
        assert all(len(v)==4 and v[2]>0 and v[3]>0 for v in x["answer"]["groups"].values())
assert last==10000 and manifest["total"]==last and dict(counts)==manifest["splitCounts"]
assert len(targets)==6 and len(seen)==10000
pkgs=list((root/"themes").glob("*.hwt"));imgs=list((root/"images").glob("*.jpg"))
assert len(pkgs)==64 and len(imgs)==64
for p in pkgs:
    with zipfile.ZipFile(p) as z:
        assert "unlock/lockscreen/manifest.xml" in z.namelist()
        assert z.testzip() is None, p
for split,n in counts.items():
    with (root/"sft"/(split+".jsonl")).open(encoding="utf8") as f:
        found=0
        for line in f:
            x=json.loads(line);found+=1
            assert len(x["images"])==1 and Path(x["images"][0]).is_file()
            assert x["messages"][-1]["role"]=="assistant"
        assert found==n
print(json.dumps({"root":str(root),"samples":last,"splits":dict(counts),"packages":len(pkgs),
                  "images":len(imgs),"targetDevices":sorted(targets),"familySplitLeakage":False,
                  "sftImagesExist":True,"status":"PASSED"},ensure_ascii=False,indent=2))
