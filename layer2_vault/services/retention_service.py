"""Retention: expire, then delete, then (optionally) retire idle keys. Called by a scheduler (or POST /vault/retention/cleanup).

Expiry is enforced at READ time (rehydration, token reuse) from the stored timestamps, so this job is housekeeping, never the control.
Design rules:
 * batched   - each batch is ONE transaction (bounded locks / WAL); a failure rolls that batch back and the run reports PARTIAL_FAILURE. Nothing is
               ever marked done unless its transaction committed, so a retry simply picks up the same rows.
 * idempotent - rows already expired/deleted are not selected again; a repeated run does nothing and does not error.
 * safe vs erasure - only records of ACTIVE borrowers in status ACTIVE/EXPIRED are touched. PENDING_ERASURE records and borrowers under erasure are skipped
               (PostgreSQL also skips rows another transaction holds, FOR UPDATE SKIP LOCKED).
 * DEK safety - a DEK is retired only if destroy_idle_keys is on AND no record of ANY status still references it; one extra check runs inside the same
               transaction as the delete. Retired keys carry reason RETENTION so a later tokenise may mint a fresh key (erasure-destroyed keys never allow that).
 * tombstone - deleting a record leaves a PII-free tombstone (id, status, time, operation) in the same transaction."""
from ..core.errors import VaultError
from .context import VaultContext, log
from ..core.tokeniser import new_id


class RetentionService:
    def __init__(self, ctx: VaultContext):
        self.ctx = ctx
        self.fault = None            # test hook: callable(step_name) may raise to simulate a failing step

    def _step(self, name):
        if self.fault:
            self.fault(name)

    def cleanup_expired_records(self, caller=None, max_batches=10_000) -> dict:
        c = self.ctx
        if caller is not None:
            c.require(caller, "retention")
        pol = c.settings.retention
        op_id = new_id("ret")
        out = {"status": "COMPLETED", "records_expired": 0, "records_deleted": 0, "keys_retired": 0, "tombstones_purged": 0, "operations_purged": 0, "idempotency_purged": 0, "contexts_purged": 0}
        try:
            for _ in range(max_batches):                                   # phase 1: expire (mapping + record EXPIRED, blind index removed)
                self._step("expire")
                with c.db.transaction() as tx:
                    ids = c.repo.due_for_expiry(tx, c.clock(), pol.batch_size)
                    out["records_expired"] += c.repo.mark_expired(tx, ids, c.clock())
                if len(ids) < pol.batch_size:
                    break
            touched = {}
            for _ in range(max_batches):                                   # phase 2: delete ciphertext (+ tombstone); optionally retire idle keys
                self._step("delete")
                with c.db.transaction() as tx:
                    now = c.clock()
                    rows = c.repo.due_for_deletion(tx, now, pol.delete_grace_seconds, pol.batch_size)
                    out["records_deleted"] += c.repo.delete_expired_records(tx, [r["vault_id"] for r in rows], now, op_id, pol.tombstone_ttl_seconds)
                    keys = {r["key_id"]: r["borrower_id"] for r in rows}
                    if pol.destroy_idle_keys:
                        self._step("retire_keys")
                        for key_id, borrower in keys.items():
                            # Lock the borrower's row FIRST (PostgreSQL: FOR UPDATE). A tokenise holds that same lock until it commits, so we wait for it and then SEE its new record;
                            # without this, "is the key still used?" could run before a concurrent tokenise committed and the key was destroyed under a live record (found by review).
                            if c.repo.borrower_state(tx, borrower) != "ACTIVE":
                                continue                                    # a borrower under erasure: the erasure owns their key
                            if not c.repo.key_in_use(tx, key_id):          # checked in the SAME transaction as the delete
                                out["keys_retired"] += c.repo.retire_key(tx, key_id, now, "RETENTION")
                    touched.update(keys)
                if len(rows) < pol.batch_size:
                    break
            self._step("purge")
            with c.db.transaction() as tx:                                 # phase 3: expire the metadata itself (tombstones, operations log, idempotency rows)
                p = c.repo.purge_expired_metadata(tx, c.clock(), pol.operations_ttl_seconds)
                out["tombstones_purged"], out["operations_purged"], out["idempotency_purged"], out["contexts_purged"] = p["tombstones"], p["operations"], p["idempotency"], p["contexts"]
                c.repo.log_op(tx, "RETENTION_CLEANUP", None, op_id, getattr(caller, "name", "scheduler"), "SUCCESS")
        except Exception as e:
            code = e.code if isinstance(e, VaultError) else "cleanup_step_failed"
            log.error("retention cleanup %s failed: %s", op_id, code)
            out.update(status="PARTIAL_FAILURE", error=code, note="not complete; run again - committed batches are kept, the failed batch was rolled back")
            c.record_failure("RETENTION_CLEANUP", None, op_id, caller, code)
            c.emit(op="retention_cleanup", status="partial_failure", code=code)
            return out
        if out["records_deleted"] or out["keys_retired"] or out["contexts_purged"]:
            c.db.scrub()                                                   # deleted ciphertext must not survive in the SQLite write-ahead log
        c.emit(op="retention_cleanup", status="completed", expired=out["records_expired"], deleted=out["records_deleted"], keys=out["keys_retired"])
        return out
