"""Run:  .venv/bin/python -m layer7_audit.test_audit"""
import json
import os
import tempfile

from .chain import AuditLog
from .proof import render_proof


def _log():
    d = tempfile.mkdtemp()
    return AuditLog(os.path.join(d, "a.jsonl"), key=b"k" * 32)


def _rec(i):
    return {"type": "erasure", "request_id": f"R{i}", "borrower_hash": "x", "status": "ERASED_VERIFIED", "passes": 2,
            "duration_s": 1.2, "stores": [{"store": "logs", "found": 3, "deleted": 3, "verified_zero": True}],
            "vault": {"entries_deleted": 4, "key_destroyed": True}}


def test_chain_intact():
    a = _log()
    for i in range(4):
        a.append(_rec(i))
    assert a.verify_chain() == (True, None, "chain_intact")


def test_edit_detected():
    a = _log()
    for i in range(4):
        a.append(_rec(i))
    lines = open(a.path).read().splitlines()
    r = json.loads(lines[1]); r["stores"][0]["found"] = 0          # quietly claim nothing was found
    lines[1] = json.dumps(r)
    open(a.path, "w").write("\n".join(lines) + "\n")
    ok, bad, why = a.verify_chain()
    assert not ok and bad == 1 and why == "record_altered"


def test_delete_and_reorder_detected():
    a = _log()
    for i in range(4):
        a.append(_rec(i))
    lines = open(a.path).read().splitlines()
    open(a.path, "w").write("\n".join(lines[:1] + lines[2:]) + "\n")   # drop record 1
    assert a.verify_chain()[0] is False
    a2 = _log()
    for i in range(3):
        a2.append(_rec(i))
    L = open(a2.path).read().splitlines()
    open(a2.path, "w").write("\n".join([L[1], L[0], L[2]]) + "\n")      # swap order
    assert a2.verify_chain()[0] is False


def test_borrower_hash_is_keyed_and_not_raw():
    a, b = _log(), AuditLog(os.path.join(tempfile.mkdtemp(), "b.jsonl"), key=b"z" * 32)
    h = a.hash_borrower("B0001")
    assert "B0001" not in h and h == a.hash_borrower("B0001") and h != b.hash_borrower("B0001")


def test_proof_renders_and_states_limits():
    a = _log()
    r = a.append(dict(_rec(1), borrower_hash=a.hash_borrower("B1"), methods=["registry"], unresolved=[]))
    txt = render_proof(r, *a.verify_chain()[::2], head=a.head())
    assert "INTACT" in txt and "DOES NOT PROVE" in txt


# ---------------------------------------------------------------- concurrency and cost (stabilisation sprint)
def _build(path, n):
    """Write n valid chained records quickly, in the exact on-disk format (used to test big logs without n^2 appends)."""
    import hashlib
    from .chain import GENESIS, _canon
    prev = GENESIS
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            b = {"event": "X", "request_id": f"R{i}", "seq": i, "ts": 1.0, "prev_hash": prev}
            b["hash"] = hashlib.sha256((prev + _canon(b)).encode()).hexdigest(); prev = b["hash"]
            f.write(json.dumps(b) + "\n")


def test_eight_threads_appending_at_once_leave_a_valid_chain():
    import threading
    a = _log(); errs = []
    def w(k):
        try:
            for i in range(60): a.append({"event": "X", "who": k, "i": i})
        except Exception as e: errs.append(repr(e))
    ts = [threading.Thread(target=w, args=(k,)) for k in range(8)]; [t.start() for t in ts]; [t.join() for t in ts]
    recs = a.records(); assert not errs and len(recs) == 480 and [r["seq"] for r in recs] == list(range(480))
    assert a.verify_chain() == (True, None, "chain_intact")


def test_several_processes_appending_at_once_leave_a_valid_chain():
    import multiprocessing as mp
    d = tempfile.mkdtemp(); path = os.path.join(d, "a.jsonl"); AuditLog(path, key=b"k" * 32)
    ps = [mp.get_context("fork").Process(target=_proc, args=(path, k)) for k in range(4)]; [p.start() for p in ps]; [p.join() for p in ps]
    assert all(p.exitcode == 0 for p in ps)
    a = AuditLog(path, key=b"k" * 32); assert len(a.records()) == 400 and a.verify_chain()[0]


def _proc(path, k):
    a = AuditLog(path, key=b"k" * 32)
    for i in range(100): a.append({"event": "X", "who": k, "i": i})


def test_two_instances_on_one_file_interleave_without_forking_the_chain():
    a = _log(); b = AuditLog(a.path, key=b"k" * 32)
    for i in range(30):
        a.append(_rec(i)); b.append(_rec(i)); a.append(_rec(i))       # a's cached tail is stale after b writes: it must notice and re-read
    assert len(a.records()) == 90 and a.verify_chain()[0] and b.verify_chain()[0]


def test_append_cost_does_not_grow_with_the_size_of_the_log():
    import time
    out = {}
    for n in (1000, 50000):
        d = tempfile.mkdtemp(); p = os.path.join(d, "a.jsonl"); _build(p, n); a = AuditLog(p, key=b"k" * 32)
        t0 = time.perf_counter()
        for i in range(30): a.append(_rec(i))
        out[n] = (time.perf_counter() - t0) / 30 * 1000
        assert a.verify_chain()[0] if n <= 1000 else True
    assert out[50000] < 5.0, f"append at 50,000 records took {out[50000]:.1f} ms (it was 78 ms when the whole file was re-read)"
    assert out[50000] < 4 * max(out[1000], 0.05), out


def test_head_is_the_newest_hash_without_reading_the_whole_log():
    a = _log(); assert a.head() == "0" * 64
    last = [a.append(_rec(i)) for i in range(5)][-1]; assert a.head() == last["hash"]
    d = tempfile.mkdtemp(); p = os.path.join(d, "big.jsonl"); _build(p, 20000); big = AuditLog(p, key=b"k" * 32)
    assert big.head() == big.records()[-1]["hash"]


def test_a_log_written_by_the_old_code_continues_correctly():
    d = tempfile.mkdtemp(); p = os.path.join(d, "old.jsonl"); _build(p, 25)
    a = AuditLog(p, key=b"k" * 32); r = a.append(_rec(1)); assert r["seq"] == 25 and a.verify_chain() == (True, None, "chain_intact")


def test_tampering_with_a_busy_log_is_still_detected():
    import threading
    a = _log()
    ts = [threading.Thread(target=lambda k=k: [a.append({"event": "X", "who": k, "i": i}) for i in range(25)]) for k in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert a.verify_chain()[0]
    lines = open(a.path).read().splitlines(); rec = json.loads(lines[50]); rec["who"] = 99; lines[50] = json.dumps(rec); open(a.path, "w").write("\n".join(lines) + "\n")
    ok, bad, why = a.verify_chain(); assert not ok and bad == 50


if __name__ == "__main__":
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v(); print("PASS", k); n += 1
    print(f"\n{n} passed")
