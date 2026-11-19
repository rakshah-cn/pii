"""ModularVault: the legacy single-file Vault interface (put/get/tokens/extend/scan_text/entries_for_borrower/...) implemented on top
of the new modular services, so the gateway (Layer 3) and erasure agent (Layer 6) can run on it unchanged.
This adapter runs IN-PROCESS with trusted identities; it is an integration shim, NOT an authentication boundary.
Differences from the legacy vault: consent_id / source are not stored here (Layer 4 / Layer 5 own those); get() returns None
instead of raising when the token cannot be rehydrated.
SAFE BY DEFAULT: without services/settings it REFUSES to start unless a master secret (VAULT_MASTER_KEY) and a database
(VAULT_DB_URL, VAULT_DB_PATH or path=) are configured - it never invents a random key or an in-memory database (data would vanish
on restart). Tests and demos opt out explicitly: ephemeral=True or VAULT_ALLOW_EPHEMERAL=1."""
import uuid

from .config.settings import ServiceIdentity, ServiceRegistry, Settings
from .core.errors import VaultDenied, VaultError, VaultUnavailable
from .schemas.vault_schemas import EraseRequest, RehydrateRequest
from .services import build_services

_GATEWAY = ServiceIdentity("compat-gateway", frozenset({"tokenize", "rehydrate", "scan", "context"}))
_ERASURE = ServiceIdentity("compat-erasure", frozenset({"erase", "erasure_sweep", "metadata"}))


class ModularVault:
    def __init__(self, master_key: bytes = None, path: str = None, services=None, settings=None, ephemeral: bool = False):
        if services is None:
            import os
            if settings is None:                                                  # an explicit Settings object is the caller's own configuration: used as given
                from .config.settings import decode_secret
                ephemeral = ephemeral or os.environ.get("VAULT_ALLOW_EPHEMERAL") == "1"
                if master_key is None and os.environ.get("VAULT_MASTER_KEY"):
                    master_key = decode_secret(os.environ["VAULT_MASTER_KEY"])     # the same secret the project keeps in .env
                db_url = os.environ.get("VAULT_DB_URL") or (f"sqlite:///{path or os.environ['VAULT_DB_PATH']}" if (path or os.environ.get("VAULT_DB_PATH")) else None)
                if not ephemeral:
                    if master_key is None:
                        raise RuntimeError("VAULT_MASTER_KEY is not set: refusing to start a vault with a throw-away key (set it; tests and demos may pass ephemeral=True or set VAULT_ALLOW_EPHEMERAL=1)")
                    if db_url is None:
                        raise RuntimeError("No vault database is configured (VAULT_DB_URL or VAULT_DB_PATH): refusing to use an in-memory database that would lose everything on restart "
                                           "(tests and demos may pass ephemeral=True or set VAULT_ALLOW_EPHEMERAL=1)")
                settings = Settings(master_secrets={1: master_key or os.urandom(32)}, db_url=db_url or "sqlite:///:memory:", registry=ServiceRegistry(),
                                    rehydrate_policy={p: frozenset({"borrower_reply"}) for p in ("service", "collections", "collection", "compliance", "analytics", "model_improvement")}).validate()
            services = build_services(settings)
        self.svc = services

    @staticmethod
    def _owner(borrower_id, conversation_id):
        return borrower_id or f"conv:{conversation_id}"

    def put(self, conversation_id, label, raw, borrower_id=None, purpose="service", consent_id=None, source=None):
        if not isinstance(raw, str) or not raw:
            raise VaultError("invalid_request")
        return self.svc.tokenizer.tokenize_values(self._owner(borrower_id, conversation_id), conversation_id, purpose, [(label, raw)], _GATEWAY)[0][0]

    def get(self, conversation_id, token, role="rehydration"):
        if role != "rehydration":
            raise VaultError("denied")
        with self.svc.ctx.db.transaction() as tx:
            r = self.svc.ctx.repo.mapping_with_record(tx, conversation_id, token)
        if not r:
            return None
        req = RehydrateRequest(request_id="compat-" + uuid.uuid4().hex[:8], borrower_id=r["borrower_id"], conversation_id=conversation_id,
                               purpose_id=r["purpose_id"], destination="borrower_reply", tokens=[token])
        try:
            return self.svc.rehydration.rehydrate(req, _GATEWAY).values.get(token)
        except (VaultDenied, VaultError):
            return None

    def tokens(self, conversation_id):
        with self.svc.ctx.db.transaction() as tx:
            return self.svc.ctx.repo.tokens_for_conversation(tx, conversation_id)

    def extend(self, conversation_id, token, piece=None, joined=None):
        return self.svc.internal.extend(conversation_id, token, _GATEWAY, piece=piece, joined=joined)

    def scan_text(self, conversation_id, text, role="audit", min_len=4):
        return self.svc.internal.scan_text(conversation_id, text, _GATEWAY, min_len)

    def entries_for_borrower(self, borrower_id):
        return [{"token": r["token"], "label": r["pii_type"], "conversation_id": r["conversation_id"], "created": r["created_at"], "purpose": r["purpose_id"], "consent_id": None}
                for r in self.svc.tokenizer.list_for_borrower(borrower_id, _ERASURE) if r["status"] == "ACTIVE"]

    def has_key(self, borrower_id):
        with self.svc.ctx.db.transaction() as tx:
            return self.svc.ctx.repo.active_key(tx, borrower_id) is not None

    # ---- sealed erasure sweep: the vault matches internally and returns categories / scores / a bool, never a value
    def erasure_scan(self, borrower_id, texts, mode="values"):
        return self.svc.internal.erasure_scan(borrower_id, list(texts), _ERASURE, mode)

    def erasure_probe(self, borrower_id, rows, embed_fn, threshold=0.0):
        return self.svc.internal.erasure_probe(borrower_id, list(rows), embed_fn, _ERASURE, threshold)

    def find_unmapped_copies(self, borrower_id, search_corpus):
        return self.svc.internal.find_unmapped_copies(borrower_id, search_corpus, _ERASURE)

    def destroy_borrower(self, borrower_id, role="erasure", job_id=None):
        """`job_id` makes a retry resume the SAME vault job (a COMPLETED job returns its stored result)."""
        r = self.svc.deletion.erase(EraseRequest(job_id=job_id or "compat-" + uuid.uuid4().hex, borrower_id=borrower_id), _ERASURE)
        if r.status != "COMPLETED":
            raise VaultError("erasure_incomplete")
        return {"entries_deleted": r.counts.get("records", 0), "key_destroyed": r.key_destroyed}

    def destroy(self, conversation_id, role="erasure"):
        """Raises VaultError('erasure_incomplete') unless EVERY job completed; a retry also picks up owners whose job was left open."""
        with self.svc.ctx.db.transaction() as tx:
            owners = list(dict.fromkeys(self.svc.ctx.repo.borrowers_in_conversation(tx, conversation_id) + self.svc.ctx.repo.owners_with_open_jobs(tx, conversation_id)))
        n, incomplete = 0, False
        for b in owners:
            conv_owner = b.startswith("conv:")
            r = self.svc.deletion.erase(EraseRequest(job_id="compat-" + uuid.uuid4().hex, borrower_id=b, scope="BORROWER" if conv_owner else "CONVERSATION",
                                                     conversation_id=None if conv_owner else conversation_id), _ERASURE)
            incomplete |= r.status != "COMPLETED"
            n += r.counts.get("records", 0)
        if incomplete:
            raise VaultError("erasure_incomplete")
        return n

    def access_log(self):
        ev = self.svc.ctx.events
        return list(getattr(ev, "events", []))
