"""Scoped keyed lookup (blind index): find an existing mapping WITHOUT storing or querying plaintext.
digest = HMAC-SHA256(blind key, canonical(borrower, conversation, purpose, pii type, normalised value)).
A keyed HMAC (never a plain hash: phone numbers are brute-forceable), scoped narrowly. It reveals equality inside one scope only.
Normalisation is per type so '98450 12345', '+91 98450-12345' and '9845012345' are the same phone."""
import hashlib
import hmac
import re
import unicodedata

from .encryption import canonical

_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "sri"}
_DIGIT_TYPES = {"PHONE", "AADHAAR", "ACCOUNT", "CARD_NUMBER", "PINCODE"}
_CODE_TYPES = {"PAN", "LOAN", "IFSC", "GSTIN", "VOTER_ID", "PASSPORT", "DRIVING_LICENCE", "VEHICLE_REG"}


_DIGITS_ONLY = re.compile(r"^[\s\d\-\.\+\(\)/]+$")


def _text(v: str) -> str:
    return re.sub(r"\s+", " ", v).strip().casefold()


def normalise(pii_type: str, value: str) -> str:
    v = unicodedata.normalize("NFKC", value or "")
    # the digits-only / code-only forms are used ONLY when the value really is digits+separators (or a code). Spoken or mixed values
    # ('nine eight four five', '98450 twelve') have no digit form: reducing them would collapse DIFFERENT values to one index
    # and give them ONE token. Found by the gateway integration suite.
    if pii_type in _DIGIT_TYPES and not _DIGITS_ONLY.match(v):
        return _text(v)
    if pii_type in _CODE_TYPES and re.search(r"[^0-9A-Za-z\s\-/\._]", v):
        return _text(v)
    if pii_type in _DIGIT_TYPES:
        d = "".join(ch for ch in v if ch.isdigit())            # unicode digits (Devanagari, Kannada...) count too
        d = "".join(str(unicodedata.digit(ch)) for ch in d)
        if pii_type == "PHONE":                                # strip ONLY the Indian prefixes; any other long number keeps all its digits
            if len(d) == 14 and d.startswith("0091"):
                d = d[4:]
            elif len(d) == 12 and d.startswith("91"):
                d = d[2:]
            elif len(d) == 11 and d.startswith("0"):
                d = d[1:]
        return d
    if pii_type in _CODE_TYPES:
        return re.sub(r"[^0-9A-Za-z]", "", v).upper()
    v = re.sub(r"\s+", " ", v).strip().casefold()
    if pii_type == "NAME":
        words = [w.strip(".,") for w in v.split()]
        while words and words[0] in _HONORIFICS:
            words = words[1:]
        v = " ".join(words) if words else v                # 'Dr' / 'Mrs' alone must NOT normalise to '' (that collapsed different values to one token)
    return v


def blind_index(key: bytes, borrower_id, conversation_id, purpose_id, pii_type, value) -> str:
    msg = canonical("bi", borrower_id, conversation_id, str(purpose_id).casefold(), pii_type, normalise(pii_type, value))
    return hmac.new(key, msg, hashlib.sha256).hexdigest()
