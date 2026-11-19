"""Phase 1: data lifecycle. Expiry is enforced on read (never relies on the cleanup job), cleanup is batched / idempotent / retryable,
DEKs that retained records need are never destroyed, tombstones carry no PII, and a v1 database upgrades in place."""
import os
import sqlite3
import tempfile
import uuid

from layer2_vault.config.settings import RetentionPolicy, ServiceIdentity
from layer2_vault.core.errors import ValidationFailed, VaultDenied
from layer2_vault.database.connection import Database
from layer2_vault.schemas.vault_schemas import EraseRequest
from layer2_vault.tests import legacy_v1_schema as v1
from layer2_vault.tests._util import ER, GW, RT, expect, make, reh_req, run, tok_req, who

PH, PH2 = "9845012345", "9811122233"


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def setup(ttl=100, **pol):
    clk = Clock()
    s, v = make(clock=clk, retention=RetentionPolicy(ttl_seconds=ttl, **pol))
    return clk, s, v, who(s, GW), who(s, RT)


def rows(v, sql, p=()):
    with v.ctx.db.transaction() as tx:
        return tx.query(sql, p)


# ---------------------------------------------------------------- the 8 required tests
def test_active_record_rehydrates():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    clk.advance(99)
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g).values == {"<PHONE_1>": PH}
    m = v.tokenizer.get_metadata(rows(v, "SELECT vault_id FROM vault_records")[0]["vault_id"], who(s, "tok-lineage-0000000000000000"))
    assert m.status == "ACTIVE" and m.expires_at == 1_700_000_100.0


def test_expired_record_cannot_rehydrate():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    clk.advance(100)                                                       # expires_at <= now means expired
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "token_not_found")


def test_expired_record_is_denied_before_cleanup_runs():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    clk.advance(500)
    st = rows(v, "SELECT status, ciphertext FROM vault_records")[0]
    assert st["status"] == "ACTIVE" and st["ciphertext"], "precondition: cleanup has NOT run, the row is still ACTIVE and still holds ciphertext"
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "token_not_found")
    assert v.tokenizer.get_metadata(rows(v, "SELECT vault_id FROM vault_records")[0]["vault_id"], who(s, "tok-lineage-0000000000000000")).status == "EXPIRED"


def test_expired_mapping_is_not_reactivated_by_a_retry():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="a"), g)
    clk.advance(150)
    t2 = v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="b"), g).mappings[0].token
    assert t2 == "<PHONE_2>", "the same value after expiry gets a NEW token, the expired mapping is not reused"
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "token_not_found")
    assert v.rehydration.rehydrate(reh_req(["<PHONE_2>"]), g).values == {"<PHONE_2>": PH}
    assert {r_["token"]: r_["status"] for r_ in rows(v, "SELECT token, status FROM token_mappings")} == {"<PHONE_1>": "EXPIRED", "<PHONE_2>": "ACTIVE"}


def test_cleanup_removes_the_blind_index():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(f"{PH} {PH2}", [("PHONE", PH), ("PHONE", PH2)]), g)
    assert len(rows(v, "SELECT 1 FROM blind_indexes")) == 2
    clk.advance(101); res = v.retention.cleanup_expired_records(r)
    assert res["status"] == "COMPLETED" and res["records_expired"] == 2 and res["records_deleted"] == 2
    assert rows(v, "SELECT 1 FROM blind_indexes") == [] and rows(v, "SELECT 1 FROM vault_records") == []
    assert {x["status"] for x in rows(v, "SELECT status FROM token_mappings")} == {"EXPIRED"}      # mapping rows hold no PII and keep token numbers from being reused


