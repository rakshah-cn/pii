"""Layer 7: append-only, hash-chained audit log.

hash_i = SHA256( hash_{i-1} + canonical_json(record_i) ).  Editing, deleting or reordering any earlier
record breaks every later hash, which verify_chain() detects.
Borrower ids are stored only as a KEYED hash (HMAC-SHA256), so the audit log is not a copy of the data.

LIMITS (honest): a tamperer who can rewrite the WHOLE file and recompute every hash defeats a bare chain.
In production anchor the latest hash somewhere the writer cannot edit (WORM storage / external timestamp).
`head()` returns the value to anchor.
"""
import hashlib
import hmac
import json
import os
import time

GENESIS = "0" * 64


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class AuditLog:
    def __init__(self, path="data/stores/audit_log.jsonl", key=None, key_path="data/stores/.audit_key"):
        self.path = path
        self.key = key or self._load_key(key_path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if not os.path.exists(path):
            open(path, "a").close()

    @staticmethod
    def _load_key(key_path):
        k = os.environ.get("AUDIT_HMAC_KEY")
        if k:
            return k.encode()
        if not os.path.exists(key_path):                      # hackathon default: random key on disk, mode 600
            with open(key_path, "wb") as f:
                f.write(os.urandom(32))
            os.chmod(key_path, 0o600)
        return open(key_path, "rb").read()

    def hash_borrower(self, borrower_id):
        return hmac.new(self.key, str(borrower_id).encode(), hashlib.sha256).hexdigest()

    def records(self):
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def head(self):
        recs = self.records()
        return recs[-1]["hash"] if recs else GENESIS

    def append(self, record):
        """Add a record. `record` must not contain raw personal data (callers pass ids/counts only)."""
        recs = self.records()
        prev = recs[-1]["hash"] if recs else GENESIS
        body = dict(record, seq=len(recs), ts=round(time.time(), 3), prev_hash=prev)
        body["hash"] = hashlib.sha256((prev + _canon(body)).encode()).hexdigest()
        with open(self.path, "a", encoding="utf-8") as f:           # append only: no update/delete API exists
            f.write(json.dumps(body, ensure_ascii=False) + "\n")
        return body

    def verify_chain(self):
        """Returns (ok, first_bad_seq_or_None, reason)."""
        prev = GENESIS
        for i, r in enumerate(self.records()):
            h = r.get("hash")
            body = {k: v for k, v in r.items() if k != "hash"}
            if r.get("seq") != i:
                return False, i, "sequence_gap_or_reorder"
            if r.get("prev_hash") != prev:
                return False, i, "prev_hash_mismatch"
            if hashlib.sha256((prev + _canon(body)).encode()).hexdigest() != h:
                return False, i, "record_altered"
            prev = h
        return True, None, "chain_intact"
