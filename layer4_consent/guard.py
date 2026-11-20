"""Layer 4 hot-path hook: guarded_write(store, record, adapter, audit_logger, registry=None) -> bool.

Zero database calls on the policy path (in-memory table, policy.py). The CONSENT REGISTRY (registry.py) is consulted only when a
write claims or needs explicit consent; the gateway's authenticated state is trusted for who the borrower is and what purpose it
declares. It never raises: a denied or malformed write returns False and emits a `write_rejected` event, so the caller can still
answer the borrower. `guarded_write_ex` returns (ok, reason) for callers that want the reason (the legacy WriteGuard does).

Rules:
  * every store write needs a valid ConsentTag (object or dict); missing/malformed/denied -> rejected, fail closed
  * borrower on the tag and on the record must agree (`borrower_mismatch`)
  * explicit consent is `explicit_consent is True` AND a consent_id; the registry (if given) must CONFIRM the id for this
    borrower and purpose (`consent_invalid`); a registry that errors or answers anything but True fails closed
    (`consent_registry_unavailable` / `consent_invalid`)
  * registry=None is the LEGACY mode: consent is only claimed, not verified. write_allowed events say so (`consent_verified`)
  * rehydrated text never goes to a store: a record flagged `rehydrated` is rejected
  * audit events carry ids + a keyed borrower hash + the reason, NEVER the record body or raw personal data
  * an adapter failure is `write_failed`; an audit-sink failure never crashes the conversation (counted in AUDIT_FAILURES)
"""
import hashlib

from .policy import ALIASES, decide, requires_explicit_consent
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


def guarded_write_ex(store, record, adapter, audit_logger, registry=None):
    """-> (written: bool, reason: str). reason is 'ok' when written."""
    record_id = (record.get("record_id") or record.get("id") or "unknown") if isinstance(record, dict) else "unknown"
    purpose, consent_id, bhash, verified = "unknown", None, "unknown", False
    try:
        if not isinstance(record, dict):
            raise ValueError("record_not_a_dict")
        tag = _coerce(record.get("consent"))
        if tag is None or not tag.purpose:
            raise ValueError("missing_or_invalid_consent_tag")
        purpose, consent_id = tag.purpose, tag.consent_id
        rec_b = record.get("borrower_id")
        if tag.borrower_id and rec_b and tag.borrower_id != rec_b:
            bhash = _borrower_hash(audit_logger, tag.borrower_id)
            raise ValueError("borrower_mismatch")
        borrower = tag.borrower_id or rec_b
        bhash = _borrower_hash(audit_logger, borrower)
        if record.get("rehydrated"):
            raise ValueError("rehydrated_data_must_not_be_stored")
        if tag.explicit_consent is not None and not isinstance(tag.explicit_consent, bool):
            raise ValueError("explicit_consent_not_boolean")
        if tag.explicit_consent and not tag.consent_id:
            raise ValueError("explicit_consent_without_consent_id")
        ok, reason = decide(store, tag)
        if not ok:
            raise ValueError(reason)
        claims = bool(tag.explicit_consent) or requires_explicit_consent(store, tag.purpose)
        if claims and registry is not None:               # a claimed or required consent must be CONFIRMED, fail closed
            if not borrower:
                raise ValueError("borrower_unknown_for_consent")
            try:
                confirmed = registry.check_consent(tag.consent_id, str(borrower), ALIASES.get(tag.purpose, tag.purpose))
            except Exception:
                raise ValueError("consent_registry_unavailable")
            if confirmed is not True:
                raise ValueError("consent_invalid")
            verified = True
    except Exception as e:
        _emit(audit_logger, {"kind": "write_rejected", "store": store, "record_id": record_id, "purpose": purpose,
                             "borrower_hash": bhash, "reason": str(e)})
        return False, str(e)
    try:
        (getattr(adapter, "put", None) or adapter.write)(record)
    except Exception as e:
        _emit(audit_logger, {"kind": "write_failed", "store": store, "record_id": record_id, "purpose": purpose,
                             "borrower_hash": bhash, "reason": type(e).__name__})
        return False, "write_failed"
    ev = {"kind": "write_allowed", "store": store, "record_id": record_id, "purpose": purpose,
          "consent_id": consent_id, "borrower_hash": bhash}
    if claims:
        ev["consent_verified"] = verified                 # False = legacy mode: consent was only CLAIMED, never checked
    _emit(audit_logger, ev)
    return True, "ok"


def guarded_write(store, record, adapter, audit_logger, registry=None):
    """Returns a plain bool (never a tuple: a (False, ...) tuple is truthy and would let a denied write through)."""
    return guarded_write_ex(store, record, adapter, audit_logger, registry)[0]
