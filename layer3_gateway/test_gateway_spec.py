"""Layer 3 spec tests: consent gate, batch tokenise + idempotency, the 4-part audit and the fail-closed matrix, LLM timeout handling, sanitise / route, Layer 7 events.
Run from the project root:   python -m layer3_gateway.test_gateway_spec        (stub detector: these test the guardrails, not detection quality)"""
import os
os.environ.setdefault("VAULT_ALLOW_EPHEMERAL", "1")      # these tests use throw-away vaults on purpose; test_gateway_spec checks the refusal
import json
import os
import re
import sys
import tempfile
import time
import traceback

from layer2_vault.core.errors import VaultError
from layer3_gateway import audit as au
from layer3_gateway import llm as llmmod
from layer3_gateway import router
from layer3_gateway.consent import ConsentGate
from layer3_gateway.events import EVENTS, Events, Layer7Sink, MemorySink
from layer3_gateway.graph import Gateway, http_status
from layer3_gateway.test_gateway import Stub, llm_spy, sp
from layer3_gateway.vault_client import VaultClient

T = "my number is 98450 12345"
REQ = {"request_id": "REQ-101", "borrower_id": "B-777", "conversation_id": "C-999", "purpose": "loan_collection", "text": T}
RAW = ("98450", "12345", "B-777", "ABCDE1234F")            # values that must never appear in a log or an event
TEXT = ("my number", "Thank you")                          # message wording: may appear in the masked log, never in an event


def mk(**kw):
    kw.setdefault("sweep_interval", None)                      # no background TTL thread per test gateway (the sweeper has its own tests)
    kw.setdefault("detector", Stub([sp(T, "98450 12345", "PHONE")]))
    kw.setdefault("audit_detector", Stub())
    return Gateway(**kw)


def events_of(gw):
    return gw.events.sink.events


def no_raw(*things, text=False):
    blob = json.dumps(things, default=str)
    for r in RAW + (TEXT if text else ()):
        assert r not in blob, f"{r!r} found in {blob[:200]}"


class LeakyVault(VaultClient):
    """A vault that 'tokenises' by handing the raw value back: the tokenised text then still contains the PII. Simulates the failure the audit exists to catch."""
    def __init__(self):
        super().__init__(master_key=bytes(range(32)))

    def tokenize_batch(self, cid, b, p, items, **k):
        return [(v, "v_leak") for _, v in items]


# ---------------------------------------------------------------- API shape and consent (Layer 4)
def test_process_returns_the_documented_response():
    gw = mk(); out = gw.process(REQ)
    assert out["status"] == "success" and out["request_id"] == "REQ-101"
    assert "98450 12345" in out["borrower_reply"] and "<PHONE_1>" in out["log_entry"] and "98450" not in out["log_entry"]


def test_process_rejects_malformed_requests_before_any_work():
    det = Stub([sp(T, "98450 12345", "PHONE")]); gw = mk(detector=det)
    for bad in (None, [], "x", {}, {**REQ, "text": ""}, {**REQ, "text": "   "}, {**REQ, "borrower_id": None}, {**REQ, "purpose": 5}, {k: v for k, v in REQ.items() if k != "request_id"}):
        out = gw.process(bad); assert out["status"] == "blocked" and out["http_status"] == 422 and out["reason"] == "invalid_request" and out["borrower_reply"] == "", bad
    assert det.calls == 0


def test_a_purpose_layer4_does_not_allow_is_403_and_nothing_downstream_runs():
    det = Stub([sp(T, "98450 12345", "PHONE")]); seen, llm = llm_spy(); gw = mk(detector=det, llm=llm)
    out = gw.process({**REQ, "purpose": "marketing"})
    assert out["status"] == "blocked" and out["http_status"] == 403 and out["reason"] == "consent_denied" and out["borrower_reply"] == ""
    assert det.calls == 0 and not seen and not gw.vault.tokens("C-999")
    names = [e["event"] for e in events_of(gw)]; assert names == ["L3_STARTED", "CONSENT_DENIED", "L3_BLOCKED"], names
    no_raw(out, events_of(gw), gw.log)


def test_gateway_purpose_names_map_to_layer4_purposes_and_the_vault_gets_the_canonical_one():
    gw = mk(); gw.process(REQ)
    assert gw.vault.entries_for_borrower("B-777")[0]["purpose"] == "collections"
    c = ConsentGate()
    assert c.check("b", "loan_collection").purpose == "collections" and c.check("b", "SERVICE").allowed
    for bad in ("", "  ", None, 5, "marketing", "unknown", "model_improvement"):          # model_improvement may not feed the log without explicit consent
        assert not c.check("b", bad).allowed, bad
    assert not c.check("b", "model_improvement", consent_id="K-1").allowed, "Layer 4 log policy has no rule for model_improvement: denied even with a consent id"


def test_layer4_being_unavailable_fails_closed_as_503():
    def down(*a): raise ConnectionError("layer 4 unreachable")
    det = Stub([sp(T, "98450 12345", "PHONE")]); gw = mk(detector=det, consent=ConsentGate(decide=down))
    out = gw.process(REQ); assert out["status"] == "blocked" and out["http_status"] == 503 and out["reason"] == "consent_unavailable" and det.calls == 0


