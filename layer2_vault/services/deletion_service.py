"""Vault-side erasure for Layer 6. It deletes and destroys; it NEVER returns plaintext.
Deployment items NOT done in code: restricted database roles and encrypted database backups (the spec lists both).
A job moves PENDING -> IN_PROGRESS -> COMPLETED, or PARTIAL_FAILURE if any step fails; it is NEVER reported COMPLETED unless every
vault-side step succeeded. Idempotent by job_id: repeating a COMPLETED job returns the stored result; repeating a failed one
resumes. While a borrower is ERASURE_PENDING / ERASED, tokenise and rehydrate are refused.
Steps: mark pending -> delete indexes, mappings, records -> (borrower scope) destroy the DEK -> finalise.
CONVERSATION scope deletes rows only: with one DEK per borrower it is not a crypto-shred of that conversation.
Layer 6 remains responsible for logs, vector stores, embeddings, eval sets and cross-store verification."""
import json

from ..core.errors import ValidationFailed, VaultError
from ..schemas.vault_schemas import EraseRequest, EraseResponse
from .context import VaultContext, log
from .key_lifecycle import KeyLifecycle, RESIDUAL_RISK


class DeletionService:
    def __init__(self, ctx: VaultContext, lifecycle: KeyLifecycle):
        self.ctx, self.lifecycle = ctx, lifecycle
        self.fault = None            # test hook: callable(step_name) may raise to simulate a failing step

    def _step(self, name):
        if self.fault:
            self.fault(name)

    @staticmethod
    def _public(counts):
        return {k: v for k, v in counts.items() if not k.startswith("_") and k != "key_destroyed"}

    def erase(self, req: EraseRequest, caller, idempotency_key=None) -> EraseResponse:
        """With an idempotency_key the whole call is claimed first: a repeat returns the stored result, a concurrent repeat is refused while it runs,
        a different request under the same key is rejected, and a run that died is recovered after the stale window. (Underneath, job_id already makes it resumable.)"""
        if idempotency_key is None:
            return self._erase(req, caller)
        self.ctx.engine.authorize(caller, "erase", req.borrower_id)
        state, out = self.ctx.idempotency.run_staged(idempotency_key, "ERASE", caller.name, req.model_dump(), lambda: self._erase(req, caller),
                                                     lambda r: r.status == "COMPLETED", lambda r: r.model_dump())
        return EraseResponse(**out) if state == "REPLAY" else out

    def _erase(self, req: EraseRequest, caller) -> EraseResponse:
        c = self.ctx
        c.engine.authorize(caller, "erase", req.borrower_id)           # the erasure agent may only erase borrowers inside its scope
        prev, prior = {}, None
        with c.db.transaction() as tx:
            job = c.repo.get_job(tx, req.job_id)
            if job:
                if (job["borrower_id"], job["scope"], job["conversation_id"]) != (req.borrower_id, req.scope, req.conversation_id):
                    raise ValidationFailed("job_id_reused_with_different_request")
                prev = json.loads(job["counts"] or "{}")
                if job["status"] == "COMPLETED":
                    return EraseResponse(job_id=req.job_id, status="COMPLETED", counts=self._public(prev), key_destroyed=bool(prev.get("key_destroyed")))
                prior = prev.get("_prior_state")                      # resume: the state the borrower was in BEFORE this job first touched it
            else:
                c.repo.upsert_job(tx, req.job_id, req.borrower_id, req.scope, req.conversation_id, "PENDING", {})
                prior = c.repo.borrower_state(tx, req.borrower_id)    # None / ACTIVE / ERASURE_PENDING / ERASED
            c.repo.upsert_job(tx, req.job_id, req.borrower_id, req.scope, req.conversation_id, "IN_PROGRESS", dict(prev, _prior_state=prior))
        counts, key_destroyed = self._public(prev), bool(prev.get("key_destroyed"))
        meta = lambda: dict(counts, key_destroyed=key_destroyed, _prior_state=prior)
        try:
            self._step("mark_pending")
            with c.db.transaction() as tx:
                # a CONVERSATION erase must never move a borrower that is already PENDING or ERASED, and never re-open one
                if req.scope == "BORROWER" or prior in (None, "ACTIVE"):
                    c.repo.set_borrower_state(tx, req.borrower_id, "ERASURE_PENDING")
                c.repo.mark_pending(tx, req.borrower_id, req.conversation_id)
            self._step("delete_rows")
            with c.db.transaction() as tx:
                for k, v in c.repo.delete_scope(tx, req.borrower_id, req.conversation_id, operation_id=req.job_id, now=c.clock(), tombstone_ttl=c.settings.retention.tombstone_ttl_seconds).items():
                    counts[k] = counts.get(k, 0) + v
            if req.scope == "BORROWER":
                self._step("destroy_key")
                with c.db.transaction() as tx:
                    if self.lifecycle.destroy(tx, req.borrower_id)["keys_destroyed"]:
                        key_destroyed = True
            self._step("finalise")
            with c.db.transaction() as tx:
                if req.scope == "BORROWER":
                    c.repo.set_borrower_state(tx, req.borrower_id, "ERASED")
                elif prior in (None, "ACTIVE"):
                    c.repo.set_borrower_state(tx, req.borrower_id, "ACTIVE")      # only restore what WE changed; ERASED / PENDING stay as they were
                c.repo.upsert_job(tx, req.job_id, req.borrower_id, req.scope, req.conversation_id, "COMPLETED", meta())
                c.repo.log_op(tx, "DELETE", req.borrower_id, req.job_id, caller.name, "SUCCESS")
        except Exception as e:
            code = e.code if isinstance(e, VaultError) else "step_failed"
            log.error("erase job=%s failed at a step: %s", req.job_id, code)
            try:
                with c.db.transaction() as tx:
                    c.repo.upsert_job(tx, req.job_id, req.borrower_id, req.scope, req.conversation_id, "PARTIAL_FAILURE", meta(), code)
            except Exception:
                pass
            c.emit(op="erase", job=req.job_id, caller=caller.name, borrower=req.borrower_id, status="partial_failure", code=code)
            return EraseResponse(job_id=req.job_id, status="PARTIAL_FAILURE", counts=counts, key_destroyed=key_destroyed, note="retry the same job_id; not complete")
        c.emit(op="erase", job=req.job_id, caller=caller.name, borrower=req.borrower_id, status="completed", scope=req.scope)
        note = ("; ".join(RESIDUAL_RISK) + ". Downstream copies and derived data are Layer 6's job.") if req.scope == "BORROWER" else "rows deleted; the borrower's key is unchanged (not a crypto-shred)"
        return EraseResponse(job_id=req.job_id, status="COMPLETED", counts=counts, key_destroyed=key_destroyed, note=note)
