"""Checksums and shape tests used as EVIDENCE when typing a span."""
import re

from layer0_data.pii_gen import verhoeff_valid   # reuse our tested Verhoeff implementation

PAN_RE = re.compile(r"[A-Z]{5}\d{4}[A-Z]")


def luhn_valid(digits: str) -> bool:
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def aadhaar_valid(digits: str) -> bool:
    return len(digits) == 12 and digits.isdigit() and digits[0] in "23456789" and verhoeff_valid(digits)


def phone_shape(digits: str) -> bool:
    return bool(re.fullmatch(r"(?:(?:0091|91|0)?[6-9]\d{9})", digits))
