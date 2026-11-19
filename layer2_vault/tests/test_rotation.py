"""Key rotation and migration: re-wrap every DEK, re-index every blind index, atomically; old versions can then be retired."""
import os
import tempfile

from layer2_vault.config.settings import Settings
from layer2_vault.core.errors import ValidationFailed, VaultDenied, VaultUnavailable
from layer2_vault.schemas.vault_schemas import RotateRequest
from layer2_vault.services import build_services
from layer2_vault.tests._util import GW, KO, expect, make, reh_req, run, tok_req, who

PH = "9845012345"


def test_rotation_rewraps_reindexes_and_survives_retiring_the_old_secret():
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/v.db"; m1, m2 = os.urandom(32), os.urandom(32)
        s, v = make(master_secrets={1: m1, 2: m2}, current_key_version=1, db_url=url); g, ko = who(s, GW), who(s, KO)
        t1 = v.tokenizer.tokenize(tok_req(f"call {PH}", [("PHONE", PH)], rid="a"), g).mappings[0]
        v.tokenizer.tokenize(tok_req("c 9811111111", [("PHONE", "9811111111")], borrower="B2", conv="C2", rid="b"), g)
        r = v.lifecycle.rotate(RotateRequest(target_key_version=2), ko)
        assert (r.keys_rewrapped, r.records_reindexed, r.current_key_version) == (2, 2, 2)
        with v.ctx.db.transaction() as tx: assert {k["kek_version"] for k in v.ctx.repo.all_keys(tx)} == {2}
        # a NEW process that only knows secret 2: data still readable, same value still reuses its token (index migrated)
        s2 = Settings.for_testing(master_secrets={2: m2}, current_key_version=2, db_url=url); v2 = build_services(s2); g2 = who(s2, GW)
        assert v2.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g2).values == {"<PHONE_1>": PH}
        again = v2.tokenizer.tokenize(tok_req(f"call {PH}", [("PHONE", PH)], rid="c"), g2).mappings[0]
        assert again.vault_id == t1.vault_id and again.token == t1.token
        # the OLD secret alone can no longer open anything
        s3 = Settings.for_testing(master_secrets={1: m1}, current_key_version=1, db_url=url); v3 = build_services(s3)
        expect(VaultUnavailable, lambda: v3.rehydration.rehydrate(reh_req(["<PHONE_1>"]), who(s3, GW)), "master_key_unavailable")      # fail closed: the DEK needs a secret this process does not hold
def test_rotation_needs_the_capability_and_a_loaded_target_version():
    s, v = make(); expect(VaultDenied, lambda: v.lifecycle.rotate(RotateRequest(target_key_version=2), who(s, GW)), "forbidden")
    expect(ValidationFailed, lambda: v.lifecycle.rotate(RotateRequest(target_key_version=9), who(s, KO)), "target_version_unavailable")
def test_destroyed_keys_are_not_resurrected_by_rotation():
    s, v = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}); g = who(s, GW)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    with v.ctx.db.transaction() as tx: v.lifecycle.destroy(tx, "B1")
    r = v.lifecycle.rotate(RotateRequest(target_key_version=2), who(s, KO)); assert r.keys_rewrapped == 0
    with v.ctx.db.transaction() as tx: assert v.ctx.repo.latest_key(tx, "B1")["wrapped_dek"] is None
def test_revoked_keys_are_rewrapped_but_stay_revoked():
    s, v = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}); g = who(s, GW)
    v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)]), g)
    with v.ctx.db.transaction() as tx: v.lifecycle.revoke(tx, "B1")
    assert v.lifecycle.rotate(RotateRequest(target_key_version=2), who(s, KO)).keys_rewrapped == 1
    expect(VaultDenied, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g))
def test_a_key_that_hit_its_usage_limit_demands_rotation_instead_of_reusing_nonces():
    s, v = make(key_usage_limit=3); g = who(s, GW)
    for i in range(3): v.tokenizer.tokenize(tok_req(f"9811{i:06d}", [("PHONE", f"9811{i:06d}")], rid=f"r{i}"), g)
    expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req("9822222222", [("PHONE", "9822222222")], rid="x"), g), "key_usage_limit_reached")
    assert v.tokenizer.tokenize(tok_req("9811000000", [("PHONE", "9811000000")], rid="again"), g).mappings[0].token == "<PHONE_1>"   # existing values still reuse

if __name__ == "__main__":
    run(globals())