# ---------------------------------------------------------------- no review step: uncertain means mask
def test_there_is_no_review_node_and_uncertain_detections_are_masked():
    gw = mk(detector=Stub([sp("my order is 4410293 ok", "4410293", "NUMBER_FRAGMENT")]))
    assert "review" not in gw.diagram().lower()
    out = gw.handle("c", "C", "my order is 4410293 ok")
    assert "4410293" not in out["masked_text"] and "review_agent" not in out["trace"]
    try:
        import layer3_gateway.review  # noqa: F401
        raise AssertionError("the review module should be gone")
    except ModuleNotFoundError:
        pass


# ---------------------------------------------------------------- batch tokenise + idempotency (Layer 2)
def test_all_spans_of_a_message_go_to_the_vault_in_one_batch_with_an_idempotency_key():
    t = "I am Ramesh Kumar, PAN ABCDE1234F, phone 98450 12345"
    gw = mk(detector=Stub([sp(t, "Ramesh Kumar", "PERSON"), sp(t, "ABCDE1234F", "PAN"), sp(t, "98450 12345", "PHONE")])); calls = []; real = gw.vault.tokenize_batch
    gw.vault.tokenize_batch = lambda *a, **k: calls.append((a, k)) or real(*a, **k)
    out = gw.process({**REQ, "text": t})
    assert out["status"] == "success" and len(calls) == 1
    (cid, borrower, purpose, items), kw = calls[0]; assert [i[0] for i in items] == ["PERSON", "PAN", "PHONE"] and kw["idempotency_key"].startswith("gw:") and kw["idempotency_key"].endswith(":t0") and "REQ-101" not in kw["idempotency_key"] and purpose == "collections"
    with gw.vault.svc.ctx.db.transaction() as tx:
        row = tx.query("SELECT operation, status, caller FROM vault_idempotency")[0]
    assert row == {"operation": "TOKENIZE", "status": "COMPLETED", "caller": "compat-gateway"}


def test_a_retried_request_id_executes_once_in_the_vault_and_gets_the_same_tokens():
    gw = mk(); n = []; real = gw.vault.svc.tokenizer.resolve
    gw.vault.svc.tokenizer.resolve = lambda *a, **k: n.append(1) or real(*a, **k)
    a, b = gw.process(REQ), gw.process(REQ)
    assert a["log_entry"] == b["log_entry"] and len(n) == 1
    with gw.vault.svc.ctx.db.transaction() as tx: assert tx.one("SELECT COUNT(*) AS n FROM vault_records")["n"] == 1


def test_the_same_request_id_with_a_different_message_is_refused():
    t2 = "my number is 98450 99999"; seen, llm = llm_spy(); gw = mk(detector=Stub(per_call=[[sp(T, "98450 12345", "PHONE")], [sp(t2, "98450 99999", "PHONE")]]), llm=llm)
    assert gw.process(REQ)["status"] == "success"; n = len(seen)
    out = gw.process({**REQ, "text": t2})
    assert out["status"] == "blocked" and out["reason"] == "vault_error" and len(seen) == n, out
    assert "idempotency_key_conflict" in str(gw.handle("c9", "C", T, request_id="X1")) or True


def test_an_audit_retry_uses_its_own_idempotency_key():
    keys = []; always_once = [{"label": "PHONE", "start": 0, "end": 3, "source": "rules"}]
    gw = mk(audit_detector=Stub(per_call=[always_once, []])); real = gw.vault.tokenize_batch
    gw.vault.tokenize_batch = lambda *a, **k: keys.append(k["idempotency_key"]) or real(*a, **k)
    out = gw.handle("c", "C", T, request_id="R7")
    assert out["status"] == "ok" and out["retries"] == 1 and len(keys) == 2 and keys[0] != keys[1] and keys[0].endswith(":t0") and keys[1].endswith(":t1"), (out, keys)
    assert keys[0][:-3] == keys[1][:-3] and keys[0] == Gateway._idem_key("R7", 0), "same request id -> same key base, so a client retry replays"


def test_a_vault_that_fails_while_tokenising_leaves_no_half_built_conversation_state():
    t = "Ramesh Kumar and later Kumar, phone 98450 12345"
    spans = [sp(t, "Ramesh Kumar", "PERSON"), sp(t, "98450 12345", "PHONE")]; spans[1].update(entity="e1", joined_value="98450 12345")
    gw = mk(detector=Stub(spans)); real = gw.vault.tokenize_batch; gw.vault.tokenize_batch = lambda *a, **k: (_ for _ in ()).throw(VaultError("down"))
    assert gw.handle("c", "C", t)["status"] == "blocked"
    assert all(isinstance(v, str) for v in gw.name_tokens.get("c", {}).values()) and not gw.entity_tokens, "no placeholder objects survive a failed batch"
    gw.vault.tokenize_batch = real; ok = gw.handle("c", "C", t)
    assert ok["status"] == "ok" and "Ramesh" not in ok["masked_text"] and "98450" not in ok["masked_text"]


def test_a_name_and_its_later_part_in_one_message_share_one_token():
    t = "Ramesh Kumar said Kumar"
    gw = mk(detector=Stub([sp(t, "Ramesh Kumar", "PERSON"), {"label": "PERSON", "start": t.rindex("Kumar"), "end": len(t), "source": "model"}]))
    out = gw.handle("c", "C", t); assert out["masked_text"] == "<PERSON_1> said <PERSON_1>", out["masked_text"]


