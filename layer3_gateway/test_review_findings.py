"""Regression tests for defects found by the INDEPENDENT review of the gateway (a fresh reviewer, read-only, reproduced each one). Every test names the finding and fails on the code as it was
when reviewed.   Run from the project root:   python -m layer3_gateway.test_review_findings"""
import os
os.environ.setdefault("VAULT_ALLOW_EPHEMERAL", "1")      # throw-away vaults on purpose (test_gateway_spec checks the refusal)
import json
import re
import sys
import threading
import time
import traceback

from layer2_vault.compat import _ERASURE
from layer2_vault.schemas.vault_schemas import EraseRequest
from layer3_gateway import audit as au
from layer3_gateway import llm as llmmod
from layer3_gateway.events import MemorySink
from layer3_gateway.graph import Gateway
from layer3_gateway.test_gateway import Stub, llm_spy, sp
from layer3_gateway.vault_client import VaultClient


def mk(**kw):
    kw.setdefault("sweep_interval", None)
    kw.setdefault("detector", Stub())
    kw.setdefault("audit_detector", Stub())
    return Gateway(**kw)


class ByText:
    """Detector stand-in that answers by the text of the newest turn, so the result does not depend on which thread asks first."""
    def __init__(self, table):
        self.table, self.calls = table, 0

    def detect_calls(self, calls):
        self.calls += 1
        return [[[] for _ in calls[0][:-1]] + [list(self.table.get(calls[0][-1]["text"], []))]]


def req(text, rid="R-1", borrower="A", conv="c1", purpose="collections", **kw):
    return {"request_id": rid, "borrower_id": borrower, "conversation_id": conv, "purpose": purpose, "text": text, **kw}


def events_of(gw):
    return gw.events.sink.events


# ============================================================ HIGH 1: a number split by separators the tripwire did not know
SEPARATED = ["0 - 1 - 2 - 3 - 4 - 5", "98450/ /12345", "98450­12345", "98450\n12345", "9 — 8 — 4 — 5 — 0 — 1 — 2 — 3 — 4 — 5", "98450 * 12345", "98450‎12345",
             "98450<br>12345", "98450 • 12345", "9845 | 012345", "98450 - 12345", "98450 : 12345", "⁹⁸⁴⁵⁰¹²³⁴⁵", "①②③④⑤⑥⑦⑧", "9 8 4 5 0 1 2 3 4 5", "98450 – 12345", "98450 ~ 12345"]


def test_the_digit_tripwire_sees_a_number_through_any_separator():
    missed = [v for v in SEPARATED if not any(f["label"] == "DIGITS" for f in au.tripwire(v))]
    assert not missed, f"not seen: {missed!r}"


def test_the_digit_tripwire_still_leaves_amounts_dates_years_and_short_numbers_alone():
    for ok in ("I paid Rs 12,345 on 15/08/2024", "see you on 2nd and 15th", "order of 5 items for Rs 2500", "call between 10 - 12 today", "1,50,000 rupees", "FY 2024 - 2025 results",
               "3 loans, 2 cards, 4 accounts", "my score is 7 out of 10", "pay 5,000 by 15.08.2024"):
        assert not any(f["label"] == "DIGITS" for f in au.tripwire(ok)), ok


def test_a_separated_number_never_reaches_the_llm_even_when_the_detector_finds_nothing():
    for v in SEPARATED:
        seen, llm = llm_spy(); gw = mk(llm=llm)
        out = gw.handle("c1", "Customer", f"call me on {v} thanks", borrower_id="A", purpose="collections")
        if seen:
            assert len(re.sub(r"\D", "", seen[-1])) < 4, f"{v!r} reached the LLM as {seen[-1]!r}"
        else:
            assert out["status"] == "blocked"


# ============================================================ HIGH 2: a spoken number split by punctuation the tripwire did not know
WORDS = ["nine", "eight", "four", "five", "zero", "one", "two", "three", "four", "five"]


def test_the_spoken_number_tripwire_is_not_beaten_by_punctuation_or_invisible_characters():
    for sep in ("—", "–", "•", "·", "#", "=", "&", "<br>", "⁣", "*", "|", "~", "…", "­", "_", "+"):
        text = "my number is " + sep.join(WORDS)
        assert any(f["label"] == "DIGIT_WORDS" for f in au.tripwire(text)), repr(sep)
    assert any(f["label"] == "DIGIT_WORDS" for f in au.tripwire("my number is " + " ".join("​".join(w[:2] + "‌" + w[2:]) for w in WORDS))), "invisible characters inside the words"
    assert any(f["label"] == "DIGIT_WORDS" for f in au.tripwire("नौ—आठ—चार—पाँच—शून्य—एक")), "Hindi words with vowel signs, joined by dashes"
    assert any(f["label"] == "DIGIT_WORDS" for f in au.tripwire("नौ आठ चार पाँच शून्य एक")), "plain spaces keep working"


