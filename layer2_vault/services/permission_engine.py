"""One place that answers: may THIS identity do THIS operation on THIS borrower for THIS purpose?   PermissionEngine.authorize(identity, operation, borrower_id, purpose_id)

Order of checks (every failure is the SAME uniform denial, 'forbidden', so the answer does not reveal which rule failed):
  1. identity exists and holds the capability for the operation        (operation scope)
  2. the purpose is one this identity may use                          (purpose scope)   - missing purpose when one is required => deny
  3. the borrower is one this identity may act for                     (borrower scope)  - missing borrower when one is required => deny
  4. the Layer-4 style policy (purpose known / destination allowed), via the injected provider; provider unavailable => VaultUnavailable (still no access)
This module knows nothing about keys, encryption or the database: it is deliberately separate from KeyManager. Capability-only callers (no borrower /
purpose in the call, e.g. rotate-key, health) pass None for both and say so with needs_borrower=False / needs_purpose=False."""
from ..core.errors import VaultDenied, VaultUnavailable

NEEDS = {                      # operation -> (needs borrower, needs purpose)
    "tokenize": (True, True), "rehydrate": (True, True), "erase": (True, False), "metadata": (False, False), "rotate": (False, False),
    "health": (False, False), "scan": (False, False), "erasure_sweep": (True, False), "admin": (False, False), "retention": (False, False), "context": (True, False),
}


class PermissionEngine:
    def __init__(self, layer4):
        self.layer4 = layer4                                  # object with .check(action, purpose_id, destination, caller, borrower_id) -> bool

    def authorize(self, identity, operation, borrower_id=None, purpose_id=None, destination=None, policy=True):
        if identity is None or operation not in identity.capabilities:
            raise VaultDenied("forbidden")
        need_b, need_p = NEEDS.get(operation, (False, False))
        if need_p and not purpose_id or need_b and not borrower_id:
            raise VaultDenied("forbidden")                    # missing information: deny, never assume
        if purpose_id is not None and not identity.covers_purpose(purpose_id):
            raise VaultDenied("forbidden")
        if borrower_id is not None and not identity.covers_borrower(borrower_id):
            raise VaultDenied("forbidden")
        if policy and operation in ("tokenize", "rehydrate"):
            try:
                ok = self.layer4.check(operation, purpose_id, destination, identity, borrower_id)
            except Exception:
                raise VaultUnavailable("permission_unavailable")      # the policy service is down: deny
            if not ok:
                raise VaultDenied("purpose_not_permitted" if operation == "tokenize" else "purpose_or_destination_not_permitted")
        return True
