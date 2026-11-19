"""MOCK embedding: hashed bag-of-words (feature hashing with random signs), L2-normalised.
Deterministic and dependency-free, so nearest-neighbour discovery works in the mock vector store.
ASSUMPTION: a real vector store uses a learned embedding model; there, nearest-neighbour probing is weaker and
must be treated as a candidate generator only (this code already does: probabilistic hits are flagged)."""
import hashlib
import re

import numpy as np

DIM = 512
_SPLIT = re.compile(r"[\s,.;:!?()\[\]{}\"'<>|/\\\-]+")


def toks(text):
    return [t for t in _SPLIT.split(str(text).casefold()) if t]


def embed(text):
    v = np.zeros(DIM, dtype=np.float32)
    for t in toks(text):
        h = hashlib.md5(t.encode("utf-8")).digest()
        v[int.from_bytes(h[:4], "big") % DIM] += 1.0 if h[4] & 1 else -1.0
    n = float(np.linalg.norm(v))
    return v / n if n else v


def cosine(a, b):
    return float(np.dot(a, b))
