"""Phase 3: PostgreSQL pooling, idempotency, tombstone lifecycle, index review, restricted roles.
Tests marked PG need PostgreSQL (VAULT_TEST_DB_URL); the roles test also needs a scratch cluster where we are superuser (VAULT_TEST_SUPER_URL)."""
import os
import threading
import time
import uuid

import psycopg
from fastapi.testclient import TestClient

from layer2_vault.api.routes import create_app
from layer2_vault.config.settings import RetentionPolicy, ServiceIdentity, Settings
from layer2_vault.core.errors import IdempotencyError, ValidationFailed, VaultDenied, VaultUnavailable
from layer2_vault.database.connection import Database
from layer2_vault.database.models import INDEXES, TABLES
from layer2_vault.schemas.vault_schemas import EraseRequest, RotateRequest
from layer2_vault.services import VaultContext, build_services
from layer2_vault.tests._util import ER, GW, KO, LI, Skip, expect, make, need_postgres, reh_req, run, tok_req, who

PH, PH2 = "9845012345", "9811122233"
H = lambda t: {"Authorization": f"Bearer {t}"}


class Clock:
    def __init__(self, t=1_700_000_000.0): self.t = t
    def __call__(self): return self.t
    def advance(self, s): self.t += s


def q(v, sql, p=()):
    with v.ctx.db.transaction() as tx:
        return tx.query(sql, p)


def pg_url(**params):
    base = os.environ["VAULT_TEST_DB_URL"]
    extra = "&".join(f"{k}={x}" for k, x in {"schema": "p_" + uuid.uuid4().hex[:10], **params}.items())
    return base + ("&" if "?" in base else "?") + extra


# ================================================================ connection pool (PostgreSQL)
def test_pg_one_pool_is_shared_by_all_transactions_and_never_grows_past_its_maximum():
    need_postgres(); db = Database(pg_url(pool_min=1, pool_max=3))
    for _ in range(40):
        with db.transaction() as tx: tx.one("SELECT 1 AS x")
    st = db.pool_stats(); assert st["pool_max"] == 3 and st["pool_size"] <= 3 and st["requests_num"] >= 40; db.close()


def test_pg_more_concurrent_transactions_than_connections_queue_and_all_succeed():
    need_postgres(); db = Database(pg_url(pool_min=0, pool_max=2, pool_timeout=10)); errs, done = [], []
    def work():
        try:
            with db.transaction() as tx: tx.execute("SELECT pg_sleep(0.05)"); done.append(1)
        except Exception as e: errs.append(repr(e))
    ts = [threading.Thread(target=work) for _ in range(10)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs and len(done) == 10 and db.pool_stats()["pool_size"] <= 2; db.close()


def test_pg_a_connection_killed_by_the_server_is_replaced_by_the_health_check():
    need_postgres(); url = pg_url(pool_min=1, pool_max=1); db = Database(url)
    with db.transaction() as tx: pid = tx.one("SELECT pg_backend_pid() AS p")["p"]
    base = os.environ["VAULT_TEST_DB_URL"]
    with psycopg.connect(base, autocommit=True) as admin: admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
    time.sleep(0.2)
    with db.transaction() as tx: assert tx.one("SELECT pg_backend_pid() AS p")["p"] != pid          # the dead connection was detected and a fresh one used
    db.close()


def test_pg_a_failed_transaction_is_rolled_back_and_the_connection_is_reusable():
    need_postgres(); db = Database(pg_url(pool_min=1, pool_max=1))
    try:
        with db.transaction() as tx:
            tx.execute("INSERT INTO vault_meta (key, value) VALUES ('probe', '1')"); raise RuntimeError("boom")
    except RuntimeError: pass
    with db.transaction() as tx:
        assert tx.one("SELECT COUNT(*) AS n FROM vault_meta WHERE key = 'probe'")["n"] == 0, "the insert was rolled back"
        assert tx.one("SELECT COUNT(*) AS n FROM pg_stat_activity WHERE state = 'idle in transaction' AND pid <> pg_backend_pid()")["n"] == 0
    db.close()


def test_pg_an_exhausted_pool_fails_closed_instead_of_hanging():
    need_postgres(); db = Database(pg_url(pool_min=0, pool_max=1, pool_timeout=0.3)); release = threading.Event(); started = threading.Event()
    def hold():
        with db.transaction() as tx: tx.one("SELECT 1 AS x"); started.set(); release.wait(5)
    t = threading.Thread(target=hold); t.start(); started.wait(5)
    t0 = time.time(); expect(VaultUnavailable, lambda: db.transaction().__enter__(), "database_unavailable"); assert time.time() - t0 < 3
    release.set(); t.join(); db.close()


def test_pg_a_closed_pool_means_database_unavailable_and_tokenisation_fails_closed():
    need_postgres(); from layer2_vault.tests._util import break_db
    s, v = make(); g = who(s, GW); break_db(v)
    expect(VaultUnavailable, lambda: v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)); assert v.tokenizer.health()["status"] == "degraded"


