import os

from layer2_vault.core.errors import DecryptionError, KeyStateError, ValidationFailed, VaultUnavailable
from layer2_vault.core.key_manager import KeyManager
from layer2_vault.tests._util import expect, run

M1, M2 = os.urandom(32), os.urandom(32)


def test_kek_and_blind_key_are_independent_and_deterministic():
    k = KeyManager({1: M1}, 1)
    assert k.kek() != k.blind_key() and k.kek() == KeyManager({1: M1}, 1).kek() and k.kek() != M1 and k.blind_key() != M1
def test_versions_derive_different_keys():
    k = KeyManager({1: M1, 2: M2}, 1); assert k.kek(1) != k.kek(2) and k.blind_key(1) != k.blind_key(2)
def test_wrap_unwrap_roundtrip_and_random_deks():
    k = KeyManager({1: M1}, 1); d1, d2 = k.new_dek(), k.new_dek(); assert d1 != d2 and len(d1) == 32
    v, n, w = k.wrap(d1, "B1", "key1"); assert k.unwrap(w, n, "B1", "key1", v) == d1
def test_wrapped_key_bound_to_borrower_key_id_and_version():
    k = KeyManager({1: M1, 2: M2}, 1); v, n, w = k.wrap(k.new_dek(), "B1", "key1")
    expect(DecryptionError, lambda: k.unwrap(w, n, "B2", "key1", v)); expect(DecryptionError, lambda: k.unwrap(w, n, "B1", "key2", v))
def test_wrong_master_cannot_unwrap():
    k = KeyManager({1: M1}, 1); v, n, w = k.wrap(k.new_dek(), "B1", "key1")
    expect(DecryptionError, lambda: KeyManager({1: M2}, 1).unwrap(w, n, "B1", "key1", 1))
def test_destroyed_key_material_cannot_unwrap():
    expect(KeyStateError, lambda: KeyManager({1: M1}, 1).unwrap(None, None, "B1", "k", 1), "key_material_gone")
def test_unknown_version_refused():
    expect(VaultUnavailable, lambda: KeyManager({1: M1}, 1).kek(9), "master_key_unavailable")      # a missing master secret is an outage (503), not a policy denial
def test_missing_current_version_refused():
    expect(ValidationFailed, lambda: KeyManager({2: M2}, 1))

if __name__ == "__main__":
    run(globals())
