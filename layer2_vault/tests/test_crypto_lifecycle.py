"""Phase 2: nonce uniqueness as an enforced invariant, and key management (versions, KEK vs DEK rotation, key loss) - always failing closed."""
import base64
import json
import os
import tempfile
import threading

from layer2_vault.config.settings import Settings
from layer2_vault.core import encryption as enc
from layer2_vault.core.errors import DecryptionError, KeyStateError, ValidationFailed, VaultDenied, VaultUnavailable
from layer2_vault.core.master_key_provider import AwsKmsMasterKeyProvider, kms_context, seal_master_secret
from layer2_vault.core.nonce import NonceSource
from layer2_vault.database.connection import ConflictError
from layer2_vault.schemas.vault_schemas import EraseRequest, RotateRequest
from layer2_vault.services import build_services
from layer2_vault.tests._util import ER, GW, KO, expect, make, reh_req, run, tok_req, who

PH = "9845012345"
vals = lambda n, base=9800000000: [str(base + i) for i in range(n)]


def q(v, sql, p=()):
    with v.ctx.db.transaction() as tx:
        return tx.query(sql, p)


def fill(v, g, n, borrower="B1", conv="C1", base=9800000000, start=0):
    for i, x in enumerate(vals(n, base)):
        v.tokenizer.tokenize(tok_req(x, [("PHONE", x)], borrower=borrower, conv=conv, rid=f"r{start + i}"), g)


def nonces(v, key_id=None):
    rows = q(v, "SELECT nonce, key_id FROM vault_records" + (" WHERE key_id = ?" if key_id else ""), (key_id,) if key_id else ())
    return [bytes(r["nonce"]) for r in rows]


# ---------------------------------------------------------------- nonce invariant
def test_no_nonce_is_reused_under_one_dek_and_counters_are_consecutive():
    s, v = make(); g = who(s, GW); fill(v, g, 120)
    ns = nonces(v); assert len(ns) == len(set(ns)) == 120
    assert sorted(NonceSource.counter_of(n) for n in ns) == list(range(120)) and len({n[:4] for n in ns}) == 1
    st = q(v, "SELECT next_counter FROM key_nonce_state")[0]["next_counter"]; assert st == 120 == q(v, "SELECT usage_count FROM borrower_keys")[0]["usage_count"]
    expect(ConflictError, lambda: _dup_insert(v, ns[0]))                                   # the database refuses a repeated (key, nonce) outright


def _dup_insert(v, nonce):
    with v.ctx.db.transaction() as tx:
        k = tx.one("SELECT key_id FROM borrower_keys")["key_id"]
        tx.execute("INSERT INTO vault_records (vault_id, borrower_id, conversation_id, token_id, pii_type, ciphertext, nonce, key_id, purpose_id, status, created_at) "
                   "VALUES ('v_dup','B1','C1','t_dup','PHONE', ?, ?, ?, 'collections','ACTIVE', 1)", (b"\x01" * 17, nonce, k))


