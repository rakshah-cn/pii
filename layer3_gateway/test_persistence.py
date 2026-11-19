"""Gateway -> Layer 4 guarded write -> Layer 5 lineage -> Layer 6 erasure, with the REAL components (nothing mocked except the stub LLM and a regex detector).
Run:  python -m layer3_gateway.test_persistence"""
import json
import os
import sys
import tempfile
import traceback
import warnings

from layer3_gateway.test_gateway_spec import PhoneRegex, Stub, LeakyVault, T, mk, no_raw, sp
from layer3_gateway.persist import LogPersistence
from layer3_gateway.vault_client import VaultClient
from layer4_consent.guard import AUDIT_FAILURES
from layer5_lineage.registry import LineageRegistry
from layer6_erasure.adapters import JsonlStore
from layer7_audit.chain import AuditLog

warnings.filterwarnings("ignore")
KEY = b"k" * 32


def stack(store="logs", adapter=None, **kw):
    d = tempfile.mkdtemp()
    logs = adapter or JsonlStore(os.path.join(d, "logs.jsonl"), store, store)
    reg, audit = LineageRegistry(key=KEY), AuditLog(os.path.join(d, "audit_log.jsonl"), key=b"a" * 32)   # the project convention: Layer 6 skips exactly this file name when it sweeps a store root
    pers = LogPersistence(logs, reg, audit, registry_path=os.path.join(d, "registry.json"), store=store)
    kw.setdefault("detector", PhoneRegex()); kw.setdefault("llm", lambda m: "Noted " + m)
    return d, logs, reg, audit, pers, mk(persistence=pers, **kw)


def say(gw, cid, borrower, raw, purpose="loan_collection", **kw):
    return gw.handle(cid, "Customer", f"my number is {raw}", borrower_id=borrower, purpose=purpose, **kw)


def test_a_finished_message_is_written_through_layer_4_and_registered_in_layer_5():
    d, logs, reg, audit, pers, gw = stack()
    out = say(gw, "c1", "BRW-ZQX-77", "11111 22222", request_id="REQ-1"); assert out["status"] == "ok"
    assert len(logs.rows) == 1; rec = logs.rows[0]
    assert rec["id"] == "log-REQ-1" and rec["conversation_id"] == "c1" and "<PHONE_1>" in rec["text"] and rec["reply_masked"]
    blob = json.dumps(rec); assert "11111" not in blob and "22222" not in blob, "only tokenised text is stored"
    assert "borrower_id" not in rec and "borrower_id" not in rec["consent"], "the raw borrower id never reaches the store"
    assert rec["consent"]["purpose"] == "collections", "the canonical purpose (loan_collection -> collections) is what is recorded"
    ref = "log:log-REQ-1"; assert reg.owner[ref] == "BRW-ZQX-77" and ref in reg.refs_for_conversation("c1") and ref in reg.refs_for_token("<PHONE_1>") and reg.ref_metadata[ref]["purpose"] == "collections"
    ev = [r for r in audit.records() if r.get("kind") == "write_allowed"]; assert len(ev) == 1 and ev[0]["borrower_hash"] == audit.hash_borrower("BRW-ZQX-77")[:16] and "borrower_id" not in ev[0]
    assert "BRW-ZQX" not in json.dumps(audit.records()) and "BRW-ZQX" not in json.dumps(logs.rows), "neither the audit chain nor the store holds the raw borrower id"
    loaded = LineageRegistry.load(os.path.join(d, "registry.json"), key=KEY); assert loaded.owner[ref] == "BRW-ZQX-77", "the registry file Layer 6 loads is current"


def test_messages_that_do_not_finish_the_pipeline_are_never_persisted():
    d, logs, reg, audit, pers, gw = stack()
    assert say(gw, "c1", "b1", "11111 22222", purpose="marketing")["status"] == "blocked"            # consent denied
    _, logs2, reg2, _, _, leaky = stack(vault=LeakyVault())
    assert say(leaky, "c1", "b1", "11111 22222")["status"] == "blocked"                                # audit trapped a leak
    assert logs.rows == [] and logs2.rows == [] and not reg.owner and not reg2.owner and gw.persist_stats["recorded"] == 0


