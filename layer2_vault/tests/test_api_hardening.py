"""Phase 4: API hardening and the central PermissionEngine. Strict validation, consistent error categories (400/422, 401, 403, 409, 413, 429, 500, 503) that never
carry ciphertext / keys / PII / SQL / stack traces, credential expiry / rotation / revocation, auth-failure throttling, per-operation capabilities, scope enforcement."""
import io
import json
import logging
import os
import threading

from fastapi.testclient import TestClient

from layer2_vault.api.routes import create_app
from layer2_vault.config.settings import ServiceIdentity, ServiceRegistry, Settings
from layer2_vault.core.errors import ValidationFailed, VaultDenied, VaultUnavailable
from layer2_vault.services.permission_engine import PermissionEngine
from layer2_vault.tests._util import AU, ER, GW, KO, LI, break_db, expect, make, run, tok_req, who

PH = "9845012345"
H = lambda t, **x: {"Authorization": f"Bearer {t}", **x}
BODY = {"request_id": "r1", "borrower_id": "B1", "conversation_id": "C1", "purpose_id": "collections", "text": f"call {PH}",
        "detected_entities": [{"type": "PHONE", "value": PH, "start": 5, "end": 15}]}
REH = {"request_id": "h1", "borrower_id": "B1", "conversation_id": "C1", "purpose_id": "collections", "destination": "borrower_reply", "tokens": ["<PHONE_1>"]}
SCOPED = "tok-scoped-0000000000000000"


def _rows(v, sql, p=()):
    with v.ctx.db.transaction() as tx:
        return tx.query(sql, p)


def _row(v, sql, p=()):
    with v.ctx.db.transaction() as tx:
        return tx.one(sql, p)


class Clock:
    def __init__(self, t=1_700_000_000.0): self.t = t
    def __call__(self): return self.t
    def advance(self, s): self.t += s


def app(**kw):
    s, v = make(**kw); return s, v, TestClient(create_app(v), raise_server_exceptions=False)


FORBIDDEN_IN_ERRORS = ("Traceback", "sqlite", "psycopg", "SELECT ", "INSERT ", "/home/", "wrapped_dek", "ciphertext", "password", PH, "File \"")


def clean(r):
    for bad in FORBIDDEN_IN_ERRORS:
        assert bad not in r.text, f"{bad!r} leaked in a {r.status_code} response: {r.text[:200]}"


# ---------------------------------------------------------------- validation and sizes
def test_an_oversized_body_is_refused_with_413_before_it_is_parsed_whether_declared_or_streamed():
    s, v, c = app(max_body_bytes=3000)
    big = {**BODY, "text": "x" * 5000}
    r = c.post("/vault/tokenize", headers=H(GW), json=big); assert r.status_code == 413 and r.json()["code"] == "request_too_large"; clean(r)
    def chunks():
        for _ in range(10): yield b"a" * 1000
    r = c.post("/vault/tokenize", headers={**H(GW), "Content-Type": "application/json"}, content=chunks()); assert r.status_code == 413      # no Content-Length: counted as it streams
    assert c.post("/vault/tokenize", headers=H(GW), json=BODY).status_code == 200


def test_text_and_entity_counts_over_the_configured_limits_are_422():
    s, v, c = app(max_text_len=100, max_entities=2)
    r = c.post("/vault/tokenize", headers=H(GW), json={**BODY, "text": "y" * 300, "detected_entities": []}); assert r.status_code == 422 and r.json()["code"] == "request_too_large"
    ents = [{"type": "PHONE", "value": "98450", "start": i * 6, "end": i * 6 + 5} for i in range(3)]
    r = c.post("/vault/tokenize", headers=H(GW), json={**BODY, "text": "98450 " * 3, "detected_entities": ents}); assert r.status_code == 422 and r.json()["code"] == "request_too_large"


