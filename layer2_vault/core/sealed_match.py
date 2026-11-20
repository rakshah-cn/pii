"""SEALED MATCHING for the erasure sweep. This code runs INSIDE the vault trust boundary.

It is built from decrypted values, lives only for the duration of one call, and answers questions about text without ever
returning a value: a category per text (strong / address / name_full / name_embedded / name_single), a similarity score per vector row,
or a plain bool. Layer 6 (the erasure agent) gets only those answers, so the agent stays blind to personal data.
Moved here from layer6_erasure/tools.py (Layer 2 spec: Layer 6 must not retrieve plaintext).
"""
import re

_PAN = re.compile(r"[A-Z]{5}\d{4}[A-Z]")
_KEEP = re.compile(r"[^0-9a-zऀ-෿]")
STRONG, ADDRESS, NAME_FULL, NAME_SINGLE, NAME_EMBEDDED = "strong", "address", "name_full", "name_single", "name_embedded"

# NAME_EMBEDDED: the full name is there, but only INSIDE a longer capitalised name ('A. Yadav' in 'Arjun A. Yadav'), which is probably another person. Found by the scale evidence:
# the agent deleted another borrower's record because the erased borrower's name was a tail of it. Layer 6 never auto-deletes on this evidence (it is reported for a human).
# The cue is a capitalised word or an initial right next to the name (separated by one space only, no punctuation), that is not a title / greeting / role word. LIMITS: it needs capital
# letters, so it does nothing for Devanagari / Kannada / Tamil text or all-lowercase text (those still match as name_full, as before); in ALL-CAPS text any neighbouring word counts.
_ROLE_WORDS = frozenset("""mr mrs ms miss dr shri smt sri sir madam mam maam ji dear hello hi customer borrower client agent caller name regards thanks thank bank loan account mobile
number aadhaar aadhar pan call please yes okay ok finance credit limited ltd pvt emi today tomorrow yesterday summary summarise summarize case note notes contact re ref update""".split())
_INITIAL = re.compile(r"(?:[^\W\d_]\.)+|[^\W\d_]\.?")         # one letter with or without a full stop, or dotted initials: 's.', 's', 'k.s.' (casefolded)
_CAPITALISED = re.compile(r"[A-Z][a-z]+(?:[-'][A-Za-z]+)*|[A-Z]\.|[A-Z]{2,}")


def _name_like(tok, right):
    """Does this neighbouring word look like part of a name (a capitalised word or an initial)? `right`: the word AFTER the name may carry sentence punctuation.
    A word glued to markup still counts: `"Arjun`, `(Arjun`, `name=Arjun` (a JSON / log field value) - the part after the last '=' or ':' is the word (found by the scale evidence: the record was
    'DEBUG payload name=Arjun A. Yadav mobile=...')."""
    tok = re.split(r"[=:]", tok)[-1] if not right else tok
    tok = tok.lstrip("\"'([{<")
    if right:
        tok = tok.rstrip(",;:!?)]}>\"'")
        if tok.endswith(".") and not re.fullmatch(r"[A-Z]\.", tok):
            tok = tok[:-1]
    return bool(tok) and tok.casefold().rstrip(".") not in _ROLE_WORDS and _CAPITALISED.fullmatch(tok) is not None


def _embedded(o, i, j):
    """Is o[i:j] (a name, original case, whitespace-normalised text) part of a longer name? True when a name-like word touches it on either side."""
    if i >= 2 and o[i - 1] == " " and _name_like(o[:i - 1].rsplit(" ", 1)[-1], right=False):
        return True
    return j < len(o) - 1 and o[j] == " " and _name_like(o[j + 1:].split(" ", 1)[0], right=True)


class SealedMaterial:
    def __init__(self, raws):
        self.digits, self.codes, self.names, self.addrs = [], [], [], []
        for r in raws:
            r = r.strip()
            if re.fullmatch(r"[\d\s\-+]+", r) and len(re.sub(r"\D", "", r)) >= 6:
                self.digits.append(re.sub(r"\D", "", r))
            elif _PAN.fullmatch(r) or (re.fullmatch(r"[A-Z0-9]{8,}", r) and any(c.isdigit() for c in r)):
                self.codes.append(r.casefold())
            elif "," in r or len([w for w in r.split() if not _INITIAL.fullmatch(w)]) >= 4:        # (initials do not make a name an address: 'Usha M. K. Tiwari' is a name)
                if len(r) >= 12:
                    self.addrs.append(" ".join(r.split()).casefold())
            elif r:
                n = " ".join(r.split()).casefold()
                self.names.append(n)
                if any(_INITIAL.fullmatch(w) for w in n.split()):          # 'S. Nadar': a copy that writes only the surname, or the initial differently ('Nadar S', 'NADAR S'), is still a copy
                    self.names += [w.strip(".,") for w in n.split() if not _INITIAL.fullmatch(w) and len(w.strip(".,")) >= 3]       # (single words: reported for a human, never deleted by themselves)
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
        o = " ".join(text.split())
        t = o.casefold()
        full = [n for n in self.names if len(n.split()) >= 2 and n in t]
        if full:
            found = False
            for n in full:
                for m in re.finditer(re.escape(n), o, re.I):          # positions in the original-case text: the cue for 'inside a longer name' is capitalisation
                    found = True
                    if m.start() > 0 and o[m.start() - 1].isalnum():       # the match starts inside a word ('Mr. Rao' contains 'r. rao'): not this name, report it as ambiguous
                        continue
                    if not _embedded(o, m.start(), m.end()):
                        return NAME_FULL                              # one clean occurrence is enough
            return NAME_EMBEDDED if found else NAME_FULL
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
