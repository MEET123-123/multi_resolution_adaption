"""Pair two cached native theme forms without re-reading either source script."""

import json
import shutil
from pathlib import Path
from .catalog import Catalog
from .contract import SCHEMA, normalized, validate_answer
from .dataset import read_json
from multires_app.evaluation.core import write_json


def suggestions(source_directory, target_directory, out, limit=100):
    source = Catalog(source_directory)
    target = Catalog(target_directory)
    sources = source.page(limit=min(limit, 500))["samples"]
    targets = target.page(limit=min(limit, 500))["samples"]
    pairs = []
    for s in sources:
        target_view = s["request"].get("targetViewport", s["request"]["safeRegion"])
        ratio = target_view.get("width", target_view.get("w")) / target_view.get(
            "height", target_view.get("h")
        )
        candidates = []
        for t in targets:
            vp = t["request"]["sourceViewport"]
            if abs(vp["width"] / vp["height"] / ratio - 1) < 0.02:
                candidates.append(
                    {"targetId": t["id"], "state": t["state"], "sourceImage": t["sourceImage"]}
                )
        pairs.append(
            {
                "sourceId": s["id"],
                "targetCandidates": candidates[:20],
                "sourceGroups": [
                    {"id": g["id"], "roles": g["roles"], "sourceHints": g["sourceHints"]}
                    for g in s["request"]["groups"]
                ],
                "groups": {g["id"]: None for g in s["request"]["groups"]},
                "confirmedCorrespondence": False,
            }
        )
    result = {
        "schema": "multires-native-pairs/1",
        "reviewer": "",
        "sourceDataset": str(Path(source_directory).resolve()),
        "targetDataset": str(Path(target_directory).resolve()),
        "pairs": pairs,
        "instructions": "Select targetId and fill source group -> target native group IDs. Confirm state and correspondence. No automatic approval.",
    }
    write_json(out, result)
    return {
        "sourceSamples": len(sources),
        "targetSamples": len(targets),
        "pairTemplates": len(pairs),
    }


def import_native_pairs(source_directory, target_directory, manifest):
    source = Catalog(source_directory)
    target = Catalog(target_directory)
    batch = read_json(manifest)
    reviews = []
    copies = []
    if batch.get("schema") != "multires-native-pairs/1" or not batch.get("reviewer", "").strip():
        raise ValueError("native pair schema and reviewer required")
    for pair in batch.get("pairs", []):
        if pair.get("confirmedCorrespondence") is not True:
            raise ValueError("native state/group correspondence must be confirmed")
        s = source.sample(pair["sourceId"])
        t = target.sample(pair["targetId"])
        vp = t["request"]["sourceViewport"]
        region = s["request"]["safeRegion"]
        tv = s["request"].get("targetViewport", {"width": region["w"], "height": region["h"]})
        if abs((vp["width"] / vp["height"]) / (tv["width"] / tv["height"]) - 1) > 0.02:
            raise ValueError("native target aspect ratio does not match requested canvas")
        mapping = pair["groups"]
        known = {g["id"] for g in s["request"]["groups"]}
        native = {g["id"]: g for g in t["request"]["groups"]}
        if set(mapping) != known:
            raise ValueError("native correspondence must cover every source group")
        answer = {"schema": SCHEMA, "groups": {}}
        used = set()
        for alias, members in mapping.items():
            if isinstance(members, str):
                members = [members]
            if (
                not isinstance(members, list)
                or not members
                or any(mid not in native for mid in members)
            ):
                raise ValueError("unknown target native group")
            if used.intersection(members):
                raise ValueError("ambiguous target group reused by multiple source groups")
            used.update(members)
            boxes = [native[mid]["sourceBox"] for mid in members]
            left = min(b[0] for b in boxes)
            top = min(b[1] for b in boxes)
            right = max(b[0] + b[2] for b in boxes)
            bottom = max(b[1] + b[3] for b in boxes)
            logical = {
                "x": left * tv["width"] / 1000,
                "y": top * tv["height"] / 1000,
                "w": (right - left) * tv["width"] / 1000,
                "h": (bottom - top) * tv["height"] / 1000,
            }
            answer["groups"][alias] = normalized(logical, region)
        validate_answer(answer, s["request"])
        name = "references/" + s["id"] + "-native.png"
        destination = source.root / name
        image = (target.root / t["sourceImage"]).resolve()
        if not image.is_relative_to(target.root):
            raise ValueError("invalid cached target image")
        copies.append((image, destination))
        reviews.append(
            {
                "id": s["id"],
                "requestKey": s["requestKey"],
                "baseRevision": s["baseRevision"],
                "status": "approved",
                "answer": answer,
                "targetReferenceImage": name,
                "pairProvenance": {
                    "origin": "human-confirmed native script correspondence",
                    "targetSample": t["id"],
                    "targetSourceSha256": t["sourceSha256"],
                    "groupMapping": mapping,
                },
                "notes": pair.get("notes", ""),
            }
        )
    # Copy only cached reference imagery; do not touch packages or run extraction.
    for image, destination in copies:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image, destination)
    return source.append({"schema": SCHEMA, "reviewer": batch["reviewer"], "reviews": reviews})
