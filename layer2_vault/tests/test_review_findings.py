"""Regression tests for defects found by the INDEPENDENT review of Layer 2 (three fresh reviewers; each finding was reproduced before it was fixed).
Each test names the finding. They fail on the code as it was when reviewed."""
import json
import os
import sys
import tempfile

from layer2_vault.config.settings import RetentionPolicy, ServiceIdentity, ServiceRegistry, Settings
from layer2_vault.core.errors import ValidationFailed, VaultDenied, VaultError
from layer2_vault.schemas.vault_schemas import EraseRequest
from layer2_vault.tests._util import ER, GW, KO, Skip, expect, make, need_postgres, reh_req, run, tok_req, who

PH = "9845012345"


def q(v, sql, p=()):
    with v.ctx.db.transaction() as tx: return tx.query(sql, p)


def boom_at(step):
    def hook(name):
        if name == step: raise RuntimeError("injected")
    return hook


# ---- finding B3: a resumed CONVERSATION-scope erase turned an ERASED borrower back into ACTIVE
def test_a_resumed_conversation_job_can_never_undo_a_completed_borrower_erasure():
    s, v = make(); g, er = who(s, GW), who(s, ER)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], conv="C1", rid="a"), g); v.tokenizer.tokenize(tok_req("9811122233", [("PHONE", "9811122233")], conv="C2", rid="b"), g)
    v.deletion.fault = boom_at("delete_rows")
    conv = EraseRequest(job_id="J-conv", borrower_id="B1", scope="CONVERSATION", conversation_id="C1")
    assert v.deletion.erase(conv, er).status == "PARTIAL_FAILURE" and q(v, "SELECT state FROM borrower_state")[0]["state"] == "ERASURE_PENDING"
    v.deletion.fault = None
    assert v.deletion.erase(EraseRequest(job_id="J-all", borrower_id="B1"), er).status == "COMPLETED" and q(v, "SELECT state FROM borrower_state")[0]["state"] == "ERASED"
    v.deletion.erase(conv, er)                                                   # the late retry of the old conversation job
    assert q(v, "SELECT state FROM borrower_state")[0]["state"] == "ERASED", "the retry must not re-open an erased borrower"
    expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], conv="C3", rid="c"), g))
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"], conv="C1"), g))


def test_a_conversation_job_never_restores_active_while_a_borrower_erasure_is_still_open():
    s, v = make(); g, er = who(s, GW), who(s, ER)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], conv="C1", rid="a"), g)
    v.deletion.fault = boom_at("destroy_key")
    assert v.deletion.erase(EraseRequest(job_id="J-all", borrower_id="B1"), er).status == "PARTIAL_FAILURE"      # borrower erasure left half done: PENDING
    v.deletion.fault = None
    v.deletion.erase(EraseRequest(job_id="J-conv", borrower_id="B1", scope="CONVERSATION", conversation_id="C1"), er)
    assert q(v, "SELECT state FROM borrower_state")[0]["state"] == "ERASURE_PENDING", "still pending: only the borrower job may finish it"
    assert v.deletion.erase(EraseRequest(job_id="J-all", borrower_id="B1"), er).status == "COMPLETED" and q(v, "SELECT state FROM borrower_state")[0]["state"] == "ERASED"


def test_a_conversation_erase_of_an_active_borrower_still_restores_active_afterwards():
    s, v = make(); g, er = who(s, GW), who(s, ER)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], conv="C1", rid="a"), g); v.tokenizer.tokenize(tok_req("9811122233", [("PHONE", "9811122233")], conv="C2", rid="b"), g)
    assert v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1", scope="CONVERSATION", conversation_id="C1"), er).status == "COMPLETED"
    assert q(v, "SELECT state FROM borrower_state")[0]["state"] == "ACTIVE" and v.rehydration.rehydrate(reh_req(["<PHONE_1>"], conv="C2"), g).values


