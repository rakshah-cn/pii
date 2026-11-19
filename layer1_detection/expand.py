"""Span expansion (post-step, runs after detection; detection rules themselves are not changed).

For every number-like span:
  LEFT   absorb a country-code / trunk prefix right before it: '+91 ', '0091-', '91-', '0 '
  RIGHT  absorb straggler digits the detector missed; also '-' or '.' but ONLY when a digit follows
         (so a sentence-final '.' is never swallowed)
  EDGES  absorb ONE spoken digit word just outside either end ('zero', 'shunya', 'ondu', 'double' ...).
         Ambiguous words ('one', 'do', 'oh') are absorbed only next to SPOKEN spans, not next to written digits.
Never expands into amounts/dates (rules.excluded_ranges).  Offsets stay valid on the original text.
"""
import re

from layer0_data import spoken
from . import rules
from .spoken_engine import AMBIG, _DW, _MULT

NUMERIC = {"PHONE", "AADHAAR", "ACCOUNT", "NUMBER_FRAGMENT", "SPOKEN_NUMBER", "BANK_ACCOUNT", "CARD_NUMBER", "ID_FRAGMENT"}
SPOKENISH = {"SPOKEN_NUMBER", "ID_FRAGMENT"}
_PREFIX = re.compile(r"(?:\+\s?91[\s-]?|0091[\s-]?|91[\s-]|0[\s-]?)$")
_P = ".,;:!?…\"'()[]{}।|"
_SPOKEN_WORDS = set(_DW) | set(_MULT)


def _word_left(text, s):
    j = s
    while j > 0 and text[j - 1] == " ":
        j -= 1
    if j == s:                       # a word must be separated from the span by whitespace
        return None
    k = j
    while k > 0 and not text[k - 1].isspace():
        k -= 1
    w = text[k:j].strip(_P)
    return (k + text[k:j].find(w), j - (len(text[k:j]) - len(text[k:j].rstrip(_P)))) if w and w.casefold() in _SPOKEN_WORDS else None


def _word_right(text, e):
    j = e
    while j < len(text) and text[j] == " ":
        j += 1
    if j == e:
        return None
    k = j
    while k < len(text) and not text[k].isspace():
        k += 1
    raw = text[j:k]
    w = raw.strip(_P)
    return (j + raw.find(w), j + raw.find(w) + len(w)) if w and w.casefold() in _SPOKEN_WORDS else None


def expand_span(text, span, excl):
    s, e = span["start"], span["end"]
    spokenish = span["label"] in SPOKENISH
    # LEFT: country code / trunk prefix (only if not glued to other alphanumerics)
    m = _PREFIX.search(text[max(0, s - 6):s])
    if m:
        ps = s - (len(text[max(0, s - 6):s]) - m.start())
        if (ps == 0 or not text[ps - 1].isalnum()) and not rules.overlaps(ps, s, excl):
            s = ps
    # RIGHT: straggler digits; '-' or '.' only when followed by a digit
    while e < len(text) and not rules.overlaps(e, e + 1, excl):
        c = text[e]
        if c.isdigit():
            e += 1
        elif c in "-." and e + 1 < len(text) and text[e + 1].isdigit() and not spokenish:
            e += 1
        else:
            break
    # EDGES: one spoken digit word outside either boundary
    for side in ("L", "R"):
        w = _word_left(text, s) if side == "L" else _word_right(text, e)
        if not w:
            continue
        word = text[w[0]:w[1]].casefold()
        if word in AMBIG and not spokenish:
            continue
        if rules.overlaps(w[0], w[1], excl):
            continue
        s, e = (w[0], e) if side == "L" else (s, w[1])
    return dict(span, start=s, end=e) if (s, e) != (span["start"], span["end"]) else span


def expand_spans(text, spans):
    excl = rules.excluded_ranges(text)
    return [expand_span(text, sp, excl) if sp["label"] in NUMERIC else sp for sp in spans]
