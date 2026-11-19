"""Run:  .venv/bin/python -m layer6_erasure.test_erasure      (plain python, PASS lines; pytest not installed)
Each test works on a fresh temp copy of the MOCK stores in data/stores/pristine (run mock_data.seed once first)."""
import json
import os
import shutil
import tempfile

from layer5_lineage.registry import LineageRegistry
from layer7_audit.chain import AuditLog

from . import adapters, mock_data
from .agent import build_agent, erase
from .evaluate import PRISTINE, present
from .vault_adapter import FallbackVault, get_vault


def _setup(vault=None, max_passes=4, disable=()):
    root = os.path.join(tempfile.mkdtemp(), "stores")
    shutil.copytree(PRISTINE, root)
    truth = json.load(open(os.path.join(root, "truth.json"), encoding="utf-8"))
    if vault is None:
        vault, _ = get_vault()
    mock_data.seed_vault(vault, truth)
    reg = LineageRegistry.load(os.path.join(root, "registry.json"))
    audit = AuditLog(os.path.join(root, "audit_log.jsonl"), key=b"t" * 32)
    app = build_agent(root, vault, reg, audit, max_passes=max_passes, disable=disable)
    return root, vault, reg, audit, app, truth


def _req(b, n="T1"):
    return {"request_id": n, "borrower_id": b}


def _copies_left(root, truth, b):
    pres = present(root)
    return [(s, i, k) for s, i, k in truth["borrowers"][b]["copies"] if i in pres.get(s, set())]


def test_end_to_end_erases_every_seeded_copy_without_collateral():
    root, vault, reg, audit, app, truth = _setup()
    others_before = {b: len(_copies_left(root, truth, b)) for b in ("B0001", "B0002", "B0003")}
    out = erase(app, _req("B0004"))
    assert out["status"] == "ERASED_VERIFIED", out["status"]
    assert _copies_left(root, truth, "B0004") == []
    assert {b: len(_copies_left(root, truth, b)) for b in others_before} == others_before        # nobody else touched
    pres = present(root)
    assert all(i in pres[s] for s, i in truth["decoys"])                                         # decoys survive


def test_vault_key_destroyed_and_registry_forgets():
    root, vault, reg, audit, app, truth = _setup()
    assert vault.has_key("B0004") and vault.entries_for_borrower("B0004")
    erase(app, _req("B0004"))
    assert not vault.has_key("B0004") and vault.entries_for_borrower("B0004") == []
    assert all(v == [] for v in reg.known_for("B0004").values())


def test_agent_state_trace_and_audit_never_contain_raw_values():
    root, vault, reg, audit, app, truth = _setup()
    out = erase(app, _req("B0004"))
    blob = json.dumps(out, default=str, ensure_ascii=False) + open(audit.path, encoding="utf-8").read()
    v = truth["borrowers"]["B0004"]["values"]
    a = v["aadhaar"]
    for raw in (v["name"], v["phone"], v["aadhaar"], v["pan"], v["account"], v["address"], f"{a[:4]} {a[4:8]} {a[8:]}"):
        assert raw not in blob, f"raw value leaked into agent output: {raw[:3]}..."
    assert "B0004" not in open(audit.path, encoding="utf-8").read()                              # keyed hash only


def test_fail_safe_when_deletes_do_nothing_reports_not_erased_and_keeps_key():
    root, vault, reg, audit, app, truth = _setup(max_passes=3)
    orig = adapters.JsonlStore.delete
    adapters.JsonlStore.delete = lambda self, ids: 0
    try:
        out = erase(app, _req("B0004"))
    finally:
        adapters.JsonlStore.delete = orig
    assert out["status"] == "NOT_ERASED" and out["record"]["escalate"] is True
    assert out["record"]["passes"] == 3 and vault.has_key("B0004")                               # key NOT shredded
    assert any(not s["verified_zero"] for s in out["record"]["stores"])


