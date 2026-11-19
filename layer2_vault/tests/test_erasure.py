"""Vault-side erasure: deletes and destroys, never returns plaintext, never claims COMPLETED unless every step succeeded."""
from layer2_vault.core.errors import ValidationFailed, VaultDenied
from layer2_vault.schemas.vault_schemas import EraseRequest
from layer2_vault.tests._util import ER, GW, LI, expect, make, reh_req, run, tok_req, who

PH = "9845012345"


def setup(conv_list=("C1",)):
    s, v = make(); g = who(s, GW)
    for i, c in enumerate(conv_list):
        v.tokenizer.tokenize(tok_req(f"call {PH} Rahul", [("PHONE", PH), ("NAME", "Rahul")], conv=c, rid=f"t{i}"), g)
    return s, v, g, who(s, ER)


def counts(v, b="B1"):
    with v.ctx.db.transaction() as tx: return v.ctx.repo.count_scope(tx, b)


def test_borrower_erasure_deletes_rows_indexes_mappings_and_destroys_the_key():
    s, v, g, er = setup(); r = v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
    assert r.status == "COMPLETED" and r.key_destroyed and r.counts == {"records": 2, "token_mappings": 2, "blind_indexes": 2}, r
    assert counts(v) == {"vault_records": 0, "token_mappings": 0, "blind_indexes": 0}
    with v.ctx.db.transaction() as tx:
        k = v.ctx.repo.latest_key(tx, "B1"); assert k["key_status"] == "DESTROYED" and k["wrapped_dek"] is None and k["wrap_nonce"] is None and k["destroyed_at"]
def test_after_erasure_rehydrate_and_tokenise_are_refused_and_nothing_is_minted():
    s, v, g, er = setup(); v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "borrower_not_active")
    expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req("call 9811111111", [("PHONE", "9811111111")], rid="n"), g), "borrower_not_active")
    assert counts(v)["vault_records"] == 0
def test_erasure_response_states_the_residual_risk_and_the_layer6_boundary():
    s, v, g, er = setup(); n = v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er).note
    assert "backups" in n and "Layer 6" in n
def test_erasure_does_not_touch_other_borrowers():
    s, v, g, er = setup(); v.tokenizer.tokenize(tok_req("9811111111", [("PHONE", "9811111111")], borrower="B2", conv="C9", rid="b2"), g)
    v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"], borrower="B2", conv="C9"), g).values == {"<PHONE_1>": "9811111111"}
def test_conversation_scope_deletes_only_that_conversation_and_keeps_the_key():
    s, v, g, er = setup(("C1", "C2")); r = v.deletion.erase(EraseRequest(job_id="J2", borrower_id="B1", scope="CONVERSATION", conversation_id="C1"), er)
    assert r.status == "COMPLETED" and not r.key_destroyed and r.counts["records"] == 2 and "not a crypto-shred" in r.note
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"], conv="C2"), g).values == {"<PHONE_1>": PH}
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"], conv="C1"), g), "token_not_found")
def test_retry_of_a_completed_job_is_idempotent():
    s, v, g, er = setup(); a = v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er); b = v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
    assert a.model_dump() == b.model_dump() or (a.status == b.status == "COMPLETED" and a.counts == b.counts and a.key_destroyed == b.key_destroyed)
def test_a_job_id_cannot_be_reused_for_a_different_request():
    s, v, g, er = setup(); v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
    expect(ValidationFailed, lambda: v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B2"), er))
def test_a_failing_step_gives_PARTIAL_FAILURE_never_COMPLETED_and_blocks_use_meanwhile():
    s, v, g, er = setup()
    def boom(step):
        if step == "destroy_key": raise RuntimeError("key service down")
    v.deletion.fault = boom; r = v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
    assert r.status == "PARTIAL_FAILURE" and not r.key_destroyed and "retry" in r.note
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g))            # pending: no rehydration
    expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req("9811111111", [("PHONE", "9811111111")], rid="x"), g), "borrower_not_active")   # pending: no new writes
    v.deletion.fault = None; r2 = v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
    assert r2.status == "COMPLETED" and r2.key_destroyed and r2.counts["records"] == 2, r2