def test_malformed_ids_pii_types_and_values_are_422_and_never_echoed():
    s, v, c = app(); evil = ["", " ", "a b", "x;DROP TABLE vault_records", "B1' OR '1'='1", "ಬಿ1", "a" * 129, "../../etc/passwd", "B1\nB2", "<script>"]
    for field in ("borrower_id", "conversation_id", "purpose_id", "request_id"):
        for bad in evil:
            r = c.post("/vault/tokenize", headers=H(GW), json={**BODY, field: bad}); assert r.status_code == 422, (field, bad); clean(r)
            assert bad.strip() == "" or bad not in r.text
    for bad_type in ("phone", "PHONE ", "PH ONE", "X" * 40, "P'; --", ""):
        ents = [{"type": bad_type, "value": PH, "start": 5, "end": 15}]
        assert c.post("/vault/tokenize", headers=H(GW), json={**BODY, "detected_entities": ents}).status_code == 422, bad_type
    for ents in ([{"type": "PHONE", "value": "0000000000", "start": 5, "end": 15}], [{"type": "PHONE", "value": PH, "start": 5, "end": 99}],
                 [{"type": "PHONE", "value": PH, "start": 15, "end": 5}], [{"type": "PHONE", "value": PH, "start": -1, "end": 15}]):
        r = c.post("/vault/tokenize", headers=H(GW), json={**BODY, "detected_entities": ents}); assert r.status_code == 422; clean(r)      # wrong offsets can never mask the wrong region
    for rt in (["PHONE_1"], ["<phone_1>"], ["<PHONE_1> extra"], []):
        assert c.post("/vault/rehydrate", headers=H(GW), json={**REH, "tokens": rt}).status_code == 422, rt
    assert c.get("/vault/records/bad id", headers=H(LI)).status_code in (404, 422) and c.get("/vault/records/" + "a" * 200, headers=H(LI)).status_code == 422
    assert c.get("/vault/records/x;y/lifecycle", headers=H(LI)).status_code in (404, 422)


def test_invalid_key_versions_and_rotation_arguments_are_rejected():
    s, v, c = app(master_secrets={1: os.urandom(32), 2: os.urandom(32)}, current_key_version=1)
    for body in ({"target_key_version": 0}, {"target_key_version": -1}, {"target_key_version": 10001}, {"target_key_version": "2"}, {"target_key_version": 2.5}, {"target_key_version": None}, {},
                 {"target_key_version": 2, "operation": "DROP"}, {"target_key_version": 2, "operation": "DEK", "target_dek_version": 1}, {"target_key_version": 2, "extra": 1}):
        assert c.post("/vault/rotate-key", headers=H(KO), json=body).status_code == 422, body
    r = c.post("/vault/rotate-key", headers=H(KO), json={"target_key_version": 9}); assert r.status_code == 422 and r.json()["code"] == "target_version_unavailable"
    v.ctx.keys.current_version = 2; v.ctx.keys.revoke_version(1)
    r = c.post("/vault/rotate-key", headers=H(KO), json={"target_key_version": 1}); assert r.status_code == 422 and r.json()["code"] == "target_version_unavailable"


def test_a_role_or_identity_written_in_the_json_is_rejected_and_never_trusted():
    s, v, c = app()
    for extra in ({"role": "admin"}, {"is_authorised": True}, {"caller": "gateway"}, {"identity": {"capabilities": ["tokenize"]}}):
        assert c.post("/vault/tokenize", headers=H(GW), json={**BODY, **extra}).status_code == 422
    r = c.post("/vault/tokenize", headers={**H(AU), "X-Role": "admin", "X-Service": "gateway"}, json=BODY); assert r.status_code == 403, "AU holds no capability: headers cannot grant any"
    assert c.post("/vault/tokenize", headers={"X-Service": "gateway"}, json=BODY).status_code == 401


# ---------------------------------------------------------------- credentials: expiry, rotation, revocation, throttling
def test_expired_and_not_yet_valid_credentials_are_401_and_rotation_allows_an_overlap():
    clk = Clock(); reg = ServiceRegistry({"tok-old-0000000000000000": ("gateway", {"tokenize"}, {"expires_at": clk.t + 100}),
                                           "tok-new-0000000000000000": ("gateway", {"tokenize"}, {"not_before": clk.t + 50}),
                                           "tok-later-000000000000000": ("later", {"tokenize"}, {"not_before": clk.t + 500})}, clock=clk)
    s, v, c = app(registry=reg, clock=clk)
    ok = lambda t: c.post("/vault/tokenize", headers=H(t), json=BODY).status_code
    assert ok("tok-old-0000000000000000") == 200 and ok("tok-new-0000000000000000") == 401, "new credential not valid yet"
    clk.advance(60); assert ok("tok-old-0000000000000000") == 200 and ok("tok-new-0000000000000000") == 200, "overlap window: both work"
    clk.advance(50); assert ok("tok-old-0000000000000000") == 401 and ok("tok-new-0000000000000000") == 200, "old credential expired"
    assert ok("tok-later-000000000000000") == 401
    clk.advance(1000); assert ok("tok-later-000000000000000") == 200