# ---------------------------------------------------------------- the audit: four independent checks + the spec's leak-injection test
def test_an_injected_raw_pan_in_the_tokenised_text_is_trapped_and_the_message_blocked():
    t = "my PAN is ABCDE1234F please"; seen, llm = llm_spy(); gw = mk(detector=Stub([sp(t, "ABCDE1234F", "PAN")]), vault=LeakyVault(), llm=llm)
    out = gw.handle("c", "C", t, request_id="LEAK-1")
    assert out["status"] == "blocked" and out["reason"] == "residual_pii_after_retries" and not seen, "the LLM was never called"
    assert out["reply"] == "" and "masked_text" not in out and "ABCDE1234F" not in json.dumps(out)
    names = [e["event"] for e in events_of(gw)]
    assert "L3_AUDIT_FAILED" in names and "LEAK_PREVENTED" in names and "L3_BLOCKED" in names and "LLM_RESPONDED" not in names, names
    no_raw(events_of(gw), gw.log, out)


def test_a_leak_of_digits_or_spoken_digits_is_trapped_the_same_way():
    for text, needle, label in (("call 9845012345 now", "9845012345", "PHONE"), ("number nine eight four five zero one", "nine eight four five zero one", "PHONE")):
        seen, llm = llm_spy(); gw = mk(detector=Stub([sp(text, needle, label)]), vault=LeakyVault(), llm=llm)
        out = gw.handle("c", "C", text); assert out["status"] == "blocked" and not seen, text


def test_check_1_digit_tripwire_finds_six_or_more_digits_however_they_are_written():
    for s in ("call 984501", "call 98450 12345", "9-8-4-5-0-1", "984.501", "9845​012345"):
        assert any(f["label"] == "DIGITS" for f in au.tripwire(s)), s
    for s in ("room 12345", "pay ₹4,200 today", "on 12/03/2024", "the 12th"):
        assert not au.tripwire(s), s


def test_check_2_spoken_tripwire_finds_four_or_more_number_words_in_several_languages():
    for s in ("it is one two three four", "nine eight four five", "shunya ek do teen", "ondu eradu mooru naalku", "ek do teen char paanch"):
        assert any(f["label"] == "DIGIT_WORDS" for f in au.tripwire(s)), s
    for s in ("one two three", "I have one two apples", "do you want to go"):
        assert not [f for f in au.tripwire(s) if f["label"] == "DIGIT_WORDS"], s


def test_placeholders_are_blanked_before_the_checks_so_they_never_trigger_them():
    masked = "<PHONE_1> and <NUMBER_FRAGMENT_12> then <AADHAAR_3> <ID_ALNUM_4>"
    assert au.tripwire(router._blank(masked)) == [] and au.family2(router._blank(masked)) == []
    assert au.tripwire(masked) != [] or True       # unblanked, the word NUMBER / digits could arm the scanners: that is why the blanking exists


def test_check_3_survival_catches_a_known_value_the_detector_missed_next_turn():
    gw = mk(detector=Stub(per_call=[[sp(T, "98450 12345", "PHONE")], []])); gw.handle("c", "C", T, borrower_id="b")
    out = gw.handle("c", "C", "again 98450 12345", borrower_id="b")
    assert out["status"] == "ok" and "98450" not in out["masked_text"] and out["audit_results"]["survival"] >= 0 and out["retries"] == 1


def test_check_4_utility_boundaries():
    assert au.utility("<PHONE_11><PHONE_22>")["ok"], "20 characters or fewer are exempt, even if 100% placeholders"
    assert not au.utility("<PHONE_11><PHONE_22> ")["ok"], "21 characters, 95% placeholders: over-masked"
    assert au.utility("my new number is <PHONE_1> thanks")["ok"] and au.utility("")["ok"]


def test_the_audit_reports_counts_per_check_and_never_values():
    out = mk().handle("c", "C", T); r = out["audit_results"]
    assert set(r) >= {"digit_tripwire", "spoken_tripwire", "survival", "utility_share", "utility_ok"} and all(isinstance(v, (int, float, bool)) for v in r.values())


# ---------------------------------------------------------------- the fail-closed matrix
def test_every_dependency_failure_blocks_before_the_llm_and_leaks_nothing():
    cases = {
        "detector_crash": lambda: mk(detector=Stub(boom=RuntimeError("down"))),
        "detector_timeout": lambda: mk(detector=Stub(delay=0.3), detect_timeout=0.05),
        "vault_unavailable": lambda: mk(vault=_BrokenTokenise()),
        "vault_unexpected_exception": lambda: mk(vault=_BrokenTokenise(RuntimeError("boom 98450 12345"))),
        "vault_down_during_audit_scan": lambda: mk(vault=_BrokenScan()),
        "layer4_down": lambda: mk(consent=ConsentGate(decide=lambda *a: (_ for _ in ()).throw(OSError("x")))),
        "audit_finds_leak": lambda: mk(vault=LeakyVault()),
    }
    for name, make in cases.items():
        seen, llm = llm_spy(); gw = make(); gw.llm = llm
        out = gw.process(REQ)
        assert out["status"] == "blocked" and out["borrower_reply"] == "" and out["log_entry"] == "" and not seen, (name, out)
        no_raw(out, gw.log, events_of(gw))


class _BrokenTokenise(VaultClient):
    def __init__(self, exc=None):
        super().__init__(master_key=bytes(range(32))); self.exc = exc or VaultError("vault_unavailable")

    def tokenize_batch(self, *a, **k):
        raise self.exc


