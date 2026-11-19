"""Nothing sensitive may appear in logs, events, the operations table, error messages or the database file."""
import io
import logging
import os
import tempfile

from layer2_vault.core.errors import VaultError, VaultUnavailable
from layer2_vault.schemas.vault_schemas import EraseRequest
from layer2_vault.tests._util import break_db, ER, GW, make, reh_req, run, tok_req, who

SECRETS = ["9845012345", "98450 12345", "Rahul Sharma", "ABCPK1234L", "rahul.sharma@example.com"]


def _flow(s, v):
    g, er = who(s, GW), who(s, ER); errs = []
    v.tokenizer.tokenize(tok_req("Rahul Sharma 98450 12345 ABCPK1234L rahul.sharma@example.com", [("NAME", "Rahul Sharma"), ("PHONE", "98450 12345"), ("PAN", "ABCPK1234L"), ("EMAIL", "rahul.sharma@example.com")]), g)
    v.rehydration.rehydrate(reh_req(["<PHONE_1>", "<NAME_1>"]), g)
    for fn in (lambda: v.rehydration.rehydrate(reh_req(["<PHONE_9>"]), g),                            # denial
               lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"], dest="llm"), g),               # policy denial
               lambda: v.tokenizer.tokenize(tok_req("98450 12345", [("PHONE", "98450 12345")], purpose="marketing"), g)):
        try: fn()
        except VaultError as e: errs.append(str(e) + repr(e))
    v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B1"), er)
    try: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g)
    except VaultError as e: errs.append(str(e) + repr(e))
    return errs


def test_logs_events_operations_and_error_messages_are_free_of_values_and_keys():
    buf, h = io.StringIO(), logging.StreamHandler()
    h.stream = buf; root = logging.getLogger("te03"); old = (root.level, list(root.handlers)); root.setLevel(logging.DEBUG); root.addHandler(h)
    try:
        master = os.urandom(32); s, v = make(master_secrets={1: master}); errs = _flow(s, v)
        with v.ctx.db.transaction() as tx: ops = str(v.ctx.repo.operations(tx))
        blob = buf.getvalue() + str(v.ctx.events.events) + ops + " ".join(errs)
        for x in SECRETS + [master.hex(), master.hex().upper()]:
            assert x not in blob, f"leaked: {x[:6]}..."
        assert master not in blob.encode()
        assert "rehydrate_attempt" in blob and "erase" in blob                                  # evidence exists, just not the values
    finally:
        root.removeHandler(h); root.setLevel(old[0])
def test_the_events_sent_to_layer7_hold_only_safe_metadata():
    s, v = make(); _flow(s, v)
    allowed = {"op", "request_id", "caller", "borrower", "status", "code", "entities", "tokens", "destination", "job", "scope", "keys", "records", "version", "conversation", "hits", "texts", "values"}
    for e in v.ctx.events.events: assert set(e) <= allowed, set(e) - allowed
def test_database_file_never_holds_plaintext_or_the_master_key_even_after_erasure_and_rotation():
    with tempfile.TemporaryDirectory() as d:
        master = os.urandom(32); s, v = make(master_secrets={1: master}, db_url=f"sqlite:///{d}/v.db"); _flow(s, v)
        v.ctx.db._conn.execute("PRAGMA wal_checkpoint(FULL)")
        blob = b"".join(open(os.path.join(d, f), "rb").read() for f in os.listdir(d))
        for x in SECRETS: assert x.encode() not in blob
        assert master not in blob and master.hex().encode() not in blob
def test_exception_text_from_a_database_failure_carries_no_values():
    s, v = make(); g = who(s, GW); break_db(v)
    try: v.tokenizer.tokenize(tok_req("Rahul Sharma 98450 12345", [("NAME", "Rahul Sharma"), ("PHONE", "98450 12345")]), g)
    except VaultError as e: t = str(e) + repr(e); assert "98450" not in t and "Rahul" not in t and isinstance(e, VaultUnavailable)
    else: raise AssertionError("tokenise must fail when the database is down")
def test_service_settings_repr_does_not_print_secrets():
    s, _ = make(); assert s.master_secrets[1].hex() not in repr(s) and "tok-gateway" not in repr(s)

if __name__ == "__main__":
    run(globals())
