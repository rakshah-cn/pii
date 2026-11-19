"""Final security regression: run EVERYTHING the vault can do (tokenise with idempotency, reuse, extend, rehydrate, expiry + cleanup, all three rotations, token revocation,
erasure incl. a failed-then-retried one, HTTP errors) and then look for secrets in every place they must never be: every cell of every table, the logs, the events sent to
Layer 7, and every HTTP error body. Needles: all PII values (and their digits-only forms), the master secrets, the derived KEK / blind-index / idempotency keys, and every DEK ever used."""
import base64
import io
import json
import logging
import os
import re

from fastapi.testclient import TestClient

from layer2_vault.api.routes import create_app
from layer2_vault.config.settings import RetentionPolicy, ServiceIdentity
from layer2_vault.database.models import TABLES
from layer2_vault.schemas.vault_schemas import EraseRequest, RotateRequest
from layer2_vault.tests._util import ER, GW, KO, make, reh_req, run, tok_req, who

PII = ["9845012345", "98450 67890", "Ramesh Kumar", "Sunita Reddy", "ABCDE1234F", "123456789012", "9811122233", "ramesh.kumar@example.com"]
ADMIN = ServiceIdentity("admin", frozenset({"admin"}))


def _rows(v, sql, p=()):
    with v.ctx.db.transaction() as tx:
        return tx.query(sql, p)


def _row(v, sql, p=()):
    with v.ctx.db.transaction() as tx:
        return tx.one(sql, p)


class Clock:
    def __init__(self, t=1_700_000_000.0): self.t = t
    def __call__(self): return self.t


def variants(x):
    d = re.sub(r"\D", "", x)
    return {x.encode(), x.replace(" ", "").encode()} | ({d.encode()} if len(d) >= 8 else set())


def key_variants(b):
    return {b, b.hex().encode(), base64.b64encode(b), base64.urlsafe_b64encode(b)}


