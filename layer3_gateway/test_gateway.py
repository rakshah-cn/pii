"""Graph-logic tests with a STUB detector (fast, no model). They check the guardrails, not detection quality.
Run from the project root:   python -m layer3_gateway.test_gateway
"""
import time

import json

from layer2_vault.core.errors import VaultError
from layer3_gateway.graph import Gateway
from layer3_gateway import audit as au
from layer3_gateway.vault_client import VaultClient


class Stub:
    """Detector stand-in: returns preset spans for the LAST turn it is asked about."""
    def __init__(self, spans=None, boom=None, delay=0.0, per_call=None):
        self.spans, self.boom, self.delay, self.per_call, self.calls = spans or [], boom, delay, per_call, 0

    def detect_calls(self, calls):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.boom:
            raise self.boom
        spans = self.per_call[min(self.calls - 1, len(self.per_call) - 1)] if self.per_call is not None else self.spans
        return [[[] for _ in calls[0][:-1]] + [spans]]


def sp(text, needle, label, source="rules"):
    i = text.index(needle)
    return {"label": label, "start": i, "end": i + len(needle), "source": source}


def llm_spy():
    seen = []
    return seen, (lambda masked: seen.append(masked) or "ok " + masked)


def test_error_fails_closed():
    seen, llm = llm_spy()
    gw = Gateway(detector=Stub(boom=RuntimeError("model down")), audit_detector=Stub(), llm=llm)
    out = gw.handle("c1", "Customer", "my number is 98450 12345")
    assert out["status"] == "blocked" and out["reason"].startswith("detector_error")
    assert not seen, "LLM must not be called when detection failed"
    assert all("98450" not in str(e) for e in gw.log)


def test_timeout_fails_closed():
    seen, llm = llm_spy()
    gw = Gateway(detector=Stub(delay=0.3), audit_detector=Stub(), llm=llm, detect_timeout=0.05)
    out = gw.handle("c1", "Customer", "my number is 98450 12345")
    assert out["status"] == "blocked" and out["reason"] == "detector_timeout" and not seen


def test_empty_input_blocked():
    gw = Gateway(detector=Stub(), audit_detector=Stub())
    assert gw.handle("c1", "Customer", "   ")["status"] == "blocked"


def test_mask_log_and_rehydrate():
    t = "my number is 98450 12345 thanks"
    seen, llm = llm_spy()
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub(), llm=llm)
    out = gw.handle("c1", "Customer", t)
    assert out["status"] == "ok" and out["masked_text"] == "my number is <PHONE_1> thanks"
    assert "98450" not in seen[0], "LLM got raw data"
    assert all("98450" not in e.get("text", "") for e in gw.log), "log got raw data"
    assert "98450 12345" in out["reply"], "borrower reply should be rehydrated"


def test_same_value_same_token():
    t = "call 98450 12345 or 98450 12345"
    a, b = t.index("98450 12345"), t.rindex("98450 12345")
    spans = [{"label": "PHONE", "start": a, "end": a + 11, "source": "rules"}, {"label": "PHONE", "start": b, "end": b + 11, "source": "rules"}]
    out = Gateway(detector=Stub(spans), audit_detector=Stub()).handle("c1", "C", t)
    assert out["masked_text"] == "call <PHONE_1> or <PHONE_1>"


def test_audit_retry_then_clean():
    t = "number 98450 12345 and PAN ABCDE1234F"
    audit = Stub(per_call=[[{"label": "PAN", "start": t.index("ABCDE") - 0, "end": 0, "source": "rules"}], []])
    # first audit pass reports a residual at the PAN position of the MASKED text; second pass finds nothing
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=audit)
    masked_first = "number <PHONE_1> and PAN ABCDE1234F"
    i = masked_first.index("ABCDE1234F")
    audit.per_call[0] = [{"label": "PAN", "start": i, "end": i + 10, "source": "rules"}]
    out = gw.handle("c", "C", t)
    assert out["status"] == "ok" and out["retries"] == 1 and out["masked_text"] == "number <PHONE_1> and PAN <PAN_1>", out
    assert out["trace"].count("tokenise") == 2


def test_audit_fails_after_retries():
    t = "number 98450 12345"
    always = [{"label": "PHONE", "start": 0, "end": 3, "source": "rules"}]
    seen, llm = llm_spy()
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub(per_call=[always]), llm=llm)
    out = gw.handle("c", "C", t)
    assert out["status"] == "blocked" and out["reason"] == "residual_pii_after_retries" and not seen


