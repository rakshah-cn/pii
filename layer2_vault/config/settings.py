"""Validated settings and secret handling.

Secrets come from the environment (prototype arrangement): VAULT_MASTER_KEY (hex or base64, >= 32 bytes), optional
VAULT_MASTER_KEY_V<n> for key versions, VAULT_KEY_VERSION, VAULT_DB_URL, VAULT_SERVICE_TOKENS (JSON).
Production note: use a managed KMS/HSM instead of an environment secret.
Unlike the legacy single-file vault, there is NO fallback to a session key: a missing/short master secret stops start-up."""
import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field

from shared.constants import PURPOSE_ALIASES, PURPOSES, canonical_purpose
from ..core.errors import ValidationFailed
from ..core.key_manager import MIN_SECRET_BYTES

CAPABILITIES = frozenset({"tokenize", "rehydrate", "metadata", "erase", "rotate", "health", "erasure_sweep", "scan", "admin", "retention", "context"})


def decode_secret(text: str) -> bytes:
    text = (text or "").strip()
    for fn in (lambda t: bytes.fromhex(t), lambda t: base64.b64decode(t, validate=True)):
        try:
            b = fn(text)
            if len(b) >= MIN_SECRET_BYTES:
                return b
        except (ValueError, binascii.Error):
            pass
    raise ValidationFailed("master_secret_invalid_or_too_short")


@dataclass(frozen=True)
class RetentionPolicy:
    """Retention comes from an APPROVED policy (compliance / legal), never from a number picked in code.
    ttl_seconds: how long a token mapping + its encrypted record stay usable after creation. None = no expiry (the safe default for a
    deployment that has not been given a policy: nothing silently disappears, but also nothing is ever auto-minimised - say so in the DPIA).
    purpose_ttl_seconds: per-purpose override (purpose names are matched case-insensitively).
    delete_grace_seconds: after expiry the record is unusable immediately; its ciphertext is hard-deleted this long afterwards (0 = at the next cleanup).
    tombstone_ttl_seconds / operations_ttl_seconds: how long the PII-free deletion tombstones and the operations log are kept (None = keep).
    idempotency_ttl_seconds, batch_size: OPERATIONAL settings, not privacy policy.
    destroy_idle_keys: after cleanup, destroy a borrower's DEK when no record at all still depends on it (reason RETENTION). A later tokenise mints a NEW DEK."""
    ttl_seconds: float = None
    purpose_ttl_seconds: dict = field(default_factory=dict)
    delete_grace_seconds: float = 0
    tombstone_ttl_seconds: float = None
    operations_ttl_seconds: float = None
    idempotency_ttl_seconds: float = 86400
    idempotency_stale_seconds: float = 120               # an IN_PROGRESS claim this old is presumed dead and may be taken over
    context_ttl_seconds: float = 3600                    # shared conversation context (encrypted recent raw turns) is dropped this long after its last update: an OPERATIONAL choice, keep it short
    batch_size: int = 500
    destroy_idle_keys: bool = False

    def validate(self):
        vals = [self.ttl_seconds, self.tombstone_ttl_seconds, self.operations_ttl_seconds, *self.purpose_ttl_seconds.values()]
        if any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0) for v in vals):
            raise ValidationFailed("retention_ttl_must_be_positive_or_none")
        if self.delete_grace_seconds < 0 or not (0 < self.idempotency_ttl_seconds) or not (0 < self.idempotency_stale_seconds) or not (0 < self.context_ttl_seconds) or not (1 <= self.batch_size <= 500):
            raise ValidationFailed("retention_settings_invalid")
        return self

    def ttl_for(self, purpose_id):
        return self.purpose_ttl_seconds.get(canonical_purpose(purpose_id), self.ttl_seconds)

    @classmethod
    def from_json(cls, text):
        d = json.loads(text) if text else {}
        unknown = set(d) - {"ttl_seconds", "purpose_ttl_seconds", "delete_grace_seconds", "tombstone_ttl_seconds", "operations_ttl_seconds", "idempotency_ttl_seconds", "idempotency_stale_seconds", "batch_size", "destroy_idle_keys", "context_ttl_seconds"}
        if unknown:
            raise ValidationFailed("retention_policy_unknown_field")
        if "purpose_ttl_seconds" in d:
            d["purpose_ttl_seconds"] = {canonical_purpose(k): v for k, v in d["purpose_ttl_seconds"].items()}
        return cls(**d).validate()


@dataclass(frozen=True)
class ServiceIdentity:
    """Who is calling, as proven by a credential (never by anything in a request body).
    capabilities     : which operations it may call at all.
    allowed_purposes : None = any purpose; otherwise only these (case-insensitive).
    borrower_scope   : None = any borrower; otherwise exact ids, or a prefix ending in '*' (e.g. "portfolioA-*"). Enforced by PermissionEngine."""
    name: str
    capabilities: frozenset
    allowed_purposes: frozenset = None
    borrower_scope: tuple = None

    def covers_borrower(self, borrower_id) -> bool:
        if self.borrower_scope is None:
            return True
        return isinstance(borrower_id, str) and any(borrower_id == b or (b.endswith("*") and borrower_id.startswith(b[:-1])) for b in self.borrower_scope)

    def covers_purpose(self, purpose_id) -> bool:
        return self.allowed_purposes is None or (isinstance(purpose_id, str) and canonical_purpose(purpose_id) in self.allowed_purposes)


