"""Local script labels, weak layout proposals, human labels and SFT exports."""

import json
import copy
import shutil
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path
from multires_app.evaluation.core import digest, write_json
from multires_app.evaluation.workflow import file_sha, source_digest
from multires_app.pipeline.scene_clusters import (
    schedule_device,
    describe_layout,
    transformed_box,
    box_union,
)
from .algorithm import RecordingAnchor
from .contract import SCHEMA, SYSTEM, normalized, validate_answer, prompt
from .render import render
from .annotations import extract


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_samples(directory):
    with (Path(directory) / "samples.jsonl").open(encoding="utf8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def assign_split(family):
    value = int(digest(family)[:8], 16) % 10
    return "test" if value == 0 else "validation" if value == 1 else "train"


def actual_boxes(project, layout, groups):
    descriptions = describe_layout(project, layout)
    index = {g["id"]: g for g in groups}

    def resolve(gid, stack=()):
        if gid in stack:
            raise ValueError("cyclic annotation group")
        if gid in index:
            return box_union([resolve(m, (*stack, gid)) for m in index[gid]["members"]])
        return transformed_box(descriptions[gid]["bounds"], layout["transforms"][gid])

    return {g["id"]: resolve(g["id"]) for g in groups}


def build(paths, out, *, max_states=3, targets=(), family_overrides=None, resume=False):
    out = Path(out).resolve()
    configuration = {
        "maxStates": max_states,
        "targets": list(targets),
        "families": family_overrides or {},
    }
    if out.exists() and not resume:
        raise ValueError("use a new dataset directory or --resume")
    out.mkdir(parents=True, exist_ok=True)
    previews = []
    projects = []
    skipped = []
    families = family_overrides or {}
    sample_ids = set()
    splits = Counter()
    total = 0
    if resume:
        previous = read_json(out / "manifest.json")
        if previous.get("buildConfiguration") != configuration:
            raise ValueError("resume configuration mismatch")
        projects = previous["projects"]
        skipped = previous["skipped"]
        for row in read_samples(out):
            sample_ids.add(row["id"])
            splits[row["split"]] += 1
            total += 1
            if len(previews) < 50:
                previews.append(row)

    def checkpoint(status):
        manifest = {
            "schema": SCHEMA,
            "datasetVersion": "1.0",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "samples": total,
            "projects": projects,
            "splits": dict(splits),
            "labelCounts": {"weak": total},
            "skipped": skipped,
            "nativeValidation": False,
            "status": status,
            "buildConfiguration": configuration,
            "splitPolicy": "theme source SHA/family; never scene-level random split",
        }
        write_json(out / "manifest.json", manifest)
        return manifest

    (out / "samples.jsonl").touch(exist_ok=True)
    checkpoint("building")
    for path in map(lambda p: Path(p).resolve(), paths):
        before = file_sha(path)
        p = read_json(path)
        source = Path(p.get("source", ""))
        source_sha = (
            source_digest(source)
            if source.exists()
            else digest({"nodes": p["model"]["nodes"], "root": p["model"].get("root")})
        )
        completed = next((e for e in projects if e["path"] == str(path)), None)
        if completed:
            if completed["sha256"] != before or completed["sourceSha256"] != source_sha:
                raise ValueError("cached input changed; use a new dataset version")
            continue
        family = families.get(source_sha, families.get(path.parent.parent.name, source_sha))
        explicit_split = None
        if isinstance(family, dict):
            explicit_split = family.get("split")
            family = family["family"]
        if not isinstance(family, str) or explicit_split not in {
            None,
            "train",
            "validation",
            "test",
        }:
            raise ValueError("invalid family/split override")
        # HWT aliases and variants always share a split. Additional near-clone
        # families can be explicitly mapped in one family manifest.
        split = explicit_split or assign_split(family)
        states = {s["id"]: s for s in p["graph"]["states"]}
        if any(
            e["sourceSha256"] == source_sha and (e["split"] != split or e["family"] != family)
            for e in projects
        ):
            raise ValueError("same source theme assigned to multiple families/splits")
        source_layouts = {l["state"]: l for l in p["sourceDevice"]["layouts"]}
        script_file = "labels/" + source_sha + ".json"
        write_json(
            out / script_file,
            {
                "provenance": "script exact attributes; inferred roles separately marked",
                "model": p["model"],
                "components": p["components"],
                "graph": p["graph"],
                "sourceSha256": source_sha,
            },
        )
        devices = [d for d in p["devices"] if not targets or d["profile"]["id"] in targets]
        if not devices:
            raise ValueError("no matching targets for " + str(path))
        for d in devices:
            try:
                schedule_device(p, d, RecordingAnchor())
            except ValueError as exc:
                skipped.append(
                    {"project": str(path), "device": d["profile"]["id"], "reason": str(exc)}
                )
                continue
            # One solved layout per independent request first; reused scenes do
            # not become hundreds of duplicate training examples.
            selected = []
            seen = set()
            ordered = sorted(
                d["layouts"],
                key=lambda l: (
                    len(states[l["state"]].get("sourceTrace", states[l["state"]].get("trace", []))),
                    l["state"],
                ),
            )
            by_kind = {}
            for l in ordered:
                meta = l.get("adaptationPlan", {}).get("layoutProposal")
                if not meta or l.get("layoutRequest", {}).get("kind") == "reuse":
                    continue
                if meta["key"] in seen:
                    continue
                seen.add(meta["key"])
                by_kind.setdefault(
                    l.get("layoutRequest", {}).get("kind", "full-reflow"), []
                ).append(l)
            while len(selected) < max_states and any(by_kind.values()):
                for kind in sorted(by_kind):
                    if by_kind[kind] and len(selected) < max_states:
                        selected.append(by_kind[kind].pop(0))
            for l in selected:
                plan = l["adaptationPlan"]
                meta = plan["layoutProposal"]
                request = meta["request"]
                if not request["groups"]:
                    continue
                source_layout = source_layouts.get(l["state"])
                if source_layout is None:
                    skipped.append(
                        {
                            "project": str(path),
                            "state": l["state"],
                            "reason": "source state missing",
                        }
                    )
                    continue
                try:
                    actual = actual_boxes(p, l, plan["groups"])
                    answer = {
                        "schema": SCHEMA,
                        "groups": {
                            a: normalized(actual[g], request["safeRegion"])
                            for a, g in meta["mapping"].items()
                        },
                    }
                    validate_answer(answer, request)
                except (KeyError, ValueError) as exc:
                    skipped.append({"project": str(path), "state": l["state"], "reason": str(exc)})
                    continue
                sample_id = digest([source_sha, meta["key"]])[:24]
                if sample_id in sample_ids:
                    continue
                src = "images/" + sample_id + "-source.png"
                dst = "images/" + sample_id + "-target.png"
                sr = render(p, p["sourceDevice"], source_layout, path.parent / "assets", out / src)
                tr = render(p, d, l, path.parent / "assets", out / dst)
                state = states[l["state"]]
                annotation = extract(p, source_layout, plan, state)
                from .space import scene_space

                annotation["compositionLevel"]["scene_space"] = scene_space(
                    out / src, annotation, request["sourceViewport"]
                )
                annotation_file = "annotations/" + sample_id + ".json"
                write_json(out / annotation_file, annotation)
                group_index = {g["id"]: g for g in plan["groups"]}

                def leaf_ids(gid):
                    return [
                        c
                        for m in group_index[gid]["members"]
                        for c in (leaf_ids(m) if m in group_index else [m])
                    ]

                owners = {cid: a for a, g in meta["mapping"].items() for cid in leaf_ids(g)}
                features = [
                    {
                        "id": "e" + str(i),
                        "group": owners.get(e["component"]),
                        "tag": e["tag"],
                        "semantic": e["semantic"]["label"],
                        "semanticOrigin": e["semantic"]["origin"],
                        "sourceBox": normalized(
                            e["sourceBox"],
                            {
                                "w": request["sourceViewport"]["width"],
                                "h": request["sourceViewport"]["height"],
                            },
                        ),
                    }
                    for i, e in enumerate(annotation["elementLevel"]["elements"])
                    if owners.get(e["component"]) and e["alpha"] > 0
                ]
                input_layout = {
                    "schema": SCHEMA,
                    "groups": answer["groups"],
                    "element_coordinate_system": "source_canvas_normalized_1000",
                    "group_coordinate_system": "target_safe_region_normalized_1000",
                    "elementLevel": features[:128],
                    "elementCount": len(features),
                    "elementFeaturesTruncated": len(features) > 128,
                    "semanticLevel": [
                        {"id": g["id"], "roles": g["roles"]} for g in request["groups"]
                    ],
                    "compositionLevel": {
                        "kind": request["requestKind"],
                        "mediaPolicy": request["mediaPolicy"],
                        "scene_space": {
                            k: v
                            for k, v in annotation["compositionLevel"]["scene_space"].items()
                            if k != "grid"
                        },
                    },
                }
                write_json(
                    out / ("layouts/" + sample_id + "-input.json"),
                    {"constraints": request, "layoutJson": input_layout},
                )
                row = {
                    "id": sample_id,
                    "schema": SCHEMA,
                    "family": family,
                    "split": split,
                    "sample_id": sample_id,
                    "theme_id": source_sha,
                    "scene_id": l["state"],
                    "source_resolution": request["sourceViewport"],
                    "target_resolution": d["profile"]["resolutionPx"],
                    "coordinate_system": "normalized_1000; source_canvas for elements, target_safe_region for group targets",
                    "sourceSha256": source_sha,
                    "project": str(path),
                    "projectSha256": before,
                    "device": d["profile"]["id"],
                    "state": l["state"],
                    "requestKey": meta["key"],
                    "request": request,
                    "sourceImage": src,
                    "targetImage": dst,
                    "rendering": {"source": sr, "target": tr},
                    "annotations": annotation_file,
                    "inputLayout": input_layout,
                    "elementIndex": {
                        "e" + str(i): e["source_node_id"]
                        for i, e in enumerate(annotation["elementLevel"]["elements"])
                    },
                    "groupMap": meta["mapping"],
                    "labels": {
                        "script": script_file,
                        "trace": state.get("sourceTrace", state.get("trace", [])),
                        "runtimeRows": source_layout["rows"],
                        "controls": source_layout.get("controlValues", {}),
                        "timeMs": source_layout.get("timeMs"),
                        "coverage": p["graph"].get("coverage", {}),
                    },
                    "label": {
                        "status": "weak",
                        "origin": "anchor teacher; not designer ground truth",
                        "answer": answer,
                    },
                }
                with (out / "samples.jsonl").open("a", encoding="utf8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                sample_ids.add(sample_id)
                total += 1
                splits[split] += 1
                if len(previews) < 50:
                    previews.append(row)
        if file_sha(path) != before:
            raise RuntimeError("input project changed during dataset build")
        projects.append(
            {
                "path": str(path),
                "sha256": before,
                "sourceSha256": source_sha,
                "family": family,
                "split": split,
            }
        )
        checkpoint("building")
    manifest = checkpoint("complete")
    write_json(
        out / "split-manifest.json",
        {
            "families": {p["family"]: p["split"] for p in projects},
            "policy": manifest["splitPolicy"],
        },
    )
    from .review_ui import build_review

    build_review(out, previews)
    from .quality import audit

    audit(out, out / "quality")
    return manifest


def import_reviews(directory, review_path):
    directory = Path(directory)
    batch = read_json(review_path)
    samples = {r["id"]: r for r in read_samples(directory)}
    if batch.get("schema") != SCHEMA or not batch.get("reviewer", "").strip():
        raise ValueError("schema and reviewer required")
    existing = []
    if (directory / "reviews.jsonl").exists():
        existing = [
            json.loads(line)
            for line in (directory / "reviews.jsonl").read_text(encoding="utf8").splitlines()
            if line
        ]
    accepted = []
    seen = set()
    for row in batch.get("reviews", []):
        sid = row["id"]
        if sid not in samples or sid in seen:
            raise ValueError("unknown or duplicate sample ID")
        seen.add(sid)
        sample = samples[sid]
        if row.get("requestKey") != sample["requestKey"] or row.get("status") not in {
            "approved",
            "rejected",
        }:
            raise ValueError("stale review or invalid status")
        if row["status"] == "approved":
            validate_answer(row.get("answer"), sample["request"])
        validate_annotation_edits(row.get("annotationEdits", {}), sample)
        accepted.append(
            {
                **row,
                "reviewer": batch["reviewer"],
                "origin": "human-reviewed",
                "reviewSequence": len(existing) + len(accepted) + 1,
            }
        )
    with (directory / "reviews.jsonl").open("a", encoding="utf8") as stream:
        for row in accepted:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    return {"imported": len(accepted)}


def reviewed(directory):
    path = Path(directory) / "reviews.jsonl"
    if not path.exists():
        return {}
    return {
        r["id"]: r
        for r in (json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line)
    }


def validate_annotation_edits(edits, sample):
    if not isinstance(edits, dict) or set(edits) - {
        "elementSemantics",
        "groupSemantics",
        "sceneIntent",
    }:
        raise ValueError("invalid annotation edit fields")
    for key, known in [
        (
            "elementSemantics",
            {e["id"] for e in sample.get("inputLayout", {}).get("elementLevel", [])},
        ),
        ("groupSemantics", {g["id"] for g in sample["request"]["groups"]}),
    ]:
        values = edits.get(key, {})
        if not isinstance(values, dict) or set(values) - known:
            raise ValueError("unknown annotation element/group ID")
        if any(not isinstance(v, str) or not v.strip() or len(v) > 120 for v in values.values()):
            raise ValueError("semantic labels must be short nonempty strings")
    if "sceneIntent" in edits and (
        not isinstance(edits["sceneIntent"], str) or len(edits["sceneIntent"]) > 500
    ):
        raise ValueError("invalid scene intent")


def input_layout(sample, review=None):
    layout = copy.deepcopy(sample.get("inputLayout", {}))
    edits = (review or {}).get("annotationEdits", {})
    validate_annotation_edits(edits, sample)
    for e in layout.get("elementLevel", []):
        if e["id"] in edits.get("elementSemantics", {}):
            e.update(
                semantic=edits["elementSemantics"][e["id"]],
                semanticOrigin="human-reviewed source semantics",
            )
    for g in layout.get("semanticLevel", []):
        if g["id"] in edits.get("groupSemantics", {}):
            g.update(
                semantic=edits["groupSemantics"][g["id"]],
                semanticOrigin="human-reviewed group semantics",
            )
    if edits.get("sceneIntent"):
        layout.setdefault("compositionLevel", {}).update(
            sceneIntent=edits["sceneIntent"], intentOrigin="human-reviewed source intent"
        )
    return layout


def export_sft(
    directory,
    out,
    *,
    include_weak=False,
    include_synthetic=False,
    allow_incomplete=False,
    image_root=None,
    input_images="source",
):
    directory = Path(directory).resolve()
    out = Path(out).resolve()
    if out.exists():
        raise ValueError("use a new SFT output directory")
    from .catalog import Catalog

    catalog = Catalog(directory)
    out.mkdir(parents=True)
    counts = Counter()
    excluded = Counter()
    family_splits = {}
    streams = {
        split: (out / (split + ".jsonl")).open("w", encoding="utf8")
        for split in ("train", "validation", "test")
    }
    index_stream = (out / "sample-index.jsonl").open("w", encoding="utf8")
    try:
        for sample in read_samples(directory):
            split = sample["split"]
            family = sample["family"]
            if split not in streams or family in family_splits and family_splits[family] != split:
                raise ValueError("family split leakage")
            family_splits[family] = split
            review = catalog.sample(sample["id"]).get("latestReview")
            if review and review["status"] == "rejected":
                excluded["rejected"] += 1
                continue
            synthetic = sample["label"]["status"] == "synthetic" and include_synthetic
            if not review and not include_weak and not synthetic:
                excluded["unreviewed"] += 1
                continue
            if not allow_incomplete and any(
                r["status"] == "incomplete" for r in sample["rendering"].values()
            ):
                excluded["incompleteRender"] += 1
                continue
            answer = review["answer"] if review else sample["label"]["answer"]
            validate_answer(answer, sample["request"])
            names = [sample["sourceImage"]] + (
                [sample["targetImage"]] if input_images == "source-and-current" else []
            )
            local_images = [(directory / name).resolve() for name in names]
            if any(
                not path.is_relative_to(directory) or not path.is_file() for path in local_images
            ):
                excluded["missingOrInvalidInputImage"] += 1
                continue
            images = [
                (Path(image_root) / name).as_posix() if image_root else str(directory / name)
                for name in names
            ]
            example = {
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {
                        "role": "user",
                        "content": prompt(
                            sample["request"], input_layout(sample, review), len(images)
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": json.dumps(answer, ensure_ascii=False, separators=(",", ":")),
                    },
                ],
                "images": images,
            }
            streams[split].write(json.dumps(example, ensure_ascii=False) + "\n")
            counts[split] += 1
            index_stream.write(
                json.dumps(
                    {
                        "id": sample["id"],
                        "family": family,
                        "split": split,
                        "origin": (
                            "human-reviewed"
                            if review
                            else "synthetic" if synthetic else "weak-teacher"
                        ),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    finally:
        for stream in streams.values():
            stream.close()
        index_stream.close()
    result = {
        "counts": dict(counts),
        "excluded": dict(excluded),
        "includeWeak": include_weak,
        "includeSynthetic": include_synthetic,
        "allowIncomplete": allow_incomplete,
        "inputImages": input_images,
        "sampleIndex": "sample-index.jsonl",
        "familySplits": family_splits,
    }
    write_json(out / "manifest.json", result)
    return result


def import_pairs(directory, manifest):
    """A desired tablet image is annotation evidence, never leaked as input.

    Pair JSONs use the existing sample ID/group correspondence from the script.
    This avoids guessing element identity from unrelated screenshots.
    """
    directory = Path(directory).resolve()
    manifest = Path(manifest).resolve()
    batch = read_json(manifest)
    rows = list(read_samples(directory))
    index = {s["id"]: s for s in rows}
    reviews = []
    if not batch.get("reviewer", "").strip():
        raise ValueError("pair reviewer required")
    for pair in batch.get("pairs", []):
        s = index[pair["id"]]
        if pair.get("requestKey") != s["requestKey"]:
            raise ValueError("pair request mismatch")
        answer = read_json(manifest.parent / pair["layoutJson"])
        validate_answer(answer, s["request"])
        image = (manifest.parent / pair["targetReferenceImage"]).resolve()
        from PIL import Image

        with Image.open(image) as im:
            im.verify()
        name = "references/" + s["id"] + image.suffix.lower()
        (directory / "references").mkdir(exist_ok=True)
        shutil.copyfile(image, directory / name)
        s["targetReferenceImage"] = name
        reviews.append(
            {
                "id": s["id"],
                "requestKey": s["requestKey"],
                "status": "approved",
                "answer": answer,
                "notes": pair.get("notes", "paired image/layout"),
                "targetReferenceImage": name,
            }
        )
    # All pairs validate before changing labels.
    temporary = directory / "pair-review-import.json"
    write_json(temporary, {"schema": SCHEMA, "reviewer": batch["reviewer"], "reviews": reviews})
    result = import_reviews(directory, temporary)
    with (directory / "samples.jsonl").open("w", encoding="utf8") as stream:
        for s in rows:
            stream.write(json.dumps(s, ensure_ascii=False, allow_nan=False) + "\n")
    from .review_ui import build_review

    build_review(directory, rows)
    return result