def test_audit_catches_surviving_raw_value():
    t = "my number is 98450 12345 and again 98450 12345"
    first = t.index("98450 12345")
    gw = Gateway(detector=Stub([{"label": "PHONE", "start": first, "end": first + 11, "source": "rules"}]), audit_detector=Stub())
    out = gw.handle("c", "C", t)                 # the second copy was missed by the detector
    assert "98450" not in out["masked_text"], out["masked_text"]


def test_erasure_hook_destroys_vault():
    t = "my number is 98450 12345"
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub())
    gw.handle("c9", "C", t)
    assert gw.vault.tokens("c9") and gw.vault.destroy("c9") == 1 and gw.vault.get("c9", "<PHONE_1>") is None


def test_real_rules_amounts_and_dates_stay_and_no_retry_loop():
    from layer1_detection.detector import Detector
    rules_only = Detector(use_rules=True, use_model=False)
    gw = Gateway(detector=rules_only, audit_detector=Detector(use_rules=True, use_model=False))
    out = gw.handle("c", "Customer", "My order 4410293 was delivered on 12 March for \u20b94,200.")
    assert "4410293" not in out["masked_text"] and "12 March for \u20b94,200." in out["masked_text"], out["masked_text"]     # the unsure order number is masked; the date and amount stay
    assert out["trace"].count("tokenise") == 1, out["trace"]


def test_real_rules_phone_in_pieces_across_turns_is_masked():
    from layer1_detection.detector import Detector
    gw = Gateway(detector=Detector(use_rules=True, use_model=False), audit_detector=Detector(use_rules=True, use_model=False))
    a = gw.handle("c", "Customer", "my number is nine eight four five")
    b = gw.handle("c", "Customer", "zero one two three four five")
    assert "nine" not in a["masked_text"] and "zero" not in b["masked_text"], (a["masked_text"], b["masked_text"])


# ======================================================================= new in the layer 2+3 upgrade
class BrokenVault(VaultClient):
    def __init__(self, fail="put"):
        super().__init__(master_key=bytes(range(32)))
        self.fail = fail

    def tokenize_batch(self, *a, **k):
        if self.fail == "put":
            raise VaultError("vault unavailable")
        return super().tokenize_batch(*a, **k)

    def rehydrate_many(self, *a, **k):
        if self.fail == "get":
            raise VaultError("key destroyed")
        return super().rehydrate_many(*a, **k)


def test_vault_failure_blocks_and_llm_not_called():
    t = "my number is 98450 12345"
    seen, llm = llm_spy()
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub(), vault=BrokenVault("put"), llm=llm)
    out = gw.handle("c1", "Customer", t)
    assert out["status"] == "blocked" and out["reason"].startswith("vault_error") and not seen
    assert "98450" not in json.dumps(gw.log)


def test_rehydration_failure_fails_closed():
    t = "my number is 98450 12345"
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub(), vault=BrokenVault("get"))
    out = gw.handle("c1", "Customer", t)
    assert out["status"] == "blocked" and out["reason"].startswith("rehydration_failed") and out["reply"] == ""


def test_llm_failure_blocks():
    t = "my number is 98450 12345"
    def boom(_): raise RuntimeError("llm down")
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub(), llm=boom)
    out = gw.handle("c1", "Customer", t)
    assert out["status"] == "blocked" and out["reason"].startswith("llm_error")


def test_joined_entity_gets_one_token_across_turns():
    t1, t2 = "my number is nine eight four five", "zero one two three four five"
    s1 = dict(sp(t1, "nine eight four five", "PHONE"), entity="e0", part=1)
    s2 = dict(sp(t2, t2, "PHONE"), entity="e0", part=2, joined_value="98450 12345")
    gw = Gateway(detector=Stub(per_call=[[s1], [s2]]), audit_detector=Stub())
    a, b = gw.handle("c", "Customer", t1), gw.handle("c", "Customer", t2)
    assert a["masked_text"] == "my number is <PHONE_1>" and b["masked_text"] == "<PHONE_1>", (a["masked_text"], b["masked_text"])
    assert gw.vault.tokens("c") == ["<PHONE_1>"] and gw.vault.get("c", "<PHONE_1>") == "98450 12345"
    assert b["reply"].count("98450 12345") == 1                       # the borrower's reply shows the full joined value