# ---- finding B2: a string-valued scope in the service config granted access to EVERY borrower (fail-open)
def test_a_malformed_service_scope_is_rejected_instead_of_widening_access():
    for bad in ({"borrowers": "portfolioA-*"}, {"borrowers": "B1"}, {"purposes": "collections"}, {"borrowers": ["B1", 5]}, {"borrowers": ["*"]}, {"borrowers": [""]}, {"borrowers": ["B1", "*"]},
                {"purposes": ["collections", ""]}, {"purposes": {"a": 1}}, {"borrowers": 7}):
        expect(ValidationFailed, lambda b=bad: ServiceRegistry({"tok-aaaaaaaaaaaaaaaaaa": ("svc", {"tokenize"}, b)}), "service_registry_invalid")
    env = {"VAULT_MASTER_KEY": os.urandom(32).hex(), "VAULT_DB_URL": "sqlite:///:memory:",
           "VAULT_SERVICE_TOKENS": json.dumps({"gateway": {"capabilities": ["tokenize"], "borrowers": "portfolioA-*", "token": "tok-g-00000000000000000"}})}
    expect(ValidationFailed, lambda: Settings.from_env(env), "service_registry_invalid")
    ok = ServiceRegistry({"tok-aaaaaaaaaaaaaaaaaa": ("svc", {"tokenize"}, {"borrowers": ["portfolioA-*", "B1"], "purposes": ["collections"]})}).authenticate("tok-aaaaaaaaaaaaaaaaaa")
    assert ok.covers_borrower("portfolioA-7") and ok.covers_borrower("B1") and not ok.covers_borrower("someone-else") and not ok.covers_borrower("") and not ok.covers_borrower(None)
    assert ServiceRegistry({"tok-bbbbbbbbbbbbbbbbbb": ("svc2", {"tokenize"}, {"borrowers": []})}).authenticate("tok-bbbbbbbbbbbbbbbbbb").covers_borrower("B1") is False, "an empty list means NO borrower"


# ---- finding B1 (PostgreSQL): retention retired a DEK that a tokenise had just committed a new record under
def test_pg_retention_never_retires_a_key_that_a_concurrent_tokenise_is_using():
    need_postgres()
    import threading, time
    class Clk:
        t = 1_700_000_000.0
        def __call__(self): return self.t
    clk = Clk(); s, v = make(clock=clk, retention=RetentionPolicy(ttl_seconds=100, destroy_idle_keys=True)); g, rt = who(s, GW), who(s, "tok-retention-0000000000000")
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="old"), g); clk.t += 150                      # the only record has expired: the key LOOKS idle
    paused, release, out = threading.Event(), threading.Event(), {}
    real = v.tokenizer.resolve
    def slow_resolve(*a, **k):
        r = real(*a, **k); paused.set(); release.wait(10); return r                                  # the new record is inserted, NOT yet committed
    v.tokenizer.resolve = slow_resolve
    t1 = threading.Thread(target=lambda: out.update(tok=v.tokenizer.tokenize(tok_req("9811122233", [("PHONE", "9811122233")], conv="C2", rid="new"), g)))
    t1.start(); assert paused.wait(10)
    t2 = threading.Thread(target=lambda: out.update(ret=v.retention.cleanup_expired_records(rt))); t2.start()
    time.sleep(0.5); release.set(); t1.join(10); t2.join(10)
    v.tokenizer.resolve = real
    assert out["ret"]["keys_retired"] == 0, f"retention destroyed a key a fresh record depends on: {out['ret']}"
    assert [k["key_status"] for k in q(v, "SELECT key_status FROM borrower_keys")] == ["ACTIVE"]
    assert v.rehydration.rehydrate(reh_req([out["tok"].mappings[0].token], conv="C2"), g).values == {out["tok"].mappings[0].token: "9811122233"}


