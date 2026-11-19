"""Shared conversation context (encrypted, expiring, erasable) - what lets several gateway processes serve one conversation."""
import os

from layer2_vault.config.settings import RetentionPolicy, ServiceIdentity
from layer2_vault.core.errors import ValidationFailed, VaultDenied
from layer2_vault.schemas.vault_schemas import EraseRequest, RotateRequest
from layer2_vault.tests._util import ER, GW, KO, expect, make, run, tok_req, who

CTX = ServiceIdentity("gw-ctx", frozenset({"context", "tokenize"}))
STATE = {"history": [{"speaker": "Customer", "text": "my number is nine eight four five"}], "names": {"kumar": "Kumar"}, "n": 3}
SECRET = "nine eight four five"


class Clock:
    def __init__(self, t=1_700_000_000.0): self.t = t
    def __call__(self): return self.t


def q(v, sql, p=()):
    with v.ctx.db.transaction() as tx: return tx.query(sql, p)


def test_a_context_round_trips_and_is_stored_encrypted():
    s, v = make(); c = v.context
    assert c.load("B1", "C1", CTX) is None
    assert c.save("B1", "C1", STATE, CTX) == 1 and c.save("B1", "C1", {**STATE, "n": 4}, CTX) == 2
    ver, st = c.load("B1", "C1", CTX); assert (ver, st["n"], st["history"][0]["text"]) == (2, 4, "my number is nine eight four five")
    row = q(v, "SELECT * FROM vault_context")[0]
    blob = b"".join(x if isinstance(x, bytes) else str(x).encode() for x in row.values())
    assert SECRET.encode() not in blob and b"Kumar" not in blob and b"nine" not in blob


def test_asking_for_changes_since_a_version_does_not_decrypt_anything():
    s, v = make(); c = v.context; c.save("B1", "C1", STATE, CTX)
    assert c.load("B1", "C1", CTX, newer_than=1) == (1, None) and c.load("B1", "C1", CTX, newer_than=0)[1] is not None
    c.save("B1", "C1", STATE, CTX); assert c.load("B1", "C1", CTX, newer_than=1)[1] is not None


def test_contexts_are_isolated_per_borrower_and_conversation_and_need_the_capability():
    s, v = make(); c = v.context; c.save("B1", "C1", STATE, CTX)
    assert c.load("B2", "C1", CTX) is None and c.load("B1", "C2", CTX) is None
    expect(VaultDenied, lambda: c.load("B1", "C1", who(s, ER)), "forbidden"); expect(VaultDenied, lambda: c.save("B1", "C1", STATE, who(s, GW)), "forbidden")
    scoped = ServiceIdentity("scoped", frozenset({"context"}), None, ("B1",)); assert c.load("B1", "C1", scoped) is not None
    expect(VaultDenied, lambda: c.load("B2", "C1", scoped), "forbidden"); expect(VaultDenied, lambda: c.save("B2", "C1", STATE, scoped), "forbidden")


def test_a_context_expires_by_itself_and_the_retention_job_purges_it():
    clk = Clock(); s, v = make(clock=clk, retention=RetentionPolicy(context_ttl_seconds=600)); c = v.context; c.save("B1", "C1", STATE, CTX)
    clk.t += 599; assert c.load("B1", "C1", CTX) is not None
    clk.t += 2; assert c.load("B1", "C1", CTX) is None, "expired: denied on read, whether or not cleanup ran"
    assert len(q(v, "SELECT 1 FROM vault_context")) == 1
    r = v.retention.cleanup_expired_records(who(s, "tok-retention-0000000000000")); assert r["contexts_purged"] == 1 and q(v, "SELECT 1 FROM vault_context") == []