def test_two_pieces_in_one_turn_share_a_token_and_distinct_entities_do_not():
    t = "nine eight four five and then zero one two three four five"
    a = dict(sp(t, "nine eight four five", "PHONE"), entity="e0")
    b = dict(sp(t, "zero one two three four five", "PHONE"), entity="e0")
    out = Gateway(detector=Stub([a, b]), audit_detector=Stub()).handle("c", "C", t)
    assert out["masked_text"] == "<PHONE_1> and then <PHONE_1>"
    b2 = dict(b, entity="e1")
    out2 = Gateway(detector=Stub([a, b2]), audit_detector=Stub()).handle("c", "C", t)
    assert out2["masked_text"] == "<PHONE_1> and then <PHONE_2>"


def test_same_entity_id_but_different_number_is_not_merged():
    t1, t2 = "number 98450 12345", "number 70000 11111"
    s1 = dict(sp(t1, "98450 12345", "PHONE"), entity="e0", joined_value="9845012345")
    s2 = dict(sp(t2, "70000 11111", "PHONE"), entity="e0", joined_value="7000011111")
    gw = Gateway(detector=Stub(per_call=[[s1], [s2]]), audit_detector=Stub())
    gw.handle("c", "C", t1)
    b = gw.handle("c", "C", t2)
    assert b["masked_text"] == "number <PHONE_2>" and gw.vault.get("c", "<PHONE_1>") == "9845012345"


def test_absent_entity_keys_keep_old_behaviour():
    t = "call 98450 12345"
    out = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub()).handle("c", "C", t)
    assert out["masked_text"] == "call <PHONE_1>"


def test_independent_audit_catches_what_the_detector_missed():
    gw = Gateway(detector=Stub(), audit_detector=Stub())                    # detector AND layer-1 re-scan are blind
    a = gw.handle("c", "C", "my number is 9845012345")
    assert a["status"] == "ok" and "9845" not in a["masked_text"] and a["trace"].count("tokenise") == 2, a["masked_text"]
    b = gw.handle("c", "C", "it is nine eight four five six")
    assert "nine" not in b["masked_text"], b["masked_text"]
    c = gw.handle("c", "C", "PAN ABCDE1234F and mail ramesh@example.com")
    assert "ABCDE" not in c["masked_text"] and "example.com" not in c["masked_text"], c["masked_text"]


def test_vault_survival_scan_reuses_the_existing_token():
    t1, t2 = "I am Ramesh Kumar", "please tell Ramesh Kumar thanks"
    gw = Gateway(detector=Stub(per_call=[[sp(t1, "Ramesh Kumar", "PERSON")], []]), audit_detector=Stub())
    gw.handle("c", "C", t1)
    b = gw.handle("c", "C", t2)                                             # detector missed the name this time
    assert b["masked_text"] == "please tell <PERSON_1> thanks" and gw.vault.tokens("c") == ["<PERSON_1>"], b["masked_text"]


def test_audit_leaves_amounts_and_dates_alone_but_an_uncertain_id_is_masked():
    gw = Gateway(detector=Stub(), audit_detector=Stub())
    t = "pay ₹4,200 on the 12th, that is four thousand two hundred rupees"
    out = gw.handle("c", "C", t)
    assert out["masked_text"] == t and out["trace"].count("tokenise") == 1
    t2 = "my order is 4410293 ok"                     # Layer 1 is unsure what this is: policy is MASK (there is no review step that could leave it)
    out2 = Gateway(detector=Stub([sp(t2, "4410293", "NUMBER_FRAGMENT")]), audit_detector=Stub()).handle("c", "C", t2)
    assert "4410293" not in out2["masked_text"] and out2["status"] == "ok"


def test_utility_check_blocks_a_mostly_placeholder_message_and_can_be_set_to_flag():
    t = " ".join(["98450 12345"] * 4)                  # 47 chars, all of it PII: the tokenised text is almost only placeholders
    phones = [{"label": "PHONE", "start": i, "end": i + 11, "source": "rules"} for i in range(0, len(t), 12)]
    default = Gateway(detector=Stub(phones), audit_detector=Stub()).handle("c", "C", t)
    assert default["status"] == "blocked" and default["reason"] == "over_masking", default
    flagged = Gateway(detector=Stub(phones), audit_detector=Stub(), overmask_action="flag").handle("c", "C", t)
    assert flagged["status"] == "ok" and any(w.startswith("over_masking") for w in flagged["warnings"])
    short = Gateway(detector=Stub([sp("98450 12345", "98450 12345", "PHONE")]), audit_detector=Stub()).handle("c", "C", "98450 12345")
    assert short["status"] == "ok" and not short["warnings"], "20 characters or fewer: a message that is just a number is legitimately 100% masked"