def test_concurrent_encryption_never_hands_out_a_duplicate_nonce():
    with tempfile.TemporaryDirectory() as d:
        url = None if os.environ.get("VAULT_TEST_DB_URL") else f"sqlite:///{d}/c.db"
        s, v = make(**({"db_url": url} if url else {})); g = who(s, GW); fill(v, g, 1)
        # a SECOND service object (its own epoch, like another process) on the same database
        v2 = build_services(Settings.for_testing(master_secrets=s.master_secrets, db_url=s.db_url, registry=s.registry, rehydrate_policy=s.rehydrate_policy))
        errs = []

        def work(svc, k):
            try:
                for i, x in enumerate(vals(20, 9700000000 + k * 100)):
                    svc.tokenizer.tokenize(tok_req(x, [("PHONE", x)], conv=f"T{k}", rid=f"t{k}-{i}"), g)
            except Exception as e:
                errs.append(repr(e))
        ts = [threading.Thread(target=work, args=(v if k % 2 else v2, k)) for k in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        assert not errs, errs[:2]
        ns = nonces(v); assert len(ns) == 161 == len(set(ns))
        assert sorted(NonceSource.counter_of(n) for n in ns) == list(range(161)), "counters are consecutive: no gaps, no duplicates, across both 'processes'"
        assert len({n[:4] for n in ns}) == 2, "two service objects used two different epochs"


def test_the_counter_survives_a_restart_and_the_new_process_uses_a_new_epoch():
    with tempfile.TemporaryDirectory() as d:
        s, v = make(**({} if os.environ.get("VAULT_TEST_DB_URL") else {"db_url": f"sqlite:///{d}/r.db"})); g = who(s, GW); fill(v, g, 3)
        v2 = build_services(Settings.for_testing(master_secrets=s.master_secrets, db_url=s.db_url, registry=s.registry, rehydrate_policy=s.rehydrate_policy))
        fill(v2, g, 2, base=9500000000, start=10)
        rows = q(v2, "SELECT nonce FROM vault_records"); cs = sorted(NonceSource.counter_of(r["nonce"]) for r in rows)
        assert cs == [0, 1, 2, 3, 4], "the second process continued from the stored counter, it did not restart at 0"
        assert v.ctx.nonces.epoch != v2.ctx.nonces.epoch


def test_a_restored_older_backup_does_not_repeat_a_nonce_that_was_already_used():
    s, v = make(); g = who(s, GW); fill(v, g, 5)
    fill(v, g, 3, base=9600000000, start=20)                                              # these 3 are written after the 'backup'
    lost = {bytes(n) for n in nonces(v)[5:]}
    with v.ctx.db.transaction() as tx:                                                   # emulate restoring the backup: counter and rows go back to the 5-record state
        k = tx.one("SELECT key_id FROM borrower_keys")["key_id"]
        for n in lost:
            vid = tx.one("SELECT vault_id FROM vault_records WHERE nonce = ?", (n,))["vault_id"]
            tx.execute("DELETE FROM blind_indexes WHERE vault_id = ?", (vid,)); tx.execute("DELETE FROM token_mappings WHERE vault_id = ?", (vid,)); tx.execute("DELETE FROM vault_records WHERE vault_id = ?", (vid,))
        tx.execute("UPDATE key_nonce_state SET next_counter = 5 WHERE key_id = ?", (k,))
    assert q(v, "SELECT next_counter FROM key_nonce_state")[0]["next_counter"] == 5, "precondition: the counter really went backwards"
    v2 = build_services(Settings.for_testing(master_secrets=s.master_secrets, db_url=s.db_url, registry=s.registry, rehydrate_policy=s.rehydrate_policy))      # the vault is restarted after a restore
    fill(v2, g, 3, base=9400000000, start=40)
    fresh = {bytes(n) for n in nonces(v2)[5:]}
    assert not (fresh & lost), "no nonce issued before the restore was issued again after it (counters repeat, but the epoch differs)"
    assert {n[:4] for n in fresh}.isdisjoint({n[:4] for n in lost}), "that is guaranteed by the random epoch - probabilistically (see core/nonce.py): rotate DEKs after a restore"


def test_dek_rotation_starts_a_new_nonce_sequence_under_a_new_key():
    s, v = make(); g, ko = who(s, GW), who(s, KO); fill(v, g, 4)
    old = q(v, "SELECT key_id FROM borrower_keys")[0]["key_id"]
    r = v.lifecycle.rotate(RotateRequest(target_key_version=1, operation="DEK"), ko)
    assert (r.status, r.deks_rotated, r.records_reencrypted, r.borrowers_failed) == ("success", 1, 4, 0)
    keys = {k["key_id"]: k for k in q(v, "SELECT * FROM borrower_keys")}
    new = next(k for k in keys if k != old)
    assert keys[old]["key_status"] == "DESTROYED" and keys[old]["destroy_reason"] == "ROTATION" and keys[old]["wrapped_dek"] is None
    assert keys[new]["key_status"] == "ACTIVE" and keys[new]["dek_version"] == 2
    assert sorted(NonceSource.counter_of(n) for n in nonces(v, new)) == [0, 1, 2, 3] and nonces(v, old) == []
    assert q(v, "SELECT next_counter FROM key_nonce_state WHERE key_id = ?", (new,))[0]["next_counter"] == 4


def test_nonce_exhaustion_blocks_further_encryption_but_not_reads():
    s, v = make(key_usage_limit=3); g = who(s, GW); fill(v, g, 3)
    e = expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req("9811111111", [("PHONE", "9811111111")], rid="x"), g), "key_usage_limit_reached")
    assert q(v, "SELECT next_counter FROM key_nonce_state")[0]["next_counter"] == 3 and len(nonces(v)) == 3, "nothing was reserved or stored past the limit"
    t = q(v, "SELECT token FROM token_mappings ORDER BY seq")[0]["token"]
    assert v.rehydration.rehydrate(reh_req([t]), g).values                                # existing data stays readable
    expect(VaultDenied, lambda: v.internal.extend("C1", t, g, joined="98000000001"), "key_usage_limit_reached")