def test_pool_settings_are_validated_before_any_connection_is_made():
    for bad in ("pool_min=5&pool_max=2", "pool_max=0", "pool_timeout=0", "pool_max=abc"):
        expect(VaultUnavailable, lambda b=bad: Database(f"postgresql://nobody@/nodb?host=/nonexistent&{b}"), "bad_pool_settings")


def test_pg_the_vault_refuses_to_run_as_a_superuser_database_role():
    need_postgres(); s, v = make()
    assert v.tokenizer.health()["db_role_safe"] is True
    real = v.ctx.db.role_info; v.ctx.db.role_info = lambda: {"role": "postgres", "superuser": True}
    expect(VaultUnavailable, lambda: VaultContext(Settings.for_testing(db_url=s.db_url), db=v.ctx.db), "db_role_too_privileged")
    assert v.tokenizer.health()["db_role_safe"] is False and v.tokenizer.health()["status"] == "degraded"; v.ctx.db.role_info = real


# ================================================================ idempotency
def counting(v):
    calls = []; real = v.tokenizer.resolve
    def resolve(*a, **k): calls.append(1); return real(*a, **k)
    v.tokenizer.resolve = resolve; return calls


def test_same_key_and_same_request_returns_the_first_result_without_executing_again():
    s, v = make(); g = who(s, GW); calls = counting(v)
    a = v.tokenizer.tokenize(tok_req(f"call {PH}", [("PHONE", PH)], rid="first"), g, idempotency_key="key-0001-abcd")
    b = v.tokenizer.tokenize(tok_req(f"call {PH}", [("PHONE", PH)], rid="retry-with-new-id"), g, idempotency_key="key-0001-abcd")
    assert len(calls) == 1 and a.masked_text == b.masked_text == "call <PHONE_1>" and a.mappings == b.mappings
    row = q(v, "SELECT * FROM vault_idempotency")[0]; assert row["status"] == "COMPLETED" and row["operation"] == "TOKENIZE" and row["caller"] == "gateway"


def test_same_key_with_a_different_request_is_rejected_and_stores_nothing_new():
    s, v = make(); g = who(s, GW)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g, idempotency_key="key-0002-abcd")
    e = expect(IdempotencyError, lambda: v.tokenizer.tokenize(tok_req(PH2, [("PHONE", PH2)]), g, idempotency_key="key-0002-abcd"), "idempotency_key_conflict")
    expect(IdempotencyError, lambda: v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], conv="OTHER"), g, idempotency_key="key-0002-abcd"))
    assert len(q(v, "SELECT 1 FROM vault_records")) == 1