def test_the_spoken_number_tripwire_does_not_fire_on_ordinary_sentences():
    for ok in ("we spoke one time, two days ago, and three weeks before that", "I have two loans and one card", "one, two - I will pay on the five of next month"):
        assert not any(f["label"] == "DIGIT_WORDS" for f in au.tripwire(ok)), ok


def test_a_punctuated_spoken_number_never_reaches_the_llm_when_the_detector_finds_nothing():
    for sep in ("—", "•", "#", "=", "&", "<br>"):
        seen, llm = llm_spy(); gw = mk(llm=llm)
        out = gw.handle("c1", "Customer", "my number is " + sep.join(WORDS), borrower_id="A", purpose="collections")
        assert not seen or "nine" not in seen[-1].lower(), f"{sep!r}: {seen[-1] if seen else ''}"
        assert seen or out["status"] == "blocked"


# ============================================================ HIGH 3: borrower B read borrower A's value through a shared conversation id
def test_a_borrower_cannot_rehydrate_another_borrowers_token_on_a_shared_conversation_id():
    t = "my phone number is 98450 12345"
    gw = mk(detector=ByText({t: [sp(t, "98450 12345", "PHONE")]}), llm=lambda m: "you said " + m)
    assert gw.process(req(t, "R-1", "A", "shared"))["status"] == "success"
    own = gw.process(req("what is <PHONE_1> ?", "R-2", "A", "shared"))
    assert "98450 12345" in own["borrower_reply"], "the owner still gets their own value back"
    other = gw.process(req("what is <PHONE_1> ?", "R-3", "B", "shared"))
    assert other["status"] == "success" and "98450" not in other["borrower_reply"], other["borrower_reply"]


def test_a_token_made_for_one_purpose_is_not_rehydrated_for_another_purpose():
    t = "my phone number is 98450 12345"
    gw = mk(detector=ByText({t: [sp(t, "98450 12345", "PHONE")]}), llm=lambda m: "you said " + m)
    assert gw.process(req(t, "R-1", "A", "c1", "collections"))["status"] == "success"
    other = gw.process(req("what is <PHONE_1> ?", "R-2", "A", "c1", "service"))
    assert other["status"] == "success" and "98450" not in other["borrower_reply"], other["borrower_reply"]
    assert "98450 12345" in gw.process(req("what is <PHONE_1> ?", "R-3", "A", "c1", "collections"))["borrower_reply"]


# ============================================================ HIGH 4: one huge message froze the whole process (and 72 KB broke the shared context)
def test_an_oversized_message_is_refused_before_any_work():
    det = Stub(); gw = mk(detector=det)
    t0 = time.perf_counter(); out = gw.process(req("a-" * 50000)); dt = time.perf_counter() - t0
    assert out["status"] == "blocked" and out["http_status"] == 413 and out["reason"] == "message_too_long" and out["borrower_reply"] == "", out
    assert det.calls == 0 and dt < 0.5, (det.calls, dt)
    ok = gw.process(req("hello " * 600, "R-2"))                                   # 3,600 characters: well inside the limit
    assert ok["status"] == "success", ok


def test_the_independent_audit_patterns_take_linear_time_on_hostile_text():
    for hostile in ("a-" * 10000, "1 " * 10000, "x@" + "a-" * 10000, "a." * 10000, "1" * 20000, "a1-" * 7000, " ".join(["nine"] * 4000), "1st " * 5000 + "nine " * 5000):
        t0 = time.perf_counter(); au.tripwire(hostile); au.family2(hostile); dt = time.perf_counter() - t0
        assert dt < 0.25, f"{hostile[:12]!r}.. {dt:.2f}s"


def test_a_long_context_still_fits_the_vault_size_limit_so_other_processes_keep_the_shared_context():
    long_turn = "बहुत" * 997                                                      # 3,988 Devanagari characters = 3 bytes each: six of them are ~72 KB, over the vault's 64 KB limit
    gw = mk(); vault_saved = []
    real = gw.vault.save_context
    gw.vault.save_context = lambda cid, b, state: (vault_saved.append(json.dumps(state, ensure_ascii=False)), real(cid, b, state))[1]
    for i in range(8):
        gw.handle("c1", "Customer", long_turn, borrower_id="A", purpose="collections", request_id=f"R-{i}")
    assert gw.context_stats["save_failed"] == 0 and gw.context_stats["saved"] == 8, dict(gw.context_stats)
    assert all(len(s.encode("utf-8")) <= 65536 for s in vault_saved) and gw.context_stats["trimmed"] > 0


