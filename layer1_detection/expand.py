"""Span expansion (post-step, runs after detection; detection rules themselves are not changed).

For every number-like span:
  LEFT   absorb a country-code / trunk prefix right before it: '+91 ', '0091-', '91-', '0 '
  RIGHT  absorb straggler digits the detector missed; also '-' or '.' but ONLY when a digit follows
         (so a sentence-final '.' is never swallowed)
  EDGES  absorb ONE spoken digit word just outside either end ('zero', 'shunya', 'ondu', 'double' ...).
         Ambiguous words ('one', 'do', 'oh') are absorbed only next to SPOKEN spans, not next to written digits.
Never expands into amounts/dates (rules.excluded_ranges).  Offsets stay valid on the original text.

For every NAME span (PERSON, from the model or the name memory):
  INITIALS  absorb the initials next to it ('S. Nadar', 'K.S. Rao', 'Usha M. Tiwari'): the model finds the surname and leaves 'S.' visible, or stops after the initial letter.
            Never an honorific ('Mr.'), never a label ('Plan B.'), never into a number / id span next to it. No surname is taken after an initial (it swallowed the next sentence's first word).
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


# ---- names: initials --------------------------------------------------------------------------------------------------------------------------------------------
NAMES = ("PERSON", "NAME")
_INITIALS_LEFT = re.compile(r"(?<![A-Za-z0-9.])(?:[A-Z]\.[ ]?)+$")        # 'S. ' / 'K. S. ' / 'K.S. ' right before the name
_INITIAL_RIGHT = re.compile(r"[ ]?[A-Z]\.(?![A-Za-z0-9])")                # ' B.' right after the name (one capital letter and its full stop)
_ENDS_ON_INITIAL = re.compile(r"(?:^|[\s.])[A-Z]$")
_WINDOW = 40                                                              # a run of more than ~10 initials is not a name: only this much text before the span is searched (keeps the cost linear)
# a capital letter + full stop right after one of these is a label ('Plan B.', 'Section A.', 'Vitamin C.'), not an initial
_LABEL_WORDS = frozenset("plan section option part category vitamin type grade class item step point figure fig table appendix annexure schedule clause group batch stage level tier".split())


def _clear(a, b, others):
    return not any(a < o["end"] and b > o["start"] for o in others)


def expand_name(text, span, others=()):
    """Grow a NAME span over the initials next to it (see the module docstring). `others` = the spans of other kinds in this turn (numbers, ids): the name never grows into characters
    they cover (a name merged into a number fragment would be retyped as a number). Returns the span itself when nothing changes. No surname is absorbed after an initial: it
    would also swallow the first word of the next sentence ('Ravi K. Monday ...')."""
    a, b = span["start"], span["end"]
    s, e = a, b
    if text[a:a + 1] == "." and a >= 1 and text[a - 1].isupper() and text[a - 1].isascii() and (a == 1 or not (text[a - 2].isalnum() or text[a - 2] == ".")):
        s = a - 1                                                        # the model's span starts at the dot of the initial ('. Murthy'): take the letter too
    lo = max(0, s - _WINDOW)
    m = _INITIALS_LEFT.search(text[lo:s])
    if m:
        cand = lo + m.start()
        before = text[:cand].split()
        word = before[-1].strip(".,;:!?()\"'").casefold() if before and text[cand - 1:cand].isspace() else ""
        if (cand == 0 or not (text[cand - 1].isalnum() or text[cand - 1] == ".")) and word not in _LABEL_WORDS and _clear(cand, s, others):
            s = cand
    if _ENDS_ON_INITIAL.search(text[a:b]) and text[b:b + 1] == "." and _clear(b, b + 1, others):      # the span stops on an initial letter and left its full stop out ('Renuka S' + '.')
        e = b + 1
    while True:
        m = _INITIAL_RIGHT.match(text, e)
        if not m or not _clear(e, m.end(), others):
            break
        e = m.end()
    return dict(span, start=s, end=e) if (s, e) != (a, b) else span


def expand_spans(text, spans):
    excl = rules.excluded_ranges(text)
    others = [sp for sp in spans if sp["label"] not in NAMES]
    out = []
    for sp in spans:
        if sp["label"] in NUMERIC:
            out.append(expand_span(text, sp, excl))
        elif sp["label"] in NAMES and sp.get("source") in ("model", "memory"):
            out.append(expand_name(text, sp, others))
        else:
            out.append(sp)
    return out
