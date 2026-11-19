"""One regression test per verified finding of the independent review of the modular vault (each bug was demonstrated first)."""
import json
import os
import sqlite3
import tempfile
import threading

from fastapi.testclient import TestClient

from layer2_vault.api.routes import create_app
from layer2_vault.compat import ModularVault
from layer2_vault.config.settings import ServiceIdentity, ServiceRegistry, Settings
from layer2_vault.core.blind_index import blind_index as BI, normalise
from layer2_vault.database.connection import ConflictError
from layer2_vault.core.errors import DecryptionError, KeyStateError, ValidationFailed, VaultDenied, VaultError, VaultUnavailable
from layer2_vault.schemas.vault_schemas import EraseRequest, RotateRequest
from layer2_vault.services import HashChainedEvents, build_services
from layer2_vault.tests._util import ER, GW, KO, expect, make, reh_req, run, tok_req, who

PH = "9845012345"
K = os.urandom(32)


# ---- HIGH: a CONVERSATION erase must never re-open a borrower that is ERASED or ERASURE_PENDING
def _setup():
    s, v = make(); g, er = who(s, GW), who(s, ER)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], conv="C1", rid="a"), g); v.tokenizer.tokenize(tok_req("9811111111", [("PHONE", "9811111111")], conv="C2", rid="b"), g)
    return s, v, g, er


def test_HIGH_conversation_erase_cannot_reopen_an_erased_borrower():
    s, v, g, er = _setup(); v.deletion.erase(EraseRequest(job_id="JB", borrower_id="B1"), er)
    v.deletion.erase(EraseRequest(job_id="JC", borrower_id="B1", scope="CONVERSATION", conversation_id="C1"), er)
    with v.ctx.db.transaction() as tx: assert v.ctx.repo.borrower_state(tx, "B1") == "ERASED"
    expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req("9822222222", [("PHONE", "9822222222")], rid="n"), g), "borrower_not_active")
def test_HIGH_conversation_erase_cannot_reopen_a_borrower_whose_erasure_is_pending():
    s, v, g, er = _setup()
    def boom(step):
        if step == "destroy_key": raise RuntimeError("down")
    v.deletion.fault = boom; assert v.deletion.erase(EraseRequest(job_id="JB", borrower_id="B1"), er).status == "PARTIAL_FAILURE"; v.deletion.fault = None
    r = v.deletion.erase(EraseRequest(job_id="JC", borrower_id="B1", scope="CONVERSATION", conversation_id="C2"), er); assert r.status == "COMPLETED"
    with v.ctx.db.transaction() as tx: assert v.ctx.repo.borrower_state(tx, "B1") == "ERASURE_PENDING" and v.ctx.repo.active_key(tx, "B1") is not None
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"], conv="C1"), g)); expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req("9822222222", [("PHONE", "9822222222")], rid="n"), g))
def test_conversation_erase_of_an_active_borrower_still_restores_active():
    s, v, g, er = _setup(); v.deletion.erase(EraseRequest(job_id="JC", borrower_id="B1", scope="CONVERSATION", conversation_id="C1"), er)
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"], conv="C2"), g).values and v.tokenizer.tokenize(tok_req("9833333333", [("PHONE", "9833333333")], rid="n"), g)
def test_resumed_job_returns_the_same_public_counts_as_a_replay():
    s, v, g, er = _setup()
    def boom(step):
        if step == "finalise": raise RuntimeError("x")
    v.deletion.fault = boom; v.deletion.erase(EraseRequest(job_id="J", borrower_id="B1"), er); v.deletion.fault = None
    a = v.deletion.erase(EraseRequest(job_id="J", borrower_id="B1"), er); b = v.deletion.erase(EraseRequest(job_id="J", borrower_id="B1"), er)
    assert a.counts == b.counts and "key_destroyed" not in a.counts and not any(k.startswith("_") for k in a.counts)
def test_compat_destroy_raises_when_a_job_is_incomplete_and_a_retry_resumes_it():
    mv = ModularVault(); t = mv.put("c1", "PHONE", PH, borrower_id="b1")
    def boom(step):
        if step == "destroy_key": raise RuntimeError("down")
    mv.svc.deletion.fault = boom; expect(VaultError, lambda: mv.destroy_borrower("b1"), "erasure_incomplete")
    mv.svc.deletion.fault = None; assert mv.destroy_borrower("b1")["key_destroyed"] and not mv.has_key("b1")
    mv2 = ModularVault(); mv2.put("cX", "PHONE", PH, borrower_id="b2")
    def boom2(step):
        if step == "delete_rows": raise RuntimeError("store down")
    mv2.svc.deletion.fault = boom2; expect(VaultError, lambda: mv2.destroy("cX"), "erasure_incomplete")
    mv2.svc.deletion.fault = None; assert mv2.destroy("cX") >= 0 and mv2.tokens("cX") == []                  # the retry picks the open job's owner up and finishes

