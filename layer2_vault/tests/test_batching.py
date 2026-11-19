"""Phase 5: batched blind-index lookups, sequence numbers, nonce allocation and inserts. Batching must change the NUMBER OF STATEMENTS, never the result or the scope."""
from layer2_vault.core.blind_index import blind_index
from layer2_vault.core.nonce import NonceSource
from layer2_vault.database.connection import ConflictError, Tx
from layer2_vault.tests._util import GW, expect, make, reh_req, run, tok_req, who

vals = lambda n, base=9400000000: [str(base + i) for i in range(n)]


def q(v, sql, p=()):
    with v.ctx.db.transaction() as tx:
        return tx.query(sql, p)


def count_statements(fn):
    """How many SQL statements (execute + executemany calls) does `fn` send?"""
    n = {"c": 0}; oe, om = Tx.execute, Tx.executemany
    def e(self, *a, **k): n["c"] += 1; return oe(self, *a, **k)
    def m(self, *a, **k): n["c"] += 1; return om(self, *a, **k)
    Tx.execute, Tx.executemany = e, m
    try:
        fn()
    finally:
        Tx.execute, Tx.executemany = oe, om
    return n["c"]


def test_the_number_of_statements_does_not_grow_with_the_number_of_values():
    s, v = make(); g = who(s, GW); v.tokenizer.tokenize(tok_req("9000000000", [("PHONE", "9000000000")], rid="warm"), g)       # key already exists: measure the steady state
    def call(n, conv, borrower="B1"):
        xs = vals(n, 9500000000 if n == 5 else 9600000000)
        return lambda: v.tokenizer.tokenize(tok_req(" ".join(xs), [("PHONE", x) for x in xs], conv=conv, borrower=borrower, rid=f"r{n}{conv}"), g)
    c5, c50 = count_statements(call(5, "CA")), count_statements(call(50, "CB"))
    assert c5 == c50, f"5 values took {c5} statements, 50 values took {c50}: still one statement per value somewhere"
    r5, r50 = count_statements(call(5, "CA")), count_statements(call(50, "CB"))
    assert r5 == r50 and r5 < c5, "reuse (lookups only) is also constant, and cheaper than creating"
    assert c50 <= 16, c50


def test_a_batch_lookup_is_confined_to_one_borrower_conversation_and_purpose():
    s, v = make(); g = who(s, GW)
    v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], borrower="B1", conv="C1", rid="a"), g)
    v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], borrower="B2", conv="C1", rid="b"), g)          # same value, same conversation id, other borrower
    km = v.ctx.keys
    d1 = blind_index(km.blind_key(1), "B1", "C1", "collections", "PHONE", "9845012345"); d2 = blind_index(km.blind_key(1), "B2", "C1", "collections", "PHONE", "9845012345")
    assert d1 != d2
    with v.ctx.db.transaction() as tx:
        repo = v.ctx.repo
        own = repo.find_by_digests_batch(tx, "B1", "C1", "collections", [("PHONE", 1, d1), ("PHONE", 1, d2)])
        assert list(own) == [("PHONE", 1, d1)], "B1's scope returns B1's row and NOT B2's, even when B2's digest is supplied"
        assert repo.find_by_digests_batch(tx, "B1", "OTHER", "collections", [("PHONE", 1, d1)]) == {} and repo.find_by_digests_batch(tx, "B1", "C1", "service", [("PHONE", 1, d1)]) == {}
        assert repo.find_by_digests_batch(tx, "B3", "C1", "collections", [("PHONE", 1, d1), ("PHONE", 1, d2)]) == {}
        assert repo.find_by_digests_batch(tx, "B1", "C1", "collections", []) == {}


def test_a_large_batch_is_split_into_chunks_and_every_hit_is_still_found():
    s, v = make(); g = who(s, GW); xs = vals(230)
    first = v.tokenizer.tokenize(tok_req(" ".join(xs), [("PHONE", x) for x in xs]), g)
    again = v.tokenizer.tokenize(tok_req(" ".join(xs), [("PHONE", x) for x in xs], rid="again"), g)
    assert [m.token for m in first.mappings] == [m.token for m in again.mappings] == [f"<PHONE_{i + 1}>" for i in range(230)]
    assert len(q(v, "SELECT 1 FROM vault_records")) == 230 and len(q(v, "SELECT 1 FROM blind_indexes")) == 230


