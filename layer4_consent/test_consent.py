"""Run:  .venv/bin/python -m layer4_consent.test_consent"""
from .policy import decide
from .write_guard import WriteGuard, new_tag


class _Store:
    def __init__(self): self.rows = []
    def put(self, r): self.rows.append(r)


def test_service_may_feed_logs_not_eval():
    assert decide("logs", new_tag("service"))[0]
    ok, why = decide("eval_sets", new_tag("service"))
    assert not ok and why.startswith("purpose_not_allowed")


def test_model_improvement_needs_explicit_consent():
    assert not decide("eval_sets", new_tag("model_improvement"))[0]
    assert not decide("eval_sets", new_tag("model_improvement", consent_id="C1", explicit=False))[0]
    assert decide("eval_sets", new_tag("model_improvement", consent_id="C1", explicit=True))[0]


def test_default_deny_unknown_store_and_missing_tag():
    assert decide("mystery_store", new_tag("service"))[1].startswith("unknown_store")
    assert decide("logs", None)[1] == "missing_consent_tag"


def test_guard_rejects_and_records_without_body():
    g, s = WriteGuard(), _Store()
    ok, why = g.guarded_write("eval_sets", s, {"id": "e1", "text": "SECRET", "consent": new_tag("service")})
    assert not ok and s.rows == []
    assert g.rejections and "SECRET" not in str(g.rejections)


def test_guard_allows_valid_write():
    g, s = WriteGuard(), _Store()
    ok, _ = g.guarded_write("logs", s, {"id": "l1", "text": "x", "consent": new_tag("collections")})
    assert ok and len(s.rows) == 1 and not g.rejections


if __name__ == "__main__":
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v(); print("PASS", k); n += 1
    print(f"\n{n} passed")
