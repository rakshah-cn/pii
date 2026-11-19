"""PostgreSQL-only tests (skipped unless VAULT_TEST_DB_URL is set). Two SEPARATE connections to one schema, so the races are real:
row locks (SELECT ... FOR UPDATE on borrower_state) and unique constraints decide the outcome, not an in-process lock.
   VAULT_TEST_DB_URL='postgresql://USER@/DBNAME?host=/var/run/postgresql' python -m layer2_vault.tests.test_postgres"""
import os
import threading
import uuid

from layer2_vault.core.errors import VaultError
from layer2_vault.schemas.vault_schemas import EraseRequest
from layer2_vault.tests._util import ER, GW, make, run, tok_req, who

URL = os.environ.get("VAULT_TEST_DB_URL")
PH = "9845012345"


def _two_connections():
    base = URL + ("&" if "?" in URL else "?") + "schema=race_" + uuid.uuid4().hex[:10]; m = os.urandom(32)
    (s1, v1), (s2, v2) = make(master_secrets={1: m}, db_url=base), make(master_secrets={1: m}, db_url=base)
    assert v1.ctx.db is not v2.ctx.db and v1.ctx.db.kind == "postgres"
    return (s1, v1), (s2, v2)


def _go(fns):
    errs = []
    def w(f):
        try: f()
        except Exception as e: errs.append(e)
    ts = [threading.Thread(target=w, args=(f,)) for f in fns]; [t.start() for t in ts]; [t.join() for t in ts]; return errs


def test_same_value_from_two_connections_gives_one_record():
    if not URL: return
    (s1, v1), (s2, v2) = _two_connections(); out = []
    fns = [lambda i=i: out.append((v1 if i % 2 else v2).tokenizer.tokenize(tok_req(f"c {PH}", [("PHONE", PH)], rid=f"r{i}"), who(s1, GW)).mappings[0].vault_id) for i in range(24)]
    errs = _go(fns); assert not errs, errs[:2]
    assert len(out) == 24 and len(set(out)) == 1
    with v1.ctx.db.transaction() as tx: assert v1.ctx.repo.count_scope(tx, "B1") == {"vault_records": 1, "token_mappings": 1, "blind_indexes": 1}
def test_distinct_values_from_two_connections_get_distinct_consecutive_tokens():
    if not URL: return
    (s1, v1), (s2, v2) = _two_connections(); out = []; vals = [f"98{i:08d}" for i in range(40)]
    errs = _go([lambda x=x, i=i: out.append((v1 if i % 2 else v2).tokenizer.tokenize(tok_req(f"c {x}", [("PHONE", x)], rid=x), who(s1, GW)).mappings[0].token) for i, x in enumerate(vals)])
    assert not errs, errs[:2]; assert sorted(out, key=lambda t: int(t[7:-1])) == [f"<PHONE_{i}>" for i in range(1, 41)]
def test_erasure_on_one_connection_racing_tokenising_on_another_stays_consistent():
    if not URL: return
    (s1, v1), (s2, v2) = _two_connections(); v1.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="seed"), who(s1, GW))
    fns = [lambda: v2.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), who(s2, ER))]
    fns += [lambda i=i: v1.tokenizer.tokenize(tok_req(f"c 98{i:08d}", [("PHONE", f"98{i:08d}")], rid=f"w{i}"), who(s1, GW)) for i in range(20)]
    errs = _go(fns); assert all(isinstance(e, VaultError) for e in errs), errs[:3]
    with v1.ctx.db.transaction() as tx:
        assert v1.ctx.repo.count_scope(tx, "B1") == {"vault_records": 0, "token_mappings": 0, "blind_indexes": 0}
        assert v1.ctx.repo.active_key(tx, "B1") is None and v1.ctx.repo.borrower_state(tx, "B1") == "ERASED"
def test_ciphertext_and_wrapped_keys_are_stored_as_binary_and_nothing_readable_is_in_the_tables():
    if not URL: return
    (s1, v1), _ = _two_connections(); v1.tokenizer.tokenize(tok_req("Rahul 98450 12345", [("NAME", "Rahul"), ("PHONE", "98450 12345")]), who(s1, GW))
    with v1.ctx.db.transaction() as tx:
        cols = {r["column_name"]: r["data_type"] for r in tx.query("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = current_schema() AND table_name IN ('vault_records','borrower_keys')")}
        assert cols["ciphertext"] == "bytea" and cols["wrapped_dek"] == "bytea" and cols["nonce"] == "bytea"
        dump = str(tx.query("SELECT * FROM vault_records")) + str(tx.query("SELECT * FROM blind_indexes")) + str(tx.query("SELECT * FROM token_mappings")) + str(tx.query("SELECT * FROM vault_operations"))
        assert "98450" not in dump and "Rahul" not in dump

if __name__ == "__main__":
    print("PostgreSQL URL:", "set" if URL else "NOT SET (tests skip)"); run(globals())
