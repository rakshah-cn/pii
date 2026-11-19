"""Layer 4 hot-path hook: guarded_write(store, record, adapter, audit_logger) -> bool.

Zero database calls: the decision is the in-memory table in policy.py (microseconds). The gateway's authenticated state
is trusted for who the borrower is and what the purpose is; THIS function only enforces purpose limitation and writes
the audit trail. It never raises: a denied or malformed write returns False and emits a `write_rejected` event, so the
caller can still answer the borrower.

Rules:
  * every store write needs a valid ConsentTag (object or dict); missing/malformed/denied -> rejected, fail closed
  * model_improvement stays explicit-consent-only (policy.py): `consent_id` AND `explicit_consent=True`
  * rehydrated text never goes to a store: a record flagged `rehydrated` is rejected
  * audit events carry ids + a keyed borrower hash + the reason, NEVER the record body or raw personal data
  * an adapter failure is reported as `write_failed` (not as a policy rejection); an audit-sink failure never
    crashes the conversation, it is counted in `AUDIT_FAILURES` (a rejection is then not silently lost: see counter)
"""
import hashlib

from .policy import decide
from .schema import ConsentTag

AUDIT_FAILURES = 0          # events that could not be delivered to the audit sink (surface this in monitoring)


def _coerce(tag):
    if isinstance(tag, ConsentTag):
        return tag
    if isinstance(tag, dict):
        return ConsentTag.from_dict(tag)         # unknown keys ignored; missing purpose -> None -> rejected below
    return None


def _borrower_hash(audit_logger, borrower_id):
    if borrower_id in (None, ""):
        return "unknown"
    hb = getattr(audit_logger, "hash_borrower", None)        # Layer 7 AuditLog: keyed HMAC (preferred)
    if callable(hb):
        return hb(borrower_id)[:16]
    return hashlib.sha256(str(borrower_id).encode()).hexdigest()[:16]    # fallback: unkeyed (guessable for small id spaces)


def _emit(audit_logger, event):
    global AUDIT_FAILURES
    try:
        fn = getattr(audit_logger, "emit", None) or getattr(audit_logger, "append")
        fn(event)
    except Exception:
        AUDIT_FAILURES += 1


def guarded_write(store, record, adapter, audit_logger):
    record_id = (record.get("record_id") or record.get("id") or "unknown") if isinstance(record, dict) else "unknown"
    purpose, consent_id, bhash = "unknown", None, "unknown"
    try:
        if not isinstance(record, dict):
            raise ValueError("record_not_a_dict")
        tag = _coerce(record.get("consent"))
        if tag is None or not tag.purpose:
            raise ValueError("missing_or_invalid_consent_tag")
        purpose, consent_id = tag.purpose, tag.consent_id
        bhash = _borrower_hash(audit_logger, tag.borrower_id or record.get("borrower_id"))
        if record.get("rehydrated"):
            raise ValueError("rehydrated_data_must_not_be_stored")
        if tag.explicit_consent and not tag.consent_id:
            raise ValueError("explicit_consent_without_consent_id")
        ok, reason = decide(store, tag)
        if not ok:
            raise ValueError(reason)
    except Exception as e:
        _emit(audit_logger, {"kind": "write_rejected", "store": store, "record_id": record_id, "purpose": purpose,
                             "borrower_hash": bhash, "reason": str(e)})
        return False
    try:
        (getattr(adapter, "put", None) or adapter.write)(record)
    except Exception as e:
        _emit(audit_logger, {"kind": "write_failed", "store": store, "record_id": record_id, "purpose": purpose,
                             "borrower_hash": bhash, "reason": type(e).__name__})
        return False
    _emit(audit_logger, {"kind": "write_allowed", "store": store, "record_id": record_id, "purpose": purpose,
                         "consent_id": consent_id, "borrower_hash": bhash})
    return True
