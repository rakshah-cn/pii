"""write_guard: every store write goes through this. Violations are REJECTED and the rejection is recorded."""
import json
import time

from .guard import guarded_write_ex
from .policy import decide
from .schema import ConsentTag


class _LegacySink:
    """Turns guard events into the old rejection-file format and (optionally) forwards every event to a Layer 7 audit log."""

    def __init__(self, guard, inner=None):
        self._g, self._inner = guard, inner

    def __getattr__(self, name):                          # expose hash_borrower of the inner audit log (keyed hash) if it has one
        if name == "hash_borrower" and self._inner is not None and hasattr(self._inner, "hash_borrower"):
            return self._inner.hash_borrower
        raise AttributeError(name)

    def emit(self, ev):
        if ev.get("kind") == "write_rejected":
            self._g._reject_entry({"ts": time.time(), "store": ev["store"], "record_id": ev.get("record_id"),
                                   "reason": ev["reason"], "purpose": ev.get("purpose")})
        if self._inner is not None:
            fn = getattr(self._inner, "emit", None) or getattr(self._inner, "append")
            fn(ev)


class WriteGuard:
    """Legacy entry point, kept so the Layer 6 mocks and the Layer 8 demo work unchanged. It now DELEGATES to guard.guarded_write_ex
    (one set of rules). registry=None keeps the old claim-only behaviour; pass a ConsentRegistry to verify consent_ids, and
    audit_logger to also send events to Layer 7."""

    def __init__(self, rejection_log_path=None, registry=None, audit_logger=None):
        self.path = rejection_log_path
        self.rejections = []                       # in-memory copy (also appended to the file if a path is given)
        self.registry = registry
        self._sink = _LegacySink(self, audit_logger)

    def check(self, store, record):
        tag = record.get("consent") if isinstance(record, dict) else None
        ok, reason = decide(store, tag)
        return ok, reason

    def _reject_entry(self, entry):
        # never store the record body: only ids and the reason (the body may hold personal data)
        self.rejections.append(entry)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def guarded_write(self, store_name, adapter, record):
        """Write through `adapter.put(record)` only if policy allows. Returns (written: bool, reason)."""
        return guarded_write_ex(store_name, record, adapter, self._sink, registry=self.registry)


def new_tag(purpose, consent_id=None, explicit=False, basis="contract", note="", at="2026-11-19T00:00:00Z",
            borrower_id=None):
    return ConsentTag(purpose=purpose, consent_id=consent_id, collected_at=at, lawful_basis=basis,
                      basis_note=note, explicit_consent=explicit, borrower_id=borrower_id).to_dict()
