"""Shared wiring for the services: settings, database, repository, key manager, permission provider, event sink."""
import logging
import threading
import time

from ..config.settings import ServiceIdentity, Settings
from ..core.errors import VaultDenied
from ..core.key_manager import KeyManager
from ..core.nonce import NonceSource
from ..database.connection import Database
from ..database.repository import Repository
from .permission_engine import PermissionEngine

log = logging.getLogger("te03.vault")        # only ids, operation types, status codes are ever logged: never values or keys


class PolicyPermissions:
    """Layer 4 stand-in: purpose / destination decisions. Layer 2 ENFORCES them; it does not decide legality itself.
    tokenize : purpose must be a known purpose.   rehydrate: DENY unless (purpose, destination) is explicitly allowed."""

    def __init__(self, settings: Settings):
        self.s = settings

    def check(self, action, purpose_id, destination, caller, borrower_id) -> bool:
        p = str(purpose_id).casefold()
        if action == "tokenize":
            return p in self.s.allowed_purposes
        if action == "rehydrate":
            return destination in self.s.rehydrate_policy.get(p, frozenset())
        return False


class InMemoryEvents:
    """Default event sink (Layer 7 hook): safe metadata only. Replace with a hash-chained audit writer."""

    def __init__(self, cap=100000):
        self.events, self.cap, self._lock = [], cap, threading.Lock()

    def __call__(self, event: dict):
        with self._lock:
            if len(self.events) >= self.cap:
                del self.events[: self.cap // 10]
            self.events.append(dict(event))


class HashChainedEvents:
    """File-backed, append-only, hash-chained event sink (the Layer 7 hook). Safe metadata only.
    Several writers (processes) may share the file: each append takes an exclusive file lock and re-reads the tail, so the chain stays
    valid. A write failure RAISES, so a decrypt path that needs its audit 'attempt' event is refused. verify() re-computes the chain from
    the FILE and requires the first event to start from 'genesis' (deleting the OLDEST events is detected). Deleting the NEWEST events
    leaves a valid shorter chain: anchor head() somewhere outside to detect that."""

    def __init__(self, path):
        self.path, self._lock = path, threading.Lock()

    @staticmethod
    def _tail_hash(fd):
        import json
        import os
        size = os.fstat(fd).st_size
        if size == 0:
            return "genesis"
        data = os.pread(fd, min(size, 65536), max(0, size - 65536))
        lines = [l for l in data.splitlines() if l.strip()]
        return json.loads(lines[-1])["hash"] if lines else "genesis"

    @property
    def head(self):
        import os
        if not os.path.exists(self.path):
            return "genesis"
        fd = os.open(self.path, os.O_RDONLY)
        try:
            return self._tail_hash(fd)
        finally:
            os.close(fd)

    def __call__(self, event: dict):
        import fcntl
        import hashlib
        import json
        import os
        import time
        with self._lock:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)                       # serialise writers across processes
                head = self._tail_hash(fd)
                body = {"ts": time.time(), "prev": head, **event}
                body["hash"] = hashlib.sha256((head + json.dumps({k: v for k, v in body.items() if k != "hash"}, sort_keys=True, default=str)).encode()).hexdigest()
                os.write(fd, (json.dumps(body, default=str) + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)                                          # closing also releases the lock

    def verify(self) -> bool:
        import hashlib
        import json
        import os
        if not os.path.exists(self.path):
            return True
        prev = "genesis"
        for line in open(self.path, encoding="utf-8").read().splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                return False
            body = {k: v for k, v in e.items() if k != "hash"}
            if e.get("prev") != prev or e.get("hash") != hashlib.sha256((str(e.get("prev")) + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest():
                return False
            prev = e["hash"]
        return True


class VaultContext:
    def __init__(self, settings: Settings, db: Database = None, permissions=None, events=None, clock=None):
        self.settings = settings.validate()
        self.clock = clock or time.time                       # UTC epoch seconds; injectable so expiry can be tested without sleeping
        self.db = db or Database(settings.db_url, migrate=settings.db_auto_migrate)
        self.repo = Repository()
        self.keys = KeyManager(settings.master_secrets, settings.current_key_version, settings.revoked_key_versions)
        self.nonces = NonceSource()                           # new random epoch per process; counters come from the database (core/nonce.py)
        self.permissions = permissions or PolicyPermissions(settings)
        self.events = events if events is not None else InMemoryEvents()
        self._engine = None
        if self.db.kind == "postgres" and settings.require_non_superuser_db_role:
            from ..core.errors import VaultUnavailable
            if self.db.role_info()["superuser"]:
                raise VaultUnavailable("db_role_too_privileged")      # the vault must run as vault_app (see database/roles.sql), never as a superuser
        self._idempotency = None

    @property
    def engine(self):
        """Built on first use from the CURRENT permissions provider, so tests / deployments that swap ctx.permissions are honoured."""
        if self._engine is None or self._engine.layer4 is not self.permissions:
            self._engine = PermissionEngine(self.permissions)
        return self._engine

    @property
    def idempotency(self):
        if self._idempotency is None:
            from .idempotency_service import IdempotencyService
            self._idempotency = IdempotencyService(self)
        return self._idempotency

    def emit(self, must=False, **event):
        """Send a safe event to Layer 7. must=True (decrypt paths): if the sink fails the operation is refused (fail closed)."""
        try:
            self.events(event)
        except Exception:
            if must:
                from ..core.errors import VaultUnavailable
                raise VaultUnavailable("audit_unavailable")
            log.warning("event sink failed (op=%s)", event.get("op"))

    @staticmethod
    def require(caller: ServiceIdentity, capability: str):
        if caller is None or capability not in caller.capabilities:
            raise VaultDenied("forbidden")

    def record_failure(self, op_type, borrower_id, request_id, caller, error_code):
        """Best effort: the failing transaction was rolled back, so the denial is written in its own."""
        try:
            with self.db.transaction() as tx:
                self.repo.log_op(tx, op_type, borrower_id, request_id, getattr(caller, "name", None), "DENIED" if error_code in ("denied", "forbidden", "borrower_not_active", "key_not_active", "token_not_found", "purpose_or_destination_not_permitted") else "FAILED", error_code=error_code)
        except Exception:
            pass