def test_encrypt_accepts_only_a_12_byte_nonce_and_uses_it():
    K, A = os.urandom(32), b"aad"
    n = NonceSource(b"\x00\x00\x00\x07").nonce(9); got, ct = enc.encrypt(K, "x", A, nonce=n)
    assert got == n and enc.decrypt(K, n, ct, A) == "x"
    expect(ValidationFailed, lambda: enc.encrypt(K, "x", A, nonce=b"short"))
    expect(OverflowError, lambda: NonceSource().nonce(1 << 64))


# ---------------------------------------------------------------- key versions and rotation kinds
def test_an_old_key_version_still_decrypts_during_migration_and_new_encryption_uses_the_active_one():
    s, v = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}, current_key_version=1); g = who(s, GW)
    fill(v, g, 1, borrower="OLD")
    v.ctx.keys.current_version = 2                                                       # new writes use version 2, old data not yet migrated
    fill(v, g, 1, borrower="NEW", conv="C2", base=9300000000, start=5)
    ver = {k["borrower_id"]: k["kek_version"] for k in q(v, "SELECT borrower_id, kek_version FROM borrower_keys")}
    assert ver == {"OLD": 1, "NEW": 2}
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"], borrower="OLD"), g).values and v.rehydration.rehydrate(reh_req(["<PHONE_1>"], borrower="NEW", conv="C2"), g).values
    assert {r["index_version"] for r in q(v, "SELECT index_version FROM blind_indexes WHERE borrower_id = 'NEW'")} == {2}