# ============================================================ MEDIUM 5: an erased borrower's name parts stayed in gateway memory
def _memory(gw):
    return json.dumps([gw.history, {c: dict(n.parts) for c, n in gw.names.items()}, gw.name_tokens, [list(k) for k in gw.entity_tokens], [list(k) for k in gw.replies]], default=str).lower()


def test_after_an_erasure_the_next_message_leaves_no_name_parts_in_memory():
    t1, t2 = "Ramesh Kumar here", "Suresh Patil here"
    gw = mk(detector=ByText({t1: [sp(t1, "Ramesh Kumar", "PERSON", "model")], t2: [sp(t2, "Suresh Patil", "PERSON", "model")]}))
    assert gw.handle("c1", "Customer", t1, borrower_id="A", purpose="collections")["status"] == "ok" and "ramesh" in _memory(gw)
    assert gw.vault.svc.deletion.erase(EraseRequest(job_id="J-1", borrower_id="A"), _ERASURE).status == "COMPLETED"
    out = gw.handle("c1", "Customer", t2, borrower_id="A", purpose="collections")
    assert out["status"] == "blocked"
    assert "ramesh" not in _memory(gw) and "suresh" not in _memory(gw), _memory(gw)


def test_a_vault_that_refuses_a_borrower_makes_the_gateway_forget_that_borrower_even_without_shared_context():
    t1, t2 = "Ramesh Kumar here", "Suresh Patil here"
    gw = mk(detector=ByText({t1: [sp(t1, "Ramesh Kumar", "PERSON", "model")], t2: [sp(t2, "Suresh Patil", "PERSON", "model")]}), shared_context=False)
    gw.handle("c1", "Customer", t1, borrower_id="A", purpose="collections")
    gw.vault.svc.deletion.erase(EraseRequest(job_id="J-1", borrower_id="A"), _ERASURE)
    assert gw.handle("c1", "Customer", t2, borrower_id="A", purpose="collections")["status"] == "blocked"
    assert "ramesh" not in _memory(gw) and "suresh" not in _memory(gw), _memory(gw)


def test_memory_made_by_a_blocked_message_is_still_covered_by_the_idle_ttl():
    now = [1000.0]; t1 = "Ramesh Kumar here"
    gw = mk(detector=ByText({t1: [sp(t1, "Ramesh Kumar", "PERSON", "model")]}), clock=lambda: now[0], history_ttl=900, audit_detector=Stub(boom=RuntimeError("audit down")))
    assert gw.handle("c9", "Customer", t1, borrower_id="A", purpose="collections")["status"] == "blocked"       # blocked AFTER detection already stored the name
    assert "ramesh" in _memory(gw)
    now[0] += 901; gw.purge_idle()
    assert "ramesh" not in _memory(gw), "no TTL clock was ever started for this conversation"


# ============================================================ MEDIUM 6: exceptions escaped instead of blocking
class Boom(VaultClient):
    def __init__(self, what):
        super().__init__(master_key=bytes(range(32)))
        self.what = what

    def scan_text(self, *a, **k):
        if self.what == "scan":
            raise RuntimeError("scan exploded")
        return super().scan_text(*a, **k)

    def tokens(self, *a, **k):
        if self.what == "tokens":
            raise RuntimeError("tokens exploded")
        return super().tokens(*a, **k)

    def rehydrate_many(self, *a, **k):
        if self.what == "rehydrate":
            raise RuntimeError("rehydrate exploded")
        return super().rehydrate_many(*a, **k)


def _blocked_cleanly(gw, out, seen=None):
    assert out["status"] == "blocked" and out["reply"] == "", out
    assert "L3_BLOCKED" in [e["event"] for e in events_of(gw)], [e["event"] for e in events_of(gw)]


def test_an_unexpected_exception_in_any_stage_blocks_instead_of_escaping():
    t = "my number is 98450 12345"
    for name, kw in (("audit detector", dict(audit_detector=Stub(boom=RuntimeError("x")))), ("vault scan", dict(vault=Boom("scan"))), ("vault tokens (sanitise)", dict(vault=Boom("tokens"))),
                     ("vault rehydrate", dict(vault=Boom("rehydrate")))):
        seen, llm = llm_spy(); gw = mk(detector=Stub([sp(t, "98450 12345", "PHONE")]), llm=llm, **kw)
        out = gw.handle("c1", "Customer", t, borrower_id="A", purpose="collections")        # must not raise
        _blocked_cleanly(gw, out)
        assert all("98450" not in s for s in seen), (name, seen)


def test_text_that_cannot_be_encoded_is_refused_not_a_crash_after_the_llm_call():
    seen, llm = llm_spy(); gw = mk(llm=llm)
    out = gw.process(req("hello \ud800 there"))
    assert out["status"] == "blocked" and out["http_status"] == 422 and not seen, out


