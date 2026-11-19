"""FastAPI surface (Layer 2). Authentication = a bearer token mapped to a service identity by the server; the caller never states
its own identity or role. Errors are coded and GENERIC: request bodies (which contain PII) are never echoed back, and bodies are
never logged. Docs/openapi are disabled: this is an internal service."""
import logging
import re
import threading
import time
import uuid
from collections import deque

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..core.errors import IdempotencyError, RateLimited, ValidationFailed, VaultDenied, VaultError, VaultUnavailable
from ..schemas.vault_schemas import (ID_RE, EraseRequest, EraseResponse, HealthResponse, LifecycleResponse, MetadataResponse, RehydrateRequest, RehydrateResponse,
                                     RotateRequest, RotateResponse, TokenizeRequest, TokenizeResponse)
from ..services import VaultServices

log = logging.getLogger("te03.vault.api")
_STATUS = {"invalid_request": 422, "unauthenticated": 401, "forbidden": 403, "denied": 403, "borrower_not_active": 403, "key_not_active": 403,
           "token_not_found": 403, "purpose_or_destination_not_permitted": 403, "purpose_not_permitted": 403, "not_found": 404,
           "vault_unavailable": 503, "internal_error": 500, "request_too_large": 413, "too_many_auth_failures": 429, "audit_unavailable": 503, "permission_unavailable": 503, "decryption_failed": 500}


def _err(code, status=None, request=None, headers=None):
    body = {"status": "error", "code": code}
    rid = getattr(getattr(request, "state", None), "rid", None)
    if rid:
        body["request_id"] = rid                                              # a safe id only (see access_log): lets an operator correlate without echoing input
    return JSONResponse(body, status_code=status or _STATUS.get(code, 400), headers=headers)


