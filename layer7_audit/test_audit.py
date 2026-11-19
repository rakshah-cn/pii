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


if __name__ == "__main__":
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v(); print("PASS", k); n += 1
    print(f"\n{n} passed")
