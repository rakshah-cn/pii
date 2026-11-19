"""Concurrency: unique constraints + retries give exactly one valid mapping; no duplicate numbering; erasure vs tokenise stays consistent."""
import threading

from layer2_vault.core.errors import VaultError
from layer2_vault.schemas.vault_schemas import EraseRequest
from layer2_vault.tests._util import ER, GW, make, run, tok_req, who


def _threads(fns):
    errs = []
    def wrap(f):
        try: f()
        except Exception as e: errs.append(e)
    ts = [threading.Thread(target=wrap, args=(f,)) for f in fns]
    [t.start() for t in ts]; [t.join() for t in ts]
    return errs


def test_concurrent_requests_for_the_same_value_produce_one_mapping():
    s, v = make(); g = who(s, GW); out = []
    errs = _threads([lambda i=i: out.append(v.tokenizer.tokenize(tok_req("call 98450 12345", [("PHONE", "98450 12345")], rid=f"r{i}"), g).mappings[0]) for i in range(24)])
    assert not errs, errs
    assert len({(m.token, m.vault_id) for m in out}) == 1 and len(out) == 24
    with v.ctx.db.transaction() as tx: assert v.ctx.repo.count_scope(tx, "B1") == {"vault_records": 1, "token_mappings": 1, "blind_indexes": 1}
def test_concurrent_distinct_values_get_distinct_consecutive_tokens():
    s, v = make(); g = who(s, GW); out = []
    vals = [f"98{i:08d}" for i in range(40)]
    errs = _threads([lambda x=x: out.append(v.tokenizer.tokenize(tok_req(f"c {x}", [("PHONE", x)], rid=x), g).mappings[0].token) for x in vals])
    assert not errs, errs
    assert sorted(out, key=lambda t: int(t[7:-1])) == [f"<PHONE_{i}>" for i in range(1, 41)]
def test_a_repeated_request_id_is_harmless():
    s, v = make(); g = who(s, GW)
    for _ in range(5): v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], rid="same"), g)
    with v.ctx.db.transaction() as tx: assert v.ctx.repo.count_scope(tx, "B1")["vault_records"] == 1
def test_concurrent_borrowers_do_not_interfere():
    s, v = make(); g = who(s, GW); out = {}
    errs = _threads([lambda b=b: out.__setitem__(b, v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], borrower=b, conv="C1", rid=b), g).mappings[0].vault_id) for b in (f"B{i}" for i in range(12))])
    assert not errs and len(set(out.values())) == 12
def test_erasure_racing_with_tokenisation_leaves_a_consistent_state():
    s, v = make(); g, er = who(s, GW), who(s, ER)
    v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], rid="seed"), g)
    fns = [lambda: v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)]
    fns += [lambda i=i: v.tokenizer.tokenize(tok_req(f"c 98{i:08d}", [("PHONE", f"98{i:08d}")], rid=f"w{i}"), g) for i in range(20)]
    errs = _threads(fns)
    assert all(isinstance(e, VaultError) for e in errs), errs                         # late writers are DENIED, nothing else goes wrong
    with v.ctx.db.transaction() as tx:
        assert v.ctx.repo.count_scope(tx, "B1") == {"vault_records": 0, "token_mappings": 0, "blind_indexes": 0}, "rows survived the erasure"
        assert v.ctx.repo.active_key(tx, "B1") is None and v.ctx.repo.borrower_state(tx, "B1") == "ERASED"

if __name__ == "__main__":
    run(globals())
