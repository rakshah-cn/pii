"""Idempotency for tokenise / erase / rotate (retries must never repeat the operation, and a reused key must never be accepted for a DIFFERENT request).

Table vault_idempotency, one row per (idempotency_key, operation, caller). The request is identified by request_hash = HMAC-SHA256 over a canonical encoding of
(operation, caller, request), keyed with a key derived from the master secret: the row therefore holds NO PII (the request text is not stored, and an HMAC of a
low-entropy value cannot be brute-forced without the key). response_reference holds only references (tokens, vault ids, offsets, counts), never values or keys.

Rules
  same key + same request, COMPLETED  -> the stored result is returned, nothing is executed again
  same key + different request        -> rejected (idempotency_key_conflict, HTTP 409)
  same key, run in progress           -> rejected (idempotency_in_progress, 409: retry shortly) - exactly one execution
  same key, IN_PROGRESS but stale     -> the earlier run is presumed dead (crash): recovery, it is taken over and re-run (idempotent underneath: erase resumes by job id)
  same key, FAILED                    -> may be retried
Two modes: TOKENIZE is one database transaction, so the claim and the work commit together (a crash leaves no stuck row, and concurrent callers queue on the row).
ERASE / ROTATE are multi-step: the IN_PROGRESS claim is committed first, so a second caller sees it."""
import hashlib
import hmac
import json

from ..core.encryption import canonical
from ..core.errors import IdempotencyError, ValidationFailed
from .context import VaultContext, log


class IdempotencyService:
    def __init__(self, ctx: VaultContext):
        self.ctx = ctx

    # ---- identity of a request
    def hash_request(self, operation, caller_name, payload: dict, version=None) -> str:
        v = version or self.ctx.keys.current_kek_version
        body = canonical("idem", operation, caller_name, json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))
        return f"v{v}:" + hmac.new(self.ctx.keys.idempotency_key(v), body, hashlib.sha256).hexdigest()

    def same_request(self, stored: str, operation, caller_name, payload) -> bool:
        try:
            v = int(stored.split(":", 1)[0][1:])
            return hmac.compare_digest(stored, self.hash_request(operation, caller_name, payload, v))
        except Exception:
            return False                          # unknown hash version / key not loaded: cannot prove it is the same request, so it is not

    @staticmethod
    def valid_key(key) -> bool:
        import re
        return isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_.:\-]{8,128}", key) is not None

    # ---- claim / complete / fail (all take an open transaction)
    def claim(self, tx, key, operation, caller_name, payload, still_valid=None):
        """Returns ("NEW", None) when the caller must execute, or ("REPLAY", reference) when a stored result exists."""
        if not self.valid_key(key):
            raise ValidationFailed("invalid_idempotency_key")
        c, now, pol = self.ctx, self.ctx.clock(), self.ctx.settings.retention
        h = self.hash_request(operation, caller_name, payload)
        tx.execute("DELETE FROM vault_idempotency WHERE idempotency_key = ? AND operation = ? AND caller = ? AND expires_at <= ?", (key, operation, caller_name, now))
        n = tx.execute("INSERT INTO vault_idempotency (idempotency_key, operation, caller, request_hash, status, created_at, updated_at, expires_at) "
                       "VALUES (?, ?, ?, ?, 'IN_PROGRESS', ?, ?, ?) ON CONFLICT (idempotency_key, operation, caller) DO NOTHING",
                       (key, operation, caller_name, h, now, now, now + pol.idempotency_ttl_seconds)).rowcount
        if n == 1:
            return "NEW", None
        row = tx.one("SELECT * FROM vault_idempotency WHERE idempotency_key = ? AND operation = ? AND caller = ?" + (" FOR UPDATE" if tx.kind == "postgres" else ""), (key, operation, caller_name))
        if not self.same_request(row["request_hash"], operation, caller_name, payload):
            raise IdempotencyError("idempotency_key_conflict")
        take_over = lambda: tx.execute("UPDATE vault_idempotency SET status = 'IN_PROGRESS', updated_at = ?, request_hash = ?, response_reference = NULL WHERE idempotency_key = ? AND operation = ? AND caller = ?",
                                       (now, h, key, operation, caller_name))
        if row["status"] == "COMPLETED":
            ref = json.loads(row["response_reference"] or "{}")
            if still_valid is not None and not still_valid(ref):
                take_over()                       # the stored result points at data that has since expired / been erased: run it again rather than return dead references
                return "NEW", None
            return "REPLAY", ref
        if row["status"] == "FAILED":
            take_over()
            return "NEW", None
        if now - row["updated_at"] >= pol.idempotency_stale_seconds:         # IN_PROGRESS but long silent: the earlier run died. Recover.
            log.warning("idempotency: recovering a stale in-progress %s", operation)
            take_over()
            return "NEW", None
        raise IdempotencyError("idempotency_in_progress")

    def complete(self, tx, key, operation, caller_name, reference: dict):
        tx.execute("UPDATE vault_idempotency SET status = 'COMPLETED', response_reference = ?, updated_at = ? WHERE idempotency_key = ? AND operation = ? AND caller = ?",
                   (json.dumps(reference, separators=(",", ":")), self.ctx.clock(), key, operation, caller_name))

    def fail(self, key, operation, caller_name):
        """Best effort, in its own transaction (the work's transaction has rolled back)."""
        try:
            with self.ctx.db.transaction() as tx:
                tx.execute("UPDATE vault_idempotency SET status = 'FAILED', updated_at = ? WHERE idempotency_key = ? AND operation = ? AND caller = ? AND status = 'IN_PROGRESS'",
                           (self.ctx.clock(), key, operation, caller_name))
        except Exception:
            pass

    def run_staged(self, key, operation, caller_name, payload, work, ok, to_ref):
        """Multi-step operations. work() -> result; ok(result) says whether it FINISHED (a PARTIAL_FAILURE result releases the key for a retry);
        to_ref(result) -> the reference dict stored for replay. Returns ("REPLAY", reference) or ("NEW", result)."""
        with self.ctx.db.transaction() as tx:
            state, ref = self.claim(tx, key, operation, caller_name, payload)         # committed here: a second caller now sees IN_PROGRESS
        if state == "REPLAY":
            return "REPLAY", ref
        try:
            result = work()
        except BaseException:
            self.fail(key, operation, caller_name)
            raise
        if ok(result):
            with self.ctx.db.transaction() as tx:
                self.complete(tx, key, operation, caller_name, to_ref(result))
        else:
            self.fail(key, operation, caller_name)
        return "NEW", result