class ServiceRegistry:
    """Maps a bearer credential to a service identity. Only SHA-256 digests of tokens are kept; comparison is constant-time.
    The caller identity is derived HERE (from the credential), never from a field the caller writes in a request.
    entries: {token: (name, capabilities)} or {token: (name, capabilities, options)} with options any of
      expires_at / not_before (epoch seconds), purposes (list), borrowers (list).
    CREDENTIAL ROTATION: a service may hold several credentials at once (old + new during the overlap window); they must all carry the same
    capabilities and scope. Give the old one an expires_at, or call revoke_credential() once the new one is deployed.
    REVOCATION: revoke_identity(name) rejects every credential of that service immediately; revoke_credential(token) rejects one."""

    def __init__(self, entries=None, clock=None):
        import time
        self._items, self._by_name, self._revoked_names, self._revoked_digests = [], {}, set(), set()
        self.clock = clock or time.time
        for token, spec in (entries or {}).items():
            name, caps = spec[0], spec[1]
            opts = dict(spec[2]) if len(spec) > 2 and spec[2] else {}
            def _names(v):
                """A scope must be a LIST of non-empty strings: a bare string would silently become a tuple of single characters, and a lone '*' would match every borrower."""
                return isinstance(v, (list, tuple, set, frozenset)) and all(isinstance(x, str) and x.strip() and x != "*" for x in v)
            if (opts.get("borrowers") is not None and not _names(opts["borrowers"])) or (opts.get("purposes") is not None and not _names(opts["purposes"])):
                raise ValidationFailed("service_registry_invalid")
            bad = set(caps) - CAPABILITIES
            if (bad or not isinstance(token, str) or len(token) < 16 or isinstance(caps, str) or set(opts) - {"expires_at", "not_before", "purposes", "borrowers"}
                    or any(opts.get(k) is not None and (isinstance(opts[k], bool) or not isinstance(opts[k], (int, float))) for k in ("expires_at", "not_before"))):
                raise ValidationFailed("service_registry_invalid")
            ident = ServiceIdentity(name, frozenset(caps),
                                    None if opts.get("purposes") is None else frozenset(canonical_purpose(x) for x in opts["purposes"]),
                                    None if opts.get("borrowers") is None else tuple(opts["borrowers"]))
            if name in self._by_name and self._by_name[name] != ident:
                raise ValidationFailed("service_registry_invalid")          # one service name = one set of rights, however many credentials it holds
            digest = hashlib.sha256(token.encode()).digest()
            if any(d == digest for d, *_ in self._items):
                raise ValidationFailed("service_registry_invalid")          # the same credential twice
            self._by_name[name] = ident
            self._items.append((digest, ident, opts.get("not_before"), opts.get("expires_at")))

    def revoke_identity(self, name):
        self._revoked_names.add(name)

    def revoke_credential(self, token):
        self._revoked_digests.add(hashlib.sha256(token.encode()).digest())

    def authenticate(self, token, now=None):
        if not token or not isinstance(token, str):
            return None
        now = self.clock() if now is None else now
        digest, found = hashlib.sha256(token.encode()).digest(), None
        for d, ident, nbf, exp in self._items:                 # no early exit: constant-time over the registry
            if hmac.compare_digest(d, digest):
                found = (ident, nbf, exp, d)
        if found is None:
            return None
        ident, nbf, exp, d = found
        if ident.name in self._revoked_names or d in self._revoked_digests or (nbf is not None and now < nbf) or (exp is not None and now >= exp):
            return None                                        # revoked / not yet valid / expired: indistinguishable from an unknown credential (401)
        return ident


def _with_revocations(registry, env):
    for name in json.loads(env.get("VAULT_REVOKED_SERVICES", "[]")):
        registry.revoke_identity(name)
    return registry


