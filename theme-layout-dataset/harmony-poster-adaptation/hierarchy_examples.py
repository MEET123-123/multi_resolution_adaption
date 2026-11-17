"""Export split-safe layered annotation tasks and local corruption/repair pairs."""
from collections import Counter
from pathlib import Path
import argparse

from prepare import jsonl, read, rows, save
from hierarchy import build, span
from policy import prepare_sample


def tasks(bundle, out):
    out.mkdir(parents=True, exist_ok=False)
    records = []
    for sample in rows(bundle / "samples.jsonl"):
        ev = read(bundle / sample["evidence"])
        if sample["groupCount"] <= 12 and len(ev["annotations"]["elementLevel"]["elements"]) < 60:
            continue
        item = prepare_sample(sample, bundle, dynamic=True)
        proxy, tree = build(item, ev)
        # Teacher unions give weak group regions. They do not certify new
        # containment, viewpoint reshaping, or business-state rewrites.
        teacher = sample["label"]["answer"]["groups"]
        regions = {p["id"]: span([teacher[g] for g in p["children"]]) for p in tree["panels"]}
        repairs = []
        for panel in tree["panels"]:
            clean = regions[panel["id"]]
            x, y, w, h = clean
            if not (min(x, y) >= 0 and x + w <= 1000 and y + h <= 1000):
                continue
            shifted = [1015 - w, y, w, h]
            repairs.append({"issue": "injected-right-overflow", "group": panel["id"],
                            "damagedBox": shifted,
                            "operation": {"op": "translate_group", "id": panel["id"],
                                          "dx": round(x - shifted[0], 3), "dy": 0}})
            break
        records.append({"id": sample["id"], "family": sample["family"], "split": sample["split"],
                        "sourceImage": str((bundle / sample["sourceImage"]).resolve()),
                        "sourceFacts": {"request": sample["request"], "dynamic": item["dynamicGroups"]},
                        "grouping": {"tree": tree["authoredTree"], "panels": tree["panels"],
                                     "layerStack": tree["layerStack"], "relations": tree["visualRelations"],
                                     "origin": "authored hierarchy plus inferred geometric relations"},
                        "recomposition": {"regions": regions, "origin": sample["label"]["status"]},
                        "repair": repairs})
    for split in ("train", "validation", "test"):
        jsonl(out / f"{split}.jsonl", [r for r in records if r["split"] == split])
    counts = Counter(r["split"] for r in records)
    save(out / "manifest.json", {"counts": counts, "coordinateSpace": "target-safe-normalized-1000",
                                 "groupingRelations": "some inferred; review before human supervision",
                                 "purpose": "structured annotation and follow-on supervised tasks"})
    print(dict(counts), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    tasks(args.bundle, args.out)