def test_one_batch_takes_consecutive_nonce_counters_and_consecutive_token_numbers_per_type():
    s, v = make(); g = who(s, GW); text = "9811111111 Ravi 9822222222 Sita 9833333333"
    r = v.tokenizer.tokenize(tok_req(text, [("PHONE", "9811111111"), ("NAME", "Ravi"), ("PHONE", "9822222222"), ("NAME", "Sita"), ("PHONE", "9833333333")]), g)
    assert [m.token for m in r.mappings] == ["<PHONE_1>", "<NAME_1>", "<PHONE_2>", "<NAME_2>", "<PHONE_3>"]
    ns = sorted(NonceSource.counter_of(x["nonce"]) for x in q(v, "SELECT nonce FROM vault_records")); assert ns == [0, 1, 2, 3, 4]
    assert q(v, "SELECT next_counter FROM key_nonce_state")[0]["next_counter"] == 5
    more = v.tokenizer.tokenize(tok_req("9844444444 9811111111", [("PHONE", "9844444444"), ("PHONE", "9811111111")], rid="m"), g)
    assert [m.token for m in more.mappings] == ["<PHONE_4>", "<PHONE_1>"]


def test_the_same_value_twice_in_one_request_is_stored_once():
    s, v = make(); g = who(s, GW)
    r = v.tokenizer.tokenize(tok_req("9845012345 and 98450 12345 again", [("PHONE", "9845012345"), ("PHONE", "98450 12345")]), g)
    assert r.mappings[0].token == r.mappings[1].token == "<PHONE_1>" and r.mappings[0].vault_id == r.mappings[1].vault_id and len(q(v, "SELECT 1 FROM vault_records")) == 1


def test_a_batch_that_fails_midway_stores_nothing():
    s, v = make(); g = who(s, GW); v.tokenizer.tokenize(tok_req("9000000000", [("PHONE", "9000000000")], rid="warm"), g)
    before = [len(q(v, f"SELECT 1 FROM {t}")) for t in ("vault_records", "token_mappings", "blind_indexes")], q(v, "SELECT next_counter FROM key_nonce_state")[0]["next_counter"]
    real = v.ctx.repo.insert_bundles
    def broken(tx, bundles, now, exp):
        real(tx, bundles[:-1], now, exp); raise RuntimeError("disk full after most of the batch")
    v.ctx.repo.insert_bundles = broken
    xs = vals(10, 9300000000)
    expect(Exception, lambda: v.tokenizer.tokenize(tok_req(" ".join(xs), [("PHONE", x) for x in xs], rid="x"), g))
    v.ctx.repo.insert_bundles = real
    after = [len(q(v, f"SELECT 1 FROM {t}")) for t in ("vault_records", "token_mappings", "blind_indexes")], q(v, "SELECT next_counter FROM key_nonce_state")[0]["next_counter"]
    assert after == before, "records, mappings, indexes AND the nonce reservation all rolled back together"
    r = v.tokenizer.tokenize(tok_req(" ".join(xs), [("PHONE", x) for x in xs], rid="retry"), g); assert r.mappings[0].token == "<PHONE_2>"


def test_a_duplicate_nonce_inside_a_batch_insert_is_rejected_by_the_database():
    s, v = make(); g = who(s, GW); v.tokenizer.tokenize(tok_req("9000000000", [("PHONE", "9000000000")], rid="warm"), g)
    row = q(v, "SELECT key_id, nonce FROM vault_records")[0]
    rec = lambda i, n: {"vault_id": f"v{i}", "borrower_id": "B1", "conversation_id": "C1", "token_id": f"t{i}", "pii_type": "PHONE", "ciphertext": b"\x01" * 20, "nonce": n, "key_id": row["key_id"], "purpose_id": "collections"}
    bundles = [(rec(i, bytes([i]) * 12 if i < 2 else bytes(row["nonce"])), {"token_id": f"t{i}", "token": f"<PHONE_{9 + i}>", "seq": 9 + i}, {"version": 1, "digest": f"d{i}"}) for i in range(3)]
    def go():
        with v.ctx.db.transaction() as tx: v.ctx.repo.insert_bundles(tx, bundles, 1.0, None)
    expect(ConflictError, go); assert len(q(v, "SELECT 1 FROM vault_records")) == 1


def test_batched_tokenisation_keeps_rehydration_exact_for_every_value():
    s, v = make(); g = who(s, GW); xs = vals(60, 9100000000)
    v.tokenizer.tokenize(tok_req(" ".join(xs), [("PHONE", x) for x in xs]), g)
    for lo in range(0, 60, 50):
        toks = [f"<PHONE_{i + 1}>" for i in range(lo, min(lo + 50, 60))]
        assert v.rehydration.rehydrate(reh_req(toks), g).values == dict(zip(toks, xs[lo:lo + 50]))


if __name__ == "__main__":
    run(globals())