# ---- finding B4: a purpose written as its alias ('collection') escaped the retention rule for 'collections'
def test_a_purpose_alias_is_the_same_scope_the_same_retention_rule_and_the_same_rights():
    class Clk:
        t = 1_700_000_000.0
        def __call__(self): return self.t
    clk = Clk(); s, v = make(clock=clk, retention=RetentionPolicy(purpose_ttl_seconds={"collections": 100})); g, rt = who(s, GW), who(s, "tok-retention-0000000000000")
    a = v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], purpose="collection", rid="a"), g).mappings[0]                  # the alias
    b = v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], purpose="collections", rid="b"), g).mappings[0]                # the canonical name
    assert a.token == b.token and a.vault_id == b.vault_id, "one purpose, one scope: the same value gets the same token"
    assert q(v, "SELECT purpose_id, expires_at FROM vault_records") == [{"purpose_id": "collections", "expires_at": clk.t + 100}], "stored canonically, and the 100 s retention rule applied"
    assert v.rehydration.rehydrate(reh_req([a.token], purpose="collection"), g).values == {a.token: PH}
    clk.t += 500; v.retention.cleanup_expired_records(rt)
    assert q(v, "SELECT 1 FROM vault_records") == [], "it expired like any 'collections' record (before the fix the alias row lived forever)"
    from layer2_vault.config.settings import ServiceRegistry
    ident = ServiceRegistry({"tok-aaaaaaaaaaaaaaaaaa": ("svc", {"tokenize"}, {"purposes": ["collections"]})}).authenticate("tok-aaaaaaaaaaaaaaaaaa")
    assert ident.covers_purpose("collection") and ident.covers_purpose("COLLECTIONS") and not ident.covers_purpose("service")
    assert RetentionPolicy.from_json('{"purpose_ttl_seconds": {"Collection": 50}}').ttl_for("collections") == 50


# ---- findings A1 / A2 / A7: the backup tool (a restore must not leave the plaintext dump behind; the database password must not be on a command line)
def test_the_decrypted_dump_is_always_removed_and_wiping_it_needs_no_big_allocation():
    import tracemalloc
    from layer2_vault.ops import backup as B
    d = tempfile.mkdtemp(); p = os.path.join(d, "dump.bin")
    with open(p, "wb") as f: f.truncate(64 * 1024 * 1024); f.write(b"SECRET-PLAINTEXT-DUMP")
    tracemalloc.start(); B._wipe_and_remove(p); peak = tracemalloc.get_traced_memory()[1]; tracemalloc.stop()
    assert not os.path.exists(p) and peak < 8 * 1024 * 1024, f"peak allocation {peak} bytes: it must zero the file in small chunks (it used to allocate the whole file)"
    p2 = os.path.join(d, "d2.bin"); open(p2, "wb").write(b"x" * 5000)
    real = os.fsync
    def boom(fd): raise MemoryError("simulated")
    os.fsync = boom
    try: B._wipe_and_remove(p2)
    finally: os.fsync = real
    assert not os.path.exists(p2), "even if wiping fails, the plaintext file is removed"


def test_the_database_password_never_appears_on_a_command_line():
    from layer2_vault.ops import backup as B
    d = tempfile.mkdtemp(); rec = os.path.join(d, "calls.jsonl")
    for tool, body in (("pg_dump", "sys.stdout.buffer.write(b'PGDMP' + b'x' * 100)"), ("pg_restore", "pass")):
        open(os.path.join(d, tool), "w").write(f"#!{sys.executable}\nimport sys, os, json\nopen({rec!r}, 'a').write(json.dumps({{'tool': {tool!r}, 'argv': sys.argv[1:], 'pgpassword': os.environ.get('PGPASSWORD')}}) + '\\n')\n{body}\n")
        os.chmod(os.path.join(d, tool), 0o755)
    old = os.environ.get("PATH"); os.environ["PATH"] = d + os.pathsep + (old or "")
    try:
        key = os.urandom(32); url = "postgresql://vault_owner:S3cretPassw0rd@db.internal:5432/vault?schema=vault&sslmode=require"
        B.backup_postgres(url, os.path.join(d, "x.bak"), key)
        try: B.restore_postgres(os.path.join(d, "x.bak"), url.replace("schema=vault&", ""), key)
        except B.BackupError: pass
    finally:
        os.environ["PATH"] = old
    calls = [json.loads(l) for l in open(rec)]; assert [c["tool"] for c in calls][:1] == ["pg_dump"]
    for c in calls:
        assert "S3cretPassw0rd" not in json.dumps(c["argv"]), f"password on the command line of {c['tool']}: {c['argv']}"
        assert c["pgpassword"] == "S3cretPassw0rd", "it is handed over through the environment of the child process instead"
        assert any("vault_owner@db.internal" in a for a in c["argv"]), "the rest of the connection string is intact"


