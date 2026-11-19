"""Layer 3's client for the Layer 2 vault (the modular, database-backed one - not the old single-file prototype).

What the gateway gets from it:
  tokenize_batch   ALL new values of a message in ONE vault transaction, with an idempotency key, so a retried request cannot create duplicate mappings
  rehydrate_many   tokens -> values for the borrower channel only (destination 'borrower_reply'); a denied token comes back None, an unavailable vault RAISES (fail closed)
  tokens / extend / scan_text / erasure helpers   inherited from layer2_vault.compat.ModularVault (the same in-process adapter the erasure agent uses)
The gateway never sees keys: it holds a service identity with the capabilities 'tokenize', 'rehydrate' and 'scan', nothing else."""
import uuid

from shared.constants import canonical_purpose
from layer2_vault.compat import ModularVault, _GATEWAY
from layer2_vault.core.errors import VaultDenied, VaultError
from layer2_vault.schemas.vault_schemas import RehydrateRequest


_ALL = object()          # "no borrower given": the legacy, unscoped behaviour (internal tools); the gateway always passes the requesting borrower


class VaultClient(ModularVault):
    def tokens(self, conversation_id, borrower_id=_ALL):
        """Tokens of the conversation. With borrower_id: only THAT borrower's (a token of another borrower sharing the conversation id is not 'known' to this one)."""
        if borrower_id is _ALL:
            return super().tokens(conversation_id)
        with self.svc.ctx.db.transaction() as tx:
            return self.svc.ctx.repo.tokens_for_conversation(tx, conversation_id, self._owner(borrower_id, conversation_id))

    def tokenize_batch(self, conversation_id, borrower_id, purpose, items, idempotency_key=None, request_id="gateway"):
        """items: [(pii_type, value)] -> [(token, vault_id)] in the same order. One transaction. Raises VaultError (never returns a partial result)."""
        if not items:
            return []
        for t, v in items:
            if not isinstance(v, str) or not v:
                raise VaultError("invalid_request")
        return self.svc.tokenizer.tokenize_values(self._owner(borrower_id, conversation_id), conversation_id, purpose, [tuple(i) for i in items],
                                                  _GATEWAY, request_id, idempotency_key)

    def load_context(self, conversation_id, borrower_id, newer_than=None, strict=False):
        """Shared conversation context from the vault (encrypted there): None | (version, None) if unchanged since `newer_than` | (version, state).
        strict=True raises VaultDenied('borrower_not_active') for a borrower that is erased / being erased."""
        return self.svc.context.load(self._owner(borrower_id, conversation_id), conversation_id, _GATEWAY, newer_than, strict)

    def save_context(self, conversation_id, borrower_id, state):
        return self.svc.context.save(self._owner(borrower_id, conversation_id), conversation_id, state, _GATEWAY)

    def rehydrate_many(self, conversation_id, tokens, borrower_id=_ALL, purpose=None):
        """{token: value or None}. None = the vault refused THAT token (revoked, expired, erased, unknown, ANOTHER BORROWER'S, or made for ANOTHER PURPOSE); a vault that is down or a
        decrypt that fails raises. Review finding: without borrower_id / purpose a borrower who reused someone else's conversation id got that person's values back, because each token
        was rehydrated under its OWN record's borrower and purpose."""
        out = {t: None for t in tokens}
        owner = None if borrower_id is _ALL else self._owner(borrower_id, conversation_id)
        want = canonical_purpose(purpose) if purpose else None
        by_scope = {}
        with self.svc.ctx.db.transaction() as tx:
            for t in tokens:
                r = self.svc.ctx.repo.mapping_with_record(tx, conversation_id, t)
                if not r or (owner is not None and r["borrower_id"] != owner) or (want is not None and canonical_purpose(r["purpose_id"]) != want):
                    continue
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