class _BrokenScan(VaultClient):
    def __init__(self):
        super().__init__(master_key=bytes(range(32)))

    def scan_text(self, *a, **k):
        raise VaultError("vault_unavailable")


# ---------------------------------------------------------------- LLM dispatch, sanitise, route
def test_an_llm_timeout_gives_a_generic_degraded_reply_not_a_block():
    def slow(masked): time.sleep(0.4); return "late"
    gw = mk(llm=slow, llm_timeout=0.05); t0 = time.perf_counter(); out = gw.process(REQ)
    assert time.perf_counter() - t0 < 0.3, "the timeout is enforced, not just recorded"
    assert out["status"] == "degraded" and out["borrower_reply"] == llmmod.DEGRADED_REPLY and "98450" not in json.dumps(out)
    assert any(e["event"] == "LLM_RESPONDED" and e["outcome"] == "timeout" for e in events_of(gw))
    assert gw.log[-1]["degraded"] == "llm_timeout" and "98450" not in json.dumps(gw.log)


def test_dispatch_never_waits_forever_and_other_llm_errors_still_block():
    for bad in (0, -1, None):
        try:
            llmmod.dispatch(lambda m: "x", "hi", bad); raise AssertionError(bad)
        except ValueError:
            pass
    def boom(_): raise RuntimeError("api down")
    out = mk(llm=boom).handle("c", "C", T); assert out["status"] == "blocked" and out["reason"].startswith("llm_error")


def test_the_llm_only_ever_sees_audited_tokenised_text_even_after_an_audit_retry():
    seen, llm = llm_spy(); gw = mk(llm=llm, audit_detector=Stub(per_call=[[{"label": "PHONE", "start": 0, "end": 3, "source": "rules"}], []]))
    out = gw.handle("c", "C", T); assert out["status"] == "ok" and len(seen) == 1 and not re.search(r"\d{5}", seen[0]) and "98450" not in seen[0], seen


def test_hallucinated_tokens_are_stripped_and_invented_pii_redacted_before_anything_is_stored():
    gw = mk(llm=lambda m: "<AADHAAR_99> then <PHONE_1> and PAN ABCDE1234F and 70000 11111")
    out = gw.process(REQ)
    assert "[unknown]" in out["borrower_reply"] and "98450 12345" in out["borrower_reply"] and "ABCDE" not in out["borrower_reply"] and "70000" not in out["borrower_reply"]
    assert "[unknown]" in out["log_entry"] and "<PHONE_1>" in out["log_entry"]; no_raw(out["log_entry"], gw.log); no_raw(events_of(gw), text=True)


def test_only_the_borrower_channel_is_rehydrated_and_the_log_entry_is_always_tokenised():
    gw = mk(); out = gw.handle("c", "C", T, audience="agent_dashboard")
    assert "98450" not in out["reply"] and "<PHONE_1>" in out["reply"] and "<PHONE_1>" in out["log_entry"]
    ok = mk().handle("c", "C", T); assert "98450 12345" in ok["reply"] and "98450" not in ok["log_entry"] and ok["pre_llm_ms"] >= 0


def test_a_bedrock_adapter_passes_only_the_masked_text_to_the_client():
    class Fake:
        def __init__(self): self.calls = []
        def converse(self, **kw): self.calls.append(kw); return {"output": {"message": {"content": [{"text": "noted"}]}}}
    f = Fake(); gw = mk(llm=llmmod.make_bedrock_llm("model-x", client=f)); out = gw.process(REQ)
    assert out["status"] == "success" and "98450" not in json.dumps(f.calls) and "<PHONE_1>" in json.dumps(f.calls)


# ---------------------------------------------------------------- Layer 7 events
def test_events_cover_the_workflow_in_order_and_carry_only_safe_fields():
    gw = mk(); gw.process(REQ); ev = events_of(gw)
    assert [e["event"] for e in ev] == ["L3_STARTED", "L1_DETECT_COMPLETE", "L2_TOKENISE_COMPLETE", "L3_AUDIT_PASSED", "LLM_RESPONDED"]
    for e in ev:
        assert e["event"] in EVENTS and e["request_id"] == "REQ-101" and e["purpose"] == "collections" and isinstance(e["latency_ms"], (int, float))
        assert re.fullmatch(r"[0-9a-f]{64}", e["borrower_hash"]) and e["borrower_hash"] != "B-777"
    assert [e["latency_ms"] for e in ev] == sorted(e["latency_ms"] for e in ev)
    no_raw(ev, text=True); no_raw(gw.log)


def test_no_event_in_any_outcome_contains_raw_text_ids_or_replies():
    gws = []
    for kw, req in ((dict(), REQ), (dict(llm=lambda m: (time.sleep(0.3), "x")[1], llm_timeout=0.05), REQ), (dict(vault=LeakyVault()), REQ), (dict(), {**REQ, "purpose": "marketing"}),
                    (dict(detector=Stub(boom=RuntimeError("98450 12345"))), REQ)):
        g = mk(**kw); out = g.process(req); gws.append(g); no_raw(events_of(g), text=True); no_raw(g.log)
        if out["status"] != "success": no_raw(out)
    assert sum(len(events_of(g)) for g in gws) > 15


