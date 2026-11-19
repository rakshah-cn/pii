"""HTTP surface: authentication, capability checks, safe errors (no echo of PII), fail-closed 503, and the full tokenise -> rehydrate -> erase path."""
import logging

from fastapi.testclient import TestClient

from layer2_vault.api.routes import create_app
from layer2_vault.tests._util import break_db, AU, ER, GW, KO, LI, make, run

H = lambda t: {"Authorization": f"Bearer {t}"}
TEXT = "Call me on 98450 12345 ok"
BODY = {"request_id": "r1", "borrower_id": "B1", "conversation_id": "C1", "purpose_id": "collections", "text": TEXT,
        "detected_entities": [{"type": "PHONE", "value": "98450 12345", "start": 11, "end": 22}]}
REH = {"request_id": "h1", "borrower_id": "B1", "conversation_id": "C1", "purpose_id": "collections", "destination": "borrower_reply", "tokens": ["<PHONE_1>"]}


def client():
    s, v = make(); return TestClient(create_app(v)), v


def test_full_flow_tokenize_rehydrate_metadata_erase():
    c, v = client()
    r = c.post("/vault/tokenize", headers=H(GW), json=BODY); assert r.status_code == 200 and r.json()["masked_text"] == "Call me on <PHONE_1> ok" and "98450" not in r.text
    r = c.post("/vault/rehydrate", headers=H(GW), json=REH); assert r.status_code == 200 and r.json()["values"] == {"<PHONE_1>": "98450 12345"}
    vid = c.post("/vault/tokenize", headers=H(GW), json={**BODY, "request_id": "r2"}).json()["mappings"][0]["vault_id"]
    m = c.get(f"/vault/records/{vid}", headers=H(LI)); assert m.status_code == 200 and "98450" not in m.text and m.json()["token"] == "<PHONE_1>"
    e = c.post("/vault/erase", headers=H(ER), json={"job_id": "J1", "borrower_id": "B1"}); assert e.status_code == 200 and e.json()["status"] == "COMPLETED" and e.json()["key_destroyed"]
    assert c.post("/vault/rehydrate", headers=H(GW), json=REH).status_code == 403
def test_no_credentials_or_bad_credentials_is_401_and_caller_cannot_claim_an_identity():
    c, _ = client()
    assert c.post("/vault/tokenize", json=BODY).status_code == 401 and c.post("/vault/tokenize", headers=H("wrong-token-wrong-token"), json=BODY).status_code == 401
    assert c.post("/vault/tokenize", headers={"Authorization": "Basic abc"}, json=BODY).status_code == 401
    r = c.post("/vault/tokenize", headers=H(ER), json={**BODY, "caller": "gateway", "role": "admin"}); assert r.status_code == 422           # unknown fields rejected
    assert c.post("/vault/tokenize", headers={**H(ER), "X-Service": "gateway"}, json=BODY).status_code == 403                                 # header claims ignored
def test_each_endpoint_enforces_its_capability():
    c, _ = client()
    assert c.post("/vault/tokenize", headers=H(ER), json=BODY).status_code == 403 and c.post("/vault/tokenize", headers=H(AU), json=BODY).status_code == 403
    assert c.post("/vault/rehydrate", headers=H(LI), json=REH).status_code == 403
    assert c.post("/vault/erase", headers=H(GW), json={"job_id": "J", "borrower_id": "B1"}).status_code == 403
    assert c.post("/vault/rotate-key", headers=H(GW), json={"target_key_version": 1}).status_code == 403
    assert c.get("/vault/health", headers=H(ER)).status_code == 403 and c.get("/vault/health", headers=H(KO)).json()["status"] == "ok"
    assert c.get("/vault/records/v_nope", headers=H(GW)).status_code == 403
def test_validation_errors_never_echo_the_request_body():
    c, _ = client(); bad = {**BODY, "detected_entities": [{"type": "PHONE", "value": "98450 12345", "start": 0, "end": 5}]}
    r = c.post("/vault/tokenize", headers=H(GW), json=bad); assert r.status_code == 422 and "98450" not in r.text and r.json()["code"] == "invalid_request"
    r = c.post("/vault/tokenize", headers=H(GW), json={"text": "Rahul Sharma 98450 12345", "detected_entities": 7}); assert r.status_code == 422 and "98450" not in r.text and "Rahul" not in r.text
def test_when_the_database_is_down_the_answer_is_503_and_carries_no_masked_text():
    c, v = client(); break_db(v)
    r = c.post("/vault/tokenize", headers=H(GW), json=BODY); assert r.status_code == 503 and "masked_text" not in r.text and "98450" not in r.text
def test_permission_denials_are_403_and_dependency_failures_503():
    c, v = client(); c.post("/vault/tokenize", headers=H(GW), json=BODY)
    assert c.post("/vault/rehydrate", headers=H(GW), json={**REH, "destination": "llm"}).status_code == 403
    class Down:
        def check(self, *a, **k): raise ConnectionError("x")
    v.ctx.permissions = Down(); assert c.post("/vault/rehydrate", headers=H(GW), json=REH).status_code == 503
def test_denied_and_missing_tokens_are_indistinguishable_over_http():
    c, _ = client(); c.post("/vault/tokenize", headers=H(GW), json=BODY)
    a = c.post("/vault/rehydrate", headers=H(GW), json={**REH, "tokens": ["<PHONE_9>"]}); b = c.post("/vault/rehydrate", headers=H(GW), json={**REH, "borrower_id": "OTHER"})
    strip = lambda r: {k: v for k, v in r.json().items() if k != "request_id"}          # the per-request correlation id is random, not information
    assert a.status_code == b.status_code == 403 and strip(a) == strip(b) and "request_id" in a.json()
def test_rotate_endpoint_and_health():
    import os
    s, v = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}); c = TestClient(create_app(v)); c.post("/vault/tokenize", headers=H(GW), json=BODY)
    r = c.post("/vault/rotate-key", headers=H(KO), json={"target_key_version": 2}); assert r.status_code == 200 and r.json()["keys_rewrapped"] == 1
    assert c.post("/vault/rehydrate", headers=H(GW), json=REH).json()["values"]
    assert c.post("/vault/rotate-key", headers=H(KO), json={"target_key_version": 7}).status_code == 422
def test_docs_and_openapi_are_not_exposed():
    c, _ = client(); assert all(c.get(p).status_code == 404 for p in ("/docs", "/redoc", "/openapi.json"))
def test_access_log_has_no_bodies():
    import io
    buf = io.StringIO(); h = logging.StreamHandler(buf); lg = logging.getLogger("te03.vault.api"); lg.setLevel(logging.INFO); lg.addHandler(h)
    try:
        c, _ = client(); c.post("/vault/tokenize", headers=H(GW), json=BODY); c.post("/vault/tokenize", headers=H(GW), json={"bad": "98450 12345"})
    finally: lg.removeHandler(h)
    out = buf.getvalue(); assert "/vault/tokenize" in out and "98450" not in out and "collections" not in out

if __name__ == "__main__":
    run(globals())
