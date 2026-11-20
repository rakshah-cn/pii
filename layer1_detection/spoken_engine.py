"""Our own spoken-number engine (nothing off the shelf does this).

Finds runs like 'nine eight double seven six', 'elu sonne naalku', 'A B W N J 0 3 H',
'ninety eight seven six', using the digit-word lexicons in layer0_data/spoken.py.

Robustness (tuned on the HARD dev set, checked on the untouched hard TEST set):
  * fuzzy words: a token one typo away from a digit word counts, but only next to a real digit word
  * ASR homophones: to/too/for/ate/won/nil count the same way
  * English tens/teens: 'ninety eight' = two digits
  * bridging: when armed (a cue like 'number' was seen), ONE garbled word inside a run is tolerated
  * spoken amounts ('five hundred rupees') are dropped
Safety against masking ordinary words: ambiguous words ('one','do','oh', teens) never start a run on
their own; a plain run needs >=3 digit-units; short runs count only when 'armed'.
"""
import re
import unicodedata

from layer0_data import spoken
from .rules import overlaps

_P = ".,;:!?…\"'()[]{}।|"
_TOK = re.compile(r"\S+")
_DW = {w: sorted(d)[0] for w, d in spoken._REV.items()}
_MULT = dict(spoken._MULT_REV)
_FILL = {w.lower() for ws in spoken.FILLERS.values() for w in ws} | {"hmm", "yes", "wait"}
_TENS = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"}
_TEENS = {"ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
          "eighteen", "nineteen"}
_HOMO = {"to", "too", "for", "ate", "won", "nil"}
AMBIG = {"do", "oh", "one"} | _TEENS
_STOP_AFTER = {"hundred", "thousand", "lakh", "lakhs", "crore", "rupees", "rupee", "rs", "percent", "%", "paise"}
_FUZZ_WORDS = sorted({w for lang, rows in spoken.LEX.items() if lang != "en" for ws in rows for w in ws if len(w) >= 3})
_MK = ("D", "W", "L", "M", "T", "FW")
_fuzz_cache = {}
_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)     # zero-width chars: ignore when classifying
_HOMO_DIGIT = {"to": "2", "too": "2", "for": "4", "ate": "8", "won": "1", "nil": "0"}
_TENS_VAL = {"twenty": 2, "thirty": 3, "forty": 4, "fifty": 5, "sixty": 6, "seventy": 7, "eighty": 8, "ninety": 9}
_TEEN_VAL = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
             "seventeen": 17, "eighteen": 18, "nineteen": 19}
# (word, digit) for fuzzy digit lookup; English words only with length >= 4 (avoid 'fine'~'nine' style accidents)
_LEX_PAIRS = [(w.casefold(), str(d)) for lang, rows in spoken.LEX.items() for d, ws in enumerate(rows) for w in ws
              if len(w) >= 4 or (lang != "en" and len(w) >= 3)]
_LEX_PAIRS_NONEN = [(w.casefold(), str(d)) for lang, rows in spoken.LEX.items() if lang != "en"
                    for d, ws in enumerate(rows) for w in ws if len(w) >= 3]
_relaxed_cache = {}
_relaxed_ne_cache = {}


def _ed1(a, b):
    """True if a and b differ by exactly one substitution / insertion / deletion / adjacent swap."""
    la, lb = len(a), len(b)
    if a == b or abs(la - lb) > 1:
        return False
    if la == lb:
        d = [i for i in range(la) if a[i] != b[i]]
        return len(d) == 1 or (len(d) == 2 and d[1] == d[0] + 1 and a[d[0]] == b[d[1]] and a[d[1]] == b[d[0]])
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def _fuzzy(c):
    if c not in _fuzz_cache:
        _fuzz_cache[c] = len(c) >= 3 and any(_ed1(c, w) for w in _FUZZ_WORDS)
    return _fuzz_cache[c]


def _wordish(w):
    """Letters, including Indic vowel signs/viramas (str.isalpha() is False for those marks)."""
    return bool(w) and any(ch.isalpha() for ch in w) and all(
        ch.isalpha() or unicodedata.category(ch).startswith("M") for ch in w)


def _lev(a, b, maxd):
    """Levenshtein distance, early exit above maxd."""
    if abs(len(a) - len(b)) > maxd:
        return maxd + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > maxd:
            return maxd + 1
        prev = cur
    return prev[-1]