def test_kek_rotation_rewraps_dek_without_decrypting_any_pii_and_keeps_the_dek_unchanged():
    s, v = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}, current_key_version=1); g, ko = who(s, GW), who(s, KO); fill(v, g, 3)
    k0 = q(v, "SELECT * FROM borrower_keys")[0]; dek0 = v.ctx.keys.unwrap(k0["wrapped_dek"], k0["wrap_nonce"], "B1", k0["key_id"], 1)
    ct0 = q(v, "SELECT ciphertext, nonce FROM vault_records ORDER BY vault_id")
    real_dec, real_recs, real_act = enc.decrypt, v.ctx.repo.records_for, v.ctx.repo.active_records
    def forbidden(*a, **k): raise AssertionError("KEK rotation touched PII")
    enc.decrypt, v.ctx.repo.records_for, v.ctx.repo.active_records = forbidden, forbidden, forbidden
    try:
        r = v.lifecycle.rotate(RotateRequest(target_key_version=2, operation="KEK"), ko)
    finally:
        enc.decrypt, v.ctx.repo.records_for, v.ctx.repo.active_records = real_dec, real_recs, real_act
    assert (r.operation, r.keys_rewrapped, r.records_reindexed) == ("KEK", 1, 0)
    k1 = q(v, "SELECT * FROM borrower_keys")[0]
    assert k1["kek_version"] == 2 and k1["wrapped_dek"] != k0["wrapped_dek"] and k1["key_id"] == k0["key_id"] and k1["dek_version"] == 1
    assert v.ctx.keys.unwrap(k1["wrapped_dek"], k1["wrap_nonce"], "B1", k1["key_id"], 2) == dek0, "same DEK, new wrapper"
    assert q(v, "SELECT ciphertext, nonce FROM vault_records ORDER BY vault_id") == ct0, "records were not re-encrypted"
    assert v.ctx.keys.current_kek_version == 2 and v.ctx.keys.current_blind_version == 1, "blind-index version moves separately"
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g).values
    again = v.tokenizer.tokenize(tok_req("9800000000", [("PHONE", "9800000000")], rid="z"), g).mappings[0]
    assert again.token == "<PHONE_1>", "lookup still works: indexes were left under version 1 and dual-read finds them"


def test_blind_index_rotation_moves_only_the_digests():
    s, v = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}, current_key_version=1); g, ko = who(s, GW), who(s, KO); fill(v, g, 2)
    r = v.lifecycle.rotate(RotateRequest(target_key_version=2, operation="BLIND_INDEX"), ko)
    assert (r.keys_rewrapped, r.records_reindexed) == (0, 2) and {x["index_version"] for x in q(v, "SELECT index_version FROM blind_indexes")} == {2}
    assert q(v, "SELECT kek_version FROM borrower_keys")[0]["kek_version"] == 1 and v.ctx.keys.current_kek_version == 1 and v.ctx.keys.current_blind_version == 2


def test_dek_rotation_keeps_every_value_and_the_token_lookup_intact():
    s, v = make(); g, ko = who(s, GW), who(s, KO); fill(v, g, 3)
    before = {r["token"]: r for r in q(v, "SELECT m.token, r.ciphertext, r.nonce, r.key_id FROM vault_records r JOIN token_mappings m ON m.vault_id = r.vault_id")}
    v.lifecycle.rotate(RotateRequest(target_key_version=1, operation="DEK"), ko)
    after = {r["token"]: r for r in q(v, "SELECT m.token, r.ciphertext, r.nonce, r.key_id FROM vault_records r JOIN token_mappings m ON m.vault_id = r.vault_id")}
    assert all(after[t]["key_id"] != before[t]["key_id"] and after[t]["ciphertext"] != before[t]["ciphertext"] for t in before)
    assert v.rehydration.rehydrate(reh_req(list(before)), g).values == dict(zip(sorted(before), vals(3)))
    assert v.tokenizer.tokenize(tok_req("9800000001", [("PHONE", "9800000001")], rid="again"), g).mappings[0].token == "<PHONE_2>"
    # tamper after rotation is still detected (AAD carries the NEW key id)
    with v.ctx.db.transaction() as tx: tx.execute("UPDATE vault_records SET ciphertext = ? WHERE key_id = ?", (b"\x00" * 17, after["<PHONE_1>"]["key_id"]))
    expect(DecryptionError, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g))