def test_name_memory_spans_the_whole_conversation_not_just_the_window():
    t1 = "am I speaking with Ramesh Kumar?"
    gw = Gateway(detector=Stub(per_call=[[dict(sp(t1, "Ramesh Kumar", "PERSON"), source="model")], [], [], [], [], [], [], [], [], [], []]), audit_detector=Stub())
    gw.handle("c", "Agent", t1)
    for i in range(8):                                   # push the name far outside the 6-turn window
        gw.handle("c", "Customer", f"okay {i}")
    out = gw.handle("c", "Customer", "Yes this is Kumar speaking, tell me")
    assert out["masked_text"].startswith("Yes this is <PERSON_1> speaking"), out["masked_text"]   # same token as the full name
    assert gw.vault.tokens("c") == ["<PERSON_1>"], gw.vault.tokens("c")                         # no extra vault entry for the part
    gw.forget("c")
    assert "c" not in gw.names and "c" not in gw.history


def test_rehydration_only_for_borrower_audience():
    t = "my number is 98450 12345"
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub())
    internal = gw.handle("c", "C", t, audience="agent_dashboard")
    assert "98450" not in internal["reply"] and "<PHONE_1>" in internal["reply"]
    gw2 = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub())
    assert "98450 12345" in gw2.handle("c", "C", t, audience="borrower")["reply"]


def test_reply_unknown_tokens_and_invented_pii_removed_before_rehydration():
    t = "my number is 98450 12345"
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub(),
                 llm=lambda m: "noted <PHONE_1>, also <PHONE_9> and your PAN ABCDE1234F, alt 70000 11111")
    out = gw.handle("c", "C", t)
    assert "[unknown]" in out["reply"] and "ABCDE1234F" not in out["reply"] and "70000" not in out["reply"], out["reply"]
    assert "98450 12345" in out["reply"] and "ABCDE" not in json.dumps(gw.log)


def test_rehydrated_text_never_reaches_a_log_or_the_vault_audit():
    t = "my number is 98450 12345 and I am Ramesh Kumar"
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE"), sp(t, "Ramesh Kumar", "PERSON")]), audit_detector=Stub())
    out = gw.handle("c", "C", t, borrower_id="b1")
    assert "98450 12345" in out["reply"] and "Ramesh Kumar" in out["reply"]
    blob = json.dumps(gw.log) + json.dumps(gw.vault.access_log())
    assert "98450" not in blob and "Ramesh" not in blob, blob
    assert all("reply_masked" not in e or "98450" not in e["reply_masked"] for e in gw.log)


def test_gateway_vault_is_encrypted_and_borrower_scoped():
    t = "my number is 98450 12345"
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub())
    gw.handle("c", "C", t, borrower_id="b7", purpose="collections")
    with gw.vault.svc.ctx.db.transaction() as tx:                                         # every cell of every table: the number is nowhere in the clear
        cells = b"".join(x if isinstance(x, bytes) else str(x).encode() for tbl in ("vault_records", "token_mappings", "blind_indexes", "borrower_keys")
                         for row in tx.query(f"SELECT * FROM {tbl}") for x in row.values())
    assert b"98450" not in cells and b"9845012345" not in cells
    e = gw.vault.entries_for_borrower("b7")
    assert len(e) == 1 and e[0]["purpose"] == "collections"          # consent_id is Layer 4's record, not stored in the vault
    assert gw.vault.destroy_borrower("b7")["key_destroyed"] and gw.vault.get("c", "<PHONE_1>") is None


def test_real_detector_pieces_across_turns_integration():
    """Works with or without the layer-1 `entity` keys: nothing may leak; if entities are emitted there is ONE token."""
    from layer1_detection.detector import Detector
    gw = Gateway(detector=Detector(use_rules=True, use_model=False), audit_detector=Detector(use_rules=True, use_model=False))
    a = gw.handle("c", "Customer", "my number is nine eight four five")
    b = gw.handle("c", "Customer", "zero one two three four five")
    assert "nine" not in a["masked_text"] and "zero" not in b["masked_text"]
    if gw.entity_tokens:
        assert len({r["token"] for r in gw.entity_tokens.values()}) == len(gw.entity_tokens)