def test_a_revoked_identity_or_credential_is_refused_immediately_even_mid_session():
    s, v, c = app(); assert c.post("/vault/tokenize", headers=H(GW), json=BODY).status_code == 200
    s.registry.revoke_identity("gateway")
    r = c.post("/vault/tokenize", headers=H(GW), json=BODY); assert r.status_code == 401 and r.json()["code"] == "unauthenticated"
    assert c.post("/vault/rehydrate", headers=H(GW), json=REH).status_code == 401
    assert c.post("/vault/erase", headers=H(ER), json={"job_id": "J1", "borrower_id": "B1"}).status_code == 200, "other identities are unaffected"
    reg = ServiceRegistry({"tok-a-00000000000000000": ("svc", {"tokenize"}), "tok-b-00000000000000000": ("svc", {"tokenize"})}); reg.revoke_credential("tok-a-00000000000000000")
    assert reg.authenticate("tok-a-00000000000000000") is None and reg.authenticate("tok-b-00000000000000000").name == "svc"


def test_credentials_are_configured_from_the_environment_with_rotation_scope_and_revocation():
    env = {"VAULT_MASTER_KEY": os.urandom(32).hex(), "VAULT_REVOKED_SERVICES": '["old-svc"]',
           "VAULT_SERVICE_TOKENS": json.dumps({"gateway": {"capabilities": ["tokenize"], "purposes": ["collections"], "borrowers": ["P-*"],
                                                           "tokens": [{"token": "tok-g1-00000000000000000", "expires_at": 9e9}, {"token": "tok-g2-00000000000000000"}]},
                                               "old-svc": {"capabilities": ["tokenize"], "token": "tok-old-00000000000000000"}})}
    st = Settings.from_env(env); g1, g2 = st.registry.authenticate("tok-g1-00000000000000000", now=1.0), st.registry.authenticate("tok-g2-00000000000000000")
    assert g1 == g2 and g1.borrower_scope == ("P-*",) and g1.allowed_purposes == frozenset({"collections"})
    assert st.registry.authenticate("tok-old-00000000000000000") is None and st.registry.authenticate("tok-g1-00000000000000000", now=9e9 + 1) is None
    env["VAULT_SERVICE_TOKENS"] = json.dumps({"a": {"capabilities": ["tokenize"], "token": "tok-same-0000000000000"}, "b": {"capabilities": ["tokenize"], "token": "tok-same-0000000000000"}})
    expect(ValidationFailed, lambda: Settings.from_env(env), "service_token_duplicated")
    expect(ValidationFailed, lambda: ServiceRegistry({"tok-a-00000000000000000": ("svc", {"tokenize"}, {"borrowers": ["B1"]}), "tok-b-00000000000000000": ("svc", {"tokenize"})}), "service_registry_invalid")
    expect(ValidationFailed, lambda: ServiceRegistry({"tok-a-00000000000000000": ("svc", {"tokenize"}, {"expires_at": "tomorrow"})}), "service_registry_invalid")
    expect(ValidationFailed, lambda: ServiceRegistry({"tok-a-00000000000000000": ("svc", {"tokenize"}, {"colour": "red"})}), "service_registry_invalid")


def test_repeated_authentication_failures_lock_the_client_out_with_429_until_the_window_passes():
    clk = Clock(); s, v, c = app(clock=clk, auth_fail_limit=5, auth_fail_window=60.0)
    for i in range(5): assert c.post("/vault/tokenize", headers=H(f"wrong-token-{i:016d}"), json=BODY).status_code == 401
    r = c.post("/vault/tokenize", headers=H(GW), json=BODY); assert r.status_code == 429 and r.json()["code"] == "too_many_auth_failures" and r.headers["retry-after"] == "60"
    assert c.post("/vault/tokenize", json=BODY).status_code == 429, "locked out for every request, with or without a credential"
    clk.advance(61); assert c.post("/vault/tokenize", headers=H(GW), json=BODY).status_code == 200
    for _ in range(20): assert c.post("/vault/tokenize", headers=H(GW), json={**BODY, "request_id": "again"}).status_code == 200, "successes never count against the limit"


