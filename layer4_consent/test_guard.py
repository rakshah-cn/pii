"""Run:  .venv/bin/python -m layer4_consent.test_guard"""
import time

from .guard import guarded_write
from .schema import ConsentTag
from .write_guard import new_tag


class Store:
    def __init__(self): self.rows = []
    def put(self, r): self.rows.append(r)


class WStore:                      # adapter that only has write()
    def __init__(self): self.rows = []
    def write(self, r): self.rows.append(r)


class Sink:
    def __init__(self): self.events = []
    def emit(self, e): self.events.append(e)


def rec(tag, **kw):
    return dict({"id": "r1", "text": "<PHONE_1> hi", "consent": tag}, **kw)


def test_allowed_write_emits_event_with_hash_not_raw_id():
    s, a = Store(), Sink()
    assert guarded_write("logs", rec(new_tag("service", borrower_id="B-0001")), s, a)
    e = a.events[0]
    assert e["kind"] == "write_allowed" and e["borrower_hash"] not in ("unknown", "B-0001") and "B-0001" not in str(e)
    assert len(s.rows) == 1 and "text" not in e


def test_dataclass_tag_and_write_adapter_work():
    s, a = WStore(), Sink()
    assert guarded_write("vector_store", rec(ConsentTag(purpose="collection", borrower_id="B1")), s, a)   # alias
    assert len(s.rows) == 1


def test_denied_malformed_missing_never_raise_and_never_write():
    s, a = Store(), Sink()
    bad = [rec(new_tag("service")), rec(None), rec("service"), rec({"nope": 1}), {"id": "x"}, "not a dict", None,
           rec(new_tag("analytics")), rec(new_tag("model_improvement"))]
    for r in bad:
        assert guarded_write("eval_sets", r, s, a) is False
    assert s.rows == [] and len(a.events) == len(bad) and all(e["kind"] == "write_rejected" for e in a.events)


def test_unknown_store_and_analytics_default_deny():
    s, a = Store(), Sink()
    assert not guarded_write("mystery", rec(new_tag("service")), s, a)
    for store in ("logs", "vector_store", "eval_sets"):
        assert not guarded_write(store, rec(new_tag("analytics")), s, a)


def test_model_improvement_needs_consent_and_flag():
    s, a = Store(), Sink()
    assert not guarded_write("eval_sets", rec(new_tag("model_improvement", consent_id="C1", explicit=False)), s, a)
    assert not guarded_write("eval_sets", rec(new_tag("model_improvement", explicit=True)), s, a)
    assert a.events[-1]["reason"] == "explicit_consent_without_consent_id"
    assert guarded_write("eval_sets", rec(new_tag("model_improvement", consent_id="C1", explicit=True)), s, a)
    assert a.events[-1]["consent_id"] == "C1"


def test_rehydrated_record_is_rejected():
    s, a = Store(), Sink()
    assert not guarded_write("logs", rec(new_tag("service"), rehydrated=True), s, a)
    assert a.events[0]["reason"] == "rehydrated_data_must_not_be_stored" and s.rows == []


def test_rejection_event_never_contains_the_body():
    s, a = Store(), Sink()
    guarded_write("eval_sets", {"id": "e", "text": "SECRET-BODY 9845012345", "consent": new_tag("service")}, s, a)
    assert "SECRET" not in str(a.events) and "9845012345" not in str(a.events)


def test_adapter_failure_is_write_failed_not_a_crash():
    class Boom:
        def put(self, r): raise RuntimeError("disk full")
    a = Sink()
    assert guarded_write("logs", rec(new_tag("service")), Boom(), a) is False
    assert a.events[0]["kind"] == "write_failed"


def test_broken_audit_sink_never_crashes_the_conversation():
    class BadSink:
        def emit(self, e): raise OSError("audit down")
    s = Store()
    from . import guard
    before = guard.AUDIT_FAILURES
    assert guarded_write("logs", rec(new_tag("service")), s, BadSink()) is True
    assert guarded_write("eval_sets", rec(new_tag("service")), s, BadSink()) is False
    assert guard.AUDIT_FAILURES == before + 2


