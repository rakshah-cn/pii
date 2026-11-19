"""Rehydration: token -> original value. DENY BY DEFAULT.
Checks (all must pass, in order): caller capability -> Layer-4 style purpose/destination permission (unavailable => deny) -> audit
'attempt' event (cannot be written => deny) -> borrower ACTIVE -> key ACTIVE -> every token inside the exact borrower/conversation
scope and ACTIVE and recorded under the SAME purpose -> unwrap DEK -> AES-GCM decrypt with the record's AAD.
The request is ATOMIC: one bad token denies the whole request, so nothing partial is ever returned.
Missing token, wrong scope and wrong purpose all give the SAME error, so the response cannot be used to probe for tokens."""
from ..core import encryption as enc
from ..core.errors import KeyStateError, VaultDenied, VaultError, VaultUnavailable
from ..schemas.vault_schemas import RehydrateRequest, RehydrateResponse
from .context import VaultContext, log


class RehydrationService:
    def __init__(self, ctx: VaultContext):
        self.ctx = ctx

    def rehydrate(self, req: RehydrateRequest, caller) -> RehydrateResponse:
        c = self.ctx
        try:
            c.engine.authorize(caller, "rehydrate", req.borrower_id, req.purpose_id, req.destination)      # operation + purpose + borrower scope + policy; policy service down: DENY
            if len(req.tokens) > c.settings.max_tokens_per_rehydrate:
                raise VaultDenied("too_many_tokens")
            c.emit(must=True, op="rehydrate_attempt", request_id=req.request_id, caller=caller.name, borrower=req.borrower_id,
                   destination=req.destination, tokens=len(req.tokens))
            values = self._decrypt(req, caller)
        except VaultError as e:
            c.record_failure("REHYDRATE", getattr(req, "borrower_id", None), getattr(req, "request_id", None), caller, e.code)
            c.emit(op="rehydrate_outcome", request_id=req.request_id, caller=getattr(caller, "name", None), borrower=req.borrower_id, status="denied", code=e.code)
            raise
        except Exception as e:
            log.error("op=REHYDRATE unexpected %s", type(e).__name__)
            c.record_failure("REHYDRATE", req.borrower_id, req.request_id, caller, "vault_unavailable")
            raise VaultUnavailable()
        c.emit(op="rehydrate_outcome", request_id=req.request_id, caller=caller.name, borrower=req.borrower_id, status="success", tokens=len(values))
        return RehydrateResponse(request_id=req.request_id, values=values)

    def _decrypt(self, req, caller):
        c = self.ctx
        values = {}
        with c.db.transaction() as tx:
            state = c.repo.borrower_state(tx, req.borrower_id)
            if state is None:
                raise VaultDenied("token_not_found")             # unknown borrower looks exactly like an unknown token: no probing of borrower ids
            if state != "ACTIVE":
                raise VaultDenied("borrower_not_active")
            key = c.repo.active_key(tx, req.borrower_id)
            if key is None:
                raise KeyStateError("key_not_active")
            dek = c.keys.unwrap(key["wrapped_dek"], key["wrap_nonce"], req.borrower_id, key["key_id"], key["kek_version"])
            purpose = req.purpose_id.casefold()
            now = c.clock()
            for token in dict.fromkeys(req.tokens):
                r = c.repo.mapping_with_record(tx, req.conversation_id, token)
                # expiry is enforced HERE, on every rehydration, using the stored timestamps: it never depends on a cleanup job having run
                if (not r or r["borrower_id"] != req.borrower_id or r["mapping_status"] != "ACTIVE" or r["record_status"] != "ACTIVE"
                        or r["key_id"] != key["key_id"] or r["purpose_id"] != purpose
                        or any(x is not None and x <= now for x in (r["mapping_expires_at"], r["record_expires_at"]))):
                    raise VaultDenied("token_not_found")
                values[token] = enc.decrypt(dek, r["nonce"], r["ciphertext"], enc.record_aad(r["borrower_id"], r["conversation_id"], r["vault_id"], r["pii_type"], r["key_id"]))
            c.repo.log_op(tx, "REHYDRATE", req.borrower_id, req.request_id, caller.name, "SUCCESS")
        return values

