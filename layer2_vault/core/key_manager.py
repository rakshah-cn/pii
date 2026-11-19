"""Key hierarchy:   master secret --HKDF-SHA256--> KEK (wraps DEKs)      and      blind-index key   (independent labels)
                                      KEK wraps/unwraps a random per-borrower DEK (AES-256-GCM, AAD = borrower|key id|KEK version)
                                      DEK encrypts the borrower's values (core.encryption)
Several KEK versions can be loaded at once so a rotation can read old data while writing new.
Only wrapped DEKs are ever stored. Plaintext DEKs live in local variables and are not cached (spec: no caching initially).
Python cannot reliably zero memory, so key material may linger in process memory until garbage-collected."""
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.exceptions import InvalidTag

from .encryption import NONCE_BYTES, canonical
from .errors import DecryptionError, KeyStateError, ValidationFailed, VaultUnavailable


class KeyManager:
    def __init__(self, master_secrets: dict, current_version: int, revoked_versions=()):
        if current_version not in master_secrets or current_version in revoked_versions:
            raise ValidationFailed("master_secret_missing")
        self._masters = dict(master_secrets)
        self.revoked = set(revoked_versions)             # versions that must never be used again (compromised / retired): fail closed
        self.current_kek_version = self.current_blind_version = current_version     # KEK rotation and blind-index rotation move independently

    @property
    def current_version(self):                           # compatibility: "the" version = the KEK version; assigning moves both
        return self.current_kek_version

    @current_version.setter
    def current_version(self, v):
        self.current_kek_version = self.current_blind_version = v

    @property
    def versions(self):
        return sorted(v for v in self._masters if v not in self.revoked)

    def revoke_version(self, version):
        if version in (self.current_kek_version, self.current_blind_version):
            raise ValidationFailed("cannot_revoke_current_version")
        self.revoked.add(version)

    def _derive(self, version, label: bytes) -> bytes:
        if version in self.revoked:
            raise KeyStateError("key_version_revoked")
        if version not in self._masters:
            raise VaultUnavailable("master_key_unavailable")      # the secret for this version is not loaded: nothing can be wrapped or unwrapped
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"te03-vault/salt/v%d" % version,
                    info=b"te03-vault/" + label + b"/v%d" % version).derive(self._masters[version])

    def kek(self, version=None) -> bytes:
        return self._derive(version or self.current_kek_version, b"kek")

    def blind_key(self, version=None) -> bytes:                 # a DIFFERENT derived key from the KEK: never reused for encryption
        return self._derive(version or self.current_blind_version, b"blind-index")

    def idempotency_key(self, version=None) -> bytes:           # keys the request hash stored in vault_idempotency (a different derived key again)
        return self._derive(version or self.current_kek_version, b"idempotency")

    @staticmethod
    def new_dek() -> bytes:
        return os.urandom(32)

    def wrap(self, dek: bytes, borrower_id: str, key_id: str, version=None):
        v = version or self.current_kek_version
        nonce = os.urandom(NONCE_BYTES)          # one wrap per DEK (+ one per KEK rotation): far below the random-nonce limit for the KEK
        return v, nonce, AESGCM(self.kek(v)).encrypt(nonce, dek, canonical("wrap", borrower_id, key_id, str(v)))

    def unwrap(self, wrapped: bytes, nonce: bytes, borrower_id: str, key_id: str, version: int) -> bytes:
        if wrapped is None or nonce is None:
            raise KeyStateError("key_material_gone")             # destroyed
        try:
            return AESGCM(self.kek(version)).decrypt(bytes(nonce), bytes(wrapped), canonical("wrap", borrower_id, key_id, str(version)))
        except InvalidTag:
            raise DecryptionError()