def test_no_credential_or_pii_appears_in_the_logs_even_when_requests_fail():
    s, v, c = app(); buf = io.StringIO(); h = logging.StreamHandler(buf); lgs = [logging.getLogger(n) for n in ("te03.vault", "te03.vault.api")]
    for lg in lgs: lg.setLevel(logging.DEBUG); lg.addHandler(h)
    try:
        c.post("/vault/tokenize", headers=H("super-secret-wrong-credential-123"), json=BODY); c.post("/vault/tokenize", headers=H(GW), json=BODY)
        c.post("/vault/tokenize", headers=H(GW), json={**BODY, "borrower_id": "bad id"}); c.post("/vault/rehydrate", headers=H(GW), json=REH)
        v.ctx.db.transaction = lambda: (_ for _ in ()).throw(RuntimeError(f"boom {PH} hunter2"))
        c.post("/vault/tokenize", headers=H(GW), json=BODY)
    finally:
        for lg in lgs: lg.removeHandler(h)
    logs = buf.getvalue(); assert logs
    for secret in ("super-secret-wrong-credential-123", GW, "Bearer", PH, "hunter2", "call 98450"): assert secret not in logs, secret


# ---------------------------------------------------------------- capabilities and scope
def test_every_endpoint_enforces_its_own_capability():
    s, v, c = app(); idents = {"gateway": GW, "erasure": ER, "lineage": LI, "keyops": KO, "audit": AU}
    calls = {"tokenize": lambda t: c.post("/vault/tokenize", headers=H(t), json=BODY), "rehydrate": lambda t: c.post("/vault/rehydrate", headers=H(t), json=REH),
             "erase": lambda t: c.post("/vault/erase", headers=H(t), json={"job_id": "Jx", "borrower_id": "NOBODY"}),
             "rotate": lambda t: c.post("/vault/rotate-key", headers=H(t), json={"target_key_version": 1}), "health": lambda t: c.get("/vault/health", headers=H(t)),
             "metadata": lambda t: c.get("/vault/records/v_x", headers=H(t)), "retention": lambda t: c.post("/vault/retention/cleanup", headers=H(t))}
    for op, call in calls.items():
        for name, tok in idents.items():
            r = call(tok); holds = op in who(s, tok).capabilities
            assert (r.status_code == 403) == (not holds) or (holds and r.status_code in (404,)), (op, name, r.status_code, r.text)
            if not holds: assert r.json()["code"] in ("forbidden",), (op, name, r.json())


def scoped_app(**ident_opts):
    reg = ServiceRegistry({SCOPED: ("scoped", {"tokenize", "rehydrate", "erase", "metadata"}, ident_opts),
                           GW: ("gateway", {"tokenize", "rehydrate", "health"}), ER: ("erasure", {"erase", "metadata"})})
    return app(registry=reg)


def test_a_valid_identity_cannot_touch_a_borrower_outside_its_scope():
    s, v, c = scoped_app(borrowers=["B1", "P-*"]); db_rows = lambda: len(_rows(v, "SELECT 1 FROM borrower_state"))
    for b in ("B1", "P-7", "P-"):
        assert c.post("/vault/tokenize", headers=H(SCOPED), json={**BODY, "borrower_id": b, "request_id": "r" + b.replace("-", "")}).status_code == 200, b
    n_before = len(_rows(v, "SELECT 1 FROM borrower_state"))
    for b in ("B2", "b1", "B1x", "XP-7", "B1 "[:2] + "9"):
        r = c.post("/vault/tokenize", headers=H(SCOPED), json={**BODY, "borrower_id": b}); assert r.status_code == 403 and r.json()["code"] == "forbidden", b
        assert c.post("/vault/rehydrate", headers=H(SCOPED), json={**REH, "borrower_id": b}).status_code == 403
        assert c.post("/vault/erase", headers=H(SCOPED), json={"job_id": "J-" + b[:2], "borrower_id": b}).status_code == 403
    assert len(_rows(v, "SELECT 1 FROM borrower_state")) == n_before, "a denied request created nothing (no borrower row, no key, no record)"
    c.post("/vault/tokenize", headers=H(GW), json={**BODY, "borrower_id": "OTHER", "conversation_id": "C9"})                     # an unscoped service writes a record for another borrower
    vid = _row(v, "SELECT vault_id FROM vault_records WHERE borrower_id = 'OTHER'")["vault_id"]
    assert c.get(f"/vault/records/{vid}", headers=H(SCOPED)).status_code == 404, "metadata outside the scope looks exactly like a missing record"
    assert c.get(f"/vault/records/{vid}", headers=H(ER)).status_code == 200
    assert c.post("/vault/rehydrate", headers=H(SCOPED), json={**REH, "borrower_id": "B1"}).status_code == 200, "inside the scope it works"


