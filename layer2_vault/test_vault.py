"""Vault tests. Run from the project root:   python -m layer2_vault.test_vault"""
import json
import os
import shutil
import tempfile

from layer2_vault.vault import RateLimited, Vault, VaultError

KEY = bytes(range(32))


def test_roundtrip_and_same_value_same_token():
    v = Vault(KEY)
    a = v.put("c1", "PHONE", "98450 12345")
    b = v.put("c1", "PHONE", "9845012345")                 # separators ignored for dedupe
    c = v.put("c1", "PHONE", "70000 11111")
    assert a == b == "<PHONE_1>" and c == "<PHONE_2>"
    assert v.get("c1", a) == "98450 12345" and v.tokens("c1") == ["<PHONE_1>", "<PHONE_2>"]


def test_ciphertext_holds_no_plaintext_and_token_not_derived_from_value():
    v = Vault(KEY)
    v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
    blob = json.dumps(list(v._rows["c1"].values()))
    assert "98450" not in blob and "9845012345" not in blob
    # same value in another conversation gets a counter token and a different blind index
    v.put("c2", "PHONE", "98450 12345", borrower_id="b1")
    assert v._rows["c1"]["<PHONE_1>"]["blind"] != v._rows["c2"]["<PHONE_1>"]["blind"]
    assert v._rows["c1"]["<PHONE_1>"]["ct"] != v._rows["c2"]["<PHONE_1>"]["ct"]


def test_tamper_detected_and_swap_detected():
    v = Vault(KEY)
    t1 = v.put("c1", "PHONE", "98450 12345")
    t2 = v.put("c1", "PAN", "ABCDE1234F")
    r1, r2 = v._rows["c1"][t1], v._rows["c1"][t2]
    r1["ct"], r2["ct"] = r2["ct"], r1["ct"]                 # swap ciphertexts: associated data must catch it
    r1["nonce"], r2["nonce"] = r2["nonce"], r1["nonce"]
    for t in (t1, t2):
        try:
            v.get("c1", t)
            assert False, "swapped ciphertext must not decrypt"
        except VaultError:
            pass


def test_get_requires_rehydration_role_and_logs_no_raw():
    v = Vault(KEY)
    tok = v.put("c1", "PHONE", "98450 12345", purpose="collections", consent_id="cons-1")
    try:
        v.get("c1", tok, role="logger")
        assert False
    except PermissionError:
        pass
    assert v.get("c1", tok) == "98450 12345"
    log = v.access_log()
    assert any(e["action"] == "get" and e["outcome"] == "denied" for e in log)
    assert any(e["action"] == "get" and e["outcome"] == "ok" and e["purpose"] == "collections" for e in log)
    assert "98450" not in json.dumps(log)


def test_entries_for_borrower_have_no_raw_and_has_key():
    v = Vault(KEY)
    v.put("c1", "PHONE", "98450 12345", borrower_id="b1", purpose="collections", consent_id="k1")
    v.put("c2", "PAN", "ABCDE1234F", borrower_id="b1")
    v.put("c3", "PHONE", "70000 11111", borrower_id="b2")
    es = v.entries_for_borrower("b1")
    assert len(es) == 2 and {e["conversation_id"] for e in es} == {"c1", "c2"}
    assert "98450" not in json.dumps(es) and es[0].keys() >= {"token", "label", "conversation_id", "created", "purpose", "consent_id"}
    assert v.has_key("b1") and not v.has_key("nobody")


def test_decrypt_for_erasure_is_audited():
    v = Vault(KEY)
    v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
    v.put("c2", "PAN", "ABCDE1234F", borrower_id="b1")
    assert sorted(v.decrypt_for_erasure("b1")) == ["98450 12345", "ABCDE1234F"]
    ev = [e for e in v.access_log() if e["action"] == "decrypt_for_erasure"]
    assert ev and ev[0]["purpose"] == "erasure" and "98450" not in json.dumps(ev)