def test_verify_loops_until_clean_when_first_delete_is_partial():
    root, vault, reg, audit, app, truth = _setup()
    orig = adapters.JsonlStore.delete
    calls = {"n": 0}

    def half(self, ids):
        calls["n"] += 1
        ids = sorted(ids)
        return orig(self, ids[: max(1, len(ids) // 2)]) if calls["n"] <= 4 else orig(self, ids)
    adapters.JsonlStore.delete = half
    try:
        out = erase(app, _req("B0004"))
    finally:
        adapters.JsonlStore.delete = orig
    assert out["record"]["passes"] >= 2 and out["status"] == "ERASED_VERIFIED"
    assert _copies_left(root, truth, "B0004") == []


def test_one_word_name_is_not_deleted_on_its_own_and_status_is_partial():
    root, vault, reg, audit, app, truth = _setup()
    assert truth["borrowers"]["B0031"]["values"]["name"] == "Sanjay"                              # single-token name
    out = erase(app, _req("B0031"))
    assert out["status"] == "PARTIAL" and out["record"]["unresolved_count"] >= 1
    assert any(u["reason"] == "raw_name_single_token" for u in out["record"]["unresolved"])
    assert all(i in present(root)[s] for s, i in truth["decoys"])


def test_shared_full_name_is_not_deleted_without_corroboration():
    root, vault, reg, audit, app, truth = _setup()
    v = truth["borrowers"]["B0007"]["values"]
    assert v["name"] == truth["borrowers"]["B0008"]["values"]["name"]
    adapters.JsonlStore(os.path.join(root, "logs.jsonl"), "logs", "logs").put(
        {"id": "LX1", "conversation_id": None, "text": f"Summarise the case of {v['name']}", "consent": {"purpose": "service"}})
    out = erase(app, _req("B0007"))
    assert "LX1" in present(root)["logs"] and out["status"] == "PARTIAL"
    assert any(u["id"] == "LX1" and u["reason"] == "raw_name_ambiguous" for u in out["record"]["unresolved"])


def test_country_code_and_spaced_variants_of_a_phone_are_found():
    root, vault, reg, audit, app, truth = _setup()
    ph = truth["borrowers"]["B0004"]["values"]["phone"]
    st = adapters.JsonlStore(os.path.join(root, "logs.jsonl"), "logs", "logs")
    for i, t in enumerate((f"+91 {ph[:5]} {ph[5:]}", f"91{ph}", f"{ph[:3]}-{ph[3:6]}-{ph[6:]}")):
        st.put({"id": f"LP{i}", "conversation_id": None, "text": f"alt contact {t} noted", "consent": {"purpose": "service"}})
    erase(app, _req("B0004"))
    left = present(root)["logs"]
    assert not any(f"LP{i}" in left for i in range(3))


def test_unmanaged_file_in_unknown_format_is_flagged_not_silently_ignored():
    root, vault, reg, audit, app, truth = _setup()
    ph = truth["borrowers"]["B0004"]["values"]["phone"]
    p = os.path.join(root, "legacy_export", "old_contacts.csv")
    open(p, "w").write(f"ref,mobile\nX1,{ph}\n")
    out = erase(app, _req("B0004"))
    assert out["status"] == "PARTIAL" and os.path.exists(p) and ph in open(p).read()               # untouched, flagged
    assert any(u["reason"] == "unmanaged_file" for u in out["record"]["unresolved"])


def test_unregistered_store_is_discovered_and_cleaned():
    root, vault, reg, audit, app, truth = _setup()
    out = erase(app, _req("B0004"))
    unmapped = out["record"]["unregistered_stores_with_hits"]
    mine = [c for s, i, c in truth["borrowers"]["B0004"]["copies"] if s.startswith("unmapped:")]
    assert (not mine) or unmapped, "borrower had unmapped copies but none were reported"


def test_audit_record_chain_and_tamper_detection():
    root, vault, reg, audit, app, truth = _setup()
    erase(app, _req("B0004", "R1")); erase(app, _req("B0005", "R2"))
    assert audit.verify_chain()[0]
    lines = open(audit.path).read().splitlines()
    r = json.loads(lines[0]); r["status"] = "ERASED_VERIFIED"; r["stores"][0]["found"] += 99
    lines[0] = json.dumps(r)
    open(audit.path, "w").write("\n".join(lines) + "\n")
    assert audit.verify_chain()[0] is False


def test_works_with_the_fallback_vault_too():
    root, vault, reg, audit, app, truth = _setup(vault=FallbackVault())
    out = erase(app, _req("B0004"))
    assert out["status"] == "ERASED_VERIFIED" and not vault.has_key("B0004")


def test_consent_guard_rejections_were_recorded_without_record_bodies():
    p = os.path.join(PRISTINE, "rejections.jsonl")
    rows = [json.loads(l) for l in open(p)]
    assert rows and all(set(r) == {"ts", "store", "record_id", "reason", "purpose"} for r in rows)
    assert all(r["store"] == "eval_sets" for r in rows)


# ---------------------------------------------------------------- spec items: blindness, resilience, audit proof
def _raw_values(truth, b):
    v = truth["borrowers"][b]["values"]
    return [x for x in v.values()] + [v["aadhaar"][:4] + " " + v["aadhaar"][4:8] + " " + v["aadhaar"][8:]]


class _SpyVault:
    """Wraps the real vault, records every value the agent side RECEIVES, and exposes only the sealed API."""
    def __init__(self, inner):
        self._inner, self.received = inner, []

    def __getattr__(self, name):
        if name == "decrypt_for_erasure":
            raise AttributeError(name)
        f = getattr(self._inner, name)
        if not callable(f) or name in ("put", "get", "tokens", "destroy"):
            return f

        def call(*a, **k):
            r = f(*a, **k)
            self.received.append((name, repr(r)))
            return r
        return call


def test_agent_is_blind_the_vault_never_returns_a_raw_value_and_no_plaintext_call_exists():
    root, vault, reg, audit, app, truth = _setup()
    assert not hasattr(vault, "decrypt_for_erasure"), "the plaintext call must be gone"
    spy = _SpyVault(vault)
    app = build_agent(root, spy, reg, audit, max_passes=4)
    out = erase(app, _req("B0004"))
    assert out["status"] == "ERASED_VERIFIED"
    blob = " ".join(r for _, r in spy.received)
    assert any(n == "erasure_scan" for n, _ in spy.received) and any(n == "erasure_probe" for n, _ in spy.received)
    for raw in _raw_values(truth, "B0004"):
        assert raw not in blob, f"the vault handed back a raw value: {raw[:3]}..."


def test_find_unmapped_copies_returns_only_a_bool():
    root, vault, reg, audit, app, truth = _setup()
    ph = truth["borrowers"]["B0004"]["values"]["phone"]
    assert vault.find_unmapped_copies("B0004", f"debug mobile={ph}") is True
    assert vault.find_unmapped_copies("B0004", "nothing personal here") is False
    assert vault.find_unmapped_copies("B0005", f"debug mobile={ph}") is False                 # someone else's number is not this borrower's


class _BoomVault:
    """Real vault, but destroy_borrower raises like a vault whose erasure job did not finish."""
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, n):
        return getattr(self._inner, n)

    def destroy_borrower(self, *a, **k):
        raise Exception("erasure_incomplete")


def test_vault_erasure_incomplete_does_not_crash_logs_not_erased_and_keeps_registry():
    root, vault, reg, audit, app, truth = _setup()
    boom = _BoomVault(vault)
    app = build_agent(root, boom, reg, audit, max_passes=3)
    out = erase(app, _req("B0004"))                                                            # must not raise
    rec = out["record"]
    assert out["status"] == "NOT_ERASED" and rec["escalate"] is True and rec["vault"]["key_destroyed"] is False
    assert "erasure_incomplete" in rec["vault"]["error"]
    assert _copies_left(root, truth, "B0004") == []                                           # the stores really were cleaned
    assert vault.has_key("B0004")                                                              # key kept: nothing was shredded
    assert any(v for v in reg.known_for("B0004").values()), "registry must keep the lineage so a retry still has it"
    assert audit.verify_chain()[0] and sum(1 for r in audit.records() if r["type"] == "erasure") == 1


def test_a_retry_after_a_failed_shred_reuses_the_same_vault_job_and_finishes():
    root, vault, reg, audit, app, truth = _setup()
    out1 = erase(build_agent(root, _BoomVault(vault), reg, audit, max_passes=3), _req("B0004", "RETRY-1"))
    assert out1["status"] == "NOT_ERASED"
    out2 = erase(build_agent(root, vault, reg, audit, max_passes=3), _req("B0004", "RETRY-1"))        # same request id => same job id
    assert out2["status"] == "ERASED_VERIFIED" and not vault.has_key("B0004")
    from .agent import _job_id
    assert _job_id("RETRY-1", "B0004") == _job_id("RETRY-1", "B0004") != _job_id("RETRY-1", "B0005")


def test_an_unexpected_error_in_a_store_ends_not_erased_instead_of_crashing():
    root, vault, reg, audit, app, truth = _setup()
    orig = adapters.JsonlStore.delete

    def bad(self, ids):
        raise OSError("disk full")
    adapters.JsonlStore.delete = bad
    try:
        out = erase(app, _req("B0004"))
    finally:
        adapters.JsonlStore.delete = orig
    assert out["status"] == "NOT_ERASED" and out["record"]["escalate"] and out["record"]["error"].startswith("delete:OSError")
    assert vault.has_key("B0004")


def test_exactly_one_hash_chained_erasure_record_per_request():
    root, vault, reg, audit, app, truth = _setup()
    for i, b in enumerate(("B0004", "B0005", "B0006")):
        erase(app, _req(b, f"AUD-{i}"))
    recs = [r for r in audit.records() if r["type"] == "erasure"]
    assert [r["request_id"] for r in recs] == ["AUD-0", "AUD-1", "AUD-2"] and len(audit.records()) == 3
    assert audit.verify_chain()[0] and all(r["borrower_hash"] and "B000" not in json.dumps(r) for r in recs)


# ---- the name-collision found by the scale evidence (EVIDENCE.md): erasing "A. Yadav" deleted a record of "Arjun A. Yadav"
def test_a_name_inside_a_longer_name_is_not_a_full_name_match_but_is_still_reported():
    from layer2_vault.core.sealed_match import SealedMaterial
    m = SealedMaterial(["A. Yadav"])
    for text in ("Hello, am I speaking with Arjun A. Yadav?", "Arjun A. Yadav called about EMI", "A. Yadav Singh asked for a callback", "Call note: Priya A. Yadav reachable",
                 "DEBUG payload name=Arjun A. Yadav mobile=7477050393 status=retry", '{"full_name": "Arjun A. Yadav", "mobile": "x"}', "(Arjun A. Yadav) asked", 'A. Yadav "Singh" noted'):
        assert m.classify(text) == "name_embedded", text                                          # inside a longer capitalised name: probably someone else
    for text in ("Hello, am I speaking with A. Yadav?", "Call note: A. Yadav reachable on the phone", "DEBUG payload name=A. Yadav mobile=1", "Summarise the repayment history of A. Yadav", '{"full_name": "A. Yadav", "mobile": "x"}',
                 "Dear A. Yadav, thanks", "Mr A. Yadav asked", "Customer: A. Yadav", "a. yadav", "A. Yadav 9845012345 loan", "Thanks A. Yadav"):
        assert m.classify(text) == "name_full", text                                              # the name stands on its own (or next to a title / greeting / punctuation)
    assert m.classify("one A. Yadav here and later Arjun A. Yadav") == "name_full", "any clean occurrence is enough"
    assert SealedMaterial(["Arjun A. Yadav"]).classify("Hello, am I speaking with Arjun A. Yadav?") == "name_full"


def test_another_borrowers_longer_name_is_not_deleted_and_the_request_is_partial():
    root, vault, reg, audit, app, truth = _setup()
    b = next(k for k, t in truth["borrowers"].items() if len(t["values"]["name"].split()) >= 2 and not reg.borrowers_sharing_name(k))
    name = truth["borrowers"][b]["values"]["name"]
    st = adapters.JsonlStore(os.path.join(root, "logs.jsonl"), "logs", "logs")
    st.put({"id": "LX-OTHER", "conversation_id": None, "text": f"Hello, am I speaking with Zubin {name}?", "consent": {"purpose": "service"}})     # a DIFFERENT person whose name ends with this one
    st.put({"id": "LX-OWN", "conversation_id": None, "text": f"Summarise the repayment history of {name}", "consent": {"purpose": "service"}})           # this borrower's own name-only copy
    out = erase(app, _req(b))
    pres = present(root)["logs"]
    assert "LX-OTHER" in pres, "another person's record must survive"
    assert "LX-OWN" not in pres, "this borrower's own clean name-only copy is still erased"
    assert out["status"] == "PARTIAL" and any(u["id"] == "LX-OTHER" and u["reason"] == "raw_name_embedded" for u in out["record"]["unresolved"]), out["record"]["unresolved"]


if __name__ == "__main__":
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v(); print("PASS", k); n += 1
    print(f"\n{n} passed")