def test_concurrent_requests_with_one_key_execute_exactly_once():
    s, v = make(); g = who(s, GW); calls = counting(v); out, errs = [], []
    def work():
        try: out.append(v.tokenizer.tokenize(tok_req(f"call {PH}", [("PHONE", PH)]), g, idempotency_key="key-0003-abcd"))
        except Exception as e: errs.append(repr(e))
    ts = [threading.Thread(target=work) for _ in range(10)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs and len(out) == 10 and len(calls) == 1 and len({o.masked_text for o in out}) == 1
    assert len(q(v, "SELECT 1 FROM vault_records")) == 1 and len(q(v, "SELECT 1 FROM vault_idempotency")) == 1


def test_the_idempotency_row_holds_no_pii_no_text_and_no_key():
    s, v = make(); g = who(s, GW); v.tokenizer.tokenize(tok_req(f"my number is {PH} thanks", [("PHONE", PH)]), g, idempotency_key="key-0004-abcd")
    blob = repr(q(v, "SELECT * FROM vault_idempotency"))
    assert PH not in blob and "my number" not in blob and "thanks" not in blob and "<PHONE_1>" in blob and "v1:" in blob
    for k in v.ctx.keys._masters.values(): assert k.hex() not in blob


def test_a_stale_in_progress_claim_is_recovered_and_a_fresh_one_is_not_stolen():
    clk = Clock(); s, v = make(clock=clk); g, er = who(s, GW), who(s, ER); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    req = EraseRequest(job_id="J-idem", borrower_id="B1")
    with v.ctx.db.transaction() as tx: assert v.ctx.idempotency.claim(tx, "key-0005-abcd", "ERASE", "erasure", req.model_dump()) == ("NEW", None)     # a run that then 'crashed'
    expect(IdempotencyError, lambda: v.deletion.erase(req, er, idempotency_key="key-0005-abcd"), "idempotency_in_progress")
    assert q(v, "SELECT state FROM borrower_state")[0]["state"] == "ACTIVE", "nothing ran while the first claim was fresh"
    clk.advance(v.ctx.settings.retention.idempotency_stale_seconds + 1)
    r = v.deletion.erase(req, er, idempotency_key="key-0005-abcd"); assert r.status == "COMPLETED"
    assert q(v, "SELECT status FROM vault_idempotency")[0]["status"] == "COMPLETED"
    again = v.deletion.erase(req, er, idempotency_key="key-0005-abcd"); assert again.status == "COMPLETED" and again.counts == r.counts


def test_a_partial_failure_releases_the_key_for_a_retry_that_resumes_the_job():
    s, v = make(); g, er = who(s, GW), who(s, ER); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g); req = EraseRequest(job_id="J-fail", borrower_id="B1")
    v.deletion.fault = lambda step: (_ for _ in ()).throw(RuntimeError("x")) if step == "destroy_key" else None
    assert v.deletion.erase(req, er, idempotency_key="key-0006-abcd").status == "PARTIAL_FAILURE"
    assert q(v, "SELECT status FROM vault_idempotency")[0]["status"] == "FAILED"
    v.deletion.fault = None
    ok = v.deletion.erase(req, er, idempotency_key="key-0006-abcd"); assert ok.status == "COMPLETED" and ok.key_destroyed


def test_rotate_is_idempotent_and_rejects_a_different_request_under_the_same_key():
    s, v = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}, current_key_version=1); g, ko = who(s, GW), who(s, KO)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    a = v.lifecycle.rotate(RotateRequest(target_key_version=2, operation="KEK"), ko, idempotency_key="key-0007-abcd")
    b = v.lifecycle.rotate(RotateRequest(target_key_version=2, operation="KEK"), ko, idempotency_key="key-0007-abcd")
    assert a == b and a.keys_rewrapped == 1, "the replay returns the first answer (a second run would have rewrapped 0)"
    expect(IdempotencyError, lambda: v.lifecycle.rotate(RotateRequest(target_key_version=2, operation="DEK"), ko, idempotency_key="key-0007-abcd"))


