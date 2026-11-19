"""StoreAdapter interface + file-backed MOCK implementations.

THE REAL STORE LAYOUT IS UNKNOWN TO US. To plug in the real stores, implement StoreAdapter for each one
(records / delete / count, and nearest for a vector store) and register them in StoreCatalog. The erasure agent only
talks to this interface.
"""
import glob
import hashlib
import json
import os
from abc import ABC, abstractmethod

import numpy as np

from .embed import DIM


class StoreAdapter(ABC):
    name: str
    kind: str                # 'logs' | 'vector_store' | 'eval_sets' | 'other'
    registered: bool = True

    @abstractmethod
    def records(self):
        """yield (record_id, text_for_scanning, meta_dict). text_for_scanning excludes embedding vectors."""

    @abstractmethod
    def delete(self, ids) -> int: ...

    @abstractmethod
    def count(self) -> int: ...

    def has(self, rid) -> bool:
        return any(i == rid for i, _, _ in self.records())

    def put(self, record):
        raise NotImplementedError

    def nearest(self, vec, k=50, threshold=0.3):
        """[(id, score)] for stores that hold embeddings; default: none."""
        return []

    def embeddings(self):
        """[(id, vector)] for rows that hold an embedding (the vault scores them; the query vector never leaves the vault)."""
        return []

    has_embeddings = False


def legacy_id(stem, line):
    """Stable id for rows without an 'id' field: content hash, so ids survive deletions of other rows."""
    return f"{stem}#{hashlib.sha1(line.strip().encode('utf-8')).hexdigest()[:8]}"


class JsonlStore(StoreAdapter):
    """One JSON object per line. Ids come from the 'id' field, or '<file>#<line>' when absent (legacy dumps)."""

    def __init__(self, path, name, kind="other", registered=True):
        self.path, self.name, self.kind, self.registered = path, name, kind, registered
        self._load()

    def _load(self):
        self.rows = []
        if os.path.exists(self.path):
            stem = os.path.splitext(os.path.basename(self.path))[0]
            for line in open(self.path, encoding="utf-8"):
                if line.strip():
                    r = json.loads(line)
                    r.setdefault("_rid", legacy_id(stem, line))
                    r.setdefault("id", r["_rid"])
                    self.rows.append(r)
        self.has_embeddings = any("embedding" in r for r in self.rows)

    def _flush(self):
        with open(self.path, "w", encoding="utf-8") as f:
            for r in self.rows:
                out = {k: v for k, v in r.items() if k != "_rid" and not (k == "id" and r.get("_rid") == v)}
                f.write(json.dumps(out, ensure_ascii=False) + "\n")

    def records(self):
        for r in self.rows:
            body = {k: v for k, v in r.items() if k not in ("embedding", "_rid")}
            yield r["id"], json.dumps(body, ensure_ascii=False), body

    def count(self):
        return len(self.rows)

    def put(self, record):
        self.rows.append(record)
        self.has_embeddings = self.has_embeddings or "embedding" in record
        self._flush()

    def delete(self, ids):
        ids = set(ids)
        before = len(self.rows)
        self.rows = [r for r in self.rows if r["id"] not in ids]
        self._flush()
        return before - len(self.rows)

    def embeddings(self):
        return [(r["id"], r["embedding"]) for r in self.rows if "embedding" in r]

    def nearest(self, vec, k=50, threshold=0.3):
        rows = [r for r in self.rows if "embedding" in r]
        if not rows:
            return []
        M = np.asarray([r["embedding"] for r in rows], dtype=np.float32)
        if M.shape[1] != DIM:
            return []
        s = M @ vec
        order = np.argsort(-s)[:k]
        return [(rows[i]["id"], float(s[i])) for i in order if s[i] >= threshold]


class StoreCatalog:
    """Registered stores (what the platform knows about) + discovery of stores nobody registered."""
    SKIP = {"registry.json", "audit_log.jsonl", "rejections.jsonl", "truth.json", "borrowers.json"}

    def __init__(self, root):
        self.root = root
        self.registered = [
            JsonlStore(os.path.join(root, "logs.jsonl"), "logs", "logs"),
            JsonlStore(os.path.join(root, "vectors.jsonl"), "vector_store", "vector_store"),
            JsonlStore(os.path.join(root, "eval_sets.jsonl"), "eval_sets", "eval_sets"),
        ]

    def discover_unregistered(self):
        known = {os.path.abspath(a.path) for a in self.registered}
        out = []
        for p in sorted(glob.glob(os.path.join(self.root, "**", "*.jsonl"), recursive=True)):
            if os.path.abspath(p) in known or os.path.basename(p) in self.SKIP:
                continue
            rel = os.path.relpath(p, self.root)
            out.append(JsonlStore(p, "unmapped:" + rel, "other", registered=False))
        return out

    def all(self):
        return self.registered + self.discover_unregistered()