def test_cleanup_does_not_destroy_a_dek_that_retained_records_still_need():
    clk, s, v, g, r = setup(ttl=None, purpose_ttl_seconds={"collections": 100}, destroy_idle_keys=True)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], purpose="collections", rid="a"), g)
    v.tokenizer.tokenize(tok_req(PH2, [("PHONE", PH2)], purpose="service", rid="b"), g)         # no TTL for 'service': retained
    clk.advance(200); res = v.retention.cleanup_expired_records(r)
    assert res["records_deleted"] == 1 and res["keys_retired"] == 0
    assert [k["key_status"] for k in rows(v, "SELECT key_status FROM borrower_keys")] == ["ACTIVE"]
    assert v.rehydration.rehydrate(reh_req(["<PHONE_2>"], purpose="service"), g).values == {"<PHONE_2>": PH2}   # the retained record still decrypts
    # when NOTHING depends on the key any more it is retired (reason RETENTION) and a later tokenise gets a fresh key
    s2 = setup(ttl=100, destroy_idle_keys=True); clk2, _, v2, g2, r2 = s2
    v2.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g2); clk2.advance(101)
    assert v2.retention.cleanup_expired_records(r2)["keys_retired"] == 1
    k = rows(v2, "SELECT key_status, destroy_reason, wrapped_dek FROM borrower_keys")[0]
    assert (k["key_status"], k["destroy_reason"], k["wrapped_dek"]) == ("DESTROYED", "RETENTION", None)
    v2.tokenizer.tokenize(tok_req(PH2, [("PHONE", PH2)], rid="n"), g2)
    assert sorted(x["key_status"] for x in rows(v2, "SELECT key_status FROM borrower_keys")) == ["ACTIVE", "DESTROYED"]


def test_repeated_cleanup_is_harmless():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g); clk.advance(500)
    a = v.retention.cleanup_expired_records(r); b = v.retention.cleanup_expired_records(r); c = v.retention.cleanup_expired_records(r)
    assert a["records_deleted"] == 1 and b["status"] == c["status"] == "COMPLETED" and b["records_expired"] == b["records_deleted"] == c["records_deleted"] == 0


def test_a_failed_cleanup_is_not_marked_complete_and_can_be_retried():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(f"{PH} {PH2}", [("PHONE", PH), ("PHONE", PH2)]), g); clk.advance(500)
    def boom(step):
        if step == "delete":
            raise RuntimeError("disk full")
    v.retention.fault = boom
    res = v.retention.cleanup_expired_records(r)
    assert res["status"] == "PARTIAL_FAILURE" and res["error"] == "cleanup_step_failed" and "not complete" in res["note"]
    assert len(rows(v, "SELECT 1 FROM vault_records")) == 2, "the failed batch rolled back: nothing is half-deleted"
    assert rows(v, "SELECT 1 FROM vault_record_tombstones") == []
    v.retention.fault = None
    ok = v.retention.cleanup_expired_records(r)
    assert ok["status"] == "COMPLETED" and ok["records_deleted"] == 2 and len(rows(v, "SELECT 1 FROM vault_record_tombstones")) == 2


# ---------------------------------------------------------------- more lifecycle behaviour
def test_batches_are_bounded_and_every_record_is_still_cleaned():
    clk, s, v, g, r = setup(batch_size=2)
    for i in range(5):
        v.tokenizer.tokenize(tok_req(f"98450000{i:02d}", [("PHONE", f"98450000{i:02d}")], rid=f"r{i}"), g)
    clk.advance(200); res = v.retention.cleanup_expired_records(r)
    assert res["records_expired"] == res["records_deleted"] == 5 and rows(v, "SELECT 1 FROM vault_records") == []


def test_tombstones_hold_no_pii_or_ciphertext_and_say_how_the_record_ended():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    vid = rows(v, "SELECT vault_id FROM vault_records")[0]["vault_id"]
    clk.advance(200); v.retention.cleanup_expired_records(r)
    t = rows(v, "SELECT * FROM vault_record_tombstones")[0]
    assert t["record_id"] == vid and t["deletion_status"] == "EXPIRED_DELETED" and t["reason"] == "RETENTION" and set(t) == {"record_id", "deletion_status", "deleted_at", "operation_id", "reason", "expires_at"}
    assert PH not in repr(t)
    v.tokenizer.tokenize(tok_req(PH2, [("PHONE", PH2)], borrower="B2", rid="x"), g)
    vid2 = rows(v, "SELECT vault_id FROM vault_records")[0]["vault_id"]
    v.deletion.erase(EraseRequest(job_id="j1", borrower_id="B2"), who(s, ER))
    assert rows(v, "SELECT deletion_status, reason FROM vault_record_tombstones WHERE record_id = ?", (vid2,)) == [{"deletion_status": "DELETED", "reason": "ERASURE"}]