@dataclass
class Settings:
    master_secrets: dict = field(repr=False)                # {key_version: secret bytes}
    current_key_version: int = 1
    db_url: str = field(default="sqlite:///:memory:", repr=False)       # may carry a database password: never part of repr() / logs
    allowed_purposes: frozenset = frozenset(PURPOSES) | frozenset(PURPOSE_ALIASES)      # from shared/constants.py: the same list Layer 4 uses
    rehydrate_policy: dict = field(default_factory=dict)    # purpose(casefold) -> set of allowed destinations; EMPTY = deny all
    registry: ServiceRegistry = field(default_factory=ServiceRegistry, repr=False)
    max_body_bytes: int = 2_000_000                         # HTTP request body cap (413). Derived from the schema bounds: 100k chars of text (<=4 bytes each) + 500 entities
    auth_fail_limit: int = 10                               # failed authentications per client address ...
    auth_fail_window: float = 60.0                          # ... within this many seconds => 429 until the window passes
    max_text_len: int = 20000
    max_entities: int = 500
    max_value_len: int = 512
    max_tokens_per_rehydrate: int = 50
    key_usage_limit: int = 2 ** 32                          # encryptions per DEK (= counter values) before encryption is refused and DEK rotation is demanded
    revoked_key_versions: frozenset = frozenset()           # master-secret versions that must never be used again
    db_auto_migrate: bool = True                            # False when the vault runs as the restricted vault_app role (schema changes run as vault_migration)
    require_non_superuser_db_role: bool = True              # PostgreSQL: refuse to start when connected as a superuser
    cache_keys: bool = False                                 # spec: no key caching initially
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)

    def validate(self):
        if not self.master_secrets or self.current_key_version not in self.master_secrets:
            raise ValidationFailed("master_secret_missing")
        if any(len(s) < MIN_SECRET_BYTES for s in self.master_secrets.values()):
            raise ValidationFailed("master_secret_too_short")
        if len({s for s in self.master_secrets.values()}) != len(self.master_secrets):
            raise ValidationFailed("master_secret_versions_must_differ")
        if self.current_key_version in self.revoked_key_versions:
            raise ValidationFailed("current_key_version_revoked")
        if not (1 <= self.key_usage_limit <= 2 ** 63):
            raise ValidationFailed("key_usage_limit_invalid")
        if any(isinstance(d, str) for d in self.rehydrate_policy.values()):
            raise ValidationFailed("rehydrate_policy_values_must_be_lists")   # a bare string would silently become a set of characters
        self.retention.validate()
        return self

    @classmethod
    def from_env(cls, env=None, kms_client=None):
        """Master secret: from AWS KMS when VAULT_KMS_CIPHERTEXT_V<n> is set (see core/master_key_provider.py), otherwise from VAULT_MASTER_KEY (prototype)."""
        from ..core.master_key_provider import AwsKmsMasterKeyProvider, EnvMasterKeyProvider
        env = os.environ if env is None else env
        if any(k.startswith("VAULT_KMS_CIPHERTEXT_V") for k in env):
            secrets_ = AwsKmsMasterKeyProvider.from_env(env, kms_client).load()        # raises VaultUnavailable if KMS cannot be reached: the vault does not start
        else:
            secrets_ = EnvMasterKeyProvider(env).load()
        entries = {}
        if env.get("VAULT_SERVICE_TOKENS"):
            # {"gateway": {"capabilities": [...], "purposes": [...], "borrowers": [...], "tokens": [{"token": "...", "expires_at": 1.7e9}, ...]}}   (or the older single "token": "...")
            for name, spec in json.loads(env["VAULT_SERVICE_TOKENS"]).items():
                creds = spec.get("tokens") or [{"token": spec["token"]}]
                for cred in creds:
                    if cred["token"] in entries:
                        raise ValidationFailed("service_token_duplicated")
                    opts = {k: v for k, v in {**{k: spec[k] for k in ("purposes", "borrowers") if k in spec}, **{k: cred[k] for k in ("expires_at", "not_before") if k in cred}}.items()}
                    entries[cred["token"]] = (name, spec.get("capabilities", []), opts)
        db_url = env.get("VAULT_DB_URL")
        if not db_url:
            if env.get("VAULT_ALLOW_EPHEMERAL") != "1":
                raise ValidationFailed("database_url_missing")                 # same rule as the library path (compat.py): an unconfigured vault must not start on a throw-away in-memory database
            db_url = "sqlite:///:memory:"
        policy = {canonical_purpose(p): frozenset(d) for p, d in json.loads(env.get("VAULT_REHYDRATE_POLICY", "{}")).items()}
        return cls(master_secrets=secrets_, current_key_version=int(env.get("VAULT_KEY_VERSION", max(secrets_) if secrets_ else 1)),
                   db_url=db_url, registry=_with_revocations(ServiceRegistry(entries), env), rehydrate_policy=policy,
                   db_auto_migrate=env.get("VAULT_DB_AUTO_MIGRATE", "true").lower() != "false",
                   revoked_key_versions=frozenset(int(x) for x in json.loads(env.get("VAULT_REVOKED_KEY_VERSIONS", "[]"))),
                   retention=RetentionPolicy.from_json(env.get("VAULT_RETENTION_POLICY", ""))).validate()

    @classmethod
    def for_testing(cls, **kw):
        base = dict(master_secrets={1: os.urandom(32)}, current_key_version=1,
                    rehydrate_policy={"collections": frozenset({"borrower_reply"}), "service": frozenset({"borrower_reply"})},
                    registry=ServiceRegistry({
                        "tok-gateway-0000000000000000": ("gateway", {"tokenize", "rehydrate", "health"}),
                        "tok-erasure-0000000000000000": ("erasure", {"erase", "metadata"}),
                        "tok-lineage-0000000000000000": ("lineage", {"metadata"}),
                        "tok-keyops-00000000000000000": ("keyops", {"rotate", "health"}),
                        "tok-audit-00000000000000000": ("audit", set()),
                        "tok-retention-0000000000000": ("retention", {"retention"}),
                    }))
        base.update(kw)
        return cls(**base).validate()
