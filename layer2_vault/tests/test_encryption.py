import os

from layer2_vault.core import encryption as E
from layer2_vault.core.errors import DecryptionError, ValidationFailed
from layer2_vault.tests._util import expect, run

K = os.urandom(32)
AAD = E.record_aad("B1", "C1", "v1", "PHONE", "k1")


def test_encrypt_then_decrypt():
    n, ct = E.encrypt(K, "98450 12345", AAD); assert E.decrypt(K, n, ct, AAD) == "98450 12345"
def test_unicode_value_roundtrip():
    n, ct = E.encrypt(K, "ರಮೇಶ ಗೌಡ", AAD); assert E.decrypt(K, n, ct, AAD) == "ರಮೇಶ ಗೌಡ"
def test_same_value_twice_has_different_nonce_and_ciphertext():
    n1, c1 = E.encrypt(K, "98450 12345", AAD); n2, c2 = E.encrypt(K, "98450 12345", AAD); assert n1 != n2 and c1 != c2
def test_nonce_is_12_bytes_and_ciphertext_carries_the_tag():
    n, ct = E.encrypt(K, "x", AAD); assert len(n) == 12 and len(ct) == 1 + 16
def test_tampered_ciphertext_fails():
    n, ct = E.encrypt(K, "98450 12345", AAD); bad = bytes([ct[0] ^ 1]) + ct[1:]
    expect(DecryptionError, lambda: E.decrypt(K, n, bad, AAD))
def test_tampered_nonce_fails():
    n, ct = E.encrypt(K, "v", AAD); expect(DecryptionError, lambda: E.decrypt(K, bytes([n[0] ^ 1]) + n[1:], ct, AAD))
def test_wrong_aad_fails_for_each_bound_field():
    n, ct = E.encrypt(K, "v", AAD)
    for other in (("B2", "C1", "v1", "PHONE", "k1"), ("B1", "C2", "v1", "PHONE", "k1"), ("B1", "C1", "v2", "PHONE", "k1"), ("B1", "C1", "v1", "NAME", "k1"), ("B1", "C1", "v1", "PHONE", "k2")):
        expect(DecryptionError, lambda o=other: E.decrypt(K, n, ct, E.record_aad(*o)))
def test_wrong_key_fails():
    n, ct = E.encrypt(K, "v", AAD); expect(DecryptionError, lambda: E.decrypt(os.urandom(32), n, ct, AAD))
def test_short_key_rejected():
    expect(ValidationFailed, lambda: E.encrypt(b"short", "v", AAD))
def test_canonical_encoding_is_unambiguous():
    assert E.canonical("ab", "c") != E.canonical("a", "bc") and E.canonical("a", "") != E.canonical("", "a")
def test_error_message_does_not_leak():
    n, ct = E.encrypt(K, "SECRET-VALUE", AAD)
    e = expect(DecryptionError, lambda: E.decrypt(K, n, ct, E.record_aad("x", "x", "x", "x", "x")))
    assert "SECRET" not in str(e) and "SECRET" not in repr(e)

if __name__ == "__main__":
    run(globals())