def test_backup_hardening_missing_database_bad_header_file_mode_and_key_reuse():
    from layer2_vault.ops import backup as B
    d = tempfile.mkdtemp(); key = os.urandom(32)
    expect(B.BackupError, lambda: B.backup_sqlite(os.path.join(d, "typo.db"), os.path.join(d, "o.bak"), key), "database_not_found"); assert not os.path.exists(os.path.join(d, "typo.db")) and not os.path.exists(os.path.join(d, "o.bak"))
    import io
    out = io.BytesIO(); B.encrypt_stream(io.BytesIO(b"hello"), out, key, {"kind": "test"}); blob = bytearray(out.getvalue())
    hl = int.from_bytes(blob[8:12], "big"); hdr = bytes(blob[12:12 + hl]).replace(b'"chunk": 1048576', b'"chunk": "x"')
    expect(B.BackupError, lambda: B.decrypt_stream(io.BytesIO(bytes(blob[:8]) + len(hdr).to_bytes(4, "big") + hdr + bytes(blob[12 + hl:])), io.BytesIO(), key))
    src = os.path.join(d, "s.db"); import sqlite3; sqlite3.connect(src).execute("create table t(x)").connection.close()
    dest = os.path.join(d, "b.bak"); open(dest, "wb").write(b"old"); os.chmod(dest, 0o644); B.backup_sqlite(src, dest, key)
    assert oct(os.stat(dest).st_mode & 0o777) == "0o600", "an existing output file is tightened to 0600"
    expect(B.BackupError, lambda: B.backup_key({"VAULT_BACKUP_KEY": key.hex(), "VAULT_MASTER_KEY": key.hex()}), "backup_key_must_differ_from_master_secret")


# ---- finding A3: after an erase the SQLite files still held the destroyed key and the ciphertext (write-ahead log, freed pages)
def test_after_an_erasure_nothing_recoverable_is_left_in_the_sqlite_files():
    d = tempfile.mkdtemp(); url = f"sqlite:///{d}/v.db"; s, v = make(db_url=url); g, er = who(s, GW), who(s, ER)
    v.tokenizer.tokenize(tok_req(f"call {PH}", [("PHONE", PH)]), g)
    with v.ctx.db.transaction() as tx: row = tx.one("SELECT r.ciphertext AS ct, k.wrapped_dek AS wk FROM vault_records r JOIN borrower_keys k ON k.key_id = r.key_id")
    ct, wk = bytes(row["ct"]), bytes(row["wk"])
    assert v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er).status == "COMPLETED"
    blob = b"".join(open(f"{d}/v.db{ext}", "rb").read() for ext in ("", "-wal", "-shm") if os.path.exists(f"{d}/v.db{ext}"))
    assert ct not in blob and wk not in blob, "the destroyed wrapped DEK / the erased ciphertext can still be read from the database files"
    assert v.ctx.db._conn.execute("PRAGMA secure_delete").fetchone()[0] in (1, 2), "freed pages are overwritten (explicitly, not by a build default)"


