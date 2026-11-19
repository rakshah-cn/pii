"""Constrained entity memory: once a name is detected in a conversation, later mentions of its PARTS are masked too.

Deliberately NOT full entity linking. Guards against over-masking:
  * only parts of >= 3 characters, never digits, never a stop-word (sir, madam, bank, loan, ...)
  * Roman-script parts must appear name-shaped (Capitalised or ALL CAPS) - 'kumar' in lower case is left alone
  * whole-token match only ('Ramesh' does not match inside 'Rameshwar')
Known limit: agglutinated Indian-language forms (Kannada 'ರಮೇಶ್ಗೆ') have no token boundary, so they are missed.
Spans carry source='memory' and label PERSON."""
import re

STOP = {s.casefold() for s in """sir madam mam maam ji mr mrs ms miss shri smt sri bank loan customer agent hello mobile number account
aadhaar aadhar pan call please yes okay thank thanks capital finance finserv housing credit limited ltd pvt emi today tomorrow
सर मैडम जी बैंक लोन ग्राहक ಸರ್ ಮೇಡಂ ಬ್ಯಾಂಕ್ ಸಾಲ சார் மேடம் வங்கி கடன்""".split()}
_SEP = r"\s.,;:!?\"'()\[\]{}\-/।|"
_ROMAN = re.compile(r"[A-Za-z]")


def _parts(name):
    out = []
    for w in re.split(r"[\s.,;:!?\"'()\[\]{}\-/]+", name):
        if len(w) >= 3 and not any(ch.isdigit() for ch in w) and w.casefold() not in STOP:
            out.append(w)
    return out


class NameMemory:
    def __init__(self):
        self.parts = {}          # casefold -> original spelling

    def update(self, text, spans):
        for s in spans:
            if s["label"] in ("PERSON", "NAME") and s.get("source") in ("model", "memory", None):
                for p in _parts(text[s["start"]:s["end"]]):
                    self.parts.setdefault(p.casefold(), p)

    def find(self, text, existing=()):
        out = []
        for key, orig in self.parts.items():
            pat = re.compile(rf"(?<![^{_SEP}]){re.escape(orig)}(?![^{_SEP}])", re.I if _ROMAN.match(orig) else 0)
            for m in pat.finditer(text):
                if _ROMAN.match(orig) and not (m.group()[0].isupper()):
                    continue                                             # not name-shaped
                if any(m.start() < e["end"] and m.end() > e["start"] for e in list(existing) + out):
                    continue
                out.append(dict(label="PERSON", start=m.start(), end=m.end(), source="memory"))
        return sorted(out, key=lambda x: x["start"])
