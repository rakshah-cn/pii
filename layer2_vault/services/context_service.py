"""Shared conversation context: lets SEVERAL gateway processes serve one conversation without losing what each of them remembers.

What is stored (by the gateway; this module treats it as an opaque JSON object): the last few RAW turns, the name parts seen, the number-pieces memory. That is personal data,
so it gets the same protection as every vault value:
  * encrypted with AES-256-GCM under the borrower's DEK (nonce from the durable counter, AAD = borrower | conversation | key id | version);
  * it EXPIRES on its own (settings.retention.context_ttl_seconds after the last update) and the retention job purges expired rows;
  * erasure (borrower or conversation scope) deletes it; destroying the borrower's key makes it unreadable; a DEK rotation drops it (working state, not a record);
  * the stored version lets a reader ask 'has anyone changed this since I last looked?' without decrypting anything.
Fail-safe reading: anything wrong (borrower erased, key revoked / rotated, expired, corrupt) returns None = 'no shared context' - the caller carries on with its own memory
(the tokenise path still fails closed on its own). An unavailable database raises, as everywhere else. Limits: last writer wins (messages of one conversation are sequential in
practice); size is capped."""
import json

from ..core import encryption as enc
from ..core.errors import DecryptionError, KeyStateError, ValidationFailed, VaultDenied
from ..schemas.vault_schemas import ID_RE
from .context import VaultContext

MAX_BYTES = 65536


class ContextService:
    def __init__(self, ctx: VaultContext, tokenizer):
        self.ctx, self.tokenizer = ctx, tokenizer

    @staticmethod
    def _check(borrower_id, conversation_id):
        import re
        if not all(isinstance(x, str) and re.match(ID_RE, x) for x in (borrower_id, conversation_id)):
            raise ValidationFailed("invalid_request")

    @staticmethod
    def _aad(borrower_id, conversation_id, key_id, version):
        return enc.canonical("ctx", borrower_id, conversation_id, key_id, str(version))

    def load(self, borrower_id, conversation_id, caller, newer_than=None):
        """-> None (no usable context) | (version, None) when version <= newer_than (nothing changed) | (version, state dict)."""
        c = self.ctx
        c.engine.authorize(caller, "context", borrower_id)
        self._check(borrower_id, conversation_id)
        with c.db.transaction() as tx:
            if c.repo.borrower_state(tx, borrower_id) != "ACTIVE":
                return None
            row = c.repo.get_context(tx, borrower_id, conversation_id)
            if row is None or row["expires_at"] <= c.clock():
                return None
            if newer_than is not None and row["version"] <= newer_than:
                return row["version"], None
            key = c.repo.active_key(tx, borrower_id)
            if key is None or key["key_id"] != row["key_id"]:
                return None                                              # key revoked / destroyed / rotated since it was written
            try:
                dek = c.keys.unwrap(key["wrapped_dek"], key["wrap_nonce"], borrower_id, key["key_id"], key["kek_version"])
                plain = enc.decrypt(dek, row["nonce"], row["ciphertext"], self._aad(borrower_id, conversation_id, row["key_id"], row["version"]))
                return row["version"], json.loads(plain)
            except (KeyStateError, DecryptionError, ValueError):
                return None

    def save(self, borrower_id, conversation_id, state: dict, caller) -> int:
        """Replace the stored context; returns the new version."""
        c = self.ctx
        c.engine.authorize(caller, "context", borrower_id)
        self._check(borrower_id, conversation_id)
        plain = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if len(plain.encode("utf-8")) > MAX_BYTES:
            raise ValidationFailed("context_too_large")

        def work():
            with c.db.transaction() as tx:
                if c.repo.ensure_borrower(tx, borrower_id) != "ACTIVE":
                    raise VaultDenied("borrower_not_active")
                key, dek = self.tokenizer._dek_for(tx, borrower_id)
                row = c.repo.get_context(tx, borrower_id, conversation_id)
                version = (row["version"] if row else 0) + 1
                counter = c.repo.allocate_nonces(tx, key["key_id"], 1, c.settings.key_usage_limit)
                nonce, ct = enc.encrypt(dek, plain, self._aad(borrower_id, conversation_id, key["key_id"], version), nonce=c.nonces.nonce(counter))
                now = c.clock()
                c.repo.put_context(tx, borrower_id, conversation_id, key["key_id"], ct, nonce, version, now, now + c.settings.retention.context_ttl_seconds)
                return version
        return self.tokenizer._run(work, "CONTEXT_SAVE", borrower_id, None, caller)