def find_leaks(v, log_text, bodies, needles):
    """Look for every needle in: every cell of every table (as bytes), the logs, the Layer-7 events, and the HTTP error bodies. Returns (leaks, per-table dump)."""
    names = [re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", d).group(1) for d in TABLES]; dump = {}
    with v.ctx.db.transaction() as tx:
        for t in names:
            dump[t] = b"\n".join(b"|".join(x if isinstance(x, bytes) else str(x).encode() for x in row.values()) for row in tx.query(f"SELECT * FROM {t}"))
    places = {**{f"table {t}": b for t, b in dump.items()}, "logs": log_text.encode(), "layer7 events": json.dumps(v.ctx.events.events, default=str).encode(), "http errors": "\n".join(bodies).encode()}
    return [(where, label) for where, blob in places.items() for label, nds in needles.items() for nd in nds if nd in blob], dump


def test_the_scan_itself_catches_a_planted_leak():
    """Negative control: a scan that cannot fail proves nothing. Plant one leak in each kind of place and require all of them to be found."""
    s, v = make(); g = who(s, GW); v.tokenizer.tokenize(tok_req(f"call {PII[0]}", [("PHONE", PII[0])]), g)
    dek = v.ctx.keys.new_dek(); needles = {"PII": variants(PII[0]), "dek": key_variants(dek), "master": key_variants(s.master_secrets[1])}
    clean, _ = find_leaks(v, "all fine", ["{}"], needles); assert clean == [], "no false alarm on a clean vault"
    with v.ctx.db.transaction() as tx: tx.execute("UPDATE vault_operations SET error_code = ?", (f"failed for {PII[0]}",))
    leaks, _ = find_leaks(v, "token=" + dek.hex(), ['{"key": "' + base64.b64encode(s.master_secrets[1]).decode() + '"}'], needles)
    assert ("table vault_operations", "PII") in leaks and ("logs", "dek") in leaks and ("http errors", "master") in leaks, leaks
    with v.ctx.db.transaction() as tx: tx.execute("UPDATE vault_records SET nonce = nonce")
    assert ("table vault_records", "PII") not in leaks, "ciphertext rows do not contain the plaintext"


def test_after_a_full_lifecycle_no_pii_and_no_key_material_is_anywhere_it_should_not_be():
    clk = Clock(); master = {1: os.urandom(32), 2: os.urandom(32)}
    s, v = make(clock=clk, master_secrets=master, current_key_version=1, retention=RetentionPolicy(purpose_ttl_seconds={"service": 1000}))
    g, ko, er, rt = who(s, GW), who(s, KO), who(s, ER), who(s, "tok-retention-0000000000000")
    buf = io.StringIO(); h = logging.StreamHandler(buf); lgs = [logging.getLogger(n) for n in ("te03.vault", "te03.vault.api")]
    for lg in lgs: lg.setLevel(logging.DEBUG); lg.addHandler(h)
    deks = set(); remember = lambda: [deks.add(v.ctx.keys.unwrap(k["wrapped_dek"], k["wrap_nonce"], k["borrower_id"], k["key_id"], k["kek_version"]))
                                      for k in _rows(v, "SELECT * FROM borrower_keys WHERE wrapped_dek IS NOT NULL")]
    try:
        text = f"I am {PII[2]}, call {PII[0]} or {PII[1]}, PAN {PII[4]}"
        v.tokenizer.tokenize(tok_req(text, [("NAME", PII[2]), ("PHONE", PII[0]), ("PHONE", PII[1]), ("PAN", PII[4])], borrower="B1", conv="C1", rid="a"), g, idempotency_key="idem-key-0001")
        v.tokenizer.tokenize(tok_req(text, [("NAME", PII[2]), ("PHONE", PII[0]), ("PHONE", PII[1]), ("PAN", PII[4])], borrower="B1", conv="C1", rid="a2"), g, idempotency_key="idem-key-0001")   # replay
        v.tokenizer.tokenize(tok_req(f"{PII[3]} {PII[6]} {PII[7]}", [("NAME", PII[3]), ("PHONE", PII[6]), ("EMAIL", PII[7])], borrower="B2", conv="C2", purpose="service", rid="b"), g)
        v.tokenizer.tokenize(tok_req(f"Aadhaar {PII[5]}", [("AADHAAR", PII[5])], borrower="B3", conv="C3", rid="c"), g); remember()
        v.rehydration.rehydrate(reh_req(["<PHONE_1>", "<NAME_1>"], borrower="B1", conv="C1"), g)
        v.internal.extend("C1", "<PHONE_1>", g, joined=PII[0] + " 1"); v.internal.extend("C1", "<PHONE_1>", g, joined=PII[0])
        v.lifecycle.rotate(RotateRequest(target_key_version=2, operation="KEK"), ko); v.lifecycle.rotate(RotateRequest(target_key_version=2, operation="BLIND_INDEX"), ko)
        v.lifecycle.rotate(RotateRequest(target_key_version=2, operation="DEK"), ko, idempotency_key="idem-rot-0001"); remember()
        v.lifecycle.revoke_token("C1", "<PAN_1>", ADMIN)
        clk.t += 2000; v.retention.cleanup_expired_records(rt)                                           # B2's 'service' records expire and are deleted
        v.deletion.fault = lambda step: (_ for _ in ()).throw(RuntimeError("x")) if step == "destroy_key" else None
        v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B3"), er, idempotency_key="idem-era-0001"); v.deletion.fault = None
        v.deletion.erase(EraseRequest(job_id="J1", borrower_id="B3"), er, idempotency_key="idem-era-0001")
        c = TestClient(create_app(v), raise_server_exceptions=False); bodies = []
        for path, body in (("/vault/tokenize", {"request_id": "x", "borrower_id": "B 1", "conversation_id": "C1", "purpose_id": "collections", "text": PII[0], "detected_entities": []}),
                           ("/vault/rehydrate", {"request_id": "x", "borrower_id": "B1", "conversation_id": "C1", "purpose_id": "collections", "destination": "nowhere", "tokens": ["<PHONE_1>"]})):
            bodies.append(c.post(path, headers={"Authorization": f"Bearer {GW}"}, json=body).text); bodies.append(c.post(path, headers={"Authorization": "Bearer nope-nope-nope-nope"}, json=body).text)
        bodies.append(c.get("/vault/health", headers={"Authorization": f"Bearer {GW}"}).text)
    finally:
        for lg in lgs: lg.removeHandler(h)
    needles = {f"PII:{p}": variants(p) for p in PII}
    for i, m in master.items(): needles[f"master{i}"] = key_variants(m)
    for label, fn in (("kek1", lambda: v.ctx.keys.kek(1)), ("kek2", lambda: v.ctx.keys.kek(2)), ("blind1", lambda: v.ctx.keys.blind_key(1)), ("blind2", lambda: v.ctx.keys.blind_key(2)), ("idem", lambda: v.ctx.keys.idempotency_key(2))):
        needles[label] = key_variants(fn())
    for n, d in enumerate(deks): needles[f"dek{n}"] = key_variants(d)
    assert len(deks) >= 3, "the scan covers the DEKs from before and after rotation"
    leaks, dump = find_leaks(v, buf.getvalue(), bodies, needles)
    assert not leaks, leaks
    assert buf.getvalue() and len(v.ctx.events.events) > 5, "the scan looked at real logs and events"
    # 3. what SHOULD be there: the erased borrower and the expired records are gone, tombstones remain
    assert b"B3" not in dump["vault_records"] and b"B2" not in dump["vault_records"] and dump["vault_record_tombstones"], "erased / expired records are gone; their PII-free tombstones remain"


if __name__ == "__main__":
    run(globals())
