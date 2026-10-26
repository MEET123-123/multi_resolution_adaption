"""Indexed, reusable annotation cache. No script parser on this code path."""

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from .contract import SCHEMA, validate_answer
from .dataset import validate_annotation_edits


class Catalog:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.database = self.root / "annotation-index.sqlite3"
        self.lock = threading.RLock()
        with closing(self.connect()) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY,value TEXT);"
                "CREATE TABLE IF NOT EXISTS samples (id TEXT PRIMARY KEY,offset INTEGER,split TEXT,family TEXT,device TEXT,state TEXT);"
                "CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY,sequence INTEGER,payload TEXT);"
                "CREATE INDEX IF NOT EXISTS sample_split ON samples(split);"
            )
        self.refresh()

    def connect(self):
        return sqlite3.connect(self.database, timeout=30)

    def refresh(self):
        with self.lock, closing(self.connect()) as db:
            for kind, file in [("samples", "samples.jsonl"), ("reviews", "reviews.jsonl")]:
                path = self.root / file
                sig = (
                    str((path.stat().st_size, path.stat().st_mtime_ns))
                    if path.exists()
                    else "missing"
                )
                old = db.execute("SELECT value FROM metadata WHERE name=?", (kind,)).fetchone()
                if old and old[0] == sig:
                    continue
                db.execute("DELETE FROM " + kind)
                if path.exists():
                    with path.open("rb") as stream:
                        while True:
                            offset = stream.tell()
                            line = stream.readline()
                            if not line:
                                break
                            row = json.loads(line)
                            if kind == "samples":
                                db.execute(
                                    "INSERT INTO samples VALUES (?,?,?,?,?,?)",
                                    (
                                        row["id"],
                                        offset,
                                        row["split"],
                                        row["family"],
                                        row["device"],
                                        row["state"],
                                    ),
                                )
                            else:
                                db.execute(
                                    "INSERT OR REPLACE INTO reviews VALUES (?,?,?)",
                                    (row["id"], row["reviewSequence"], line.decode("utf8")),
                                )
                db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (kind, sig))
            db.commit()

    def sample(self, identity):
        with closing(self.connect()) as db:
            item = db.execute("SELECT offset FROM samples WHERE id=?", (identity,)).fetchone()
            if not item:
                raise ValueError("unknown sample ID")
            review = db.execute(
                "SELECT sequence,payload FROM reviews WHERE id=?", (identity,)
            ).fetchone()
        with (self.root / "samples.jsonl").open("rb") as stream:
            stream.seek(item[0])
            row = json.loads(stream.readline())
        row["baseRevision"] = review[0] if review else 0
        if review:
            row["latestReview"] = json.loads(review[1])
        return row

    def page(self, offset=0, limit=50, split="", pending=False):
        if not 0 <= offset or not 1 <= limit <= 500:
            raise ValueError("invalid page budget")
        if split not in {"", "train", "validation", "test"}:
            raise ValueError("invalid split")
        with self.lock:
            self.refresh()
            where = []
            values = []
            if split:
                where.append("s.split=?")
                values.append(split)
            if pending:
                where.append("r.id IS NULL")
            condition = " WHERE " + " AND ".join(where) if where else ""
            base = " FROM samples s LEFT JOIN reviews r ON s.id=r.id" + condition
            with closing(self.connect()) as db:
                total = db.execute("SELECT count(*)" + base, values).fetchone()[0]
                ids = [
                    r[0]
                    for r in db.execute(
                        "SELECT s.id" + base + " ORDER BY s.rowid LIMIT ? OFFSET ?",
                        (*values, limit, offset),
                    )
                ]
            return {
                "total": total,
                "offset": offset,
                "limit": limit,
                "samples": [self.sample(sid) for sid in ids],
            }

    def append(self, batch):
        if batch.get("schema") != SCHEMA or not batch.get("reviewer", "").strip():
            raise ValueError("schema and reviewer required")
        rows = batch.get("reviews")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise ValueError("batch must contain 1..500 reviews")
        with self.lock:
            self.refresh()
            accepted = []
            seen = set()
            with closing(self.connect()) as db:
                seq = db.execute("SELECT COALESCE(MAX(sequence),0) FROM reviews").fetchone()[0]
            for r in rows:
                sample = self.sample(r["id"])
                if r["id"] in seen:
                    raise ValueError("duplicate sample ID")
                seen.add(r["id"])
                if r.get("baseRevision") != sample["baseRevision"]:
                    raise RuntimeError("stale annotation revision; reload the sample")
                if r.get("requestKey") != sample["requestKey"] or r.get("status") not in {
                    "approved",
                    "rejected",
                }:
                    raise ValueError("stale request or invalid status")
                if r["status"] == "approved":
                    validate_answer(r.get("answer"), sample["request"])
                validate_annotation_edits(r.get("annotationEdits", {}), sample)
                seq += 1
                accepted.append(
                    {
                        **r,
                        "reviewer": batch["reviewer"],
                        "origin": "human-reviewed",
                        "reviewSequence": seq,
                    }
                )
            with (self.root / "reviews.jsonl").open("a", encoding="utf8") as stream:
                for r in accepted:
                    stream.write(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n")
            self.refresh()
            return {
                "imported": len(accepted),
                "revisions": {r["id"]: r["reviewSequence"] for r in accepted},
            }
