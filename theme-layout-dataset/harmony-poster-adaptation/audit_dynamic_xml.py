"""Reopen exported XML and verify live slot updates under both engine inputs."""
from collections import Counter, defaultdict
from pathlib import Path
import argparse

from prepare import read, save, sha
from dynamic import PROFILES
from dynamic_contract import semantic_dependencies
from multires_app.engine.scene_engine import extract_assets, load_model
from multires_app.interaction.interaction_semantics import replay_human_state
from multires_app.learning.annotations import semantic
from multires_app.preview.paint_visibility import font, raster_snapshot, read_bitmap, runtime_elements


def node_signature(model):
    results = Counter()
    for node in model["nodes"].values():
        picked = tuple(sorted((name, value) for name, value in node["attributes"].items()
                              if name in {"format", "textExp", "string", "paras", "mapList", "srcExp"}))
        if picked:
            results[node["tag"], picked] += 1
    return results


def dynamic_elements(model, runtime, assets, viewport):
    result = defaultdict(list)
    elements = runtime_elements(runtime, assets, viewport)
    dependencies = semantic_dependencies(model)
    for e in elements:
        node = model["nodes"][e["id"]]
        kind = semantic(node, {}, dependencies)["label"].split(".")[0]
        if kind not in {"clock", "datetime", "date", "battery"}:
            continue
        text = e.get("text", "")
        glyph_width = font(e["size"]).getlength(text) if text and e.get("size", 0) > 0 else None
        result[e["id"]].append({"kind": kind, "asset": e["asset"], "text": text,
                                "logicalXYWH": e["rect"], "textAdvance": glyph_width})
    return elements, result


def run(project_path, delivery, out):
    project = read(project_path)
    out.mkdir(parents=True, exist_ok=False)
    source = load_model(Path(project["source"]), project["model"]["file"])
    original_signature = node_signature(source)
    all_targets = {}
    for target in read(delivery / "delivery-summary.json")["targets"]:
        name = target["target"]
        viewport = next(d["viewport"] for d in project["devices"] if d["profile"]["id"] == name)
        theme = Path(target["themeDirectory"])
        root = out / name
        model = load_model(theme, project["model"]["file"])
        assets = extract_assets(theme, model, root)
        # Runtime snapshots use archive-relative names; raster reads cached paths.
        import urllib.parse
        asset_files = {key: urllib.parse.unquote(urllib.parse.urlsplit(value["url"]).path).removeprefix("assets/")
                       for key, value in assets.items()}
        missing = original_signature - node_signature(model)
        if missing:
            raise RuntimeError(f"Dynamic script attributes missing in {name}: {dict(missing)}")
        reference_trace = project["graph"]["states"][0]["trace"]
        values = {}
        for label, inputs in PROFILES.items():
            model["inputs"] = {"system": inputs}
            runtime = replay_human_state(model, viewport["width"], viewport["height"],
                                         {"trace": reference_trace}, assets, verify_hit=False)
            elements, slots = dynamic_elements(model, runtime, assets, viewport)
            for e in elements:
                e["asset"] = asset_files.get(e["asset"], "")
            rendered, paint = raster_snapshot(elements, viewport, root / "assets", max_size=(640, 640))
            picture = root / f"{label}.png"
            rendered.convert("RGB").save(picture)
            values[label] = {"input": inputs, "slots": slots,
                             "imageSha256": sha(picture),
                             "unsupportedPaintNodes": sum(not p["supported"] for p in paint.values())}
            read_bitmap.cache_clear()
        short, long = values["short"]["slots"], values["long"]["slots"]
        changed = [node for node in sorted(short.keys() & long.keys())
                   if [(e["text"], e["asset"]) for e in short[node]] !=
                      [(e["text"], e["asset"]) for e in long[node]]]
        all_targets[name] = {"portableDynamicAttributeCount": sum(original_signature.values()),
                             "changedVisibleDynamicNodeCount": len(changed),
                             "changedNodes": changed, "presets": values}
        if not changed:
            raise RuntimeError(f"Expected visible clock updates in {name}")
    save(out / "report.json", {"targets": all_targets, "scope": "startup trace in offline interpreter",
                               "nativeValidation": "not_measured"})
    for name, entry in all_targets.items():
        print(name, entry["changedVisibleDynamicNodeCount"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.project, args.delivery, args.out)
