"""Layer 3's client for the Layer 2 vault (the modular, database-backed one - not the old single-file prototype).

What the gateway gets from it:
  tokenize_batch   ALL new values of a message in ONE vault transaction, with an idempotency key, so a retried request cannot create duplicate mappings
  rehydrate_many   tokens -> values for the borrower channel only (destination 'borrower_reply'); a denied token comes back None, an unavailable vault RAISES (fail closed)
  tokens / extend / scan_text / erasure helpers   inherited from layer2_vault.compat.ModularVault (the same in-process adapter the erasure agent uses)
The gateway never sees keys: it holds a service identity with the capabilities 'tokenize', 'rehydrate' and 'scan', nothing else."""
import uuid

from layer2_vault.compat import ModularVault, _GATEWAY
from layer2_vault.core.errors import VaultDenied, VaultError
from layer2_vault.schemas.vault_schemas import RehydrateRequest


class VaultClient(ModularVault):
    def tokenize_batch(self, conversation_id, borrower_id, purpose, items, idempotency_key=None, request_id="gateway"):
        """items: [(pii_type, value)] -> [(token, vault_id)] in the same order. One transaction. Raises VaultError (never returns a partial result)."""
        if not items:
            return []
        for t, v in items:
            if not isinstance(v, str) or not v:
                raise VaultError("invalid_request")
        return self.svc.tokenizer.tokenize_values(self._owner(borrower_id, conversation_id), conversation_id, purpose, [tuple(i) for i in items],
                                                  _GATEWAY, request_id, idempotency_key)

    def load_context(self, conversation_id, borrower_id, newer_than=None):
        """Shared conversation context from the vault (encrypted there): None | (version, None) if unchanged since `newer_than` | (version, state)."""
        return self.svc.context.load(self._owner(borrower_id, conversation_id), conversation_id, _GATEWAY, newer_than)

    def save_context(self, conversation_id, borrower_id, state):
        return self.svc.context.save(self._owner(borrower_id, conversation_id), conversation_id, state, _GATEWAY)

    def rehydrate_many(self, conversation_id, tokens):
        """{token: value or None}. None = the vault refused THAT token (revoked, expired, erased, unknown); a vault that is down or a decrypt that fails raises."""
        out = {t: None for t in tokens}
        by_scope = {}
        with self.svc.ctx.db.transaction() as tx:
            for t in tokens:
                r = self.svc.ctx.repo.mapping_with_record(tx, conversation_id, t)
                if r:
                    by_scope.setdefault((r["borrower_id"], r["purpose_id"]), []).append(t)
        for (borrower, purpose), group in by_scope.items():
            for i in range(0, len(group), 50):                         # the vault caps one request at 50 tokens
                chunk = group[i:i + 50]
                try:
                    out.update(self._rehydrate(conversation_id, borrower, purpose, chunk))
                except VaultDenied:                                    # one bad token denies the whole atomic request: find which
                    for t in chunk:
                        try:
                            out.update(self._rehydrate(conversation_id, borrower, purpose, [t]))
                        except VaultDenied:
                            out[t] = None
        return out

    def _rehydrate(self, conversation_id, borrower, purpose, tokens):
        req = RehydrateRequest(request_id="gw-" + uuid.uuid4().hex[:10], borrower_id=borrower, conversation_id=conversation_id,
                               purpose_id=purpose, destination="borrower_reply", tokens=tokens)
        return self.svc.rehydration.rehydrate(req, _GATEWAY).values