def test_a_failure_while_deleting_rows_is_also_partial_and_resumable():
    s, v, g, er = setup()
    def boom(step):
        if step == "delete_rows": raise RuntimeError("store down")
    v.deletion.fault = boom; assert v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er).status == "PARTIAL_FAILURE" and counts(v)["vault_records"] == 2
    v.deletion.fault = None; assert v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er).status == "COMPLETED" and counts(v)["vault_records"] == 0
def test_erasure_caller_cannot_tokenise_or_rehydrate_and_others_cannot_erase():
    s, v, g, er = setup()
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), er), "forbidden")
    expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req("9811111111", [("PHONE", "9811111111")]), er), "forbidden")
    expect(VaultDenied, lambda: v.deletion.erase(EraseRequest(job_id="Jx", borrower_id="B1"), who(s, LI)), "forbidden"); expect(VaultDenied, lambda: v.deletion.erase(EraseRequest(job_id="Jy", borrower_id="B1"), g), "forbidden")
def test_borrower_with_no_data_erases_cleanly():
    s, v, g, er = setup(); r = v.deletion.erase(EraseRequest(job_id="J0", borrower_id="NOBODY"), er); assert r.status == "COMPLETED" and not r.key_destroyed
def test_reinstating_an_erased_borrower_needs_the_admin_capability():
    s, v, g, er = setup(); v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
    expect(VaultDenied, lambda: v.lifecycle.reinstate_borrower("B1", er), "forbidden")
def test_sealed_match_returns_positions_not_plaintext_and_nothing_after_erasure():
    s, v, g, er = setup()
    from layer2_vault.services.internal_ops import InternalOps
    from layer2_vault.config.settings import ServiceIdentity
    sweeper = ServiceIdentity("sweeper", frozenset({"erasure_sweep"}))
    hits = v.internal.match_borrower("B1", [f"debug payload mobile={PH} ok", "nothing here"], sweeper)
    assert hits[0] and hits[0][0][0] == "<PHONE_1>" and hits[1] == [] and PH not in str(hits)
    v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er); assert v.internal.match_borrower("B1", [f"mobile={PH}"], sweeper) == [[]]

def test_the_plaintext_sweep_is_gone_and_the_sealed_calls_return_no_values():
    s, v, g, er = setup()
    from layer2_vault.config.settings import ServiceIdentity
    from layer2_vault.compat import ModularVault
    sweeper = ServiceIdentity("sweeper", frozenset({"erasure_sweep"}))
    assert not hasattr(v.internal, "decrypt_for_erasure") and not hasattr(ModularVault, "decrypt_for_erasure")
    kinds = v.internal.erasure_scan("B1", [f"debug mobile={PH} ok", "nothing here", f"{PH[:5]} {PH[5:]} spaced"], sweeper)
    assert kinds == ["strong", None, "strong"] and all(k is None or isinstance(k, str) and PH not in k for k in kinds)
    assert v.internal.erasure_scan("B1", [f"x{PH[:4]}-{PH[4:]}y"], sweeper, mode="collapsed") == ["strong"]
    assert v.internal.find_unmapped_copies("B1", f"call {PH}", sweeper) is True and v.internal.find_unmapped_copies("B1", "hello", sweeper) is False
    rows = [("r1", [1.0, 0.0]), ("r2", [0.0, 1.0])]
    got = v.internal.erasure_probe("B1", rows, lambda t: [1.0, 0.0], sweeper, 0.5)
    assert got == [("r1", 1.0)] and PH not in str(got)
    v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
    assert v.internal.erasure_scan("B1", [f"mobile={PH}"], sweeper) == [None] and v.internal.find_unmapped_copies("B1", PH, sweeper) is False

if __name__ == "__main__":
    run(globals())