def test_dek_rotation_failing_halfway_leaves_the_old_key_active_and_is_retryable():
    s, v = make(); g, ko = who(s, GW), who(s, KO); fill(v, g, 4); fill(v, g, 2, borrower="B2", conv="C2", base=9200000000, start=9)
    old = {k["borrower_id"]: k["key_id"] for k in q(v, "SELECT borrower_id, key_id FROM borrower_keys")}
    def boom(step):
        if step == "dek_mid" and not getattr(boom, "fired", False):
            boom.fired = True; raise RuntimeError("crash halfway through the first borrower")
    v.lifecycle.fault = boom
    r = v.lifecycle.rotate(RotateRequest(target_key_version=1, operation="DEK"), ko)
    assert r.status == "partial_failure" and r.borrowers_failed == 1 and r.deks_rotated == 1 and r.target_dek_version == 2 and "retry" in r.note.lower()
    st = {k["borrower_id"]: (k["key_id"], k["key_status"], k["dek_version"]) for k in q(v, "SELECT * FROM borrower_keys WHERE key_status = 'ACTIVE'")}
    assert st["B1"][0] == old["B1"] and st["B1"][2] == 1, "the failed borrower was rolled back to its old, intact DEK"
    assert st["B2"][2] == 2
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g).values and v.rehydration.rehydrate(reh_req(["<PHONE_1>"], borrower="B2", conv="C2"), g).values
    v.lifecycle.fault = None
    r2 = v.lifecycle.rotate(RotateRequest(target_key_version=1, operation="DEK", target_dek_version=2), ko)
    assert (r2.status, r2.deks_rotated, r2.borrowers_failed) == ("success", 1, 0), "the retry rotated only the borrower that had failed"
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g).values


def test_dek_rotation_skips_a_borrower_that_is_being_erased():
    s, v = make(); g, ko = who(s, GW), who(s, KO); fill(v, g, 2)
    with v.ctx.db.transaction() as tx: v.ctx.repo.set_borrower_state(tx, "B1", "ERASURE_PENDING")
    r = v.lifecycle.rotate(RotateRequest(target_key_version=1, operation="DEK"), ko); assert r.deks_rotated == 0 and r.borrowers_failed == 0


# ---------------------------------------------------------------- key loss: everything fails closed
def test_a_destroyed_key_cannot_be_restored_by_the_application_or_by_sql():
    s, v = make(); g = who(s, GW); fill(v, g, 1)
    v.deletion.erase(EraseRequest(job_id="j", borrower_id="B1"), who(s, ER))
    kid = q(v, "SELECT key_id FROM borrower_keys")[0]["key_id"]
    with v.ctx.db.transaction() as tx: v.ctx.repo.set_borrower_state(tx, "B1", "ACTIVE")                 # even if someone re-opens the borrower
    expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req(PH, [("PHONE", PH)], rid="n"), g), "key_not_active")       # an erased borrower never gets a new key
    for sql, p in (("UPDATE borrower_keys SET key_status = 'ACTIVE' WHERE key_id = ?", (kid,)),
                   ("UPDATE borrower_keys SET key_status = 'REVOKED' WHERE key_id = ?", (kid,)),
                   ("UPDATE borrower_keys SET wrapped_dek = ?, wrap_nonce = ? WHERE key_id = ?", (b"\x01" * 48, b"\x02" * 12, kid))):
        def attempt(sql=sql, p=p):
            with v.ctx.db.transaction() as tx: tx.execute(sql, p)
        expect(Exception, attempt)                                                          # database trigger / CHECK constraint: a destroyed key is final
    k = q(v, "SELECT key_status, wrapped_dek FROM borrower_keys")[0]; assert (k["key_status"], k["wrapped_dek"]) == ("DESTROYED", None)


def test_missing_master_key_blocks_encryption_and_rehydration_and_health_says_so():
    s, v = make(); g = who(s, GW); fill(v, g, 1)
    saved = v.ctx.keys._masters.pop(1)                                                       # the master secret is no longer available
    expect(VaultUnavailable, lambda: v.tokenizer.tokenize(tok_req("9811111111", [("PHONE", "9811111111")], rid="a"), g), "master_key_unavailable")
    expect(VaultUnavailable, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "master_key_unavailable")
    assert v.tokenizer.health()["status"] == "degraded" and v.tokenizer.health()["key_material"] is False
    assert len(nonces(v)) == 1, "nothing was stored while the key was missing"
    v.ctx.keys._masters[1] = saved
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g).values