def test_layer7_chain_integration_keyed_hash():
    import os, tempfile
    from layer7_audit.chain import AuditLog
    d = tempfile.mkdtemp()
    log = AuditLog(path=os.path.join(d, "a.jsonl"), key=b"k" * 32)
    assert guarded_write("logs", rec(new_tag("service", borrower_id="B-77")), Store(), log)
    assert not guarded_write("eval_sets", rec(new_tag("service", borrower_id="B-77")), Store(), log)
    recs = log.records()
    assert [r["kind"] for r in recs] == ["write_allowed", "write_rejected"] and log.verify_chain()[0]
    assert recs[0]["borrower_hash"] == log.hash_borrower("B-77")[:16] and "B-77" not in open(log.path).read()


def test_decision_is_under_1ms():
    s, a = Store(), Sink()
    r = rec(new_tag("service", borrower_id="B1"))
    n = 2000
    t = time.perf_counter()
    for _ in range(n):
        guarded_write("logs", r, s, a)
    per = (time.perf_counter() - t) / n * 1000
    print(f"   guarded_write ≈ {per:.4f} ms per call (in-memory sink + store)")
    assert per < 1.0


# ---------------- consent registry, borrower mismatch, legacy delegation ----------------
from .guard import guarded_write_ex
from .registry import ConsentRegistry
from .write_guard import WriteGuard


def _reg():
    return ConsentRegistry({"C-123": {"borrower_id": "B-001", "purposes": ["model_improvement", "service"], "status": "active"}})


def _mi(cid="C-123", b="B-001", **kw):
    return rec(new_tag("model_improvement", consent_id=cid, explicit=True, borrower_id=b), **kw)


def test_registry_check_consent_rules():
    t = [0.0]
    r = ConsentRegistry(clock=lambda: t[0])
    r.grant("C1", "B1", ["collection", "model_improvement"], expires_at=100)
    assert r.check_consent("C1", "B1", "collections") and r.check_consent("C1", "B1", "model_improvement")
    assert not r.check_consent("C1", "B2", "model_improvement")          # someone else's consent
    assert not r.check_consent("C1", "B1", "analytics")                  # not covered
    assert not r.check_consent("nope", "B1", "model_improvement") and not r.check_consent(None, "B1", "x")
    t[0] = 100
    assert not r.check_consent("C1", "B1", "model_improvement")          # expired
    t[0] = 0
    assert r.withdraw("C1") and not r.check_consent("C1", "B1", "model_improvement") and not r.withdraw("zzz")


def test_guard_accepts_a_confirmed_consent_and_marks_it_verified():
    s, a = Store(), Sink()
    assert guarded_write("eval_sets", _mi(), s, a, registry=_reg())
    assert a.events[-1]["kind"] == "write_allowed" and a.events[-1]["consent_verified"] is True and len(s.rows) == 1


def test_guard_rejects_unknown_wrong_borrower_wrong_purpose_and_withdrawn_consent():
    reg = _reg()
    cases = [("unknown id", _mi(cid="C-999")), ("other borrower", _mi(b="B-002")),
             ("withdrawn", None)]
    for name, r in cases[:2]:
        s, a = Store(), Sink()
        assert guarded_write_ex("eval_sets", r, s, a, registry=reg) == (False, "consent_invalid"), name
        assert s.rows == [] and a.events[-1]["reason"] == "consent_invalid"
    s, a = Store(), Sink()
    reg2 = ConsentRegistry({"C-5": {"borrower_id": "B-001", "purposes": ["service"], "status": "active"}})      # purpose not covered
    assert guarded_write_ex("eval_sets", _mi(cid="C-5"), s, a, registry=reg2) == (False, "consent_invalid")
    reg.withdraw("C-123")
    assert guarded_write_ex("eval_sets", _mi(), s, a, registry=reg) == (False, "consent_invalid") and s.rows == []


def test_registry_failure_or_nonsense_answer_fails_closed():
    class Down:
        def check_consent(self, *a): raise ConnectionError("registry down")
    class Maybe:
        def check_consent(self, *a): return "yes"                         # truthy but not True
    for reg, why in ((Down(), "consent_registry_unavailable"), (Maybe(), "consent_invalid")):
        s, a = Store(), Sink()
        assert guarded_write_ex("eval_sets", _mi(), s, a, registry=reg) == (False, why) and s.rows == []