# ---- blind-index collisions (same bug class as the earlier PHONE one)
def test_honorific_only_names_do_not_collide():
    vals = ["Dr", "Mrs", "Smt", "Sri", "Mr"]; assert len({BI(K, "B", "C", "p", "NAME", v) for v in vals}) == len(vals)
    s, v = make(); r = v.tokenizer.tokenize(tok_req("hello Dr and Mrs", [("NAME", "Dr"), ("NAME", "Mrs")]), who(s, GW)); assert r.masked_text == "hello <NAME_1> and <NAME_2>"
def test_honorifics_are_still_ignored_when_a_real_name_follows():
    assert normalise("NAME", "Dr. Rahul Sharma") == normalise("NAME", "rahul sharma")
def test_phones_with_different_country_codes_do_not_collide_but_indian_prefixes_still_unify():
    assert normalise("PHONE", "+1 415 555 2671") != normalise("PHONE", "+44 415 555 2671")
    assert len({normalise("PHONE", x) for x in ("9845012345", "+91 98450 12345", "0091 9845012345", "09845012345")}) == 1

# ---- internal tokenise path now enforces what the HTTP path enforces
def test_tokenize_values_refuses_unknown_purposes_bad_labels_and_ids():
    mv = ModularVault()
    expect(VaultDenied, lambda: mv.put("c1", "PHONE", PH, borrower_id="b1", purpose="evil_purpose"))
    expect(ValidationFailed, lambda: mv.put("c1", "<x> alice@x.com", "v", borrower_id="b1"))
    expect(ValidationFailed, lambda: mv.put("bad id with spaces", "PHONE", PH, borrower_id="b1"))
    assert mv.tokens("c1") == []
def test_tokenize_values_fails_closed_when_the_permission_service_is_down():
    class Down:
        def check(self, *a, **k): raise ConnectionError("x")
    s, v = make(permissions=Down()); expect(VaultUnavailable, lambda: v.tokenizer.tokenize_values("B1", "C1", "service", [("PHONE", PH)], who(s, GW)), "permission_unavailable")

# ---- audit chain
def test_audit_chain_stays_valid_with_two_writers_and_detects_deleting_the_oldest_events():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "e.jsonl"); a, b = HashChainedEvents(p), HashChainedEvents(p)
        for i in range(6): (a if i % 2 == 0 else b)({"op": "x", "n": i})
        assert a.verify() and b.verify()
        lines = open(p).read().splitlines(); open(p, "w").write("\n".join(lines[2:]) + "\n"); assert not HashChainedEvents(p).verify()
def test_audit_chain_threads_share_one_valid_chain():
    with tempfile.TemporaryDirectory() as d:
        sink = HashChainedEvents(os.path.join(d, "e.jsonl")); ts = [threading.Thread(target=lambda i=i: sink({"op": "t", "n": i})) for i in range(20)]
        [t.start() for t in ts]; [t.join() for t in ts]; assert sink.verify()

# ---- database error handling
def test_only_uniqueness_violations_are_conflicts_other_integrity_errors_surface():
    s, v = make()
    with v.ctx.db.transaction() as tx:
        try:
            tx.execute("INSERT INTO borrower_state (borrower_id, state, updated_at) VALUES (NULL, 'ACTIVE', 1.0)"); raise AssertionError("accepted")
        except ConflictError: raise AssertionError("a NOT NULL violation must NOT be treated as a retryable conflict")
        except Exception as e:                                                       # sqlite3.IntegrityError or psycopg NotNullViolation
            assert "NOT NULL" in str(e).upper() or "NOTNULL" in type(e).__name__.upper(), repr(e)
def test_two_separate_database_connections_on_one_file_keep_one_mapping():
    with tempfile.TemporaryDirectory() as d:
        url, m = f"sqlite:///{d}/v.db", os.urandom(32); s1, v1 = make(master_secrets={1: m}, db_url=url); s2, v2 = make(master_secrets={1: m}, db_url=url); out = []
        def work(v, s, i): out.append(v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid=f"r{i}"), who(s, GW)).mappings[0].vault_id)
        ts = [threading.Thread(target=work, args=(v1 if i % 2 else v2, s1 if i % 2 else s2, i)) for i in range(16)]; [t.start() for t in ts]; [t.join() for t in ts]
        assert len(out) == 16 and len(set(out)) == 1
        with v1.ctx.db.transaction() as tx: assert v1.ctx.repo.count_scope(tx, "B1")["vault_records"] == 1