def test_idempotency_keys_expire_and_are_validated():
    clk = Clock(); s, v = make(clock=clk, retention=RetentionPolicy(idempotency_ttl_seconds=60)); g = who(s, GW); calls = counting(v)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g, idempotency_key="key-0008-abcd"); clk.advance(61)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g, idempotency_key="key-0008-abcd"); assert len(calls) == 2, "after the TTL the key is free again"
    for bad in ("short", "has space in it", "x" * 200, "semi;colon-key", 5):
        expect(ValidationFailed, lambda b=bad: v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g, idempotency_key=b), "invalid_idempotency_key")


def test_a_replay_whose_data_has_since_expired_is_executed_again_not_returned_stale():
    clk = Clock(); s, v = make(clock=clk, retention=RetentionPolicy(ttl_seconds=100)); g = who(s, GW)
    a = v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g, idempotency_key="key-0009-abcd"); clk.advance(150)
    b = v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g, idempotency_key="key-0009-abcd")
    assert a.mappings[0].token == "<PHONE_1>" and b.mappings[0].token == "<PHONE_2>"
    assert v.rehydration.rehydrate(reh_req(["<PHONE_2>"]), g).values == {"<PHONE_2>": PH}


def test_the_same_key_from_two_callers_is_two_independent_claims():
    s, v = make(); a, b = who(s, GW), ServiceIdentity("gateway2", frozenset({"tokenize"})); calls = counting(v)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), a, idempotency_key="key-0010-abcd"); v.tokenizer.tokenize(tok_req(PH2, [("PHONE", PH2)]), b, idempotency_key="key-0010-abcd")
    assert len(calls) == 2 and len(q(v, "SELECT 1 FROM vault_idempotency")) == 2


def test_the_http_header_drives_idempotency_and_a_conflict_is_409():
    s, v = make(); c = TestClient(create_app(v)); body = {"request_id": "r1", "borrower_id": "B1", "conversation_id": "C1", "purpose_id": "collections", "text": f"call {PH}",
                                                         "detected_entities": [{"type": "PHONE", "value": PH, "start": 5, "end": 15}]}
    h = {**H(GW), "Idempotency-Key": "http-key-0001"}
    a, b = c.post("/vault/tokenize", headers=h, json=body), c.post("/vault/tokenize", headers=h, json={**body, "request_id": "r2"})
    assert a.status_code == b.status_code == 200 and a.json()["mappings"] == b.json()["mappings"]
    r = c.post("/vault/tokenize", headers=h, json={**body, "conversation_id": "C2"}); assert r.status_code == 409 and r.json()["code"] == "idempotency_key_conflict"
    assert c.post("/vault/tokenize", headers={**H(GW), "Idempotency-Key": "x"}, json=body).status_code == 422


# ================================================================ tombstones / lifecycle
def test_a_records_lifecycle_distinguishes_never_existed_live_expired_pending_partial_and_deleted():
    clk = Clock(); s, v = make(clock=clk, retention=RetentionPolicy(ttl_seconds=100)); g, er, li = who(s, GW), who(s, ER), who(s, LI)
    life = lambda vid: v.tokenizer.lifecycle(vid, li).lifecycle
    vid = lambda borrower: q(v, "SELECT vault_id FROM vault_records WHERE borrower_id = ?", (borrower,))[0]["vault_id"]
    assert life("v_never_was_here") == "NEVER_EXISTED"
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], borrower="LIVE"), g); assert life(vid("LIVE")) == "ACTIVE"
    clk.advance(101); assert life(vid("LIVE")) == "EXPIRED", "expired is reported even before cleanup has run"
    v.retention.cleanup_expired_records(who(s, "tok-retention-0000000000000")); id1 = q(v, "SELECT record_id FROM vault_record_tombstones")[0]["record_id"]; assert life(id1) == "EXPIRED_DELETED"
    v.tokenizer.tokenize(tok_req(PH2, [("PHONE", PH2)], borrower="B2", conv="C2", rid="x"), g); id2 = vid("B2")
    v.deletion.fault = lambda step: (_ for _ in ()).throw(RuntimeError("x")) if step == "delete_rows" else None
    assert v.deletion.erase(EraseRequest(job_id="J-p", borrower_id="B2"), er).status == "PARTIAL_FAILURE"
    assert life(id2) == "PARTIALLY_FAILED", "records are marked for deletion but the job failed: NOT 'deleted'"
    v.deletion.fault = None; assert v.deletion.erase(EraseRequest(job_id="J-p", borrower_id="B2"), er).status == "COMPLETED"
    assert life(id2) == "DELETED" and v.tokenizer.lifecycle(id2, li).reason == "ERASURE"
    expect(VaultDenied, lambda: v.tokenizer.lifecycle(id2, g), "forbidden")
    c = TestClient(create_app(v)); r = c.get(f"/vault/records/{id2}/lifecycle", headers=H(LI)); assert r.status_code == 200 and r.json()["lifecycle"] == "DELETED"


