"""Revoked mappings, token-collision handling, Layer 7 hash-chained events, backup behaviour, and fail-closed behaviour of the gateway on this vault."""
import json
import os
import shutil
import tempfile

from layer2_vault.config.settings import ServiceIdentity, Settings
from layer2_vault.core.errors import VaultDenied, VaultError, VaultUnavailable
from layer2_vault.database.connection import ConflictError
from layer2_vault.schemas.vault_schemas import EraseRequest, RotateRequest
from layer2_vault.services import HashChainedEvents, build_services
from layer2_vault.tests._util import ER, GW, KO, expect, make, reh_req, run, tok_req, who

PH = "9845012345"
ADMIN = ServiceIdentity("admin", frozenset({"admin"}))


def test_revoked_mapping_cannot_be_rehydrated_and_the_value_gets_a_new_token():
    s, v = make(); g = who(s, GW); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="a"), g)
    assert v.lifecycle.revoke_token("C1", "<PHONE_1>", ADMIN)
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "token_not_found")
    assert v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="b"), g).mappings[0].token == "<PHONE_2>"
    assert v.rehydration.rehydrate(reh_req(["<PHONE_2>"]), g).values == {"<PHONE_2>": PH}
def test_revoking_needs_admin_and_an_unknown_token_is_reported():
    s, v = make(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), who(s, GW))
    expect(VaultDenied, lambda: v.lifecycle.revoke_token("C1", "<PHONE_1>", who(s, GW)), "forbidden"); assert v.lifecycle.revoke_token("C1", "<PHONE_7>", ADMIN) is False
def test_a_duplicate_mapping_hits_the_database_constraint_and_is_surfaced_as_a_conflict():
    s, v = make(); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), who(s, GW))
    with v.ctx.db.transaction() as tx:
        try:
            tx.execute("INSERT INTO token_mappings (token_id, vault_id, token, borrower_id, conversation_id, pii_type, seq, status) VALUES ('t_x','v_x','<PHONE_1>','B1','C1','PHONE',1,'ACTIVE')")
            raise AssertionError("duplicate accepted")
        except ConflictError:
            pass
def test_a_persistent_conflict_fails_closed_instead_of_looping_or_returning_a_token():
    s, v = make(); g = who(s, GW)
    v.tokenizer.resolve = lambda *a, **k: (_ for _ in ()).throw(ConflictError("always"))
    e = expect(VaultUnavailable, lambda: v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)); assert e.code == "conflict_retries_exhausted"
def test_hash_chained_events_detect_edits_and_survive_a_restart():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "events.jsonl"); sink = HashChainedEvents(p)
        s, v = make(events=sink); g = who(s, GW); v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g); v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g)
        assert sink.verify() and oct(os.stat(p).st_mode & 0o777) == "0o600" and PH not in open(p).read()
        sink2 = HashChainedEvents(p); assert sink2.head == sink.head and sink2.verify()              # resumes the chain after a restart
        lines = open(p).read().splitlines(); rec = json.loads(lines[1]); rec["caller"] = "someone-else"; lines[1] = json.dumps(rec)
        open(p, "w").write("\n".join(lines) + "\n"); assert not HashChainedEvents(p).verify()
def test_if_the_audit_file_cannot_be_written_rehydration_is_refused():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "events.jsonl"); s, v = make(events=HashChainedEvents(p)); g = who(s, GW)
        v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g); os.remove(p); os.mkdir(p)             # a directory where the file should be: every write fails
        expect(VaultUnavailable, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "audit_unavailable")
def test_backup_taken_before_erasure_still_holds_the_wrapped_key_and_rotation_closes_that():
    """The documented residual risk, demonstrated: erasure cannot reach a copy of the database made earlier."""
    with tempfile.TemporaryDirectory() as d:
        url, m1, m2 = f"sqlite:///{d}/v.db", os.urandom(32), os.urandom(32)
        s, v = make(master_secrets={1: m1, 2: m2}, db_url=url); g, er, ko = who(s, GW), who(s, ER), who(s, KO)
        v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g); v.ctx.db._conn.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy(f"{d}/v.db", f"{d}/backup.db")
        v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
        sb = Settings.for_testing(master_secrets={1: m1}, current_key_version=1, db_url=f"sqlite:///{d}/backup.db"); vb = build_services(sb)
        assert vb.rehydration.rehydrate(reh_req(["<PHONE_1>"]), who(sb, GW)).values == {"<PHONE_1>": PH}      # KNOWN LIMIT: old backup + old master secret still decrypts
        # the fix the vault recommends: rotate the master secret (the live DB no longer depends on secret 1), then retire secret 1 and the old backup
        v.lifecycle.rotate(RotateRequest(target_key_version=2), ko)
        with v.ctx.db.transaction() as tx: assert all(k["kek_version"] == 2 for k in v.ctx.repo.all_keys(tx))
def test_the_gateway_blocks_and_calls_no_llm_when_the_vault_database_is_down():
    from layer3_gateway.graph import Gateway
    from layer3_gateway.test_gateway import Stub, sp
    from layer2_vault.compat import ModularVault
    t = f"my number is {PH}"; mv = ModularVault(); called = []
    gw = Gateway(detector=Stub([sp(t, PH, "PHONE")]), audit_detector=Stub(), vault=mv, llm=lambda m: called.append(m) or "x")
    mv.svc.ctx.db._conn.close()
    out = gw.handle("c", "C", t, borrower_id="B1")
    assert out["status"] == "blocked" and not called and PH not in str(out), out
def test_vault_errors_are_one_type_for_old_and_new_vault():
    from layer2_vault.vault import VaultError as Legacy
    assert Legacy is VaultError

if __name__ == "__main__":
    run(globals())
