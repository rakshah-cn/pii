"""Rehydration: deny by default; every denial path; atomicity; fail closed when a dependency is down."""
from layer2_vault.core.errors import VaultDenied, VaultUnavailable
from layer2_vault.tests._util import AU, ER, GW, LI, expect, make, reh_req, run, tok_req, who

PH = "9845012345"


def setup(**kw):
    s, v = make(**kw); g = who(s, GW)
    v.tokenizer.tokenize(tok_req(f"call {PH} Rahul", [("PHONE", PH), ("NAME", "Rahul")]), g)
    return s, v, g


def test_authorised_rehydration_returns_only_the_requested_value():
    s, v, g = setup(); r = v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g); assert r.values == {"<PHONE_1>": PH}
def test_unicode_and_name_roundtrip():
    s, v, g = setup(); assert v.rehydration.rehydrate(reh_req(["<NAME_1>"]), g).values == {"<NAME_1>": "Rahul"}
def test_invalid_and_unknown_tokens_denied():
    s, v, g = setup(); expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_9>"]), g), "token_not_found")
def test_wrong_conversation_wrong_borrower_wrong_purpose_all_give_the_SAME_denial():
    s, v, g = setup(); codes = set()
    for kw in ({"conv": "C2"}, {"borrower": "B2"}, {"purpose": "service"}):
        codes.add(expect(VaultDenied, lambda kw=kw: v.rehydration.rehydrate(reh_req(["<PHONE_1>"], **kw), g)).code)
    assert codes == {"token_not_found"}, codes                  # a caller cannot tell 'missing' from 'someone else's'
def test_destination_not_in_policy_denied_and_empty_policy_denies_everything():
    s, v, g = setup(); expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"], dest="llm_provider"), g), "purpose_or_destination_not_permitted")
    s2, v2, g2 = setup(rehydrate_policy={}); expect(VaultDenied, lambda: v2.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g2))
def test_callers_without_the_capability_cannot_rehydrate():
    s, v, g = setup()
    for t in (ER, LI, AU):
        expect(VaultDenied, lambda t=t: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), who(s, t)), "forbidden")
class _Switch:
    """allows tokenising (so setup works) but behaves as configured for rehydration"""
    def __init__(self, mode): self.mode = mode
    def check(self, action, *a, **k):
        if action == "tokenize": return True
        if self.mode == "down": raise ConnectionError("permission service down")
        return self.mode == "yes"


def test_permission_service_unavailable_denies_never_bypasses():
    s, v, g = setup(permissions=_Switch("down"))
    expect(VaultUnavailable, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "permission_unavailable")
def test_a_permission_service_saying_no_is_enforced():
    s, v, g = setup(permissions=_Switch("no")); expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g))
    s2, v2, g2 = setup(permissions=_Switch("yes")); assert v2.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g2).values
def test_request_is_atomic_one_bad_token_returns_nothing():
    s, v, g = setup(); e = expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>", "<PHONE_7>"]), g)); assert "9845" not in str(e)
def test_too_many_tokens_denied():
    s, v, g = setup(max_tokens_per_rehydrate=1); expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>", "<NAME_1>"]), g), "too_many_tokens")
def test_revoked_key_makes_rehydration_impossible_and_tokenise_refuses_to_mint_a_new_key():
    s, v, g = setup()
    with v.ctx.db.transaction() as tx: v.lifecycle.revoke(tx, "B1")
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "key_not_active")
    expect(Exception, lambda: v.tokenizer.tokenize(tok_req("call 9811111111", [("PHONE", "9811111111")], rid="n"), g))
    with v.ctx.db.transaction() as tx: assert v.ctx.repo.active_key(tx, "B1") is None
def test_audit_sink_failure_on_the_decrypt_path_refuses_and_returns_no_plaintext():
    class BadSink:
        def __call__(self, e): raise OSError("disk full")
    s, v, g = setup(events=BadSink()); e = expect(VaultUnavailable, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "audit_unavailable"); assert PH not in str(e)
def test_denials_are_recorded_as_safe_metadata():
    s, v, g = setup(); expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_9>"], rid="deny-1"), g))
    with v.ctx.db.transaction() as tx: ops = v.ctx.repo.operations(tx, "B1")
    d = [o for o in ops if o["request_id"] == "deny-1"][0]; assert d["status"] == "DENIED" and d["error_code"] == "token_not_found" and PH not in str(ops)
def test_metadata_never_contains_plaintext_or_ciphertext():
    s, v, g = setup(); rows = v.tokenizer.list_for_borrower("B1", who(s, ER))
    assert rows and all("ciphertext" not in r and "nonce" not in r for r in rows) and PH not in str(rows)
    m = v.tokenizer.get_metadata(rows[0]["vault_id"], who(s, LI)); assert PH not in m.model_dump_json() and m.key_status == "ACTIVE"

if __name__ == "__main__":
    run(globals())