def test_erasure_deletes_the_context_in_both_scopes():
    s, v = make(); c = v.context; er = who(s, ER)
    c.save("B1", "C1", STATE, CTX); c.save("B1", "C2", STATE, CTX)
    v.deletion.erase(EraseRequest(job_id="j1", borrower_id="B1", scope="CONVERSATION", conversation_id="C1"), er)
    assert c.load("B1", "C1", CTX) is None and c.load("B1", "C2", CTX) is not None
    v.deletion.erase(EraseRequest(job_id="j2", borrower_id="B1"), er); assert c.load("B1", "C2", CTX) is None and q(v, "SELECT 1 FROM vault_context") == []
    expect(VaultDenied, lambda: c.save("B1", "C3", STATE, CTX), "borrower_not_active")             # an erased borrower cannot get a new context


def test_destroying_or_revoking_the_key_makes_the_context_unreadable():
    s, v = make(); c = v.context; c.save("B1", "C1", STATE, CTX)
    with v.ctx.db.transaction() as tx: v.lifecycle.revoke(tx, "B1")
    assert c.load("B1", "C1", CTX) is None


def test_a_dek_rotation_drops_the_context_instead_of_leaving_it_undecryptable():
    s, v = make(); c = v.context; g = who(s, GW)
    v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")]), g); c.save("B1", "C1", STATE, CTX)
    assert v.lifecycle.rotate(RotateRequest(target_key_version=1, operation="DEK"), who(s, KO)).deks_rotated == 1
    assert c.load("B1", "C1", CTX) is None and q(v, "SELECT 1 FROM vault_context") == []
    assert c.save("B1", "C1", STATE, CTX) == 1 and c.load("B1", "C1", CTX) is not None, "a new context can be written under the new DEK"


def test_a_key_that_a_context_still_uses_is_not_retired_as_idle():
    clk = Clock(); s, v = make(clock=clk, retention=RetentionPolicy(ttl_seconds=100, destroy_idle_keys=True, context_ttl_seconds=100000)); g, rt = who(s, GW), who(s, "tok-retention-0000000000000")
    v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")]), g); v.context.save("B1", "C1", STATE, CTX); clk.t += 500
    assert v.retention.cleanup_expired_records(rt)["keys_retired"] == 0 and v.context.load("B1", "C1", CTX) is not None


def test_oversized_malformed_and_unreadable_contexts_are_handled_safely():
    s, v = make(); c = v.context
    expect(ValidationFailed, lambda: c.save("B1", "C1", {"x": "y" * 70000}, CTX), "context_too_large")
    for bad in ("bad id", "a" * 200, "x;y"): expect(ValidationFailed, lambda b=bad: c.load(b, "C1", CTX), "invalid_request")
    expect(VaultDenied, lambda: c.load("", "C1", CTX), "forbidden")                                 # missing borrower: denied, never assumed
    c.save("B1", "C1", STATE, CTX)
    with v.ctx.db.transaction() as tx: tx.execute("UPDATE vault_context SET ciphertext = ?", (b"\x00" * 40,))
    assert c.load("B1", "C1", CTX) is None, "a corrupted context reads as 'no shared context', never as an error or as someone else's data"


def test_a_context_is_bound_to_its_conversation_by_the_aad():
    s, v = make(); c = v.context; c.save("B1", "C1", STATE, CTX); c.save("B1", "C2", {"n": 9}, CTX)
    r = {x["conversation_id"]: x for x in q(v, "SELECT * FROM vault_context")}
    with v.ctx.db.transaction() as tx:                               # swap the two ciphertexts (same borrower, same key): a unique (key, nonce) index forces a scratch step
        tx.execute("UPDATE vault_context SET nonce = ? WHERE conversation_id = 'C1'", (b"\x00" * 12,))
        tx.execute("UPDATE vault_context SET ciphertext = ?, nonce = ? WHERE conversation_id = 'C2'", (r["C1"]["ciphertext"], r["C1"]["nonce"]))
        tx.execute("UPDATE vault_context SET ciphertext = ?, nonce = ? WHERE conversation_id = 'C1'", (r["C2"]["ciphertext"], r["C2"]["nonce"]))
    assert c.load("B1", "C1", CTX) is None and c.load("B1", "C2", CTX) is None, "a context copied into another conversation fails authentication"


if __name__ == "__main__":
    run(globals())