def test_purpose_scope_and_missing_context_are_denied_uniformly():
    s, v, c = scoped_app(purposes=["Collections"])
    assert c.post("/vault/tokenize", headers=H(SCOPED), json=BODY).status_code == 200
    r = c.post("/vault/tokenize", headers=H(SCOPED), json={**BODY, "purpose_id": "service"}); assert r.status_code == 403 and r.json()["code"] == "forbidden"
    eng = v.ctx.engine; ident = ServiceIdentity("x", frozenset({"tokenize", "rehydrate", "erase"}))
    for args in (("tokenize", None, "collections"), ("tokenize", "B1", None), ("tokenize", "", "collections"), ("rehydrate", "B1", None), ("erase", None, None)):
        expect(VaultDenied, lambda a=args: eng.authorize(ident, *a), "forbidden")
    expect(VaultDenied, lambda: eng.authorize(None, "tokenize", "B1", "collections"), "forbidden")
    expect(VaultDenied, lambda: eng.authorize(ident, "admin", "B1", "collections"), "forbidden")
    assert eng.authorize(ident, "erase", "B1") is True


def test_the_engine_applies_layer4_policy_and_fails_closed_when_it_is_down():
    class Allow:
        def check(self, *a): return True
    class Deny:
        def check(self, *a): return False
    class Down:
        def check(self, *a): raise ConnectionError("policy service unreachable")
    ident = ServiceIdentity("x", frozenset({"tokenize", "rehydrate"}))
    assert PermissionEngine(Allow()).authorize(ident, "tokenize", "B1", "p")
    expect(VaultDenied, lambda: PermissionEngine(Deny()).authorize(ident, "tokenize", "B1", "p"), "purpose_not_permitted")
    expect(VaultDenied, lambda: PermissionEngine(Deny()).authorize(ident, "rehydrate", "B1", "p", "dest"), "purpose_or_destination_not_permitted")
    expect(VaultUnavailable, lambda: PermissionEngine(Down()).authorize(ident, "rehydrate", "B1", "p", "dest"), "permission_unavailable")
    assert PermissionEngine(Deny()).authorize(ident, "tokenize", "B1", "p", policy=False)


def test_the_permission_engine_is_separate_from_key_management():
    import ast
    import layer2_vault.services.permission_engine as m
    imported = {n.module or "" for n in ast.walk(ast.parse(open(m.__file__).read())) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(ast.parse(open(m.__file__).read())) if isinstance(n, ast.Import) for a in n.names}
    assert imported == {"core.errors"} or all(("errors" in i) for i in imported), imported          # it imports error classes only: no keys, crypto, database, repository


def test_internal_operations_also_respect_the_borrower_scope():
    s, v = make(); g = who(s, GW)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], borrower="B1"), g); v.tokenizer.tokenize(tok_req("9811111111", [("PHONE", "9811111111")], borrower="B2", rid="x"), g)
    sweeper = ServiceIdentity("sweeper", frozenset({"erasure_sweep", "scan", "tokenize"}), None, ("B1",))
    assert v.internal.find_unmapped_copies("B1", f"x {PH}", sweeper) is True
    expect(VaultDenied, lambda: v.internal.find_unmapped_copies("B2", "9811111111", sweeper), "forbidden"); expect(VaultDenied, lambda: v.internal.erasure_scan("B2", ["x"], sweeper), "forbidden"); expect(VaultDenied, lambda: v.internal.match_borrower("B2", ["9811111111"], sweeper), "forbidden")
    hits = v.internal.scan_text("C1", f"{PH} and 9811111111", sweeper); assert len(hits) == 1, "only the borrower inside the scope is scanned"
    expect(VaultDenied, lambda: v.internal.extend("C1", "<PHONE_2>", sweeper, joined="98111111112"), "token_not_found")        # another borrower's token cannot be extended