def relaxed_digit(c):
    """Digit a garbled word most likely stands for (<=2 typos; <=1 for short/English words), else None.
    Only used NEXT TO an already-accepted number, so ordinary words are not affected."""
    if c in _relaxed_cache:
        return _relaxed_cache[c]
    res = None
    if _wordish(c) and len(c) >= (3 if any(ord(ch) > 127 for ch in c) else 4):
        native = any(ord(ch) > 127 for ch in c)
        best = 3
        for w, d in _LEX_PAIRS:
            if not native and w[0] != c[0]:
                continue
            lim = 2 if len(w) >= 5 else 1
            dist = _lev(c, w, lim)
            if dist <= lim and dist < best:
                best, res = dist, d
    _relaxed_cache[c] = res
    return res


def relaxed_digit_nonen(c):
    """Like relaxed_digit but only against Indian-language digit words (safe to use anywhere in an armed run)."""
    if c not in _relaxed_ne_cache:
        res, best = None, 3
        if _wordish(c) and len(c) >= 3:
            native = any(ord(ch) > 127 for ch in c)
            for w, d in _LEX_PAIRS_NONEN:
                if not native and w[0] != c[0]:
                    continue
                lim = 2 if len(w) >= 5 else 1
                dist = _lev(c, w, lim)
                if dist <= lim and dist < best:
                    best, res = dist, d
        _relaxed_ne_cache[c] = res
    return _relaxed_ne_cache[c]


def _classify(core, armed=False):
    core = core.translate(_ZW)
    c = core.casefold()
    if core.isdigit():
        return "D", len(core)
    if c in _MULT:
        return "M", _MULT[c]
    if c in _DW:
        return "W", 2 if c in _TEENS else 1
    if c in _TENS:
        return "T", 1
    if c in _TEENS:
        return "W", 2
    if len(core) == 1 and core.isalpha() and (core.isupper() or armed):          # a lower-case letter is a spelled letter only in an armed turn (a PAN / loan / number was asked for): 'a' and 'i' are also words
        return "L", 1
    if c in _FILL:
        return "F", 0
    if c in _HOMO or (_wordish(core) and _fuzzy(c)):
        return "FW", 1
    if armed and _wordish(core) and relaxed_digit_nonen(c):
        return "FW", 1                      # two typos away from an Indian-language digit word, inside an armed turn
    return None, 0


def _tokens(text, excl, armed=False):
    toks = []
    for m in _TOK.finditer(text):
        s, e = m.span()
        while s < e and text[s] in _P:
            s += 1
        while e > s and text[e - 1] in _P:
            e -= 1
        if s >= e:
            continue
        core = text[s:e]
        kind, units = _classify(core, armed)
        if kind and overlaps(s, e, excl):
            kind = None
        toks.append(dict(i=len(toks), s=s, e=e, kind=kind, units=units, core=core))
    for k, t in enumerate(toks):                        # a lower-case 'a' / 'i' is a letter of a spelled id only next to another letter or a digit ('c b i s i 6'), never on its own ('i have a pan')
        if t["kind"] == "L" and t["core"] in ("a", "i") and not any(0 <= j < len(toks) and toks[j]["kind"] in ("L", "D") for j in (k - 1, k + 1)):
            t["kind"], t["units"] = None, 0
    return toks


def _next_member(toks, i):
    j = i + 1
    while j < len(toks) and toks[j]["kind"] == "F":
        j += 1
    return j < len(toks) and toks[j]["kind"] in _MK