def test_a_retried_request_is_persisted_once():
    d, logs, reg, audit, pers, gw = stack()
    say(gw, "c1", "b1", "11111 22222", request_id="REQ-9"); say(gw, "c1", "b1", "11111 22222", request_id="REQ-9")
    assert len(logs.rows) == 1 and len([r for r in audit.records() if r.get("kind") == "write_allowed"]) == 1


def test_a_degraded_answer_is_persisted_without_a_reply():
    d, logs, reg, audit, pers, gw = stack(llm=lambda m: __import__("time").sleep(0.4), llm_timeout=0.05)
    assert say(gw, "c1", "b1", "11111 22222")["status"] == "degraded"
    assert len(logs.rows) == 1 and "<PHONE_1>" in logs.rows[0]["text"]


def test_layer_4_can_refuse_the_write_and_then_nothing_is_registered():
    d, logs, reg, audit, pers, gw = stack(store="eval_sets")                       # eval_sets accept only model_improvement with explicit consent
    out = say(gw, "c1", "b1", "11111 22222"); assert out["status"] == "ok", "the conversation itself is unaffected"
    assert logs.rows == [] and not reg.owner and pers.stats["rejected_or_failed"] == 1
    rej = [r for r in audit.records() if r.get("kind") == "write_rejected"]; assert len(rej) == 1 and "purpose_not_allowed" in rej[0]["reason"] and "11111" not in json.dumps(rej)


def test_a_failing_store_never_blocks_the_conversation_and_is_not_registered():
    class Broken:
        name = "logs"
        def put(self, r): raise OSError("disk full")
    d, logs, reg, audit, pers, gw = stack(adapter=Broken())
    out = say(gw, "c1", "b1", "11111 22222"); assert out["status"] == "ok" and "11111 22222" in out["reply"]
    assert not reg.owner and pers.stats["rejected_or_failed"] == 1 and [r for r in audit.records() if r.get("kind") == "write_failed"]


def test_without_persistence_the_gateway_stores_nothing():
    gw = mk(detector=PhoneRegex()); say(gw, "c1", "b1", "11111 22222"); assert gw.persistence is None and gw.persist_stats["recorded"] == 0


def test_the_whole_chain_gateway_then_layer_4_then_layer_5_then_the_real_layer_6_erases_one_borrower_and_only_that_borrower():
    from layer6_erasure.agent import build_agent, erase
    v = VaultClient(ephemeral=True)
    d, logs, reg, audit, pers, gw = stack(vault=v)
    for i, (cid, b, raw) in enumerate([("c1", "b1", "11111 22222"), ("c1", "b1", "11111 99999"), ("c2", "b1", "11111 33333"), ("c3", "b2", "44444 55555"), ("c3", "b2", "44444 66666")]):
        assert say(gw, cid, b, raw, request_id=f"R{i}")["status"] == "ok"
    pers.flush(); assert len(logs.rows) == 5 and len(v.entries_for_borrower("b1")) >= 3 and len(v.entries_for_borrower("b2")) >= 2
    app = build_agent(d, v, LineageRegistry.load(os.path.join(d, "registry.json"), key=KEY), audit, registry_path=os.path.join(d, "registry.json"))
    out = erase(app, {"request_id": "ER-1", "borrower_id": "b1"}); rec = out["record"]
    assert rec["status"] == "ERASED_VERIFIED", rec["status"]
    left = [json.loads(l) for l in open(os.path.join(d, "logs.jsonl")) if l.strip()]
    assert [r["conversation_id"] for r in left] == ["c3", "c3"], "b1's three log records are gone, b2's two are untouched"
    assert v.entries_for_borrower("b1") == [] and not v.has_key("b1") and v.entries_for_borrower("b2") and v.has_key("b2")
    assert audit.verify_chain()[0], "Layer 4's write events and Layer 6's erasure record share one intact hash chain"
    after = say(gw, "c1", "b1", "77777 88888"); assert after["status"] == "blocked" and "11111" not in json.dumps(gw.history) and "c1" not in gw.history, "and the gateway forgot b1 (step-5 flush)"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]; bad = 0
    for name, fn in tests:
        try: fn(); print("PASS", name)
        except Exception: bad += 1; print("FAIL", name); traceback.print_exc()
    print(f"\n{len(tests) - bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