# ---- rotation / keys
def test_a_failed_rotation_restores_the_active_key_version_and_changes_nothing():
    s, v = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}); g = who(s, GW); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    orig = v.ctx.repo.replace_index; v.ctx.repo.replace_index = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    expect(RuntimeError, lambda: v.lifecycle.rotate(RotateRequest(target_key_version=2), who(s, KO))); v.ctx.repo.replace_index = orig
    assert v.ctx.keys.current_version == 1 and s.current_key_version == 1
    with v.ctx.db.transaction() as tx: assert {k["kek_version"] for k in v.ctx.repo.all_keys(tx)} == {1}
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g).values
def test_records_still_indexed_under_the_old_version_are_reused_in_the_middle_of_a_migration():
    s, v = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}); g = who(s, GW)
    a = v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="a"), g).mappings[0]
    v.ctx.keys.current_version = 2                                                          # new version active, data not yet migrated
    b = v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="b"), g).mappings[0]
    assert a.vault_id == b.vault_id
def test_swapping_ciphertext_between_two_rows_of_one_borrower_fails_authentication():
    s, v = make(); g = who(s, GW); v.tokenizer.tokenize(tok_req("9811111111 9822222222", [("PHONE", "9811111111"), ("PHONE", "9822222222")]), g)
    with v.ctx.db.transaction() as tx:
        rows = tx.query("SELECT vault_id, ciphertext, nonce FROM vault_records ORDER BY vault_id"); a, b = rows
        # a database-level UNIQUE(key_id, nonce) now forbids two rows sharing a nonce, so swap in three steps through a scratch value
        tx.execute("UPDATE vault_records SET nonce = ? WHERE vault_id = ?", (b"\x00" * 12, a["vault_id"]))
        tx.execute("UPDATE vault_records SET ciphertext = ?, nonce = ? WHERE vault_id = ?", (a["ciphertext"], a["nonce"], b["vault_id"]))
        tx.execute("UPDATE vault_records SET ciphertext = ?, nonce = ? WHERE vault_id = ?", (b["ciphertext"], b["nonce"], a["vault_id"]))
    expect(Exception, lambda: [v.rehydration.rehydrate(reh_req([t]), g) for t in ("<PHONE_1>", "<PHONE_2>")])
def test_extend_counts_toward_the_key_usage_limit():
    from layer2_vault.config.settings import ServiceIdentity
    s, v = make(key_usage_limit=2); g = who(s, GW); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    v.internal.extend("C1", "<PHONE_1>", g, joined="9845012345 1"); expect(KeyStateError, lambda: v.internal.extend("C1", "<PHONE_1>", g, joined="9845012345 2"), "key_usage_limit_reached")

# ---- API / config hardening
def test_a_hostile_request_id_header_is_not_logged_verbatim():
    import io, logging
    buf = io.StringIO(); h = logging.StreamHandler(buf); lg = logging.getLogger("te03.vault.api"); lg.setLevel(logging.INFO); lg.addHandler(h)
    try:
        s, v = make(); c = TestClient(create_app(v)); c.get("/vault/health", headers={"Authorization": "Bearer " + KO, "X-Request-ID": "x" * 5000 + "\nFAKE LOG LINE 98450 12345"})
    finally: lg.removeHandler(h)
    out = buf.getvalue(); assert "FAKE LOG LINE" not in out and "98450" not in out and len(out) < 400
def test_config_mistakes_are_rejected_not_silently_accepted():
    expect(ValidationFailed, lambda: ServiceRegistry({"tok-aaaaaaaaaaaaaaaaaa": ("svc", {"tokenize"}), "tok-bbbbbbbbbbbbbbbbbb": ("svc", {"rehydrate"})}))
    expect(ValidationFailed, lambda: Settings.for_testing(rehydrate_policy={"collections": "borrower_reply"}))
    expect(ValidationFailed, lambda: ServiceRegistry({"tok-aaaaaaaaaaaaaaaaaa": ("svc", "tokenize")}))
    expect(ValidationFailed, lambda: Settings.from_env({"VAULT_MASTER_KEY": os.urandom(32).hex(), "VAULT_DB_URL": "sqlite:///:memory:", "VAULT_SERVICE_TOKENS": json.dumps({"a": {"token": "t" * 20, "capabilities": ["tokenize"]}, "b": {"token": "t" * 20, "capabilities": ["tokenize"]}})}))

if __name__ == "__main__":
    run(globals())