# ---- finding A4: a KEK rotation in one process was not seen by another, which kept wrapping NEW keys under the old master version
def test_a_second_process_wraps_new_keys_under_the_rotated_master_version():
    from layer2_vault.services import build_services
    d = tempfile.mkdtemp(); url = f"sqlite:///{d}/v.db"; m1, m2 = os.urandom(32), os.urandom(32)
    s, p1 = make(master_secrets={1: m1, 2: m2}, current_key_version=1, db_url=url)
    p2 = build_services(Settings.for_testing(master_secrets={1: m1, 2: m2}, current_key_version=1, db_url=url, registry=s.registry, rehydrate_policy=s.rehydrate_policy))      # another process, still on version 1
    g = who(s, GW); p1.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], borrower="A", rid="a"), g)
    r = p1.lifecycle.rotate(__import__("layer2_vault.schemas.vault_schemas", fromlist=["x"]).RotateRequest(target_key_version=2, operation="KEK"), who(s, KO))
    assert r.keys_by_kek_version == {"2": 1}, r
    p2.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], borrower="B", rid="b"), g)
    assert {k["borrower_id"]: k["kek_version"] for k in q(p1, "SELECT borrower_id, kek_version FROM borrower_keys")} == {"A": 2, "B": 2}, "the new borrower's DEK must be under the CURRENT master version"
    s3 = Settings.for_testing(master_secrets={1: m1}, current_key_version=1, db_url=url, registry=s.registry, rehydrate_policy=s.rehydrate_policy); p3 = build_services(s3)       # a process that does not hold version 2
    expect(VaultError, lambda: p3.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], borrower="C", rid="c"), who(s3, GW)), "master_key_unavailable")


# ---- finding A5: the context version restarted at 1 after a deletion, so a reader that had seen version 5 ignored the new context
def test_a_reader_notices_a_context_that_was_deleted_and_rewritten_with_a_lower_version():
    from layer2_vault.config.settings import ServiceIdentity
    s, v = make(); c = v.context; ctx = ServiceIdentity("gw", frozenset({"context", "tokenize"})); g = who(s, GW)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="a"), g)
    for i in range(5): c.save("B1", "C1", {"n": i}, ctx)
    assert c.load("B1", "C1", ctx)[0] == 5
    from layer2_vault.schemas.vault_schemas import RotateRequest
    v.lifecycle.rotate(RotateRequest(target_key_version=1, operation="DEK"), who(s, KO))                # drops the context
    assert c.save("B1", "C1", {"n": "fresh"}, ctx) == 1
    ver, state = c.load("B1", "C1", ctx, newer_than=5); assert state == {"n": "fresh"}, "a lower version is a DIFFERENT context, not 'unchanged'"


# ---- finding B6: extend (internal) skipped the purpose check and the value-length cap
def test_extend_respects_the_callers_purpose_scope_and_the_length_limit():
    from layer2_vault.config.settings import ServiceIdentity
    s, v = make(); g = who(s, GW); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], purpose="collections"), g)
    service_only = ServiceIdentity("svc", frozenset({"tokenize"}), frozenset({"service"}), None)
    expect(VaultDenied, lambda: v.internal.extend("C1", "<PHONE_1>", service_only, joined=PH + " 1"), "token_not_found")        # same answer as an unknown token: no existence oracle
    expect(ValidationFailed, lambda: v.internal.extend("C1", "<PHONE_1>", g, joined="9" * 100000), "bad_value")
    assert v.internal.extend("C1", "<PHONE_1>", g, joined=PH + " 1") == "<PHONE_1>"


# ---- finding B7: opening a NEWER database with older code rewrote its schema version (a later strict open then passed)
def test_older_code_refuses_a_newer_database_and_does_not_restamp_it():
    from layer2_vault.database.connection import Database
    d = tempfile.mkdtemp(); url = f"sqlite:///{d}/v.db"; Database(url).close()
    import sqlite3; c = sqlite3.connect(f"{d}/v.db"); c.execute("UPDATE vault_meta SET value = '99' WHERE key = 'schema_version'"); c.commit(); c.close()
    expect(VaultError, lambda: Database(url), "schema_newer_than_this_code")
    assert sqlite3.connect(f"{d}/v.db").execute("SELECT value FROM vault_meta WHERE key = 'schema_version'").fetchone()[0] == "99"
    expect(VaultError, lambda: Database(url, migrate=False), "schema_version_mismatch")