def test_a_revoked_key_version_fails_closed_and_cannot_be_a_rotation_target():
    s, v = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}, current_key_version=1); g, ko = who(s, GW), who(s, KO); fill(v, g, 1)
    v.ctx.keys.current_version = 2; v.ctx.keys.revoke_version(1)                             # version 1 revoked while this borrower's DEK is still wrapped under it
    expect(KeyStateError, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g), "key_version_revoked")
    expect(KeyStateError, lambda: v.tokenizer.tokenize(tok_req("9811111111", [("PHONE", "9811111111")], rid="b"), g), "key_version_revoked")
    expect(ValidationFailed, lambda: v.lifecycle.rotate(RotateRequest(target_key_version=1), ko), "target_version_unavailable")
    expect(ValidationFailed, lambda: v.ctx.keys.revoke_version(2), "cannot_revoke_current_version")
    expect(ValidationFailed, lambda: Settings.for_testing(master_secrets={1: os.urandom(32)}, revoked_key_versions=frozenset({1})), "current_key_version_revoked")
    # the right order: migrate first, then revoke
    s2, v2 = make(master_secrets={1: os.urandom(32), 2: os.urandom(32)}, current_key_version=1); g2 = who(s2, GW); fill(v2, g2, 1)
    v2.lifecycle.rotate(RotateRequest(target_key_version=2), who(s2, KO)); v2.ctx.keys.revoke_version(1)      # ALL = KEK + blind index: both moved off version 1
    assert v2.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g2).values


def test_a_dek_that_fails_to_unwrap_stops_everything_and_never_mints_a_replacement():
    s, v = make(); g = who(s, GW); fill(v, g, 1)
    with v.ctx.db.transaction() as tx: tx.execute("UPDATE borrower_keys SET wrapped_dek = ?", (b"\x00" * 48,))     # corrupted wrapped key
    expect(DecryptionError, lambda: v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g))
    expect(Exception, lambda: v.tokenizer.tokenize(tok_req("9811111111", [("PHONE", "9811111111")], rid="c"), g))
    assert len(q(v, "SELECT 1 FROM borrower_keys")) == 1, "no replacement key was created: a corrupt key is an incident, not something to paper over"


def test_a_restored_backup_with_an_unloaded_master_version_stays_unreadable():
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/b.db"; m1, m2 = os.urandom(32), os.urandom(32)
        s, v = make(master_secrets={1: m1, 2: m2}, current_key_version=1, db_url=url); g = who(s, GW); fill(v, g, 1)
        import shutil; v.ctx.db._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)"); shutil.copy(f"{d}/b.db", f"{d}/backup.db")                              # backup: DEK wrapped under master v1
        v.lifecycle.rotate(RotateRequest(target_key_version=2, operation="KEK"), who(s, KO))
        s2 = Settings.for_testing(master_secrets={2: m2}, current_key_version=2, db_url=f"sqlite:///{d}/backup.db"); v2 = build_services(s2)      # master v1 retired, backup restored
        expect(VaultUnavailable, lambda: v2.rehydration.rehydrate(reh_req(["<PHONE_1>"]), who(s2, GW)), "master_key_unavailable")


# ---------------------------------------------------------------- managed-key provider (fake KMS)
class FakeKms:
    """Stands in for boto3's KMS client: 'encrypts' by binding the plaintext to the key id and the encryption context."""
    def __init__(self, down=False): self.down = down
    def encrypt(self, KeyId, Plaintext, EncryptionContext):
        return {"CiphertextBlob": json.dumps({"k": KeyId, "c": EncryptionContext, "p": base64.b64encode(Plaintext).decode()}).encode()}
    def decrypt(self, CiphertextBlob, EncryptionContext, KeyId=None):
        if self.down: raise ConnectionError("kms.eu-west-1.amazonaws.com unreachable arn:aws:kms:000000000000:key/secret-id")
        d = json.loads(CiphertextBlob)
        if d["c"] != EncryptionContext or (KeyId and d["k"] != KeyId): raise ValueError("InvalidCiphertextException")
        return {"Plaintext": base64.b64decode(d["p"])}