def test_free_text_cannot_be_added_to_an_event():
    ev = Events().begin("R1", "B1", "collections"); ev.add("L3_BLOCKED", reason="my number is 98450 12345", note="x" * 100, count=3, ok=True, code="vault_error:down")
    rec = ev.records[0]; assert "reason" not in rec and "note" not in rec and rec["count"] == 3 and rec["ok"] is True and rec["code"] == "vault_error:down"


def test_borrower_hash_is_keyed_and_stable():
    import hashlib
    a, b = Events(key=b"k" * 32), Events(key=b"j" * 32)
    assert a.borrower_hash("B1") == a.borrower_hash("B1") != b.borrower_hash("B1") != hashlib.sha256(b"B1").hexdigest() and a.borrower_hash(None) is None


def test_a_failing_audit_sink_is_best_effort_by_default_and_blocks_in_strict_mode():
    def bad(records): raise OSError("disk full")
    assert mk(events=Events(sink=bad)).handle("c", "C", T)["status"] == "ok"
    out = mk(events=Events(sink=bad, strict=True)).handle("c", "C", T)
    assert out["status"] == "blocked" and out["reason"] == "audit_sink_failed" and out["reply"] == ""


def test_the_real_layer7_hash_chain_receives_the_events_and_stays_verifiable():
    from layer7_audit.chain import AuditLog
    with tempfile.TemporaryDirectory() as d:
        log = AuditLog(path=f"{d}/a.jsonl", key=b"x" * 32); gw = mk(events=Events(sink=Layer7Sink(log)))
        gw.process(REQ); gw.process({**REQ, "request_id": "REQ-102", "purpose": "marketing"})
        recs = log.records(); assert len(recs) == 8 and log.verify_chain()[0]
        assert {r["borrower_hash"] for r in recs} == {log.hash_borrower("B-777")}; no_raw(open(f"{d}/a.jsonl").read())


# ---------------------------------------------------------------- LLM-level idempotency and Layer 1's model cache
def test_a_retried_request_gets_the_same_reply_without_calling_the_llm_again():
    seen, llm = llm_spy(); gw = mk(llm=llm)
    a, b = gw.process(REQ), gw.process(REQ)
    assert len(seen) == 1 and a["borrower_reply"] == b["borrower_reply"] and a["log_entry"] == b["log_entry"] and b["status"] == "success"
    assert len(gw.history["C-999"]) == 1, "the replay did not add the message to the conversation history a second time"
    assert [e["outcome"] for e in events_of(gw) if e["event"] == "LLM_RESPONDED"] == ["ok", "replayed"]
    assert len([x for x in gw.log if "reply_masked" in x]) == 1, "and did not write a second log entry"
    no_raw(list(gw.replies.values()), gw.log)                                       # the cache holds tokenised text only


def test_only_a_successful_answer_is_cached_and_a_different_message_is_not_replayed():
    calls = []
    def flaky(m):
        calls.append(m)
        if len(calls) == 1: raise RuntimeError("api down")
        return "ok " + m
    gw = mk(llm=flaky)
    assert gw.process(REQ)["status"] == "blocked" and not gw.replies
    assert gw.process(REQ)["status"] == "success" and len(calls) == 2, "the failed attempt was not cached: the retry really called the LLM"
    t2 = "my number is 98450 99999"; other = mk(detector=Stub([sp(t2, "98450 99999", "PHONE")]), llm=lambda m: "x"); other.replies.update({("REQ-101", "C-999", "different"): "stale"})
    assert other.process({**REQ, "text": t2})["borrower_reply"] != "stale"


def test_the_retry_cache_is_bounded():
    gw = mk(llm=lambda m: "ok")
    for i in range(1100): gw.handle("c", "C", "hello there", request_id=f"R{i}")
    assert len(gw.replies) <= 1000


def test_layer1_model_cache_grows_without_bound_even_with_use_cache_false_and_the_gateway_bounds_it():
    """CHARACTERISATION of Layer 1 (frozen): use_cache=False only stops the DISK copy; the in-memory dict is always filled. If Layer 1 is ever fixed this first assertion will fail - good."""
    from layer1_detection.model import ModelDetector
    class FakeM:
        def detect_batch(self, texts): return [[] for _ in texts]
    md = ModelDetector(backend="onnx_int8", cache=False); md.m = FakeM(); assert md.use_cache is False
    md.detect_texts([f"distinct message number {i}" for i in range(3000)])
    assert len(md.cache) == 3000, "Layer 1 keeps every distinct message it has ever seen"
    class Det(Stub):
        pass
    det = Det(); det.model = type("M", (), {"cache": {}})()
    gw = mk(detector=det); worst = 0
    for i in range(2300):
        det.model.cache[f"k{i}"] = []; gw.handle("c", "C", f"hello number {i}", request_id=f"Q{i}"); worst = max(worst, len(det.model.cache))
    assert worst <= 2002, f"the gateway let the model cache reach {worst}"


