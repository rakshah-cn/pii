"""Consent / purpose gate: the gateway's call into Layer 4 (layer4_consent).

Before anything is detected or tokenised, the declared purpose must be one Layer 4 allows to feed the transcript log (the store the gateway writes).
DEFAULT DENY: an unknown purpose, a missing purpose, or a Layer 4 that cannot answer all mean 'no' (an unavailable Layer 4 is reported as such: 503, not 403).
Gateway purpose names are mapped to Layer 4's canonical names first (e.g. 'loan_collection' -> 'collections').
ASSUMPTION: Layer 4's table (layer4_consent/policy.py) is the team's reading of purpose limitation, not legal advice."""
from dataclasses import dataclass
from typing import Optional

ALIASES = {"loan_collection": "collections", "collection": "collections", "loan_servicing": "service", "customer_service": "service"}


@dataclass(frozen=True)
class ConsentDecision:
    allowed: bool
    purpose: Optional[str] = None        # canonical purpose name (what the vault and Layer 4 use)
    reason: str = "ok"                   # a code, never free text from the request
    unavailable: bool = False            # True = Layer 4 could not answer (fail closed, but a dependency failure, not a policy 'no')


class ConsentGate:
    def __init__(self, decide=None, store="logs"):
        if decide is None:
            from layer4_consent.policy import decide as layer4_decide
            decide = layer4_decide
        self._decide, self.store = decide, store

    def check(self, borrower_id, purpose, consent_id=None) -> ConsentDecision:
        if not isinstance(purpose, str) or not purpose.strip():
            return ConsentDecision(False, None, "missing_purpose")
        canon = ALIASES.get(purpose.strip().casefold(), purpose.strip().casefold())
        try:
            ok, reason = self._decide(self.store, {"purpose": canon, "consent_id": consent_id, "explicit_consent": bool(consent_id)})
        except Exception:
            return ConsentDecision(False, None, "consent_unavailable", unavailable=True)
        return ConsentDecision(bool(ok), canon if ok else None, "ok" if ok else str(reason).split(":")[0][:48])
