"""Consent registry: the thing that says whether a consent_id REALLY exists, belongs to this borrower and covers this purpose.

This is an in-memory stand-in with the interface a real enterprise consent system would implement:
    check_consent(consent_id, borrower_id, purpose) -> bool       (True only if the consent is active, unexpired, this borrower's, and covers `purpose`)
Anything else (unknown id, wrong borrower, wrong purpose, withdrawn, expired, bad input) is False. It must not raise for a
normal 'no'; an unavailable backend MAY raise, and the guard then fails closed (write rejected, reason consent_registry_unavailable).
Never stores personal data: only ids, purposes and status.
"""
import threading
import time

from .policy import ALIASES


def _canon(purpose):
    return ALIASES.get(purpose, purpose)


class ConsentRegistry:
    def __init__(self, consents=None, clock=time.time):
        """consents: {"C-123": {"borrower_id": "B-001", "purposes": ["model_improvement", "service"], "status": "active"}}
        optional per entry: "expires_at" (epoch seconds)."""
        self._lock = threading.RLock()
        self._clock = clock
        self._c = {}
        for cid, c in (consents or {}).items():
            self._c[cid] = {"borrower_id": c["borrower_id"], "purposes": {_canon(p) for p in c["purposes"]},
                            "status": c.get("status", "active"), "expires_at": c.get("expires_at")}

    def grant(self, consent_id, borrower_id, purposes, expires_at=None):
        with self._lock:
            self._c[consent_id] = {"borrower_id": borrower_id, "purposes": {_canon(p) for p in purposes},
                                   "status": "active", "expires_at": expires_at}

    def withdraw(self, consent_id):
        """The borrower withdrew: every later check is False. Returns False if the id is unknown."""
        with self._lock:
            c = self._c.get(consent_id)
            if c is None:
                return False
            c["status"] = "withdrawn"
            return True

    def check_consent(self, consent_id, borrower_id, purpose):
        if not (isinstance(consent_id, str) and isinstance(borrower_id, str) and isinstance(purpose, str)) or not consent_id:
            return False
        with self._lock:
            c = self._c.get(consent_id)
            if c is None or c["status"] != "active":
                return False
            if c["expires_at"] is not None and self._clock() >= c["expires_at"]:
                return False
            return c["borrower_id"] == borrower_id and _canon(purpose) in c["purposes"]