# ================================================================ index review
HOT = {
    "scoped blind-index lookup": ("blind_indexes", "SELECT vault_id FROM blind_indexes WHERE borrower_id = ? AND conversation_id = ? AND purpose_id = ? AND pii_type = ? AND index_version = ? AND index_digest = ?", ("B", "C", "p", "PHONE", 1, "d")),
    "token -> record": ("token_mappings", "SELECT vault_id FROM token_mappings WHERE conversation_id = ? AND token = ?", ("C", "<PHONE_1>")),
    "next token number": ("token_mappings", "SELECT MAX(seq) FROM token_mappings WHERE conversation_id = ? AND pii_type = ?", ("C", "PHONE")),
    "active key of a borrower": ("borrower_keys", "SELECT key_id FROM borrower_keys WHERE borrower_id = ? AND key_status = 'ACTIVE'", ("B",)),
    "keys by status": ("borrower_keys", "SELECT key_id FROM borrower_keys WHERE key_status = ?", ("ACTIVE",)),
    "records of a borrower": ("vault_records", "SELECT vault_id FROM vault_records WHERE borrower_id = ?", ("B",)),
    "records of a conversation": ("vault_records", "SELECT vault_id FROM vault_records WHERE conversation_id = ?", ("C",)),
    "records using a key": ("vault_records", "SELECT vault_id FROM vault_records WHERE key_id = ?", ("k",)),
    "expiry sweep": ("vault_records", "SELECT vault_id FROM vault_records WHERE status = 'ACTIVE' AND expires_at <= ?", (1.0,)),
    "mapping expiry": ("token_mappings", "SELECT vault_id FROM token_mappings WHERE status = 'ACTIVE' AND expires_at <= ?", (1.0,)),
    "mappings of a borrower": ("token_mappings", "SELECT token FROM token_mappings WHERE borrower_id = ? AND conversation_id = ?", ("B", "C")),
    "blind indexes of a record": ("blind_indexes", "SELECT index_id FROM blind_indexes WHERE vault_id = ?", ("v",)),
    "blind indexes of a borrower": ("blind_indexes", "SELECT index_id FROM blind_indexes WHERE borrower_id = ?", ("B",)),
    "operations by borrower": ("vault_operations", "SELECT operation_id FROM vault_operations WHERE borrower_id = ?", ("B",)),
    "operations by time": ("vault_operations", "SELECT operation_id FROM vault_operations WHERE timestamp <= ?", (1.0,)),
    "erasure jobs of a borrower": ("erasure_jobs", "SELECT job_id FROM erasure_jobs WHERE borrower_id = ? AND status <> 'COMPLETED'", ("B",)),
    "tombstone purge": ("vault_record_tombstones", "SELECT record_id FROM vault_record_tombstones WHERE expires_at <= ?", (1.0,)),
    "idempotency purge": ("vault_idempotency", "DELETE FROM vault_idempotency WHERE expires_at <= ?", (1.0,)),
    "context lookup": ("vault_context", "SELECT version FROM vault_context WHERE borrower_id = ? AND conversation_id = ?", ("B", "C")),
    "context expiry purge": ("vault_context", "DELETE FROM vault_context WHERE expires_at <= ?", (1.0,)),
    "contexts using a key": ("vault_context", "SELECT 1 FROM vault_context WHERE key_id = ?", ("k",)),
    "idempotency claim": ("vault_idempotency", "SELECT status FROM vault_idempotency WHERE idempotency_key = ? AND operation = ? AND caller = ?", ("k", "TOKENIZE", "c")),
}


