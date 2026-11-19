"""Tokens. The visible placeholder (<PHONE_1>) is a PRESENTATION token; it points to a random internal token_id / vault_id.
Nothing in a token comes from the value: no hash, no fragment, no length hint beyond the type name."""
import re
import secrets

TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,23}$")
PLACEHOLDER_RE = re.compile(r"^<([A-Z][A-Z0-9_]{1,23})_(\d{1,9})>$")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def placeholder(pii_type: str, seq: int) -> str:
    return f"<{pii_type}_{seq}>"
