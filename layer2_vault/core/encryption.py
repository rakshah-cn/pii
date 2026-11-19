"""AES-256-GCM primitives. Small on purpose: nothing here knows about databases or tokens.

* a unique 96-bit nonce per encryption: the vault supplies epoch||counter from a durable per-key counter (core/nonce.py); random only when called stand-alone
* AAD binds a ciphertext to its record context (borrower | conversation | vault id | PII type | key id), so a ciphertext
  copied into another record fails authentication
* canonical() length-prefixes every field, so ('ab','c') and ('a','bc') can never produce the same bytes"""
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import DecryptionError, ValidationFailed

NONCE_BYTES = 12


def canonical(*parts) -> bytes:
    out = [len(parts).to_bytes(2, "big")]
    for p in parts:
        b = p if isinstance(p, bytes) else str(p).encode("utf-8")
        out += [len(b).to_bytes(4, "big"), b]
    return b"".join(out)


def record_aad(borrower_id, conversation_id, vault_id, pii_type, key_id) -> bytes:
    return canonical("rec", borrower_id, conversation_id, vault_id, pii_type, key_id)


def encrypt(key: bytes, plaintext: str, aad: bytes, nonce: bytes = None):
    """nonce=None draws a random one (stand-alone use / tests). The vault ALWAYS passes a nonce from core.nonce.NonceSource (a durable per-key counter)."""
    if len(key) != 32:
        raise ValidationFailed("bad_key_length")
    if nonce is None:
        nonce = os.urandom(NONCE_BYTES)
    elif len(nonce) != NONCE_BYTES:
        raise ValidationFailed("bad_nonce_length")
    return nonce, AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)          # ciphertext || 16-byte tag


def decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> str:
    try:
        return AESGCM(key).decrypt(bytes(nonce), bytes(ciphertext), aad).decode("utf-8")
    except (InvalidTag, ValueError):                                                   # tamper, wrong key, wrong AAD: one generic error
        raise DecryptionError()
