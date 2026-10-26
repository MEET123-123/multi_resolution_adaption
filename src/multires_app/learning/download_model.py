"""Download an official HF snapshot with revision and LFS SHA checks."""

import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path


def download(model, out, revision="main", weights_hub="huggingface"):
    root = Path(out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(
        "https://huggingface.co/api/models/" + model + "/revision/" + revision + "?blobs=true",
        timeout=60,
    ) as r:
        info = json.load(r)
    commit = info["sha"]
    files = []
    for row in info["siblings"]:
        name = row["rfilename"]
        if not name.endswith((".json", ".safetensors", ".txt", ".model", ".jinja", ".md")):
            continue
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("unsafe hub filename")
        path.parent.mkdir(parents=True, exist_ok=True)
        lfs = row.get("lfs") or {}
        expected = lfs.get("sha256")
        size = row.get("size")

        def valid():
            if not path.is_file() or size is not None and path.stat().st_size != size:
                return False
            if expected:
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                        return False
            return True

        if not valid():
            partial = path.with_name(path.name + ".part")
            for attempt in range(4):
                try:
                    offset = partial.stat().st_size if partial.exists() else 0
                    url = "https://huggingface.co/" + model + "/resolve/" + commit + "/" + name
                    direct = name.endswith(".safetensors") and weights_hub == "modelscope"
                    if direct:
                        url = (
                            "https://www.modelscope.cn/models/" + model + "/resolve/master/" + name
                        )
                    req = urllib.request.Request(
                        url, headers={"Range": "bytes=" + str(offset) + "-"} if offset else {}
                    )
                    opener = (
                        urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        if direct
                        else urllib.request.build_opener()
                    )
                    with opener.open(req, timeout=45) as response:
                        append = offset and response.status == 206
                        with partial.open("ab" if append else "wb") as stream:
                            received = offset if append else 0
                            last = received
                            while chunk := response.read(1024 * 1024):
                                stream.write(chunk)
                                received += len(chunk)
                                if received - last >= 128 * 1024 * 1024:
                                    print(name, round(received / 2**20), "MiB", flush=True)
                                    last = received
                    partial.replace(path)
                    if not valid():
                        raise ValueError("download size/hash mismatch: " + name)
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(2)
        print(name, path.stat().st_size, flush=True)
        files.append({"file": name, "size": path.stat().st_size, "sha256": expected})
    (root / "snapshot-manifest.json").write_text(
        json.dumps({"model": model, "revision": commit, "files": files}, indent=2), encoding="utf8"
    )
    return commit


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="Qwen/Qwen3-VL-2B-Instruct")
    p.add_argument("--revision", default="main")
    p.add_argument("--out", required=True)
    p.add_argument("--weights-hub", choices=["huggingface", "modelscope"], default="huggingface")
    a = p.parse_args()
    print("revision:", download(a.model, a.out, a.revision, a.weights_hub))


if __name__ == "__main__":
    main()
