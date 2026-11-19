"""Request/response schemas (pydantic). Strict: unknown fields are rejected, ids are restricted to a safe alphabet, and every
detected span must match the text it points at - so a wrong offset can never cause the wrong region to be masked or leaked."""
import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core.tokeniser import PLACEHOLDER_RE, TYPE_RE

ID_RE = r"^[A-Za-z0-9_.:\-]{1,128}$"


def is_id(x) -> bool:
    """True for a safe id. Python's re.match lets '$' match BEFORE a trailing newline ('b1\\n' would pass and become a SEPARATE borrower), so the whole string must match."""
    return isinstance(x, str) and re.fullmatch(ID_RE, x) is not None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)          # strict: "2" is not 2, 1 is not True, no silent coercion of anything a caller sends


class DetectedEntity(_Strict):
    type: str
    value: str = Field(min_length=1, max_length=512)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    entity: Optional[str] = Field(default=None, pattern=ID_RE)           # pieces of ONE number share this id (from Layer 1)
    joined_value: Optional[str] = Field(default=None, max_length=512)

    @field_validator("type")
    @classmethod
    def _type(cls, v):
        if not TYPE_RE.match(v):
            raise ValueError("bad_pii_type")
        return v


class TokenizeRequest(_Strict):
    request_id: str = Field(pattern=ID_RE)
    borrower_id: str = Field(pattern=ID_RE)
    conversation_id: str = Field(pattern=ID_RE)
    purpose_id: str = Field(pattern=ID_RE)
    text: str = Field(min_length=1, max_length=100000)
    detected_entities: List[DetectedEntity] = Field(max_length=500)

    @model_validator(mode="after")
    def _spans(self):
        last = 0
        for e in sorted(self.detected_entities, key=lambda x: (x.start, x.end)):
            if e.end <= e.start or e.end > len(self.text):
                raise ValueError("span_out_of_range")
            if self.text[e.start:e.end] != e.value:
                raise ValueError("span_text_mismatch")
            if e.start < last:
                raise ValueError("spans_overlap")
            last = e.end
        return self


class Mapping(_Strict):
    token: str
    vault_id: str
    pii_type: str
    start: int
    end: int


class TokenizeResponse(_Strict):
    status: Literal["success"] = "success"
    request_id: str
    masked_text: str
    mappings: List[Mapping]


class RehydrateRequest(_Strict):
    request_id: str = Field(pattern=ID_RE)
    borrower_id: str = Field(pattern=ID_RE)
    conversation_id: str = Field(pattern=ID_RE)
    purpose_id: str = Field(pattern=ID_RE)
    destination: str = Field(pattern=ID_RE)
    tokens: List[str] = Field(min_length=1, max_length=50)

    @field_validator("tokens")
    @classmethod
    def _tokens(cls, v):
        if any(not PLACEHOLDER_RE.match(t) for t in v):
            raise ValueError("bad_token_format")
        return v


class RehydrateResponse(_Strict):
    status: Literal["success"] = "success"
    request_id: str
    values: Dict[str, str]


class MetadataResponse(_Strict):
    vault_id: str
    token: str
    pii_type: str
    status: str
    mapping_status: str
    purpose_id: str
    borrower_id: str
    conversation_id: str
    key_status: Optional[str]
    created_at: float
    expires_at: Optional[float] = None


class LifecycleResponse(_Strict):
    vault_id: str
    lifecycle: Literal["NEVER_EXISTED", "ACTIVE", "EXPIRED", "PENDING_DELETION", "PARTIALLY_FAILED", "DELETED", "EXPIRED_DELETED"]
    deleted_at: Optional[float] = None
    reason: Optional[str] = None


class EraseRequest(_Strict):
    job_id: str = Field(pattern=ID_RE)
    borrower_id: str = Field(pattern=ID_RE)
    scope: Literal["BORROWER", "CONVERSATION"] = "BORROWER"
    conversation_id: Optional[str] = Field(default=None, pattern=ID_RE)

    @model_validator(mode="after")
    def _scope(self):
        if (self.scope == "CONVERSATION") != (self.conversation_id is not None):
            raise ValueError("conversation_id_required_exactly_for_conversation_scope")
        return self


class EraseResponse(_Strict):
    job_id: str
    status: Literal["PENDING", "IN_PROGRESS", "COMPLETED", "PARTIAL_FAILURE"]
    counts: Dict[str, int] = {}
    key_destroyed: bool = False
    note: Optional[str] = None


class RotateRequest(_Strict):
    """operation: KEK = re-wrap DEKs under the target master version (never decrypts a PII value);  BLIND_INDEX = recompute the lookup digests;
    DEK = new random DEK per borrower and re-encrypt their records (new nonce sequence, old DEK destroyed);  ALL (default) = KEK + BLIND_INDEX, the original behaviour.
    For DEK, target_key_version is ignored; target_dek_version makes a retry skip borrowers that were already rotated."""
    target_key_version: int = Field(ge=1, le=10_000)
    operation: Literal["ALL", "KEK", "BLIND_INDEX", "DEK"] = "ALL"
    target_dek_version: Optional[int] = Field(default=None, ge=2, le=10_000)


class RotateResponse(_Strict):
    status: Literal["success", "partial_failure"] = "success"
    operation: str = "ALL"
    keys_rewrapped: int = 0
    records_reindexed: int = 0
    current_key_version: int
    deks_rotated: int = 0
    records_reencrypted: int = 0
    borrowers_failed: int = 0
    target_dek_version: Optional[int] = None
    keys_by_kek_version: Optional[Dict[str, int]] = None      # how many borrower keys are wrapped under each master version AFTER this call: retire an old master secret only when its count is 0
    note: Optional[str] = None


class HealthResponse(_Strict):
    status: Literal["ok", "degraded"]
    database: bool
    key_material: bool
    db_role_safe: Optional[bool] = None
