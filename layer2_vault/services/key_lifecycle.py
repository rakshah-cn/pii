"""Key lifecycle: states ACTIVE -> REVOKED -> DESTROYED, master-key rotation with re-wrap and re-index.
DESTROYED means the wrapped DEK and its nonce are OVERWRITTEN with NULL in the database (a tombstone row stays for audit).
That does NOT retire copies the database does not control: backups and replicas taken earlier, old master secrets. destroy()
returns that residual risk explicitly instead of claiming the key is gone everywhere."""
from ..core import encryption as enc
from ..core.blind_index import blind_index
from ..core.errors import ValidationFailed
from ..core.tokeniser import new_id
from ..schemas.vault_schemas import RotateRequest, RotateResponse
from .context import VaultContext, log

RESIDUAL_RISK = ("database backups/replicas taken before destruction may still hold the wrapped key",
                 "PostgreSQL only: the destroyed key row and deleted records stay as dead row versions until VACUUM, and as old page images in WAL segments until those are recycled or their archive expires (SQLite is zero-filled and checkpointed by Database.scrub)",
                 "any retained copy of the master secret that unwrapped it can still decrypt such a copy: rotate the master secret and retire old backups")


class KeyLifecycle:
    def __init__(self, ctx: VaultContext):
        self.ctx = ctx
        self.fault = None            # test hook: callable(step_name) may raise to simulate a failure during DEK rotation

    def _step(self, name):
        if self.fault:
            self.fault(name)

    def revoke(self, tx, borrower_id):
        """Block all use of the borrower's key (tokenise AND rehydrate) without destroying it."""
        return self.ctx.repo.set_key_status(tx, borrower_id, "REVOKED")

    def destroy(self, tx, borrower_id) -> dict:
        n = self.ctx.repo.set_key_status(tx, borrower_id, "DESTROYED")
        return {"keys_destroyed": n, "residual_risk": list(RESIDUAL_RISK)}

    def revoke_token(self, conversation_id, token, caller) -> bool:
        """Retire ONE mapping: rehydration of that token stops; the same value later gets a NEW token. (Capability 'admin'.)"""
        self.ctx.require(caller, "admin")
        with self.ctx.db.transaction() as tx:
            ok = self.ctx.repo.revoke_mapping(tx, conversation_id, token)
        self.ctx.emit(op="revoke_token", caller=caller.name, status="success" if ok else "not_found")
        return ok

    def reinstate_borrower(self, borrower_id, caller):
        self.ctx.require(caller, "admin")
        with self.ctx.db.transaction() as tx:
            self.ctx.repo.set_borrower_state(tx, borrower_id, "ACTIVE")
        self.ctx.emit(op="reinstate_borrower", caller=caller.name, borrower=borrower_id, status="success")

    def rotate(self, req: RotateRequest, caller, idempotency_key=None) -> RotateResponse:
        self.ctx.require(caller, "rotate")
        if idempotency_key is None:
            return self._rotate(req, caller)
        state, out = self.ctx.idempotency.run_staged(idempotency_key, "ROTATE", caller.name, req.model_dump(), lambda: self._rotate(req, caller),
                                                     lambda r: r.status == "success", lambda r: r.model_dump())
        return RotateResponse(**out) if state == "REPLAY" else out

    def _rotate(self, req: RotateRequest, caller) -> RotateResponse:
        """Key rotation, with the three kinds kept separate (RotateRequest.operation):
          KEK         re-wrap every DEK under the target master version. Only wrapped keys are touched: NO PII value is decrypted, no record is read.
          BLIND_INDEX recompute lookup digests under the target version's blind-index key (this must decrypt values, so it needs the DEKs).
          DEK         new random DEK per borrower, records re-encrypted under it with a fresh nonce sequence, old DEK destroyed (one transaction per borrower).
          ALL         KEK + BLIND_INDEX in ONE transaction (the original behaviour).
        Both master versions must be loaded; afterwards the old one can be retired or revoked. The active version is switched INSIDE the transaction and restored on failure."""
        c = self.ctx
        c.require(caller, "rotate")
        if req.operation == "DEK":
            return self._rotate_deks(req, caller)
        target = req.target_key_version
        if target not in c.keys.versions:
            raise ValidationFailed("target_version_unavailable")             # unknown OR revoked
        do_kek, do_blind = req.operation in ("ALL", "KEK"), req.operation in ("ALL", "BLIND_INDEX")
        rewrapped = reindexed = 0
        old_kek, old_blind = c.keys.current_kek_version, c.keys.current_blind_version
        try:
            with c.db.transaction() as tx:
                deks = {}
                for k in c.repo.all_keys(tx):
                    if do_blind:
                        deks[k["key_id"]] = c.keys.unwrap(k["wrapped_dek"], k["wrap_nonce"], k["borrower_id"], k["key_id"], k["kek_version"])
                    if do_kek and k["kek_version"] != target:
                        dek = deks.get(k["key_id"]) or c.keys.unwrap(k["wrapped_dek"], k["wrap_nonce"], k["borrower_id"], k["key_id"], k["kek_version"])
                        v, nonce, wrapped = c.keys.wrap(dek, k["borrower_id"], k["key_id"], target)
                        c.repo.rewrap_key(tx, k["key_id"], wrapped, nonce, v)
                        rewrapped += 1
                if do_blind:
                    bkey = c.keys.blind_key(target)
                    for r in c.repo.active_records(tx):
                        dek = deks.get(r["key_id"])
                        if dek is None:
                            continue
                        value = enc.decrypt(dek, r["nonce"], r["ciphertext"], enc.record_aad(r["borrower_id"], r["conversation_id"], r["vault_id"], r["pii_type"], r["key_id"]))
                        c.repo.replace_index(tx, r["vault_id"], target, blind_index(bkey, r["borrower_id"], r["conversation_id"], r["purpose_id"], r["pii_type"], value))
                        reindexed += 1
                c.repo.log_op(tx, "ROTATE_" + req.operation, None, None, caller.name, "SUCCESS")
                if do_kek:
                    c.repo.set_key_state(tx, "kek", target, c.clock())                 # in the SAME transaction: every other process now wraps new keys under this version
                if do_blind:
                    c.repo.set_key_state(tx, "blind_index", target, c.clock())
                remaining = c.repo.keys_by_kek_version(tx)
                if do_kek:
                    c.keys.current_kek_version = target
                if do_blind:
                    c.keys.current_blind_version = target
                c.settings.current_key_version = c.keys.current_kek_version
        except BaseException:
            c.keys.current_kek_version, c.keys.current_blind_version = old_kek, old_blind
            c.settings.current_key_version = old_kek
            raise
        c.db.scrub()                                                       # the old wrapped keys / digests must not survive in the write-ahead log
        c.emit(op="rotate", caller=caller.name, status="success", kind=req.operation, keys=rewrapped, records=reindexed, version=target)
        return RotateResponse(operation=req.operation, keys_rewrapped=rewrapped, records_reindexed=reindexed, current_key_version=c.keys.current_kek_version, keys_by_kek_version=remaining)

    def _rotate_deks(self, req: RotateRequest, caller) -> RotateResponse:
        c = self.ctx
        with c.db.transaction() as tx:
            keys = [k for k in c.repo.key_versions(tx) if k["key_status"] == "ACTIVE"]
            borrowers = sorted(k["borrower_id"] for k in keys)
            target_dek = req.target_dek_version or (max([k["dek_version"] for k in keys] or [1]) + 1)
        done = reenc = failed = 0
        for b in borrowers:
            try:
                self._step("dek_begin")
                with c.db.transaction() as tx:
                    if c.repo.borrower_state(tx, b) != "ACTIVE":
                        continue                                              # never rotate under a borrower being erased
                    old = c.repo.active_key(tx, b)
                    if old is None or old["dek_version"] >= target_dek:
                        continue                                              # already rotated (a retry) or no usable key
                    old_dek = c.keys.unwrap(old["wrapped_dek"], old["wrap_nonce"], b, old["key_id"], old["kek_version"])
                    plain = [(r, enc.decrypt(old_dek, r["nonce"], r["ciphertext"], enc.record_aad(r["borrower_id"], r["conversation_id"], r["vault_id"], r["pii_type"], r["key_id"])))
                             for r in c.repo.records_for(tx, b, with_cipher=True) if r["key_id"] == old["key_id"]]
                    new_id_, new_dek = new_id("key"), c.keys.new_dek()
                    kv, wnonce, wrapped = c.keys.wrap(new_dek, b, new_id_, c.persisted_versions(tx)[0])
                    c.repo.retire_rotated_key(tx, old["key_id"], c.clock())    # one ACTIVE key per borrower: the old one must stop being active first
                    c.repo.insert_key(tx, new_id_, b, wrapped, wnonce, kv, dek_version=target_dek, now=c.clock())
                    c.repo.delete_contexts(tx, b)                              # shared conversation context was encrypted under the old DEK: it is working state, so it is dropped (clients reload from their own memory)
                    for i, (r, value) in enumerate(plain):
                        if i == 1:
                            self._step("dek_mid")                             # fault hook: fail halfway through a borrower's records
                        counter = c.repo.allocate_nonces(tx, new_id_, 1, c.settings.key_usage_limit)
                        nonce, ct = enc.encrypt(new_dek, value, enc.record_aad(r["borrower_id"], r["conversation_id"], r["vault_id"], r["pii_type"], new_id_), nonce=c.nonces.nonce(counter))
                        c.repo.rekey_record(tx, r["vault_id"], new_id_, ct, nonce)
                    c.repo.log_op(tx, "ROTATE_DEK", b, None, caller.name, "SUCCESS")
                    done += 1; reenc += len(plain)
            except Exception as e:
                failed += 1
                log.error("dek rotation failed for one borrower: %s", getattr(e, "code", type(e).__name__))      # that borrower's transaction rolled back: old DEK still ACTIVE and intact
        c.emit(op="rotate", caller=caller.name, status="success" if not failed else "partial_failure", kind="DEK", keys=done, records=reenc)
        note = ("old DEKs are destroyed in this database; backups taken BEFORE the rotation still hold the old wrapped DEKs and the old ciphertext. "
                "Retire those backups. Run again with the same target_dek_version to retry failed borrowers.")
        with c.db.transaction() as tx:
            remaining = c.repo.keys_by_kek_version(tx)
        c.db.scrub()
        return RotateResponse(status="success" if not failed else "partial_failure", operation="DEK", deks_rotated=done, records_reencrypted=reenc, borrowers_failed=failed,
                              target_dek_version=target_dek, current_key_version=c.keys.current_kek_version, keys_by_kek_version=remaining, note=note)
