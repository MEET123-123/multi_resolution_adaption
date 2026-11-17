"""Bind portable training records to the user's exact local HWT bytes."""
from pathlib import Path
from collections import Counter
import argparse
import json
import os
import shutil
import sys

ENGINE = Path(os.environ.get("MULTIRES_ROOT", Path(__file__).resolve().parents[2])).resolve()
ENGINE_DATA = ENGINE / "theme-layout-dataset"
sys.path[:0] = [str(ENGINE_DATA), str(ENGINE / "src")]
from build_training_pairs import read, rows, save, sha, jsonl

DEFAULT_CACHE = ENGINE_DATA / "runs" / "auto-pairs-v1"


def build(source, cache, out):
    workers = [read(p) for p in sorted((cache / "workers").glob("*/worker.json"))]
    records = list(rows(cache / "server-bundle" / "samples.jsonl"))
    by_hash = Counter(row["sourceSha256"] for row in records)
    known = {w["sourceSha256"] for w in workers}
    sources = []
    for path in sorted(source.rglob("*.hwt")):
        digest = sha(path)
        sources.append({"path": str(path.resolve()), "sha256": digest,
                        "cacheMatched": digest in known, "acceptedPairs": by_hash[digest]})
        print(path.name, digest in known, by_hash[digest], flush=True)
    allowed = {s["sha256"] for s in sources}
    selected = [s for s in records if s["sourceSha256"] in allowed]
    splits = Counter(s["split"] for s in selected)
    if set(splits) != {"train", "validation", "test"}:
        raise ValueError("Exact package match must cover train, validation and test")
    out.mkdir(parents=True, exist_ok=False)
    original = cache / "server-bundle"
    verified = read(original / "file-sha256.json")
    for name in {s[k] for s in selected for k in ("sourceImage", "targetImage", "evidence")}:
        src, dst = original / name, out / name
        if sha(src) != verified[name]:
            raise ValueError(f"Sealed artifact differs: {name}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    if sha(original / "samples.jsonl") != verified["samples.jsonl"]:
        raise ValueError("Sealed sample manifest differs")
    jsonl(out / "samples.jsonl", selected)
    save(out / "manifest.json", {
        "sources": sources, "workers": str((cache / "workers").resolve()),
        "samples": len(selected), "counts": dict(splits),
        "familySplits": {s["family"]: s["split"] for s in selected},
        "labelOrigin": "anchor solver with source and target offline simulation",
    })
    print(json.dumps(splits), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.cache, args.out)