# ============================================================ LOW 7/8: two requests of one conversation shared 'pending' references
class SlowVault(VaultClient):
    def __init__(self, delay):
        super().__init__(master_key=bytes(range(32)))
        self.delay = delay

    def tokenize_batch(self, *a, **k):
        time.sleep(self.delay)
        return super().tokenize_batch(*a, **k)


def test_two_concurrent_messages_of_one_conversation_never_swap_each_others_tokens():
    ta, tb = "Ramesh Kumar speaking", "Kumar called from 98450 12345"
    gw = mk(detector=ByText({ta: [sp(ta, "Ramesh Kumar", "PERSON")], tb: [sp(tb, "Kumar", "PERSON"), sp(tb, "98450 12345", "PHONE")]}), vault=SlowVault(0.4))
    res = {}
    a = threading.Thread(target=lambda: res.__setitem__("a", gw.handle("c1", "Customer", ta, borrower_id="A", purpose="collections", internal=True)))
    b = threading.Thread(target=lambda: res.__setitem__("b", gw.handle("c1", "Customer", tb, borrower_id="A", purpose="collections", internal=True)))
    a.start(); time.sleep(0.15); b.start(); a.join(); b.join()
    assert res["a"]["status"] == res["b"]["status"] == "ok"
    assert res["b"]["masked_text"].count("<PHONE_1>") == 1 and res["b"]["masked_text"].startswith("<PERSON_1> called"), res["b"]["masked_text"]
    assert "<PHONE_1>" not in res["a"]["masked_text"], res["a"]["masked_text"]


# ============================================================ LOW 9: free text in an identifier reached the events, the store and the audit chain
def test_free_text_in_an_identifier_is_refused_and_never_reaches_an_event_or_a_log():
    for field, bad in (("request_id", "call-98450 12345-now"), ("consent_id", "CID Ramesh Kumar 98450 12345"), ("conversation_id", "conv 98450 12345"), ("borrower_id", "Ramesh Kumar 98450 12345"),
                       ("request_id", "r\n98450"), ("conversation_id", "x" * 200)):
        gw = mk(); out = gw.process(req("hello there", **{field: bad}))
        assert out["status"] == "blocked" and out["http_status"] == 422 and out["reason"] == "invalid_request", (field, out)
        blob = json.dumps([out, events_of(gw), gw.log], default=str)
        assert "98450" not in blob and "Ramesh" not in blob, (field, blob[:300])
    assert mk().process(req("hello there", consent_id="CID-2024-001"))["status"] == "success"


# ============================================================ LOW 10: token-shaped text typed by the user was exempt from the audit
def test_token_shaped_text_typed_by_the_user_cannot_hide_a_number_from_the_audit():
    for sneaky in ("<NINE_EIGHT_FOUR_FIVE_ZERO_ONE_TWO_THREE_1>", "<X_9845012345>", "<PHONE_9845012345>"):
        seen, llm = llm_spy(); gw = mk(llm=llm)
        out = gw.handle("c1", "Customer", f"my number is {sneaky}", borrower_id="A", purpose="collections")
        blob = " ".join(seen)
        assert "NINE_EIGHT" not in blob and "9845012345" not in blob, (sneaky, seen)
        assert seen or out["status"] == "blocked"


# ============================================================ LOW 11: another borrower's request id could block a message
def test_a_request_id_used_by_another_borrower_does_not_block_this_borrower():
    t = "my number is 98450 12345"
    gw = mk(detector=ByText({t: [sp(t, "98450 12345", "PHONE")]}))
    first = gw.process(req(t, "R-1", "A", "c1")); second = gw.process(req(t, "R-1", "B", "c2"))
    assert first["status"] == "success" and second["status"] == "success", (first, second)


# ============================================================ LOW 13: structures that grew without a bound
def test_the_in_memory_event_sink_is_bounded():
    s = MemorySink(cap=50)
    for i in range(500):
        s([{"event": "L3_STARTED", "i": i}])
    assert len(s.events) <= 50 and s.events[-1]["i"] == 499


def test_stuck_llm_calls_are_capped_instead_of_piling_up_threads():
    gate, calls = threading.Event(), []
    def stuck(text):
        calls.append(1); gate.wait(5); return "late"
    for _ in range(2):
        try: llmmod.dispatch(stuck, "x", timeout=0.05, max_abandoned=2)
        except llmmod.LLMTimeout: pass
    n = len(calls)
    try: llmmod.dispatch(stuck, "x", timeout=0.05, max_abandoned=2); raise AssertionError("a third call must be refused while two are still stuck")
    except llmmod.LLMTimeout: pass
    assert len(calls) == n, "no new worker thread was started"
    gate.set()


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