def test_destroy_borrower_crypto_shred():
    v = Vault(KEY)
    v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
    v.put("c9", "PHONE", "70000 11111", borrower_id="b2")
    backup = [dict(r) for r in v._rows["c1"].values()]          # simulate a backup copy of the ciphertext
    saved_wrapped = dict(v._keys["b1"])
    res = v.destroy_borrower("b1")
    assert res == {"entries_deleted": 1, "key_destroyed": True}
    assert not v.has_key("b1") and v.entries_for_borrower("b1") == [] and v.get("c1", "<PHONE_1>") is None
    try:
        v.decrypt_for_erasure("b1")
        assert False
    except VaultError:
        pass
    v._rows["c1"] = {backup[0]["token"]: backup[0]}               # a restored backup row...
    try:
        v._decrypt_row(backup[0])                                  # ...cannot be decrypted: the key is gone
        assert False, "backup ciphertext must be unreadable after key destruction"
    except VaultError:
        pass
    del v._rows["c1"]                                              # drop the simulated restored row again
    assert saved_wrapped and v.get("c9", "<PHONE_1>") == "70000 11111"      # other borrowers untouched
    assert v.destroy_borrower("b1") == {"entries_deleted": 0, "key_destroyed": False}   # idempotent


def test_destroy_conversation_keeps_borrower_key_but_drops_conv_scoped_key():
    v = Vault(KEY)
    v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
    v.put("c2", "PHONE", "70000 11111")                           # conversation-scoped owner
    assert v.destroy("c1") == 1 and v.has_key("b1")
    assert v.has_key("conv:c2") and v.destroy("c2") == 1 and not v.has_key("conv:c2")


def test_unavailable_or_bad_input_raises():
    v = Vault(KEY)
    for bad in ("", None):
        try:
            v.put("c1", "PHONE", bad)
            assert False
        except VaultError:
            pass
    try:
        Vault(b"short")
        assert False
    except VaultError:
        pass


def test_persistence_roundtrip_is_encrypted_and_wrong_key_fails():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "vault.json")
        v = Vault(KEY, path=p)
        tok = v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
        assert "98450" not in open(p).read() and oct(os.stat(p).st_mode & 0o777) == "0o600"
        v2 = Vault(KEY, path=p)
        assert v2.get("c1", tok) == "98450 12345" and v2.put("c1", "PHONE", "9845012345", borrower_id="b1") == tok
        assert v2.put("c1", "PHONE", "70000 11111", borrower_id="b1") == "<PHONE_2>"
        try:
            Vault(bytes(range(1, 33)), path=p)
            assert False, "wrong master key must fail at load"
        except VaultError:
            pass
        audit = open(p + ".audit.jsonl").read()
        assert "98450" not in audit and "put" in audit


def test_extend_keeps_one_token():
    v = Vault(KEY)
    t = v.put("c1", "PHONE", "nine eight four five")
    v.extend("c1", t, joined="98450 12345")
    assert v.get("c1", t) == "98450 12345" and v.put("c1", "PHONE", "9845012345") == t
    v.extend("c1", t, piece="x")
    assert v.get("c1", t) == "98450 12345 x"


def test_scan_text_finds_survivors_without_returning_values():
    v = Vault(KEY)
    v.put("c1", "PHONE", "98450 12345")
    v.put("c1", "PAN", "ABCDE1234F")
    text = "call 9845012345 or 98450-12345 and PAN ABCDE1234F ok"
    hits = v.scan_text("c1", text)
    assert {t for t, _, _ in hits} == {"<PHONE_1>", "<PAN_1>"} and len(hits) == 3
    assert all(isinstance(h[1], int) for h in hits) and v.scan_text("other", text) == []


# ======================================================================= review-fix tests (coordinator items 1-6)
OLD, NEW = bytes(range(32)), bytes(range(100, 132))


def _backup(p, dest):
    for suffix in ("", ".keys"):
        if os.path.exists(p + suffix):
            shutil.copy(p + suffix, os.path.join(dest, os.path.basename(p) + suffix))


def _restore(p, src, which=("", ".keys")):
    for suffix in which:
        shutil.copy(os.path.join(src, os.path.basename(p) + suffix), p + suffix)