def test_tombstones_are_purged_only_when_policy_says_so():
    clk, s, v, g, r = setup(tombstone_ttl_seconds=1000); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    clk.advance(200); v.retention.cleanup_expired_records(r); assert len(rows(v, "SELECT 1 FROM vault_record_tombstones")) == 1
    clk.advance(900); assert v.retention.cleanup_expired_records(r)["tombstones_purged"] == 0
    clk.advance(200); assert v.retention.cleanup_expired_records(r)["tombstones_purged"] == 1
    clk2, s2, v2, g2, r2 = setup(); v2.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g2); clk2.advance(10 ** 8); v2.retention.cleanup_expired_records(r2)
    assert len(rows(v2, "SELECT 1 FROM vault_record_tombstones")) == 1, "no tombstone TTL in the policy -> kept"


def test_cleanup_skips_records_whose_borrower_is_under_erasure():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g); clk.advance(500)
    with v.ctx.db.transaction() as tx: v.ctx.repo.set_borrower_state(tx, "B1", "ERASURE_PENDING")
    res = v.retention.cleanup_expired_records(r)
    assert res["records_expired"] == res["records_deleted"] == 0 and len(rows(v, "SELECT 1 FROM vault_records")) == 1
    with v.ctx.db.transaction() as tx: v.ctx.repo.set_borrower_state(tx, "B1", "ACTIVE"); v.ctx.repo.mark_pending(tx, "B1")          # conversation-level erasure marks records PENDING_ERASURE
    assert v.retention.cleanup_expired_records(r)["records_deleted"] == 0 and rows(v, "SELECT status FROM vault_records") == [{"status": "PENDING_ERASURE"}]


def test_erasure_still_removes_records_that_expired_but_were_never_cleaned():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g); clk.advance(500)
    out = v.deletion.erase(EraseRequest(job_id="j2", borrower_id="B1"), who(s, ER))
    assert out.status == "COMPLETED" and out.counts["records"] == 1 and rows(v, "SELECT 1 FROM vault_records") == []


def test_an_erasure_destroyed_key_never_lets_a_new_key_be_minted_but_the_sweep_still_sees_expired_data():
    clk, s, v, g, r = setup(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g); clk.advance(500)
    assert v.internal.find_unmapped_copies("B1", f"debug mobile={PH}", ServiceIdentity("sweeper", frozenset({"erasure_sweep"}))) is True, "expired-but-not-yet-deleted data is still found by the erasure sweep"
    v.deletion.erase(EraseRequest(job_id="j3", borrower_id="B1"), who(s, ER))
    expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req(PH2, [("PHONE", PH2)], rid="z"), g))


def test_the_retention_policy_is_validated_and_has_no_invented_default():
    assert RetentionPolicy().ttl_for("collections") is None, "no policy supplied -> nothing expires"
    for bad in ({"ttl_seconds": 0}, {"ttl_seconds": -5}, {"ttl_seconds": "30d"}, {"purpose_ttl_seconds": {"x": -1}}, {"batch_size": 0}, {"batch_size": 5000}):
        expect(ValidationFailed, lambda b=bad: RetentionPolicy(**b).validate())
    expect(ValidationFailed, lambda: RetentionPolicy.from_json('{"ttl": 5}'), "retention_policy_unknown_field")
    p = RetentionPolicy.from_json('{"ttl_seconds": 100, "purpose_ttl_seconds": {"Collections": 50}}'); assert p.ttl_for("COLLECTIONS") == 50 and p.ttl_for("service") == 100


def test_cleanup_needs_the_retention_capability():
    clk, s, v, g, r = setup(); expect(VaultDenied, lambda: v.retention.cleanup_expired_records(g), "forbidden")