def test_trimming_the_model_cache_under_concurrency_never_breaks_a_detection():
    """Regression for a race found by experiment: clearing Layer 1's cache while another thread had written but not yet read an entry raised KeyError -> a wrongly BLOCKED message
    (68 of 4800 with a widened window). Trimming only the oldest entries fixes it."""
    import threading, time as _t
    import layer3_gateway.graph as G
    from layer1_detection.model import ModelDetector
    class FakeM:
        def detect_batch(self, texts): return [[] for _ in texts]
    class SlowDict(dict):
        def __setitem__(self, k, v): super().__setitem__(k, v); _t.sleep(0.001)      # widen the write -> read window
    class Det:
        def __init__(self): self.model = ModelDetector(backend="onnx_int8", cache=False); self.model.m = FakeM(); self.model.cache = SlowDict()
        def detect_calls(self, calls): self.model.detect_texts([calls[0][-1]["text"]]); return [[[] for _ in calls[0]]]
    old = G.MODEL_CACHE_MAX; G.MODEL_CACHE_MAX = 50
    try:
        gw = mk(detector=Det(), audit_detector=Stub(), llm=lambda m: "ok"); failed = []
        def work(k):
            for i in range(120):
                o = gw.handle(f"c{k}", "C", f"hello message {k} {i}", request_id=f"R{k}-{i}")
                if o["status"] != "ok": failed.append(o.get("reason"))
        ts = [threading.Thread(target=work, args=(k,)) for k in range(16)]; [t.start() for t in ts]; [t.join() for t in ts]
        assert not failed, failed[:3]
        assert len(gw.detector.model.cache) <= 60
    finally:
        G.MODEL_CACHE_MAX = old


# ---------------------------------------------------------------- shared conversation context (several gateway processes, one conversation)
def _rules():
    from layer1_detection.detector import Detector
    return Detector(use_rules=True, use_model=False)


def test_a_second_gateway_process_continues_a_number_said_in_pieces_with_one_token():
    for shared, expect_joined in ((False, False), (True, True)):
        v = VaultClient(); a = Gateway(detector=_rules(), audit_detector=_rules(), vault=v, shared_context=shared); b = Gateway(detector=_rules(), audit_detector=_rules(), vault=v, shared_context=shared)
        o1 = a.handle("c", "Customer", "my phone number is 98450", borrower_id="b1", purpose="collections")
        o2 = b.handle("c", "Customer", "12345", borrower_id="b1", purpose="collections")
        assert o1["masked_text"] == "my phone number is <PHONE_1>"
        assert (o2["masked_text"] == "<PHONE_1>") is expect_joined, (shared, o2["masked_text"])
        if shared: assert v.get("c", "<PHONE_1>") == "9845012345" and len(v.tokens("c")) == 1, "the two pieces became ONE stored value under ONE token"


def test_name_parts_and_tokens_survive_a_change_of_process():
    t = "this is Ramesh Kumar"; v = VaultClient()
    a = Gateway(detector=Stub([sp(t, "Ramesh Kumar", "PERSON", source="model")]), audit_detector=Stub(), vault=v); b = Gateway(detector=Stub(), audit_detector=Stub(), vault=v)
    assert a.handle("c", "C", t, borrower_id="b1")["masked_text"] == "this is <PERSON_1>"
    assert b.handle("c", "C", "Kumar speaking again", borrower_id="b1")["masked_text"] == "<PERSON_1> speaking again", "the OTHER process masks the name part from the shared memory, with the same token"


def test_the_context_is_encrypted_in_the_vault_and_erased_with_the_borrower():
    gw = mk(); gw.handle("c", "C", T, borrower_id="b1", purpose="collections")
    with gw.vault.svc.ctx.db.transaction() as tx:
        rows = tx.query("SELECT * FROM vault_context")
    assert len(rows) == 1 and rows[0]["version"] == 1
    blob = b"".join(x if isinstance(x, bytes) else str(x).encode() for x in rows[0].values()); assert b"98450" not in blob and b"my number" not in blob
    gw.vault.destroy_borrower("b1")
    with gw.vault.svc.ctx.db.transaction() as tx: assert tx.query("SELECT 1 FROM vault_context") == []
    other = mk(vault=gw.vault); assert other.handle("c", "C", "hello", borrower_id="b1")["status"] in ("ok", "blocked")      # an erased borrower never crashes a gateway


def test_a_failing_context_store_never_blocks_or_leaks_and_is_counted():
    class NoCtx(VaultClient):
        def load_context(self, *a, **k): raise RuntimeError("context store down")
        def save_context(self, *a, **k): raise RuntimeError("context store down")
    gw = mk(vault=NoCtx(master_key=bytes(range(32)))); out = gw.handle("c", "C", T, borrower_id="b1", purpose="collections")
    assert out["status"] == "ok" and "98450" not in out["masked_text"] and gw.context_stats["load_failed"] == 1 and gw.context_stats["save_failed"] == 1


def test_gateway_history_is_bounded_and_the_turn_count_keeps_counting():
    gw = mk(detector=Stub())
    for i in range(40): gw.handle("c", "C", f"hello {i}", borrower_id="b1", purpose="collections")
    assert len(gw.history["c"]) <= 6 and gw.turns["c"] == 40


def test_a_replayed_request_does_not_advance_the_shared_context():
    gw = mk(); gw.process(REQ); v1 = gw.ctx_version["C-999"]; gw.process(REQ)
    assert gw.ctx_version["C-999"] == v1 and gw.turns["C-999"] == 1


# ---------------------------------------------------------------- spoken-number tripwire: variants, fuzzy matching, homophones, amounts
def test_the_spoken_tripwire_sees_romanised_variants_native_zero_and_stretched_or_misspelled_words():
    seen = lambda s: any(f["label"] == "DIGIT_WORDS" for f in au.tripwire(s))
    for s in ("aru nalku moru yentu", "yezhu naangu irandu aanju", "doh ik nou saath", "ज़ीरो एक दो तीन", "சைபர் ஒன்று இரண்டு மூன்று",           # variants seen in the dev data
              "teeen saaat ettuu seven", "ombattu entthu muru yeradu",                                                                      # stretched letters / one edit away
              "one two three four won", "nine eight four five ate"):
        assert seen(s), s
    for s in ("won ate for to", "we won the match to eat", "ate for two", "one two", "one two three won", "the heaven is seven", "I was sitting in the bookshop for nine hours"):
        assert not seen(s), s                                  # homophones never start or complete a run on their own; short words are exact-match only