def find(text, armed, excl):
    toks = _tokens(text, excl, armed)
    runs, cur, pend_f, gap = [], None, 0, None
    for i, t in enumerate(toks):
        k = t["kind"]
        if k in _MK:
            if cur is None:
                if k == "FW" and not _next_member(toks, i):
                    continue                          # a lone typo-word never starts a run
                cur = []
            if gap is not None:                        # one garbled word bridged between members
                cur.append(dict(gap, kind="U", units=1))
            cur.append(t)
            pend_f, gap = 0, None
        elif k == "F" and cur is not None and pend_f < 1:
            pend_f += 1
        elif k is None and armed and cur is not None and gap is None and _wordish(t["core"].translate(_ZW)):
            gap = t
        else:
            if cur: runs.append(cur)
            cur, pend_f, gap = None, 0, None
    if cur:
        runs.append(cur)

    non_filler = [t for t in toks if t["kind"] != "F"]
    members = sum(1 for r in runs for t in r if t["kind"] != "U")
    numeric_turn = bool(non_filler) and members / len(non_filler) >= 0.5

    out = []
    for r in runs:
        while r and r[-1]["kind"] == "M":
            r = r[:-1]
        if not r:
            continue
        nxt = toks[r[-1]["i"] + 1] if r[-1]["i"] + 1 < len(toks) else None
        if nxt and nxt["core"].casefold() in _STOP_AFTER:
            continue                                   # 'five hundred rupees' etc.
        units, strong_d, strong_l, mult, has_digit = 0, 0, 0, 1, False
        for t in r:
            k = t["kind"]
            if k == "M":
                mult = t["units"]
                continue
            u = t["units"] * (mult if k in ("W", "FW") else 1)
            mult = 1
            units += u
            if k in ("D", "W", "T", "FW"):
                has_digit = True
            if k in ("D", "T") or (k == "W" and t["core"].casefold() not in AMBIG) or \
                    (k == "FW" and t["core"].casefold() not in _HOMO):
                strong_d += u                     # real digit evidence (typo'd Indian digit words count too)
            elif k == "L":
                strong_l += u
        strong = strong_d + (strong_l if strong_d else 0)   # lone letters / 'do' / 'one' are not evidence by themselves
        if has_digit and strong_d == 0 and strong_l >= 3:     # spelled letters + a garbled/homophone digit word
            ok = units >= 4 or (armed and units >= 3)
        elif has_digit:
            ok = (units >= 3 and strong >= 2 and (strong_d >= 1 or armed)) or \
                 (armed and strong_d >= 1 and units >= 1 and (numeric_turn or units >= 2))
        else:                                   # letters only, e.g. 'A B W N J'
            ok = units >= 4 or (armed and units >= 3)
        if ok:
            s0, e0 = r[0]["s"], r[-1]["e"]
            if armed:                                   # a mangled edge word still belongs to the number
                for step, edge in ((-1, "L"), (1, "R")):
                    j, used = (r[0]["i"] if edge == "L" else r[-1]["i"]) + step, 0
                    while 0 <= j < len(toks) and used < 2:
                        tk = toks[j]
                        if tk["kind"] == "F":
                            j += step
                            continue
                        if tk["kind"] is None and _wordish(tk["core"].translate(_ZW)) and \
                                relaxed_digit(tk["core"].translate(_ZW).casefold()):
                            s0, e0 = (tk["s"], e0) if edge == "L" else (s0, tk["e"])
                            used += 1
                            j += step
                        else:
                            break
            out.append(dict(label="SPOKEN_NUMBER", start=s0, end=e0, source="spoken"))
    if armed and numeric_turn and len(out) > 1:         # one number said in pieces within a turn
        out = [dict(out[0], end=out[-1]["end"])]
    if armed:                                           # id fragments written as one token: 'RIFMM', '87H'
        for t in toks:
            c = t["core"]
            if _FRAG.fullmatch(c) and c not in _STOP and not overlaps(t["s"], t["e"], excl) \
                    and not any(t["s"] < o["end"] and t["e"] > o["start"] for o in out) \
                    and (any(ch.isdigit() for ch in c) or len(c) >= 3):
                out.append(dict(label="ID_FRAGMENT", start=t["s"], end=t["e"], source="spoken"))
    return out


_FRAG = re.compile(r"[A-Z0-9]{3,}")
_STOP = {"EMI", "KYC", "OTP", "UPI", "PAN", "SMS", "NEFT", "RTGS", "IMPS", "HDFC", "ICICI", "OKAY", "YES"}


def span_value(text, start, end):
    """Digits/letters a (possibly spoken) span stands for. '?' marks a word we could not map."""
    toks = _tokens(text[start:end], [])
    out, i, mult = [], 0, 1
    while i < len(toks):
        t = toks[i]
        k, core = t["kind"], t["core"].translate(_ZW)
        c = core.casefold()
        if k == "D":
            out.append(core)
        elif k == "M":
            mult = t["units"]
        elif k == "W":
            v = str(_TEEN_VAL[c]) if c in _TEEN_VAL else _DW[c]
            out.append(v * mult); mult = 1
        elif k == "T":
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt and nxt["kind"] == "W" and nxt["core"].casefold() in _DW and nxt["core"].casefold() not in _TEEN_VAL:
                out.append(str(_TENS_VAL[c]) + _DW[nxt["core"].casefold()]); i += 1
            else:
                out.append(str(_TENS_VAL[c]) + "0")
        elif k == "L":
            out.append(core.upper())
        elif k == "FW":
            out.append((_HOMO_DIGIT.get(c) or relaxed_digit_nonen(c) or relaxed_digit(c) or "?") * mult); mult = 1
        elif k is None and _wordish(core):
            out.append(relaxed_digit(c) or "?")
        i += 1
    return "".join(out)
