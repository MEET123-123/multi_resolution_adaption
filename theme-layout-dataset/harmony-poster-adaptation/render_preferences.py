"""Generate preference images from real layers and preserve every XML identity."""
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
import argparse
import html
import random

import torch

from prepare import read, rows, save, sha, jsonl
from policy import decode, prepare_sample, violations
from multires_app.evaluation.report import elements
from multires_app.preview.paint_visibility import raster_snapshot, read_bitmap


@lru_cache(maxsize=1)
def load_project(path, expected_hash):
    if sha(path) != expected_hash:
        raise ValueError(f"Changed source render cache {path}")
    read_bitmap.cache_clear()
    return read(path)


def teacher_action(item):
    c = item["constants"]
    t = item["targets"]
    w, h, ax, ay, px, py, scale, mx, my = c.T
    ds = (torch.log(((t[:, 2] / w) * (t[:, 3] / h)).sqrt() / scale) /
          torch.log(torch.tensor(4.0)))
    action = torch.stack(((t[:, 0] + ax * t[:, 2] - px) / .25 * mx,
                          (t[:, 1] + ay * t[:, 3] - py) / .25 * my, ds), dim=-1)
    return torch.atanh(action.clamp(-.92, .92))


def scene_elements(bundle, item, workers):
    sample = item["sample"]
    worker = workers[sample["sourceSha256"], sample["manifest"]]
    path = Path(worker["project"])
    project = load_project(path, worker["projectSha256"])
    device = next(d for d in project["devices"] if d["profile"]["id"] == sample["device"])
    layout = next(l for l in device["layouts"] if l["state"] == sample["state"])
    evidence = read(bundle / sample["evidence"])
    layout = {**layout, "transforms": evidence["targetTransforms"]}
    tree = {g["id"]: g["members"] for g in evidence["annotations"]["compositionLevel"]["groups"]}

    def components(gid):
        return [leaf for child in tree[gid] for leaf in components(child)] if gid in tree else [gid]

    aliases = {}
    for index, gid in enumerate(item["gids"]):
        if item["mask"][index]:
            for leaf in components(evidence["groupMap"][gid]):
                if leaf in aliases:
                    raise ValueError("Overlapping movable group membership")
                aliases[leaf] = index
    rendered = elements(project, layout)
    ownership = [aliases.get(project["nodeComponents"].get(e["id"])) for e in rendered]
    return rendered, ownership, path.parent / "assets", evidence["targetViewport"]


def candidate(item, action, scene, changed):
    src, owners, assets, viewport = scene
    boxes = decode(item, action)
    current = item["targets"]
    safe = item["sample"]["request"]["safeRegion"]
    altered = []
    for element, owner in zip(src, owners):
        if owner is None or not changed[owner]:
            altered.append(element)
            continue
        x, y, w, h = current[owner].tolist()
        u, v, W, H = boxes[owner].tolist()
        scale = min(W / w, H / h)
        rect = element["rect"]
        altered.append({**element, "size": element["size"] * scale,
                        "rect": {"x": safe["x"] + u * safe["w"] +
                                      (rect["x"] - safe["x"] - x * safe["w"]) * scale,
                                 "y": safe["y"] + v * safe["h"] +
                                      (rect["y"] - safe["y"] - y * safe["h"]) * scale,
                                 "w": rect["w"] * scale, "h": rect["h"] * scale}})
    image, paint = raster_snapshot(altered, viewport, assets, max_size=(360, 360))
    watched = [paint[e["key"]] for e, owner in zip(altered, owners)
               if owner is not None and changed[owner] and e["tag"] != "Button" and
               paint[e["key"]]["status"] != "transparent"]
    if not watched or any(not info["supported"] for info in paint.values()):
        return None
    visibility = sum(p["paintFraction"] for p in watched) / len(watched)
    clipping = sum(p["clipFraction"] for p in watched) / len(watched)
    realized = torch.where(changed[:, None], boxes, current)
    geometry_penalty = float(violations(realized, item))
    total = .75 * visibility + .25 * clipping - 2 * geometry_penalty
    return total, image.convert("RGB"), {"visibility": visibility, "clipping": clipping,
                                        "newOverlapAndOverflow": geometry_penalty}