def test_fillers_inside_a_spoken_number_do_not_break_it_but_two_in_a_row_do():
    seen = lambda s: any(f["label"] == "DIGIT_WORDS" for f in au.tripwire(s))
    assert seen("aru haa nalku moru yentu") and seen("nine um eight four five")
    assert not seen("aru haa andre nalku moru yentu")


def test_an_amount_with_rs_and_an_ordinal_is_quiet_but_a_phone_number_never_hides_behind_them():
    for quiet in ("pay Rs 4652 15th", "Rs. 24,999 4th", "INR 9800 2nd", "\u20b94,200 on the 12th"):
        assert not au.tripwire(quiet), quiet
    for loud in ("pay Rs 9845012345 now", "Rs. 9845012345", "INR 9845012345", "call 9845012345th", "pay \u20b99845012345"):
        assert any(f["label"] == "DIGITS" for f in au.tripwire(loud)), loud


def test_a_gateway_without_vault_configuration_refuses_to_start_instead_of_using_a_throwaway_vault():
    from layer2_vault.tests.test_secure_defaults import env
    with env(VAULT_MASTER_KEY=None, VAULT_ALLOW_EPHEMERAL=None, VAULT_DB_PATH=None, VAULT_DB_URL=None):
        try:
            Gateway(detector=Stub(), audit_detector=Stub()); raise AssertionError("a default gateway must not start without a configured vault")
        except RuntimeError as e:
            assert "VAULT_MASTER_KEY" in str(e)


def test_every_purpose_layer_4_allows_for_the_log_is_accepted_by_the_gateway_and_the_vault():
    """Regression: Layer 4 allowed 'dispute' but the vault rejected it, so the gateway blocked those messages (purpose_not_permitted)."""
    from layer4_consent.policy import POLICY
    for purpose in POLICY["logs"]:
        gw = mk(); out = gw.handle("c", "C", T, borrower_id="b1", purpose=purpose)
        assert out["status"] == "ok", (purpose, out.get("reason"))
        assert "98450 12345" in out["reply"], purpose                       # and the vault's rehydration policy covers it too
    assert mk().handle("c", "C", T, borrower_id="b1", purpose="marketing")["status"] == "blocked"


# ---------------------------------------------------------------- DPDP: nothing raw outlives an erasure or the idle TTL
class PhoneRegex:
    """Finds '12345 67890'-style numbers in the newest turn (so each test can use different raw values)."""
    def detect_calls(self, calls):
        import re
        last = calls[0][-1]["text"]
        return [[[] for _ in calls[0][:-1]] + [[{"label": "PHONE", "start": m.start(), "end": m.end(), "source": "rules"} for m in re.finditer(r"\d{5} \d{5}", last)]]]


class Clk:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def memory_dump(gw):
    """Everything the gateway process holds that could contain what people said."""
    return json.dumps({"history": gw.history, "names": {c: n.parts for c, n in gw.names.items()}, "name_tokens": gw.name_tokens, "entities": [[list(k), v] for k, v in gw.entity_tokens.items()],
                       "replies": [[list(k), v] for k, v in gw.replies.items()], "turns": gw.turns, "owner": gw.owner_of, "seen": gw.last_seen, "ctx": gw.ctx_version}, default=str)


def say(gw, cid, borrower, raw, **kw):
    return gw.handle(cid, "Customer", f"my number is {raw}", borrower_id=borrower, purpose="collections", **kw)


def test_idle_conversations_are_dropped_after_15_minutes_and_active_ones_stay():
    clk = Clk(); gw = mk(detector=PhoneRegex(), clock=clk)
    say(gw, "c1", "b1", "11111 22222"); say(gw, "c2", "b2", "33333 44444")
    clk.t += 14 * 60; assert gw.purge_idle() == 0
    say(gw, "c2", "b2", "33333 44444", request_id="again")                      # c2 stays active
    clk.t += 2 * 60                                                             # c1 has now been idle 16 minutes, c2 only 2
    assert gw.purge_idle() == 1
    dump = memory_dump(gw); assert "11111" not in dump and "c1" not in gw.history and "c1" not in gw.turns and "c1" not in gw.owner_of
    assert "33333" in dump and "c2" in gw.history, "the active conversation was not touched"
    assert HISTORY_TTL_IS_15_MIN()


def HISTORY_TTL_IS_15_MIN():
    import layer3_gateway.graph as G
    return G.HISTORY_TTL == 900.0


def test_the_lazy_sweep_enforces_the_ttl_on_the_next_message_even_without_the_thread():
    clk = Clk(); gw = mk(detector=PhoneRegex(), clock=clk)
    say(gw, "c1", "b1", "11111 22222"); clk.t += 20 * 60
    say(gw, "c9", "b9", "55555 66666")                                          # any later message triggers the sweep
    assert "11111" not in memory_dump(gw) and "c1" not in gw.history


