"""Layer 7: append-only, hash-chained audit log.

hash_i = SHA256( hash_{i-1} + canonical_json(record_i) ).  Editing, deleting or reordering any earlier
record breaks every later hash, which verify_chain() detects.
Borrower ids are stored only as a KEYED hash (HMAC-SHA256), so the audit log is not a copy of the data.

WRITING (concurrency + cost): every append takes a thread lock AND an exclusive file lock (fcntl.flock), so any number of threads or processes can append to one log and the chain stays valid
(before, two writers read the same tail and forked the chain: 8 threads left it invalid at entry 2). The previous hash comes from an in-memory tail that is trusted only while the file is
exactly the size our last append left it; otherwise (another writer, another process) it is re-read from the END of the file. Either way an append is O(1) (it used to re-read the whole
file: 160 ms at 100,000 records). The on-disk format is unchanged. verify_chain() and records() still read the whole file (they are for audits, not the hot path).

LIMITS (honest): a tamperer who can rewrite the WHOLE file and recompute every hash defeats a bare chain.
In production anchor the latest hash somewhere the writer cannot edit (WORM storage / external timestamp).
`head()` returns the value to anchor.
"""
import fcntl
import hashlib
import hmac
import json
import os
import threading
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
        self._lock = threading.Lock()
        self._tail = None             # (seq, hash) of the last record this instance wrote or read
        self._tail_size = -1          # the file size at that moment: if the file is bigger/smaller now, someone else wrote and the tail is re-read

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

    @staticmethod
    def _read_tail(fd, size):
        """(seq, hash) of the LAST record of the open file, reading only its end. (-1, GENESIS) for an empty file."""
        if size == 0:
            return -1, GENESIS
        window = min(size, 65536)
        data = os.pread(fd, window, size - window)
        lines = [l for l in data.splitlines() if l.strip()]
        if len(lines) < 2 and window < size:                     # a record longer than the window: read the whole file (rare)
            data = os.pread(fd, size, 0)
            lines = [l for l in data.splitlines() if l.strip()]
        last = json.loads(lines[-1])
        return last["seq"], last["hash"]

    def head(self):
        """The hash of the newest record (the value to anchor). O(1)."""
        fd = os.open(self.path, os.O_RDONLY)
        try:
            return self._read_tail(fd, os.fstat(fd).st_size)[1]
        finally:
            os.close(fd)

    def append(self, record):
        """Add a record. `record` must not contain raw personal data (callers pass ids/counts only). Safe for many threads and processes; O(1)."""
        with self._lock:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)       # append only: no update/delete API exists
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)                                          # one writer at a time, across processes; released when the fd closes
                size = os.fstat(fd).st_size
                if self._tail is None or size != self._tail_size:
                    self._tail = self._read_tail(fd, size)                              # someone else wrote since our last append (or this is our first)
                seq, prev = self._tail[0] + 1, self._tail[1]
                body = dict(record, seq=seq, ts=round(time.time(), 3), prev_hash=prev)
                body["hash"] = hashlib.sha256((prev + _canon(body)).encode()).hexdigest()
                line = (json.dumps(body, ensure_ascii=False) + "\n").encode("utf-8")
                os.write(fd, line)
                self._tail, self._tail_size = (seq, body["hash"]), size + len(line)
                return body
            finally:
                os.close(fd)

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