def test_the_kms_provider_unseals_the_master_secret_and_fails_closed_otherwise():
    kms, m1, m2 = FakeKms(), os.urandom(32), os.urandom(32)
    b1, b2 = seal_master_secret(kms, "key-a", 1, m1), seal_master_secret(kms, "key-a", 2, m2)
    assert AwsKmsMasterKeyProvider({1: b1, 2: b2}, kms, "key-a").load() == {1: m1, 2: m2}
    expect(VaultUnavailable, lambda: AwsKmsMasterKeyProvider({1: b1}, FakeKms(down=True), "key-a").load(), "master_key_unavailable")
    expect(VaultUnavailable, lambda: AwsKmsMasterKeyProvider({2: b1}, kms, "key-a").load(), "master_key_unavailable")          # blob sealed for v1 replayed as v2: context mismatch
    expect(VaultUnavailable, lambda: AwsKmsMasterKeyProvider({1: b1}, kms, "other-key").load(), "master_key_unavailable")
    e = expect(VaultUnavailable, lambda: AwsKmsMasterKeyProvider({1: b1}, FakeKms(down=True), "key-a").load()); assert "amazonaws" not in str(e) and "arn:" not in str(e)
    expect(ValidationFailed, lambda: AwsKmsMasterKeyProvider({1: seal_master_secret(kms, "key-a", 1, b"short")}, kms, "key-a").load(), "master_secret_invalid_or_too_short")
    expect(ValidationFailed, lambda: AwsKmsMasterKeyProvider({}, kms))
    env = {"VAULT_KMS_CIPHERTEXT_V1": b1, "VAULT_KMS_KEY_ID": "key-a", "VAULT_SERVICE_TOKENS": "{}", "VAULT_DB_URL": "sqlite:///:memory:"}
    st = Settings.from_env(env, kms_client=kms); assert st.master_secrets == {1: m1} and "VAULT_MASTER_KEY" not in env
    expect(VaultUnavailable, lambda: Settings.from_env(env, kms_client=FakeKms(down=True)), "master_key_unavailable")           # cannot reach KMS: the vault does not start


def test_the_kms_smoke_test_script_logic_passes_against_the_fake_client_and_would_flag_a_kms_that_accepts_bad_blobs():
    from layer2_vault.ops import kms_smoke_test as smoke
    class Down(FakeKms):
        def decrypt(self, **kw): raise ConnectionError("endpoint http://kms.example unreachable key-id-secret")
    lines = []
    res = smoke.run(FakeKms(), "key-a", make_unreachable=lambda: Down(), out=lines.append)
    assert len(res) == 8 and all(ok for _, ok, _ in res), res
    assert not any("kms.example" in l or "key-id-secret" in l for l in lines), "no endpoint or key id leaks into the output"
    class Lax(FakeKms):                                                                     # a broken 'KMS' that ignores the encryption context
        def decrypt(self, CiphertextBlob, EncryptionContext, KeyId=None): return super().decrypt(CiphertextBlob, json.loads(CiphertextBlob)["c"], KeyId)
    bad = smoke.run(Lax(), "key-a", out=lambda *_: None)
    assert any(not ok for _, ok, _ in bad), "the script must FAIL when the service does not enforce the encryption context"


def test_rotate_request_validates_the_operation():
    assert RotateRequest(target_key_version=2).operation == "ALL"
    for bad in ({"operation": "ALL_AT_ONCE"}, {"target_dek_version": 1}, {"unknown": 1}):
        expect(Exception, lambda b=bad: RotateRequest(target_key_version=2, **b))


if __name__ == "__main__":
    run(globals())