def test_every_hot_query_is_served_by_an_index():
    s, v = make(); g = who(s, GW)
    for i in range(30): v.tokenizer.tokenize(tok_req(f"98450000{i:02d}", [("PHONE", f"98450000{i:02d}")], borrower=f"B{i % 3}", conv=f"C{i % 5}", rid=f"r{i}"), g)
    bad = []
    with v.ctx.db.transaction() as tx:
        if tx.kind == "postgres": tx.execute("SET LOCAL enable_seqscan = off")                       # tiny tables: force the planner to show whether an index COULD serve the query
        for name, (table, sql, params) in HOT.items():
            if tx.kind == "sqlite":
                plan = " | ".join(r["detail"] for r in tx.query("EXPLAIN QUERY PLAN " + sql, params))
                if "SCAN" in plan and "INDEX" not in plan: bad.append((name, plan))
            else:
                plan = " | ".join(list(r.values())[0] for r in tx.query("EXPLAIN " + sql.replace("DELETE FROM", "SELECT 1 FROM") if sql.startswith("DELETE") else "EXPLAIN " + sql, params))
                if "Index" not in plan: bad.append((name, plan))
    assert not bad, bad
    assert len(INDEXES) >= 16 and all("IF NOT EXISTS" in i for i in INDEXES) and len(TABLES) == 13


# ================================================================ restricted roles (scratch cluster)
ROLE_DB = "te03_roles_test"


def _super():
    url = os.environ.get("VAULT_TEST_SUPER_URL")
    if not url: raise Skip("needs a scratch PostgreSQL cluster where we are superuser (VAULT_TEST_SUPER_URL)")
    return url


def _as(role, url, **extra):
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    sp = urlsplit(url); q_ = dict(parse_qsl(sp.query)); q_.update(extra)
    host = sp.hostname or ""
    return urlunsplit(sp._replace(netloc=f"{role}@{host}", path=f"/{ROLE_DB}", query=urlencode(q_)))