# ---- finding B8: an id with a trailing newline created a SEPARATE borrower (erasing 'b1' would have left 'b1\n' behind)
def test_ids_with_a_trailing_newline_are_rejected_everywhere():
    s, v = make(); g = who(s, GW)
    for bad in ("b1\n", "b1\r\n", "B 1", "b1\x00"):
        expect(ValidationFailed, lambda b=bad: v.tokenizer.tokenize_values(b, "C1", "collections", [("PHONE", PH)], g), "invalid_request")
        expect(ValidationFailed, lambda b=bad: v.tokenizer.tokenize_values("b1", b, "collections", [("PHONE", PH)], g), "invalid_request")
        from layer2_vault.config.settings import ServiceIdentity
        expect(ValidationFailed, lambda b=bad: v.context.load(b, "C1", ServiceIdentity("x", frozenset({"context"}))), "invalid_request")
    assert q(v, "SELECT 1 FROM borrower_state") == []
    from fastapi.testclient import TestClient
    from layer2_vault.api.routes import create_app
    from layer2_vault.tests._util import LI
    c = TestClient(create_app(v)); assert c.get("/vault/records/v_x%0A", headers={"Authorization": f"Bearer {LI}"}).status_code == 422


# ---- finding B9 / A7: the HTTP configuration path defaulted to an in-memory database, and repr(Settings) printed the database URL (with its password)
def test_the_http_configuration_needs_a_database_and_never_prints_its_url():
    env = {"VAULT_MASTER_KEY": os.urandom(32).hex(), "VAULT_SERVICE_TOKENS": "{}"}
    expect(ValidationFailed, lambda: Settings.from_env(env), "database_url_missing")
    assert Settings.from_env({**env, "VAULT_ALLOW_EPHEMERAL": "1"}).db_url == "sqlite:///:memory:"
    st = Settings.from_env({**env, "VAULT_DB_URL": "postgresql://vault_app:S3cretPassw0rd@db/vault"}); assert "S3cretPassw0rd" not in repr(st) and st.db_url.endswith("@db/vault")
    from layer2_vault.core.key_manager import KeyManager
    for short in (b"", b"x" * 31): expect(ValidationFailed, lambda b=short: KeyManager({1: b}, 1), "master_secret_too_short")


def test_a_second_process_indexes_new_values_under_the_rotated_blind_index_version_and_still_finds_old_ones():
    from layer2_vault.services import build_services
    from layer2_vault.schemas.vault_schemas import RotateRequest
    d = tempfile.mkdtemp(); url = f"sqlite:///{d}/v.db"; m1, m2 = os.urandom(32), os.urandom(32)
    s, p1 = make(master_secrets={1: m1, 2: m2}, current_key_version=1, db_url=url)
    p2 = build_services(Settings.for_testing(master_secrets={1: m1, 2: m2}, current_key_version=1, db_url=url, registry=s.registry, rehydrate_policy=s.rehydrate_policy))         # a process that never saw the rotation
    g = who(s, GW); p1.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="a"), g)
    p1.lifecycle.rotate(RotateRequest(target_key_version=2, operation="BLIND_INDEX"), who(s, KO))
    other = "9811122233"; p2.tokenizer.tokenize(tok_req(other, [("PHONE", other)], rid="b"), g)
    assert {r["index_version"] for r in q(p1, "SELECT index_version FROM blind_indexes")} == {2}, "the stale process must index under the version the deployment is on"
    again = p2.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="c"), g); assert again.mappings[0].token == "<PHONE_1>" and len(q(p1, "SELECT 1 FROM vault_records")) == 2, "same value, same token"


if __name__ == "__main__":
    run(globals())