def test_1_shred_defeats_backup_of_rows_file_and_of_whole_state_after_rotation():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as bk:
        p = os.path.join(d, "vault.json")
        v = Vault(OLD, path=p)
        v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
        v.put("c2", "PHONE", "70000 11111", borrower_id="b2")
        assert "98450" not in open(p).read() and "wrapped" not in open(p).read(), "rows file must hold NO key material"
        _backup(p, bk)                                              # pre-shred copy of BOTH files
        v.destroy_borrower("b1")
        # (a) restore only the pre-shred ROWS file onto the current key store: b1's rows are unreadable
        shutil.copy(os.path.join(bk, "vault.json"), p)
        v2 = Vault(OLD, path=p)
        try:
            v2.get("c1", "<PHONE_1>")
            assert False, "restored rows must not decrypt after the key was shredded"
        except VaultError:
            pass
        assert v2.get("c2", "<PHONE_1>") == "70000 11111"            # other borrowers still fine
        # (b) restore BOTH pre-shred files: only protected once the master key was rotated
        v.rotate_master_key(NEW)
        _restore(p, bk)
        try:
            Vault(NEW, path=p)
            assert False, "pre-shred state must not open under the rotated master key"
        except VaultError:
            pass


def test_1_documented_limit_old_master_plus_full_backup_still_decrypts():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as bk:
        p = os.path.join(d, "vault.json")
        v = Vault(OLD, path=p)
        v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
        _backup(p, bk)
        v.destroy_borrower("b1")
        _restore(p, bk)
        assert Vault(OLD, path=p).get("c1", "<PHONE_1>") == "98450 12345"      # exactly the out-of-scope case in the docstring


def test_2_aad_binds_conversation_and_vault_id_and_key_id_is_checked_on_load():
    v = Vault(OLD)
    a = v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
    b = v.put("c2", "PHONE", "70000 11111", borrower_id="b1")        # same owner, same label, other conversation
    ra, rb = v._rows["c1"][a], v._rows["c2"][b]
    for k in ("ct", "nonce"):
        ra[k], rb[k] = rb[k], ra[k]
    for conv, tok in (("c1", a), ("c2", b)):
        try:
            v.get(conv, tok)
            assert False, "cross-conversation swap must be detected"
        except VaultError:
            pass
    v2 = Vault(OLD)
    t = v2.put("c1", "PHONE", "98450 12345", borrower_id="b1")
    v2._rows["c1"][t]["vault_id"] = "x" * 32                          # vault id is authenticated too
    try:
        v2.get("c1", t)
        assert False
    except VaultError:
        pass
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "v.json")
        w = Vault(OLD, path=p)
        w.put("c1", "PHONE", "98450 12345", borrower_id="b1")
        data = json.load(open(p))
        data["rows"][0]["key_id"] = "k-forged"
        json.dump(data, open(p, "w"))
        try:
            Vault(OLD, path=p)
            assert False, "key id mismatch must be rejected at load"
        except VaultError:
            pass


def test_3_dedupe_respects_the_owner():
    v = Vault(OLD)
    t1 = v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
    t2 = v.put("c1", "PHONE", "98450 12345", borrower_id="b2")       # same conv+label+value, different borrower
    assert t1 != t2 and len(v.entries_for_borrower("b1")) == 1 and len(v.entries_for_borrower("b2")) == 1
    assert v.put("c1", "PHONE", "9845012345", borrower_id="b1") == t1  # same owner still dedupes
    v.destroy_borrower("b2")
    assert v.get("c1", t1) == "98450 12345" and v.entries_for_borrower("b2") == []


def test_4_roles_are_allowlisted_attempts_logged_before_and_failures_audited():
    v = Vault(OLD)
    v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
    for call in (lambda: v.decrypt_for_erasure("b1", role="rehydration"), lambda: v.scan_text("c1", "x", role="erasure"),
                 lambda: v.get("c1", "<PHONE_1>", role="erasure")):
        try:
            call()
            assert False, "role not in the allowlist for this action"
        except PermissionError:
            pass
    assert sum(1 for e in v.access_log() if e.get("outcome") == "denied") == 3
    # attempt is written BEFORE the outcome, and a tampered decrypt is audited as failed
    v._rows["c1"]["<PHONE_1>"]["ct"] = v._rows["c1"]["<PHONE_1>"]["ct"][:-4] + "AAAA"
    n0 = len(v.access_log())
    try:
        v.decrypt_for_erasure("b1")
        assert False
    except VaultError:
        pass
    ev = v.access_log()[n0:]
    assert [e["outcome"] for e in ev] == ["attempt", "failed"] and all(e["action"] == "decrypt_for_erasure" for e in ev)
    assert v.verify_audit()
    # strict mode: no implicit role
    s = Vault(OLD, strict_roles=True)
    try:
        s.decrypt_for_erasure("b1", role=None)
        assert False
    except PermissionError:
        pass
    # custom allowlist removes the erasure capability entirely
    nobody = Vault(OLD, capabilities={"rehydration": {"get"}})
    try:
        nobody.decrypt_for_erasure("b1")
        assert False
    except PermissionError:
        pass