# ======================================================================= review-fix tests (coordinator items 7-10)
def test_9_tripwire_evasions_are_caught_and_amounts_dates_do_not_trigger():
    evasions = ["number,9845012345", "no.9845012345", "\u20b99845012345", "9845012345 rupees", "9.8.4.5.0.1.2.3.4.5",
                "98450\u200b12345", "98450  12345", "(98450) 12345", "+91-98450-12345", "9 - 8 4 5 0 1 2 3 4 5",
                "98450\u00a012345", "Rs 98450 12345 only", "nine eight um seven six five", "nine-eight-seven-six-five",
                "ninety eight seven six five four", "9845012345th"]
    for t in evasions:
        assert au.scan(t), f"tripwire missed: {t!r}"
        out = Gateway(detector=Stub(), audit_detector=Stub()).handle("c", "C", t)       # detector blind: only the audit stands
        assert out["status"] == "ok" and "<" in out["masked_text"], (t, out["masked_text"])      # masked, not blocked
        assert "98450" not in out["masked_text"].replace(" ", "") and "nine" not in out["masked_text"], (t, out["masked_text"])
    fine = ["\u20b94,200", "Rs. 1,50,000", "12/03/2024", "1,23,456 rupees", "pay 4,200 on the 12th", "three thousand rupees",
            "10:30 sharp", "at \u20b9 1,850 only", "due 05.03.2025"]
    for t in fine:
        assert not au.scan(t), f"false alarm on a plain amount/date: {t!r}"
        out = Gateway(detector=Stub(), audit_detector=Stub()).handle("c", "C", t)
        assert out["status"] == "ok" and out["masked_text"] == t and out["trace"].count("tokenise") == 1


def test_10_result_is_sanitised_and_blocked_results_carry_no_residual_text():
    t = "my number is 98450 12345"
    gw = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub())
    ok = gw.handle("c", "C", t, borrower_id="b1")
    assert not ({"text", "work_text", "raw_values", "allowed", "spans"} & set(ok)) and "98450" not in json.dumps(
        {k: v for k, v in ok.items() if k != "reply"}), ok
    always = [{"label": "PHONE", "start": 0, "end": 3, "source": "rules"}]
    blocked = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub(per_call=[always])).handle("c", "C", t)
    assert blocked["status"] == "blocked" and "98450" not in json.dumps(blocked) and "masked_text" not in blocked and blocked["reply"] == ""
    err = Gateway(detector=Stub(boom=RuntimeError("x")), audit_detector=Stub()).handle("c", "C", t)
    assert "98450" not in json.dumps(err) and "text" not in err
    full = Gateway(detector=Stub([sp(t, "98450 12345", "PHONE")]), audit_detector=Stub()).handle("c", "C", t, internal=True)
    assert full["text"] == t and "raw_values" in full                      # raw only behind the explicit internal flag


def test_10_detector_timeout_has_a_default_and_a_stuck_call_does_not_starve_later_ones():
    t = "my number is 98450 12345"
    assert 0 < Gateway(detector=Stub(), audit_detector=Stub(), detect_timeout=None).detect_timeout <= 30
    det = Stub([sp(t, "98450 12345", "PHONE")], delay=0.4)
    gw = Gateway(detector=det, audit_detector=Stub(), detect_timeout=0.05)
    assert gw.handle("c", "C", t)["reason"] == "detector_timeout"
    det.delay = 0                                                          # the first call is still sleeping in its thread
    t0 = time.perf_counter()
    out = gw.handle("c", "C", t)
    assert out["status"] == "ok" and time.perf_counter() - t0 < 0.3, "a stuck call must not starve the next one"
    # a detector that hangs on every call is cut off after MAX_STUCK abandoned calls (no unbounded thread pile-up)
    hung = Gateway(detector=Stub(delay=1.0), audit_detector=Stub(), detect_timeout=0.02)
    reasons = [hung.handle("c", "C", t)["reason"] for _ in range(5)]
    assert reasons[:3] == ["detector_timeout"] * 3 and reasons[3:] == ["detector_unhealthy"] * 2, reasons


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(tests)} passed")