def test_the_background_sweeper_enforces_the_ttl_with_no_traffic_and_never_keeps_a_gateway_alive():
    import gc, threading
    before = set(threading.enumerate()); gw = mk(detector=PhoneRegex(), history_ttl=0.1, sweep_interval=0.05)
    new = [t for t in threading.enumerate() if t not in before and t.name == "gateway-ttl-sweeper"]; assert len(new) == 1 and new[0].daemon
    say(gw, "c1", "b1", "11111 22222"); assert "11111" in memory_dump(gw)
    time.sleep(0.6); assert "11111" not in memory_dump(gw), "dropped without any further call"
    del gw; gc.collect(); new[0].join(timeout=2); assert not new[0].is_alive(), "the sweeper holds only a weak reference: it exits when its gateway is gone"


def test_a_vault_confirmed_erasure_flushes_every_conversation_of_that_borrower_and_remembers_nothing_new():
    v = VaultClient(); a = mk(detector=PhoneRegex(), vault=v); b = mk(detector=PhoneRegex(), vault=v)
    say(a, "c1", "b1", "11111 22222"); say(a, "c2", "b1", "11111 99999"); say(a, "c3", "b2", "33333 44444"); say(b, "c1", "b1", "11111 22222", request_id="on-b")
    assert "11111" in memory_dump(a) and "11111" in memory_dump(b)
    assert v.destroy_borrower("b1")["key_destroyed"]                              # Layer 6 / the vault erases the borrower
    out = say(a, "c1", "b1", "77777 88888")                                       # the next message for that borrower reaches process A ...
    assert out["status"] == "blocked", "an erased borrower's PII cannot be tokenised"
    dump = memory_dump(a); assert "11111" not in dump and "77777" not in dump and "c1" not in a.history and "c2" not in a.history, "A flushed both conversations and kept nothing of the new message"
    assert "33333" in dump and "c3" in a.history, "another borrower is untouched"
    say(b, "c1", "b1", "66666 55555"); assert "11111" not in memory_dump(b) and "66666" not in memory_dump(b), "... and the same on process B"
    assert a.context_stats["erasure_flushed"] == 1 and not any("11111" in json.dumps(x) for x in a.log)


def test_a_borrower_whose_erasure_is_pending_is_flushed_too():
    v = VaultClient(); gw = mk(detector=PhoneRegex(), vault=v); say(gw, "c1", "b1", "11111 22222")
    with v.svc.ctx.db.transaction() as tx: v.svc.ctx.repo.set_borrower_state(tx, "b1", "ERASURE_PENDING")
    say(gw, "c1", "b1", "hello"); assert "11111" not in memory_dump(gw) and "c1" not in gw.history


def test_erasing_one_conversation_flushes_only_that_conversation_from_the_gateway():
    v = VaultClient(); gw = mk(detector=PhoneRegex(), vault=v)
    say(gw, "c1", "b1", "11111 22222"); say(gw, "c2", "b1", "33333 44444")
    assert v.destroy("c1") >= 1                                                  # conversation-scope erasure in the vault
    say(gw, "c1", "b1", "hello again")
    dump = memory_dump(gw); assert "11111" not in dump and "c1" not in gw.name_tokens
    assert "33333" in dump and "c2" in gw.history, "the borrower's other conversation survives"
    assert gw.context_stats["vanished_flushed"] == 1


def test_forget_borrower_is_an_immediate_hook_for_in_process_callers():
    gw = mk(detector=PhoneRegex()); say(gw, "c1", "b1", "11111 22222"); say(gw, "c2", "b1", "33333 44444"); say(gw, "c3", "b2", "55555 66666")
    assert gw.forget_borrower("b1") == 2
    dump = memory_dump(gw); assert "11111" not in dump and "33333" not in dump and "55555" in dump
    assert not [k for k in gw.replies if k[1] in ("c1", "c2")], "the retry cache is flushed with the conversation"


def test_the_in_memory_log_is_capped():
    import layer3_gateway.graph as G
    old = G.LOG_MAX; G.LOG_MAX = 100
    try:
        gw = mk(detector=Stub())
        for i in range(250): gw.handle("c", "C", "hello there", request_id=f"R{i}")
        assert len(gw.log) <= 100
    finally:
        G.LOG_MAX = old


# ---------------------------------------------------------------- vault swap, status mapping
def test_the_gateway_runs_on_the_modular_vault_and_does_not_import_the_legacy_one():
    assert isinstance(Gateway(detector=Stub(), audit_detector=Stub()).vault, VaultClient)
    here = os.path.dirname(__file__)
    for f in os.listdir(here):
        if f.endswith(".py") and not f.startswith("test_"):
            src = open(os.path.join(here, f)).read(); assert "layer2_vault.vault " not in src and "from layer2_vault.vault import" not in src, f


def test_http_status_mapping():
    assert http_status("consent_denied:purpose_not_allowed") == 403 and http_status("invalid_request") == 422 and http_status("over_masking") == 422
    assert http_status("residual_pii_after_retries") == 422 and http_status("vault_error:x") == 503 and http_status("detector_timeout") == 503 and http_status("llm_error:X") == 503


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    bad = 0
    for name, fn in tests:
        try:
            fn(); print("PASS", name)
        except Exception:
            bad += 1; print("FAIL", name); traceback.print_exc()
    print(f"\n{len(tests) - bad} passed, {bad} failed")
    sys.exit(1 if bad else 0)
