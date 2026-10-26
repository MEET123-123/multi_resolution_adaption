"""Native bitmap-glyph scaling through generated resource families.

ImageSeries/bitmap Time do not support width/height in the legacy engine.
Ordinary Image/Video use script geometry; never apply both methods to a node.
"""

import hashlib
import io
import json
import posixpath
from pathlib import Path
from multires_app.engine.scene_engine import resolve_asset


def glyph_nodes(project):
    if "_glyphNodes" in project:
        return project["_glyphNodes"]
    model = project["model"]
    result = {}
    for uid, node in model["nodes"].items():
        a = node["attributes"]
        tag = node["tag"]
        if tag not in {"ImageSeries", "Time"} or not a.get("src") or a.get("srcExp"):
            continue
        asset = resolve_asset({**model, "assetNames": list(project["assets"])}, a["src"])
        base, ext = posixpath.splitext(asset)
        family = [
            name
            for name in project["assets"]
            if name.startswith(base + "_") and posixpath.splitext(name)[1] == ext
        ]
        if family:
            result[uid] = (asset, family)
    project["_glyphNodes"] = result
    return result


def glyph_components(project):
    return {
        project["nodeComponents"][uid]
        for uid in glyph_nodes(project)
        if uid in project["nodeComponents"]
    }


def generate_glyph_resources(project, device, script_path):
    from PIL import Image

    remaps = {}
    files = {}
    warnings = []
    model = project["model"]
    # Scripts normally live in scenes/scripts. Live exports use a nested
    # directory; locate the actual original assets without relying on cwd.
    assets_folder = next(
        (
            p / "assets"
            for p in [script_path.parent, *script_path.parents]
            if (p / "assets").is_dir()
        ),
        None,
    )
    for uid, (asset, family) in glyph_nodes(project).items():
        cid = project["nodeComponents"].get(uid)
        visible = [
            l
            for l in device["layouts"]
            if any(project["nodeOrder"][r[0]] == uid for r in l["rows"])
        ]
        scales = {round(l["transforms"].get(cid, {"sx": 1})["sx"], 9) for l in visible}
        if not scales or scales == {1.0}:
            continue
        if len(scales) != 1:
            warnings.append(
                uid + ": bitmap glyph scale varies by state; native family cannot be resized once"
            )
            continue
        scale = next(iter(scales))
        if not assets_folder:
            warnings.append(uid + ": original bitmap glyph asset folder unavailable")
            continue
        token = hashlib.sha256((asset + ":" + str(scale)).encode()).hexdigest()[:16]
        prefix = posixpath.join(
            posixpath.dirname(model["file"]), "_multires", device["profile"]["id"], token, "glyph"
        )
        original_base, ext = posixpath.splitext(asset)
        for name in family:
            new_name = prefix + name[len(original_base) :]
            destination = script_path.parent / "resource-assets" / new_name
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(assets_folder / name) as im:
                    size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
                    resized = im.resize(size, Image.Resampling.LANCZOS)
                    resized.save(destination)
            with Image.open(destination) as im:
                width, height = im.size
            data = destination.read_bytes()
            files[new_name] = {
                "file": str(destination.resolve()),
                "width": width,
                "height": height,
                "kind": "image",
                "source": name,
                "scale": scale,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        remaps[uid] = posixpath.relpath(prefix + ext, posixpath.dirname(model["file"]))
    manifest = {
        "policy": "bitmap-glyph-resource-family",
        "target": device["profile"]["id"],
        "remaps": remaps,
        "files": files,
        "warnings": warnings,
        "rounding": "Integer bitmap dimensions; at most 0.5 pixel per glyph dimension before device density scaling.",
    }
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.with_suffix(".resources.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def manifest_resources(script_path):
    path = script_path.with_suffix(".resources.json")
    return (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"files": {}, "warnings": []}
    )