def build(bundle, out, limit):
    workers = {(w["sourceSha256"], w["manifest"]): w for w in
               (read(p) for p in Path(read(bundle / "manifest.json")["workers"]).glob("*/worker.json"))}
    source = [row for row in rows(bundle / "samples.jsonl") if row["split"] == "train"]
    queues = defaultdict(list)
    rng = random.Random(31)
    rng.shuffle(source)
    for row in source:
        queues[row["family"]].append(row)
    interleaved = []
    for position in range(max(map(len, queues.values()))):
        interleaved.extend(queue[position] for queue in queues.values() if position < len(queue))
    out.mkdir(parents=True, exist_ok=False)
    (out / "images").mkdir()
    pairs, skipped = [], Counter()
    for n, sample in enumerate(interleaved):
        if len(pairs) >= limit:
            break
        item = prepare_sample(sample, bundle, supervised=True)
        source_boxes = item["features"][:, :4]
        onscreen = ((source_boxes[:, :2] >= -.015) &
                    (source_boxes[:, :2] + source_boxes[:, 2:] <= 1.015)).all(-1)
        selected = (item["mask"] & onscreen).nonzero().flatten().tolist()
        if not selected:
            skipped["no_visible_group"] += 1
            continue
        try:
            scene = scene_elements(bundle, item, workers)
            baseline = teacher_action(item)
            changed = torch.zeros(len(item["gids"]), dtype=torch.bool)
            chosen = rng.choice(selected)
            changed[chosen] = True
            reference = candidate(item, baseline, scene, changed)
            trials = [(reference, baseline)] if reference else []
            for _ in range(5):
                action = baseline.clone()
                c = item["constants"][chosen]
                sign = rng.choice((-1, 1))
                axis_options = [axis for axis in (0, 1) if c[7 + axis]]
                if axis_options:
                    action[chosen, rng.choice(axis_options)] += sign * rng.uniform(.35, 1.8)
                action[chosen, 2] += rng.uniform(-.52, .55)
                result = candidate(item, action, scene, changed)
                if result:
                    trials.append((result, action))
            if not trials:
                skipped["unsupported_render"] += 1
                continue
            good, good_action = max(trials, key=lambda t: t[0][0])
            bad, bad_action = min(trials, key=lambda t: t[0][0])
            if good[0] - bad[0] < .035:
                skipped["ambiguous_order"] += 1
                continue
            name = sample["id"]
            paths = [f"images/{name}-{kind}.png" for kind in ("preferred", "rejected")]
            for result, target_path in zip((good, bad), paths):
                result[1].save(out / target_path)
            pairs.append({"id": name, "family": sample["family"], "split": "train",
                          "preferred": good_action.tolist(), "rejected": bad_action.tolist(),
                          "preferredScore": good[0], "rejectedScore": bad[0],
                          "preferredMetrics": good[2], "rejectedMetrics": bad[2],
                          "changed": changed.tolist(), "images": paths,
                          "origin": "offline render metrics, machine preference"})
            if len(pairs) % 10 == 0:
                print(f"{len(pairs)} image preference pairs from {n + 1} scenes", flush=True)
        except (KeyError, ValueError) as exc:
            skipped[str(exc)[:140]] += 1
        finally:
            if n % 8 == 0:
                read_bitmap.cache_clear()
    jsonl(out / "pairs.jsonl", pairs)
    save(out / "manifest.json", {"pairs": len(pairs), "skipped": skipped,
                                 "origin": "rendered variants from original theme layers",
                                 "native": False, "humanPreference": False, "split": "train"})
    cards = ["<section><p>" + html.escape(p["id"]) + f" · {p['preferredScore']:.3f} / {p['rejectedScore']:.3f}</p>" +
             "".join(f'<img width="270" loading="lazy" src="{path}">' for path in p["images"]) +
             "</section>" for p in pairs]
    (out / "gallery.html").write_text("<!doctype html><meta charset=utf-8><title>布局偏好样本</title>"
        "<style>body{font:16px system-ui;margin:24px}main{display:flex;flex-wrap:wrap;gap:18px}"
        "img{object-fit:contain;max-height:300px;background:#20242b}section{padding:12px;background:#edf0f5}</style>"
        "<h1>原主题素材重渲染</h1><p>左为规则偏好样本，右为对照样本。</p><main>" +
        "".join(cards) + "</main>", encoding="utf8")
    print(f"Saved {len(pairs)} pairs. {dict(skipped)}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=64)
    args = parser.parse_args()
    build(args.bundle, args.out, args.limit)