def test_pg_restricted_roles_can_run_the_vault_but_cannot_change_schema_read_secrets_or_revive_keys():
    url = _super(); here = os.path.dirname(os.path.dirname(__file__)) + "/database/"
    with psycopg.connect(url, autocommit=True) as su:
        su.execute(f"DROP DATABASE IF EXISTS {ROLE_DB}"); su.execute(f"CREATE DATABASE {ROLE_DB}")
    from urllib.parse import urlsplit, urlunsplit
    sp = urlsplit(url); db_url = urlunsplit(sp._replace(path=f"/{ROLE_DB}"))                      # same superuser, the scratch database
    with psycopg.connect(db_url, autocommit=True) as su:
        su.execute(open(here + "roles.sql").read())
    mig, app, ro, adm = (_as(r, url, schema="vault") for r in ("vault_migration", "vault_app", "vault_readonly", "vault_admin"))
    Database(mig, migrate=True).close()                                                            # the migration role creates the schema objects
    with psycopg.connect(_as("vault_migration", url), autocommit=True) as c: c.execute(open(here + "grants.sql").read())
    # --- the vault runs as vault_app: no DDL needed at start-up, tokenise / rehydrate / erase / rotate(DEK) / retention all work
    expect(VaultUnavailable, lambda: Database(_as("vault_app", url, schema="nonexistent_schema"), migrate=False))          # wrong schema / no schema: refuses, does not create
    st = Settings.for_testing(db_url=app, db_auto_migrate=False); v = build_services(st); g = who(st, GW)
    assert v.tokenizer.health()["db_role_safe"] is True and v.ctx.db.role_info()["role"] == "vault_app"
    v.tokenizer.tokenize(tok_req(f"call {PH}", [("PHONE", PH)]), g); assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g).values == {"<PHONE_1>": PH}
    assert v.lifecycle.rotate(RotateRequest(target_key_version=1, operation="DEK"), who(st, KO)).deks_rotated == 1
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g).values and v.retention.cleanup_expired_records(who(st, "tok-retention-0000000000000"))["status"] == "COMPLETED"
    assert v.deletion.erase(EraseRequest(job_id="J-roles", borrower_id="B1"), who(st, ER)).status == "COMPLETED"
    # --- ... and EVERY other thing the app does works under the least-privilege grants (tightening must not break a feature): idempotency, context, extend, all four rotations,
    #     token revocation, conversation-scope erase, retention with key retirement and metadata purge, borrower erase
    import time as _t
    from layer2_vault.config.settings import RetentionPolicy, ServiceIdentity
    gwid, off = ServiceIdentity("gw", frozenset({"context", "tokenize", "admin"})), [0.0]
    st2 = Settings.for_testing(db_url=app, db_auto_migrate=False, retention=RetentionPolicy(ttl_seconds=60, destroy_idle_keys=True, tombstone_ttl_seconds=60, operations_ttl_seconds=60))
    v2 = build_services(st2, clock=lambda: _t.time() + off[0]); g2 = who(st2, GW)
    for _ in range(2): v2.tokenizer.tokenize(tok_req(f"call {PH}", [("PHONE", PH)], borrower="B2", conv="C1", rid="p1"), g2, idempotency_key="idem-key-0001")      # the second one is a replay
    v2.tokenizer.tokenize(tok_req("mail a@b.co", [("EMAIL", "a@b.co")], borrower="B2", conv="C2", rid="p2"), g2)
    assert v2.context.save("B2", "C1", {"n": 1}, gwid) == 1 and v2.context.load("B2", "C1", gwid)[1] == {"n": 1}
    assert v2.internal.extend("C1", "<PHONE_1>", gwid, joined=PH + " 1") == "<PHONE_1>"
    for op in ("KEK", "BLIND_INDEX", "ALL", "DEK"): assert v2.lifecycle.rotate(RotateRequest(target_key_version=1, operation=op), who(st2, KO)).status == "success", op
    with psycopg.connect(db_url, autocommit=True) as su: assert su.execute("SELECT version FROM vault.vault_key_state WHERE name = 'kek'").fetchone() == (1,), "the rotation was recorded by the app role"
    assert v2.lifecycle.revoke_token("C2", "<EMAIL_1>", gwid)
    assert v2.deletion.erase(EraseRequest(job_id="J-conv", borrower_id="B2", scope="CONVERSATION", conversation_id="C2"), who(st2, ER)).status == "COMPLETED"
    off[0] = 7200.0                                                                                # two hours later: records, tombstones, log rows and the context have all expired
    r = v2.retention.cleanup_expired_records(who(st2, "tok-retention-0000000000000")); assert r["status"] == "COMPLETED" and r["records_deleted"] >= 1 and r["operations_purged"] >= 1, r
    assert v2.deletion.erase(EraseRequest(job_id="J-b2", borrower_id="B2"), who(st2, ER)).status == "COMPLETED"
    # --- what vault_app must NOT be able to do
    def denied(sql, role_url=app):
        raw = role_url.replace("&schema=vault", "").replace("schema=vault&", "") + "&options=-csearch_path%3Dvault"       # libpq does not know the vault-only 'schema' parameter
        try:
            with psycopg.connect(raw, autocommit=True) as c: c.execute(sql)
        except psycopg.errors.InsufficientPrivilege: return True
        return False                                                                          # any OTHER outcome (success, or a different error) is not a proven denial
    assert denied("CREATE TABLE vault.evil (x int)") and denied("DROP TABLE vault.vault_records") and denied("TRUNCATE vault.vault_records")
    assert denied("DROP TRIGGER destroyed_key_is_final ON vault.borrower_keys") and denied("ALTER TABLE vault.borrower_keys DISABLE TRIGGER destroyed_key_is_final")
    assert denied("ALTER TABLE vault.borrower_keys DROP CONSTRAINT borrower_keys_destroyed_check") and denied("CREATE ROLE sneaky") and denied("CREATE EXTENSION pgcrypto")
    # least privilege (review finding): the app cannot forge the schema version, delete key / state / counter rows, or rewrite the operations log
    assert denied("UPDATE vault.vault_meta SET value = '1' WHERE key = 'schema_version'") and denied("DELETE FROM vault.vault_meta") and denied("INSERT INTO vault.vault_meta VALUES ('x', 'y')")
    assert denied("DELETE FROM vault.borrower_keys") and denied("DELETE FROM vault.key_nonce_state") and denied("DELETE FROM vault.vault_key_state") and denied("DELETE FROM vault.borrower_state")
    assert denied("UPDATE vault.vault_operations SET status = 'FORGED'") and denied("DELETE FROM vault.erasure_jobs")
    with psycopg.connect(_as("vault_migration", url), autocommit=True) as c: c.execute("CREATE TABLE vault.added_later (x int)")
    assert denied("SELECT * FROM vault.added_later"), "a table added by a later migration is NOT open to the app role until it is granted on purpose"
    # --- vault_readonly: metadata yes, secrets no, writes no
    for sql in ("SELECT ciphertext FROM vault.vault_records", "SELECT nonce FROM vault.vault_records", "SELECT wrapped_dek FROM vault.borrower_keys", "SELECT index_digest FROM vault.blind_indexes",
                "SELECT * FROM vault.vault_records", "SELECT request_hash FROM vault.vault_idempotency", "INSERT INTO vault.vault_meta VALUES ('x','y')", "DELETE FROM vault.vault_meta"):
        assert denied(sql, ro), sql
    assert not denied("SELECT vault_id, pii_type, status FROM vault.vault_records", ro) and not denied("SELECT key_id, key_status FROM vault.borrower_keys", ro)
    # --- vault_admin: can un-stick a job, still cannot read secrets or change schema
    assert not denied("UPDATE vault.erasure_jobs SET status = status", adm) and denied("SELECT ciphertext FROM vault.vault_records", adm) and denied("DROP TABLE vault.erasure_jobs", adm)
    assert denied("DELETE FROM vault.vault_records", adm) and denied("UPDATE vault.borrower_keys SET key_status = 'ACTIVE'", adm)
    with psycopg.connect(url, autocommit=True) as su:
        roles = su.execute("SELECT rolname, rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname LIKE 'vault_%'").fetchall()
    assert len(roles) == 4 and not any(r[1] or r[2] or r[3] for r in roles), "none of the vault roles is a superuser / can create roles or databases"


def test_the_sql_files_cover_every_table_and_never_grant_to_public():
    here = os.path.dirname(os.path.dirname(__file__)) + "/database/"; grants, roles = open(here + "grants.sql").read(), open(here + "roles.sql").read()
    import re
    tables = [re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", d).group(1) for d in TABLES]
    assert all(t in grants for t in tables), [t for t in tables if t not in grants]
    assert "SUPERUSER" not in roles.replace("NOSUPERUSER", "") and "TO PUBLIC" not in grants + roles and "GRANT ALL" not in grants.upper().replace("REVOKE ALL", "")


if __name__ == "__main__":
    run(globals())