class AuthThrottle:
    """Failed authentications per client address: after `limit` failures inside `window` seconds the client gets 429 until the window passes, so a credential cannot
    be guessed at wire speed. In-memory and per process (several vault processes each keep their own count); behind a reverse proxy every client shares the proxy's address -
    put the throttle at the proxy too. Memory is bounded."""

    def __init__(self, limit, window, clock, max_clients=10000):
        self.limit, self.window, self.clock, self.max = limit, window, clock, max_clients
        self._hits, self._lock = {}, threading.Lock()

    def _prune(self, q, now):
        while q and now - q[0] >= self.window:
            q.popleft()

    def blocked(self, client) -> bool:
        with self._lock:
            q = self._hits.get(client)
            if not q:
                return False
            self._prune(q, self.clock())
            if not q:
                del self._hits[client]
            return len(q) >= self.limit

    def failure(self, client):
        with self._lock:
            now = self.clock()
            if len(self._hits) >= self.max and client not in self._hits:
                for k in ([k for k, q in self._hits.items() if not q or now - q[-1] >= self.window][: self.max // 10] or [next(iter(self._hits))]):
                    self._hits.pop(k, None)
            q = self._hits.setdefault(client, deque())
            self._prune(q, now)
            q.append(now)


class BodyLimit:
    """Pure ASGI middleware: a body larger than max_bytes is refused (413) BEFORE it is parsed, whether it announces its size or streams it.
    (FastAPI turns any error raised while it reads the body into a generic 400, so the limit is enforced here, ahead of the framework.)"""

    def __init__(self, app, max_bytes):
        self.app, self.max = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length")
        try:
            too_big = declared is not None and int(declared) > self.max
        except ValueError:
            too_big = True
        if too_big:
            return await _err("request_too_large", 413)(scope, receive, send)
        body, more = b"", True                                              # read the body ourselves, never holding more than max_bytes (+ one chunk)
        while more:
            msg = await receive()
            if msg["type"] != "http.request":
                return                                                      # client went away
            body += msg.get("body", b"")
            if len(body) > self.max:
                return await _err("request_too_large", 413)(scope, receive, send)
            more = msg.get("more_body", False)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        await self.app(scope, replay, send)


def create_app(svc: VaultServices) -> FastAPI:
    app = FastAPI(title="te03 vault", docs_url=None, redoc_url=None, openapi_url=None)
    registry, cfg = svc.ctx.settings.registry, svc.ctx.settings
    throttle = AuthThrottle(cfg.auth_fail_limit, cfg.auth_fail_window, svc.ctx.clock)
    app.add_middleware(BodyLimit, max_bytes=cfg.max_body_bytes)

    def caller(request: Request, authorization: str = Header(default=None)):
        client = request.client.host if request.client else "unknown"
        if throttle.blocked(client):
            raise RateLimited()                                               # checked BEFORE the credential is looked at: a locked-out client learns nothing
        ident = registry.authenticate(authorization[7:].strip()) if authorization and authorization.lower().startswith("bearer ") else None
        if ident is None:
            throttle.failure(client)
            raise VaultDenied("unauthenticated")
        return ident

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        import re
        t0, hdr = time.perf_counter(), request.headers.get("x-request-id") or ""
        rid = hdr if re.match(ID_RE, hdr) else uuid.uuid4().hex[:12]          # a caller-controlled header is only logged if it is a safe id
        request.state.rid = rid
        status = 500
        try:
            resp = await call_next(request)
            status = resp.status_code
            return resp
        finally:                                                                # logged even when the request raised
            log.info("req=%s %s %s -> %s %.1fms", rid, request.method, request.url.path, status, (time.perf_counter() - t0) * 1000)   # no bodies, no query

    @app.exception_handler(VaultError)
    async def _vault_error(request: Request, e: VaultError):
        # status by CLASS, so a new error code can never fall through to a wrong status (an unavailable dependency is always 503)
        status = 429 if isinstance(e, RateLimited) else 409 if isinstance(e, IdempotencyError) else 503 if isinstance(e, VaultUnavailable) else 403 if isinstance(e, VaultDenied) else 422 if isinstance(e, ValidationFailed) else _STATUS.get(e.code, 400)
        return _err(e.code, {"unauthenticated": 401, "not_found": 404}.get(e.code, status), request, {"Retry-After": str(int(cfg.auth_fail_window))} if isinstance(e, RateLimited) else None)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_, e: RequestValidationError):
        return JSONResponse({"status": "error", "code": "invalid_request", "fields": [".".join(str(x) for x in err["loc"][1:]) for err in e.errors()]}, status_code=422)   # locations only, never input

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, e: Exception):
        log.error("unhandled %s", type(e).__name__)                           # the class name only: never the message (it could contain SQL, values, paths)
        return _err("internal_error", 500, request)                           # fail closed: no result body, callers must not forward raw text

    @app.post("/vault/tokenize", response_model=TokenizeResponse)
    def tokenize(req: TokenizeRequest, who=Depends(caller), idempotency_key: str = Header(default=None)):
        return svc.tokenizer.tokenize(req, who, idempotency_key)

    @app.post("/vault/rehydrate", response_model=RehydrateResponse)
    def rehydrate(req: RehydrateRequest, who=Depends(caller)):
        return svc.rehydration.rehydrate(req, who)

    @app.get("/vault/records/{vault_id}", response_model=MetadataResponse)
    def metadata(vault_id: str, who=Depends(caller)):
        if not re.match(ID_RE, vault_id):
            raise ValidationFailed("invalid_request")
        return svc.tokenizer.get_metadata(vault_id, who)

    @app.get("/vault/records/{vault_id}/lifecycle", response_model=LifecycleResponse)
    def lifecycle(vault_id: str, who=Depends(caller)):
        if not re.match(ID_RE, vault_id):
            raise ValidationFailed("invalid_request")
        return svc.tokenizer.lifecycle(vault_id, who)

    @app.post("/vault/erase", response_model=EraseResponse)
    def erase(req: EraseRequest, who=Depends(caller), idempotency_key: str = Header(default=None)):
        return svc.deletion.erase(req, who, idempotency_key)

    @app.post("/vault/rotate-key", response_model=RotateResponse)
    def rotate(req: RotateRequest, who=Depends(caller), idempotency_key: str = Header(default=None)):
        return svc.lifecycle.rotate(req, who, idempotency_key)

    @app.post("/vault/retention/cleanup")
    def cleanup(who=Depends(caller)):
        return svc.retention.cleanup_expired_records(who)

    @app.get("/vault/health", response_model=HealthResponse)
    def health(who=Depends(caller)):
        svc.ctx.require(who, "health")
        return svc.tokenizer.health()
    return app
