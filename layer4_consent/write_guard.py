"""write_guard: every store write goes through this. Violations are REJECTED and the rejection is recorded."""
import json
import time

from .policy import decide
from .schema import ConsentTag


class WriteGuard:
    def __init__(self, rejection_log_path=None):
        self.path = rejection_log_path
        self.rejections = []                       # in-memory copy (also appended to the file if a path is given)

    def check(self, store, record):
        tag = record.get("consent") if isinstance(record, dict) else None
        ok, reason = decide(store, tag)
        return ok, reason

    def _reject(self, store, record, reason):
        # never store the record body: only ids and the reason (the body may hold personal data)
        entry = {"ts": time.time(), "store": store, "record_id": record.get("id"), "reason": reason,
                 "purpose": (record.get("consent") or {}).get("purpose")}
        self.rejections.append(entry)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def guarded_write(self, store_name, adapter, record):
        """Write through `adapter.put(record)` only if policy allows. Returns (written: bool, reason)."""
        ok, reason = self.check(store_name, record)
        if not ok:
            self._reject(store_name, record, reason)
            return False, reason
        adapter.put(record)
        return True, "ok"


def new_tag(purpose, consent_id=None, explicit=False, basis="contract", note="", at="2026-11-19T00:00:00Z",
            borrower_id=None):
    return ConsentTag(purpose=purpose, consent_id=consent_id, collected_at=at, lawful_basis=basis,
                      basis_note=note, explicit_consent=explicit, borrower_id=borrower_id).to_dict()
