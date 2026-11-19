"""Layer 7 events from the gateway: operational metadata ONLY.

Every event carries request_id, borrower_hash (a KEYED hash - never the borrower id), purpose and latency_ms. Extra fields are limited to small numbers, booleans and
short code-like strings (a free-text field cannot be added, so raw text, LLM output and keys have no way in). Events are collected while the graph runs and
FLUSHED once at the end, so writing to the audit chain never sits inside the 50 ms detect -> tokenise -> audit path.
Sinks: MemorySink (default, for tests), Layer7Sink (layer7_audit.chain.AuditLog: hash-chained file). A failing sink never leaks anything; with strict=True the message is
blocked because its audit trail could not be written."""
import hashlib
import hmac
import os
import re
import threading
import time

EVENTS = ("L3_STARTED", "L1_DETECT_COMPLETE", "L2_TOKENISE_COMPLETE", "L3_AUDIT_PASSED", "L3_AUDIT_FAILED", "LLM_RESPONDED", "LEAK_PREVENTED", "L3_BLOCKED", "CONSENT_DENIED")
_SAFE = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")


def _safe_value(v):
    if isinstance(v, bool) or (isinstance(v, (int, float)) and abs(v) < 1e12):
        return v
    if isinstance(v, str) and _SAFE.match(v):
        return v
    return None


class MemorySink:
    def __init__(self, cap=100_000):
        self.events, self._lock, self.cap = [], threading.Lock(), cap          # bounded: only the newest `cap` events are kept (a long-running process must not grow without limit)

    def __call__(self, records):
        with self._lock:
            self.events.extend(dict(r) for r in records)
            if len(self.events) > self.cap:
                del self.events[: len(self.events) - self.cap]

    def hash_borrower(self, borrower_id):
        return None


class Layer7Sink:
    """Adapter for layer7_audit.chain.AuditLog. NOTE: AuditLog.append re-reads the whole file for every record (O(n)); fine for a demo, replace before real volume."""
    def __init__(self, audit_log):
        self.log = audit_log

    def __call__(self, records):
        for r in records:
            self.log.append(r)

    def hash_borrower(self, borrower_id):
        return self.log.hash_borrower(borrower_id)


class Events:
    def __init__(self, sink=None, key=None, strict=False):
        self.sink = sink if sink is not None else MemorySink()
        self.strict = strict
        k = key or os.environ.get("GATEWAY_HASH_KEY")
        self._key = k.encode() if isinstance(k, str) else (k or os.urandom(32))      # without GATEWAY_HASH_KEY the hash differs per process (cannot be correlated across restarts)

    def borrower_hash(self, borrower_id):
        h = getattr(self.sink, "hash_borrower", lambda b: None)(borrower_id) if borrower_id is not None else None
        if h:
            return h
        return hmac.new(self._key, str(borrower_id).encode(), hashlib.sha256).hexdigest() if borrower_id is not None else None

    def begin(self, request_id, borrower_id, purpose):
        return RequestEvents(self, request_id, self.borrower_hash(borrower_id), purpose if isinstance(purpose, str) and _SAFE.match(purpose) else None)


class RequestEvents:
    def __init__(self, parent, request_id, borrower_hash, purpose):
        self.parent, self.base, self.t0, self.records = parent, {"request_id": request_id, "borrower_hash": borrower_hash, "purpose": purpose}, time.perf_counter(), []

    def add(self, event, **fields):
        assert event in EVENTS, event
        rec = {"event": event, **self.base, "latency_ms": round((time.perf_counter() - self.t0) * 1000, 2)}
        for k, v in fields.items():
            sv = _safe_value(v)
            if sv is not None:
                rec[k] = sv
        self.records.append(rec)

    def flush(self):
        """True if written. A failure is swallowed (no detail, nothing sensitive in it); the caller decides whether that blocks (strict mode)."""
        try:
            self.parent.sink(self.records)
            return True
        except Exception:
            return False