# ---------------------------------------------------------------- duplicates, concurrency, failures, leakage
def test_a_duplicate_request_never_makes_a_second_mapping_and_concurrent_http_requests_agree():
    s, v, c = app(); a = c.post("/vault/tokenize", headers=H(GW), json=BODY).json(); b = c.post("/vault/tokenize", headers=H(GW), json={**BODY, "request_id": "r2"}).json()
    assert a["mappings"] == b["mappings"] and len(_rows(v, "SELECT 1 FROM vault_records")) == 1
    out, errs = [], []
    def work(i):
        try: out.append(TestClient(create_app(v)).post("/vault/tokenize", headers=H(GW), json={**BODY, "request_id": f"c{i}", "conversation_id": "CC"}).json()["masked_text"])
        except Exception as e: errs.append(repr(e))
    ts = [threading.Thread(target=work, args=(i,)) for i in range(12)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs and set(out) == {"call <PHONE_1>"}, (errs, set(out))


def test_a_database_disconnect_is_503_with_no_text_and_nothing_sensitive():
    s, v, c = app(); c.post("/vault/tokenize", headers=H(GW), json=BODY); break_db(v)
    for r in (c.post("/vault/tokenize", headers=H(GW), json=BODY), c.post("/vault/rehydrate", headers=H(GW), json=REH), c.post("/vault/erase", headers=H(ER), json={"job_id": "J", "borrower_id": "B1"})):
        assert r.status_code == 503, r.text; clean(r); assert "masked_text" not in r.text and "values" not in r.text
    assert c.get("/vault/health", headers=H(GW)).json()["status"] == "degraded"


def test_an_unexpected_exception_is_a_plain_500_that_leaks_nothing():
    s, v, c = app()
    def boom(*a, **k): raise RuntimeError(f"SELECT * FROM secrets WHERE phone='{PH}' password=hunter2 File \"/home/x/y.py\"")
    v.tokenizer.tokenize = boom
    r = c.post("/vault/tokenize", headers=H(GW), json=BODY); assert r.status_code == 500 and r.json()["code"] == "internal_error" and "masked_text" not in r.text
    clean(r); assert "hunter2" not in r.text
    assert set(r.json()) <= {"status", "code", "request_id"}


def test_no_error_response_in_a_wide_battery_contains_pii_keys_sql_or_a_stack_trace():
    s, v, c = app(max_body_bytes=4000); c.post("/vault/tokenize", headers=H(GW), json=BODY)
    battery = [c.post("/vault/tokenize", content=b"{not json" + PH.encode(), headers={**H(GW), "Content-Type": "application/json"}),
               c.post("/vault/tokenize", content=PH.encode(), headers={**H(GW), "Content-Type": "text/plain"}),
               c.post("/vault/tokenize", headers=H(GW), json={**BODY, "detected_entities": [{"type": "PHONE", "value": PH, "start": 0, "end": 4}]}),
               c.post("/vault/tokenize", headers=H(GW), json={**BODY, "text": PH * 1000, "detected_entities": []}),
               c.post("/vault/tokenize", headers=H("bad-bad-bad-bad-bad-bad"), json=BODY), c.post("/vault/rehydrate", headers=H(GW), json={**REH, "borrower_id": "NOBODY"}),
               c.post("/vault/rehydrate", headers=H(GW), json={**REH, "tokens": ["<PHONE_9>"]}), c.post("/vault/rehydrate", headers=H(GW), json={**REH, "destination": "somewhere_else"}),
               c.get("/vault/records/" + "z" * 200, headers=H(LI)), c.get("/vault/nope", headers=H(GW)), c.put("/vault/tokenize", headers=H(GW), json=BODY),
               c.post("/vault/erase", headers=H(GW), json={"job_id": "J", "borrower_id": "B1"}), c.post("/vault/rotate-key", headers=H(KO), json={"target_key_version": 9})]
    for r in battery:
        assert r.status_code >= 400, r.text; clean(r)
        for k in v.ctx.keys._masters.values(): assert k.hex() not in r.text
    assert [r.status_code for r in battery][:4] == [422, 422, 422, 413]


if __name__ == "__main__":
    run(globals())