def test_claimed_consent_on_a_normal_purpose_is_also_checked_when_a_registry_is_given():
    s, a = Store(), Sink()
    bad = rec(new_tag("service", consent_id="C-fake", explicit=True, borrower_id="B-001"))
    assert guarded_write_ex("logs", bad, s, a, registry=_reg()) == (False, "consent_invalid")
    assert guarded_write("logs", rec(new_tag("service", borrower_id="B-001")), s, a, registry=_reg())     # no claim, no check


def test_borrower_mismatch_and_missing_borrower():
    s, a = Store(), Sink()
    r = rec(new_tag("service", borrower_id="B-001"), borrower_id="B-002")
    assert guarded_write_ex("logs", r, s, a) == (False, "borrower_mismatch") and s.rows == []
    assert a.events[-1]["borrower_hash"] != "unknown"
    assert guarded_write_ex("eval_sets", rec(new_tag("model_improvement", consent_id="C-123", explicit=True)), s, a,
                            registry=_reg()) == (False, "borrower_unknown_for_consent")
    assert guarded_write("logs", rec(new_tag("service", borrower_id="B-001"), borrower_id="B-001"), s, a)      # agreeing ids are fine


def test_legacy_mode_without_registry_still_writes_but_says_consent_was_only_claimed():
    s, a = Store(), Sink()
    assert guarded_write("eval_sets", _mi(), s, a)                        # registry=None: the old claim-only behaviour
    assert a.events[-1]["consent_verified"] is False


def test_explicit_consent_must_be_the_boolean_true():
    for bad in ("false", "no", "true", 1, [1]):
        s, a = Store(), Sink()
        r = rec({"purpose": "model_improvement", "consent_id": "C-123", "explicit_consent": bad, "borrower_id": "B-001"})
        assert guarded_write("eval_sets", r, s, a, registry=_reg()) is False and s.rows == [], bad
    assert a.events[-1]["reason"] in ("explicit_consent_not_boolean",)


def test_guarded_write_returns_a_plain_bool_never_a_truthy_tuple():
    s, a = Store(), Sink()
    assert guarded_write("eval_sets", rec(new_tag("service")), s, a) is False
    assert guarded_write("logs", rec(new_tag("service")), s, a) is True


def test_legacy_writeguard_delegates_and_keeps_its_contract():
    import os, tempfile, json as _j
    path = os.path.join(tempfile.mkdtemp(), "rej.jsonl")
    g, s = WriteGuard(path), Store()
    assert g.guarded_write("eval_sets", s, {"id": "e1", "text": "SECRET", "consent": new_tag("service")}) == (False, "purpose_not_allowed:service->eval_sets")
    assert g.guarded_write("logs", s, {"id": "l1", "consent": new_tag("service")}) == (True, "ok")
    assert g.guarded_write("logs", s, {"id": "l2"}) == (False, "missing_or_invalid_consent_tag")
    assert len(g.rejections) == 2 and set(g.rejections[0]) == {"ts", "store", "record_id", "reason", "purpose"}
    lines = [_j.loads(x) for x in open(path)]
    assert len(lines) == 2 and "SECRET" not in open(path).read() and lines[0]["record_id"] == "e1"
    # with a registry and an audit log the SAME rules apply and Layer 7 gets the events
    from layer7_audit.chain import AuditLog
    log = AuditLog(path=os.path.join(tempfile.mkdtemp(), "a.jsonl"), key=b"k" * 32)
    g2, s2 = WriteGuard(registry=_reg(), audit_logger=log), Store()
    assert g2.guarded_write("eval_sets", s2, _mi())[0] and not g2.guarded_write("eval_sets", s2, _mi(cid="C-999"))[0]
    assert [r["kind"] for r in log.records()] == ["write_allowed", "write_rejected"] and log.verify_chain()[0]
    assert log.records()[0]["borrower_hash"] == log.hash_borrower("B-001")[:16]


def test_gateway_consent_gate_never_infers_explicit_consent_from_a_consent_id():
    from layer3_gateway.consent import ConsentGate
    seen = []
    gate = ConsentGate(decide=lambda store, tag: (seen.append(tag) or (True, "ok")))
    gate.check("B", "service", consent_id="C-1")
    gate.check("B", "service", consent_id="C-1", explicit_consent=True)
    assert seen[0]["explicit_consent"] is False and seen[1]["explicit_consent"] is True


if __name__ == "__main__":
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v(); print("PASS", k); n += 1
    print(f"\n{n} passed")
