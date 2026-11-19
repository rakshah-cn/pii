"""SEALED MATCHING for the erasure sweep. This code runs INSIDE the vault trust boundary.

It is built from decrypted values, lives only for the duration of one call, and answers questions about text without ever
returning a value: a category per text (strong / address / name_full / name_single), a similarity score per vector row,
or a plain bool. Layer 6 (the erasure agent) gets only those answers, so the agent stays blind to personal data.
Moved here from layer6_erasure/tools.py (Layer 2 spec: Layer 6 must not retrieve plaintext).
"""
import re

_PAN = re.compile(r"[A-Z]{5}\d{4}[A-Z]")
_KEEP = re.compile(r"[^0-9a-zऀ-෿]")
STRONG, ADDRESS, NAME_FULL, NAME_SINGLE = "strong", "address", "name_full", "name_single"


class SealedMaterial:
    def __init__(self, raws):
        self.digits, self.codes, self.names, self.addrs = [], [], [], []
        for r in raws:
            r = r.strip()
            if re.fullmatch(r"[\d\s\-+]+", r) and len(re.sub(r"\D", "", r)) >= 6:
                self.digits.append(re.sub(r"\D", "", r))
            elif _PAN.fullmatch(r) or (re.fullmatch(r"[A-Z0-9]{8,}", r) and any(c.isdigit() for c in r)):
                self.codes.append(r.casefold())
            elif "," in r or len(r.split()) >= 4:
                if len(r) >= 12:
                    self.addrs.append(" ".join(r.split()).casefold())
            elif r:
                self.names.append(" ".join(r.split()).casefold())
        self._dpat = [re.compile(r"(?<!\d)" + (r"(?:\+?91[ \-]?|0)?" if len(d) == 10 else "")
                                 + r"[ \-]?".join(map(re.escape, d)) + r"(?!\d)") for d in self.digits]

    def __bool__(self):
        return bool(self.digits or self.codes or self.names or self.addrs)

    def strong(self, text):
        low = text.casefold()
        return any(p.search(text) for p in self._dpat) or any(c in low for c in self.codes)

    def address(self, text):
        t = " ".join(text.split()).casefold()
        return any(a in t for a in self.addrs)

    def name(self, text):
        t = " ".join(text.split()).casefold()
        if any(len(n.split()) >= 2 and n in t for n in self.names):
            return NAME_FULL
        if any(len(n.split()) == 1 and n in t for n in self.names):
            return NAME_SINGLE
        return None

    def classify(self, text):
        """Strongest kind of match in `text`, or None. A category only: never the value, never a position."""
        if self.strong(text):
            return STRONG
        if self.address(text):
            return ADDRESS
        return self.name(text)

    def classify_collapsed(self, text):
        """Second, different matcher: drop everything except letters/digits, substring-search the long identifiers (>= 8)."""
        keys = [d for d in self.digits if len(d) >= 8] + [c for c in self.codes if len(c) >= 8]
        flat = _KEEP.sub("", text.casefold())
        return STRONG if any(k in flat for k in keys) else None

    def probe_text(self):
        parts = list(self.digits) + list(self.codes)
        for n in self.names:
            parts += n.split()
        return " ".join(parts)
