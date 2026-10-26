"""A file-based annotation UI: no server or annotation platform dependency."""

import json
from pathlib import Path


def review_document(samples):
    light = [
        {
            k: s[k]
            for k in (
                "id",
                "device",
                "state",
                "split",
                "requestKey",
                "request",
                "sourceImage",
                "targetImage",
                "targetReferenceImage",
                "rendering",
                "label",
                "inputLayout",
                "elementIndex",
                "baseRevision",
                "latestReview",
            )
            if k in s
        }
        for s in samples
    ]
    template = Path(__file__).with_name("review.html").read_text(encoding="utf8")
    payload = (
        json.dumps(light, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return template.replace("__SAMPLES__", payload)


def build_review(directory, samples):
    (Path(directory) / "review.html").write_text(review_document(samples), encoding="utf8")
