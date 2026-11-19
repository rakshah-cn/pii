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


if __name__ == "__main__":
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v(); print("PASS", k); n += 1
    print(f"\n{n} passed")
