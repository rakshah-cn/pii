"""Layer 4: every stored record carries a ConsentTag saying WHY it was collected and what consent covered it."""
from dataclasses import asdict, dataclass, field
from typing import Optional

PURPOSES = ("service", "collections", "compliance", "dispute", "analytics", "model_improvement")
LAWFUL_BASIS = ("consent", "contract", "legal_obligation", "legitimate_use")


@dataclass(slots=True)
class ConsentTag:
    purpose: str                       # one of PURPOSES
    consent_id: Optional[str] = None   # id of the consent artefact; None = no explicit consent on file
    collected_at: str = ""             # ISO timestamp
    lawful_basis: str = "contract"     # one of LAWFUL_BASIS
    basis_note: str = ""               # free text, e.g. 'loan servicing under agreement'
    explicit_consent: bool = False     # True only if the borrower consented to THIS purpose
    borrower_id: Optional[str] = None  # used only to make a keyed hash for the audit event; never logged raw

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return ConsentTag(**{k: d.get(k) for k in ConsentTag.__dataclass_fields__ if k in d})
