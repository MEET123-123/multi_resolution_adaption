"""Export review tasks and ingest exact authored group-box corrections."""
from datetime import datetime
from pathlib import Path
import argparse
import shutil

from prepare import read, rows, save, sha, jsonl
from multires_app.learning.contract import validate_answer


def export(bundle, out, split):
    samples = list(rows(bundle / "samples.jsonl"))
    requests = []
    for sample in samples:
        if sample["split"] != split:
            continue
        requests.append({"id": sample["id"], "sourceName": sample["sourceName"],
                         "sourceImage": str((bundle / sample["sourceImage"]).resolve()),
                         "referencePreview": str((bundle / sample["targetImage"]).resolve()),
                         "evidence": str((bundle / sample["evidence"]).resolve()),
                         "request": sample["request"], "answer": sample["label"]["answer"],
                         "labelStatus": sample["label"]["status"], "reviewer": "",
                         "reviewedAt": "", "reason": "", "reviewEvidence": []})
    if out.exists():
        raise ValueError("Use a new review file")
    jsonl(out, requests)


def ingest(bundle, review, out):
    index = {s["id"]: s for s in rows(bundle / "samples.jsonl")}
    seen = set()
    for record in rows(review):
        id = record["id"]
        if id in seen or id not in index:
            raise ValueError(f"Duplicate or unrecognized review id: {id}")
        seen.add(id)
        sample = index[id]
        for key in ("reviewer", "reason"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(f"Review {id} needs {key}")
        if datetime.fromisoformat(record["reviewedAt"]).utcoffset() is None:
            raise ValueError(f"Review {id} needs a timestamp with an offset")
        items = record.get("reviewEvidence")
        if not isinstance(items, list) or not items or not all(isinstance(v, str) and v.strip() for v in items):
            raise ValueError(f"Review {id} needs evidence")
        validate_answer(record["answer"], sample["request"])
        for group in sample["request"]["groups"]:
            if group["fixed"]:
                gid = group["id"]
                previous = sample["label"]["answer"]["groups"][gid]
                if record["answer"]["groups"][gid] != previous:
                    raise ValueError(f"Review {id} attempted to edit fixed group {gid}")
        sample.setdefault("weakLabel", sample["label"])
        sample["label"] = {"status": "human_reviewed", "origin": "local review",
                           "answer": record["answer"],
                           "review": {k: record[k] for k in ("reviewer", "reviewedAt", "reason", "reviewEvidence")}}
    if not seen:
        raise ValueError("No reviews to import")
    out.mkdir(parents=True, exist_ok=False)
    for directory in ("images", "evidence"):
        shutil.copytree(bundle / directory, out / directory)
    jsonl(out / "samples.jsonl", index.values())
    save(out / "manifest.json", {**read(bundle / "manifest.json"),
        "labelOrigin": "mixed weak and explicitly reviewed labels",
        "reviewCount": len(seen), "reviewSha256": sha(review),
        "referencePreview": "original weak anchor preview retained for audit"})
    print(f"Imported {len(seen)} reviewed scenes", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for mode in ("export", "ingest"):
        command = sub.add_parser(mode)
        command.add_argument("--bundle", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
        if mode == "export":
            command.add_argument("--split", choices=("train", "validation", "test"), default="train")
        else:
            command.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export":
        export(args.bundle, args.out, args.split)
    else:
        ingest(args.bundle, args.review, args.out)
