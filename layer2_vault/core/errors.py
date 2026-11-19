"""Safe, coded errors. Messages are GENERIC on purpose: they never carry values, keys or ciphertext."""


class VaultError(Exception):
    code = "vault_error"

    def __init__(self, code=None):
        self.code = code or self.code
        super().__init__(self.code)


class ValidationFailed(VaultError):
    code = "invalid_request"


class VaultDenied(VaultError):
    code = "denied"


class KeyStateError(VaultDenied):
    code = "key_not_active"


class IdempotencyError(VaultError):
    """The Idempotency-Key was reused for a different request, or its first run is still in progress (HTTP 409)."""
    code = "idempotency_key_conflict"


class PayloadTooLarge(VaultError):
    code = "request_too_large"


class RateLimited(VaultError):
    code = "too_many_auth_failures"


class DecryptionError(VaultError):
    code = "decryption_failed"


class VaultUnavailable(VaultError):
    """A required dependency (database, key material, permission service) is unavailable: callers must FAIL CLOSED."""
    code = "vault_unavailable"