# ---------------------------------------------------------------- v1 -> v2 migration
def _make_v1(url):
    blob, real = ("BLOB", "REAL") if url.startswith("sqlite") else ("BYTEA", "DOUBLE PRECISION")
    ddl = [d.replace("{BLOB}", blob).replace("{REAL}", real) for d in v1.TABLES]
    if url.startswith("sqlite"):
        c = sqlite3.connect(url[len("sqlite:///"):], isolation_level=None)
        for d in ddl: c.execute(d)
        c.execute("INSERT INTO borrower_state VALUES ('B1','ACTIVE',1)")
        c.execute("INSERT INTO vault_records (vault_id,borrower_id,conversation_id,token_id,pii_type,ciphertext,nonce,key_id,purpose_id,status,created_at) VALUES ('v_old','B1','C1','t_old','PHONE',x'00',x'01','k1','collections','ACTIVE',5)")
        c.execute("INSERT INTO token_mappings (token_id,vault_id,token,borrower_id,conversation_id,pii_type,seq,status) VALUES ('t_old','v_old','<PHONE_1>','B1','C1','PHONE',1,'ACTIVE')")
        c.close()
    else:
        import psycopg
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        sp = urlsplit(url); q = dict(parse_qsl(sp.query)); schema = q.pop("schema")
        with psycopg.connect(urlunsplit(sp._replace(query=urlencode(q))), autocommit=True) as c:
            c.execute(f'CREATE SCHEMA "{schema}"'); c.execute(f'SET search_path = "{schema}"')
            for d in ddl: c.execute(d)
            c.execute("INSERT INTO borrower_state VALUES ('B1','ACTIVE',1)")
            c.execute("INSERT INTO vault_records (vault_id,borrower_id,conversation_id,token_id,pii_type,ciphertext,nonce,key_id,purpose_id,status,created_at) VALUES ('v_old','B1','C1','t_old','PHONE','\\x00','\\x01','k1','collections','ACTIVE',5)")
            c.execute("INSERT INTO token_mappings (token_id,vault_id,token,borrower_id,conversation_id,pii_type,seq,status) VALUES ('t_old','v_old','<PHONE_1>','B1','C1','PHONE',1,'ACTIVE')")


def test_a_v1_database_is_upgraded_in_place_keeping_its_data():
    pg = os.environ.get("VAULT_TEST_DB_URL")
    d = tempfile.mkdtemp()
    url = (pg + ("&" if "?" in pg else "?") + "schema=m_" + uuid.uuid4().hex[:12]) if pg else f"sqlite:///{d}/legacy.db"
    _make_v1(url)
    db = Database(url)
    assert db.migrated_from == 1
    with db.transaction() as tx:
        assert tx.one("SELECT value FROM vault_meta WHERE key = 'schema_version'")["value"] == "4"
        r = tx.one("SELECT status, expires_at, expired_at FROM vault_records WHERE vault_id = 'v_old'"); assert r == {"status": "ACTIVE", "expires_at": None, "expired_at": None}
        assert tx.one("SELECT status, expires_at FROM token_mappings WHERE token_id = 't_old'")["expires_at"] is None
        tx.execute("UPDATE vault_records SET status = 'EXPIRED' WHERE vault_id = 'v_old'")                   # the widened CHECK accepts EXPIRED
        tx.execute("UPDATE token_mappings SET status = 'EXPIRED' WHERE token_id = 't_old'")
        for t in ("key_nonce_state", "vault_record_tombstones", "vault_idempotency"): tx.query(f"SELECT 1 FROM {t}")
    expect(Exception, lambda: db.transaction().__enter__().execute("UPDATE vault_records SET status = 'BOGUS'"))
    db2 = Database(url); assert db2.migrated_from == 4, "running it again is a no-op"
    with db2.transaction() as tx: assert tx.one("SELECT COUNT(*) AS n FROM vault_records")["n"] == 1


def test_a_migrated_database_works_with_the_services():
    pg = os.environ.get("VAULT_TEST_DB_URL"); d = tempfile.mkdtemp()
    url = (pg + ("&" if "?" in pg else "?") + "schema=m_" + uuid.uuid4().hex[:12]) if pg else f"sqlite:///{d}/legacy2.db"
    _make_v1(url)
    clk = Clock(); s, v = make(db_url=url, clock=clk, retention=RetentionPolicy(ttl_seconds=100)); g = who(s, GW)
    t = v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], borrower="B9", conv="C9"), g).mappings[0].token
    assert v.rehydration.rehydrate(reh_req([t], borrower="B9", conv="C9"), g).values == {t: PH}


if __name__ == "__main__":
    run(globals())