def test_4_rate_limit():
    v = Vault(OLD, rate_limits={"decrypt_for_erasure": (2, 60.0)})
    v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
    v.decrypt_for_erasure("b1")
    v.decrypt_for_erasure("b1")
    try:
        v.decrypt_for_erasure("b1")
        assert False
    except RateLimited:
        pass
    assert any(e.get("outcome") == "rate_limited" for e in v.access_log())


def test_5_audit_write_failure_is_fatal_on_decrypt_paths_and_files_are_private():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "vault.json")
        v = Vault(OLD, path=p)
        tok = v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
        assert oct(os.stat(p + ".audit.jsonl").st_mode & 0o777) == "0o600" and oct(os.stat(p + ".keys").st_mode & 0o777) == "0o600"
        assert v.get("c1", tok) == "98450 12345"
        os.remove(p + ".audit.jsonl")
        os.mkdir(p + ".audit.jsonl")                                   # the audit file can no longer be written
        for call in (lambda: v.get("c1", tok), lambda: v.decrypt_for_erasure("b1"), lambda: v.scan_text("c1", "98450 12345")):
            try:
                call()
                assert False, "plaintext must not be returned when the audit write fails"
            except VaultError:
                pass
        assert v.audit_degraded


def test_5_audit_chain_detects_tampering():
    v = Vault(OLD)
    v.put("c1", "PHONE", "98450 12345")
    v.get("c1", "<PHONE_1>")
    assert v.verify_audit()
    v._audit[0]["role"] = "someone-else"
    assert not v.verify_audit()


def test_6_failed_persist_rolls_back_put_and_extend():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "vault.json")
        v = Vault(OLD, path=p)
        t = v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
        good_rows = open(p).read()
        os.remove(p)
        os.mkdir(p)                                                    # rows file cannot be written any more
        try:
            v.put("c1", "PHONE", "70000 11111", borrower_id="b1")
            assert False
        except VaultError:
            pass
        assert v.tokens("c1") == [t] and v._counts[("c1", "PHONE")] == 1, "a failed put must leave no trace"
        try:
            v.extend("c1", t, joined="9845012345 99")
            assert False
        except VaultError:
            pass
        assert v.get("c1", t) == "98450 12345" and v.put("c1", "PHONE", "9845012345", borrower_id="b1") == t
        os.rmdir(p)
        open(p, "w").write(good_rows)
        assert v.put("c1", "PHONE", "70000 11111", borrower_id="b1") == "<PHONE_2>"          # a retry works and is persisted
        assert "<PHONE_2>" in open(p).read()


def test_6_failed_keystore_write_rolls_back_a_shred():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "vault.json")
        v = Vault(OLD, path=p)
        t = v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
        os.remove(p + ".keys")
        os.mkdir(p + ".keys")
        try:
            v.destroy_borrower("b1")
            assert False
        except VaultError:
            pass
        assert v.has_key("b1") and v.get("c1", t) == "98450 12345", "nothing was destroyed on disk, so memory must not claim it was"


def test_rotation_keeps_data_readable_and_blind_index_stable():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "vault.json")
        v = Vault(OLD, path=p)
        t = v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
        v.rotate_master_key(NEW)
        w = Vault(NEW, path=p)
        assert w.get("c1", t) == "98450 12345" and w.put("c1", "PHONE", "9845012345", borrower_id="b1") == t
        try:
            Vault(OLD, path=p)
            assert False
        except VaultError:
            pass


def test_audit_file_edit_is_detected_even_after_a_restart():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "vault.json")
        v = Vault(OLD, path=p)
        tok = v.put("c1", "PHONE", "98450 12345", borrower_id="b1")
        v.get("c1", tok); v.get("c1", tok)
        assert v.verify_audit() and v.verify_audit_file()
        lines = open(v.audit_path, encoding="utf-8").read().splitlines()
        import json as _j
        rec = _j.loads(lines[1]); rec["role"] = "someone-else"; lines[1] = _j.dumps(rec)
        open(v.audit_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        assert not v.verify_audit(), "an edited event on disk must be detected by the live object"
        assert not Vault(OLD, path=p).verify_audit_file(), "...and by a fresh process that only has the file"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(tests)} passed")
