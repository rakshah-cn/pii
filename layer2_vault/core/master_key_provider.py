"""Where the master secret comes from. The vault only ever asks a provider for {version: secret bytes}.

EnvMasterKeyProvider  : prototype arrangement (VAULT_MASTER_KEY in the environment).
AwsKmsMasterKeyProvider: the master secret is stored ONLY as a KMS-encrypted blob (VAULT_KMS_CIPHERTEXT_V<n>, base64). At start-up the process asks AWS KMS
                         to decrypt it (the KMS key policy decides which role may). Nothing usable is left in the environment or in source control.
                         If KMS cannot be reached the provider raises VaultUnavailable('master_key_unavailable') and the vault does not start (fail closed).
WHAT THIS IS NOT: the decrypted secret still lives in process memory, and the DEKs are still wrapped with a KEK derived from it. The stronger design - wrapping
every DEK with a KMS key (GenerateDataKey / Decrypt per borrower, no master secret in the process at all) - costs one KMS call per unwrap and is NOT built here.
Tested only against a fake KMS client; no real AWS KMS was available."""
import base64

from ..config.settings import decode_secret
from .errors import ValidationFailed, VaultUnavailable


class MasterKeyProvider:
    def load(self) -> dict:
        raise NotImplementedError


class EnvMasterKeyProvider(MasterKeyProvider):
    def __init__(self, env):
        self.env = env

    def load(self) -> dict:
        out = {}
        if self.env.get("VAULT_MASTER_KEY"):
            out[1] = decode_secret(self.env["VAULT_MASTER_KEY"])
        for k, v in self.env.items():
            if k.startswith("VAULT_MASTER_KEY_V") and k[len("VAULT_MASTER_KEY_V"):].isdigit():
                out[int(k[len("VAULT_MASTER_KEY_V"):])] = decode_secret(v)
        return out


def kms_context(version: int) -> dict:
    return {"app": "te03-vault", "purpose": "master-secret", "version": str(version)}      # binds a blob to this use: it cannot be replayed as another secret


class AwsKmsMasterKeyProvider(MasterKeyProvider):
    def __init__(self, ciphertexts: dict, kms_client=None, key_id=None):
        if not ciphertexts:
            raise ValidationFailed("kms_ciphertext_missing")
        self.blobs, self.key_id = {int(v): base64.b64decode(b, validate=True) for v, b in ciphertexts.items()}, key_id
        self.kms = kms_client

    def _client(self):
        if self.kms is None:
            try:
                import boto3
                self.kms = boto3.client("kms")
            except Exception:
                raise VaultUnavailable("master_key_unavailable")
        return self.kms

    def load(self) -> dict:
        out = {}
        for version, blob in self.blobs.items():
            try:
                kw = {"CiphertextBlob": blob, "EncryptionContext": kms_context(version)}
                if self.key_id:
                    kw["KeyId"] = self.key_id
                secret = self._client().decrypt(**kw)["Plaintext"]
            except VaultUnavailable:
                raise
            except Exception:
                raise VaultUnavailable("master_key_unavailable")          # never include the provider's message: it may name keys or accounts
            if len(secret) < 32:
                raise ValidationFailed("master_secret_invalid_or_too_short")
            out[version] = bytes(secret)
        return out

    @classmethod
    def from_env(cls, env, kms_client=None):
        blobs = {int(k[len("VAULT_KMS_CIPHERTEXT_V"):]): v for k, v in env.items() if k.startswith("VAULT_KMS_CIPHERTEXT_V") and k[len("VAULT_KMS_CIPHERTEXT_V"):].isdigit()}
        return cls(blobs, kms_client, env.get("VAULT_KMS_KEY_ID"))


def seal_master_secret(kms_client, key_id: str, version: int, secret: bytes) -> str:
    """Provisioning helper (run once by an operator): returns the base64 blob to store as VAULT_KMS_CIPHERTEXT_V<version>."""
    r = kms_client.encrypt(KeyId=key_id, Plaintext=secret, EncryptionContext=kms_context(version))
    return base64.b64encode(r["CiphertextBlob"]).decode()
