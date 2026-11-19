"""Independent leakage checks for the gateway's final audit and for scanning LLM replies.

Written SEPARATELY from layer1_detection on purpose (own word list, own patterns, no imports from it), so a blind
spot in the detector is less likely to be a blind spot here too. They are blunt on purpose: a false alarm only costs
one more masking pass (the span is tokenised, never blocked); a miss leaks.

  tripwire(text)          6+ digits (any separators / split by punctuation / zero-width chars) or 4+ digit-words
  family2(text)           PAN / IFSC / email / UPI-like / id-like tokens
  utility(text)           check 4: placeholders must not be more than 85% of a message longer than 20 characters (a message that is nearly all placeholders is useless to the LLM)
  overmask_share(...)     how much of the message was replaced by tokens (kept for the warning level)
Everything is run on text where vault placeholders were blanked first (they contain words like NUMBER).

Amounts and dates are excluded ONLY when they really look like one (comma-grouped, rupee-marked, d/m/y) AND have at
most 8 digits, so a 10-digit phone number can never hide behind a rupee sign, "rupees" or a comma.

Digits may be separated by up to 4 characters that are not letters or digits (spaces, dashes of every kind, dots, slashes, bullets, pipes, line breaks, soft hyphens, direction marks, <br> ...)
or by up to 8 whitespace characters; invisible format characters never split a number or a word; superscript / circled digits count as digits; spoken digit words are split by ANY
non-letter. Every pattern here is linear in the length of the text (a review showed the earlier ones took seconds on 100 KB of hostile text, and that a timeout cannot fire while a
regular expression holds the interpreter).
KNOWN LIMITS (accepted, documented): a LETTER between digits ("9x8x4...") is read as a word boundary, not a separator; an amount of at most 8 digits next to a rupee marker is treated as
an amount ("Rs 12345678" passes): the price of not blocking every message that mentions money.
"""
import bisect
import re
import unicodedata

_WORDS = set("""
zero oh one two three four five six seven eight nine double triple
ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen
twenty thirty forty fifty sixty seventy eighty ninety
shunya ek do teen char chaar paanch panch chhe chah saat sat aath aat nau nav
sonne ondu eradu mooru naalku aidu aaru elu entu ombattu
poojyam ondru rendu moonu naalu anju ezhu ettu onbadhu
don paach saha
शून्य एक दो तीन चार पाँच पांच छह छः सात आठ नौ दोन पाच सहा नऊ डबल ट्रिपल
ಸೊನ್ನೆ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು ಆರು ಏಳು ಎಂಟು ಒಂಬತ್ತು
பூஜ்யம் ஒன்று இரண்டு மூன்று நான்கு ஐந்து ஆறு ஏழு எட்டு ஒன்பது
""".split())
# Variants and fillers observed in the DEV split (never the test split) after measuring what the first version of this tripwire missed.
_WORDS |= set("""
nil zro sunya sunye siffar pujyam poojiyam poojyam sonna sunne ज़ीरो சைபர் छे
doh ik nou nao ath aath atah saath sata theen tin chhah cheh panj pach pcah
aru muru moru yelu aelu ayidu aydu ondhu onde eradhu yeradu yentu entthu ombatthu ombhattu naalk nalku
yezhu nangu naangu ombadhu onbathu renndu yettu ettuu ainthu aanju irandu onru onnu moonru moondru ondhru munu
""".split())
_WEAK = {"won", "ate", "for", "to", "too", "tu"}            # English homophones of one / eight / four / two: count ONLY after real digit words (never start a run)
_FILLERS = {"um", "umm", "uh", "hmm", "ah", "er", "okay", "ok", "so", "like", "haa", "haan", "ho", "andre", "aama", "adhu", "mhanje", "toh",
            "ಹಾ", "ಅಂದ್ರೆ", "हाँ", "हो", "अम्म", "तो", "ஆமா", "அது", "म्हणजे"}
_MULT = {"double", "triple", "डबल", "ट्रिपल"}

def _digit_map():
    """Digits that are not Unicode DECIMAL digits (superscripts, subscripts, circled / parenthesised / dingbat digits) -> '0'..'9', one character for one character."""
    t = {}
    for lo, hi in ((0xB2, 0xB3), (0xB9, 0xB9), (0x2070, 0x209F), (0x2460, 0x249B), (0x24EA, 0x24FF), (0x2776, 0x2793), (0x3220, 0x3229), (0x3280, 0x3289), (0x1F100, 0x1F10A)):
        for cp in range(lo, hi + 1):
            d = unicodedata.digit(chr(cp), None)
            if d is not None:
                t[cp] = str(d)
    return t


_DIGITS = _digit_map()
_FMT = re.compile("[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]")    # invisible format characters: soft hyphen, zero-width, direction marks, joiners, BOM ...
_TAG = re.compile(r"</?(?:br|p|div|span|li|ul|ol|td|tr)\b[^<>]{0,12}>", re.I)                       # layout tags a client may paste between digits


def _clean(text):
    """-> (clean, idx). clean = the text with exotic digits mapped to 0-9 and layout tags blanked (both same length) and invisible format characters REMOVED, so none of them can split a
    number or a word. idx[i] = position in the ORIGINAL text of clean[i]; None when the two are identical index for index. Findings are mapped back with _back()."""
    t = text if text.isascii() else text.translate(_DIGITS)
    if "<" in t:
        t = _TAG.sub(lambda m: " " * len(m.group()), t)
    if t.isascii() or not _FMT.search(t):
        return t, None
    out, idx, pos = [], [], 0
    for m in _FMT.finditer(t):
        out.append(t[pos:m.start()]); idx.extend(range(pos, m.start())); pos = m.end()
    out.append(t[pos:]); idx.extend(range(pos, len(t)))
    return "".join(out), idx


def _back(idx, a, b):
    return (a, b) if idx is None else (idx[a], idx[b - 1] + 1)


_GAP = r"(?:\s{1,8}|[\W_]{1,4})?"                                    # between two digits: up to 8 spaces, or up to 4 characters that are neither letters nor digits
_DIGIT_RUN = re.compile(rf"(?<!\d)\d(?:{_GAP}\d){{5,}}(?!\d)")
_AMOUNT_SHAPE = re.compile(r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?")
_DATE_SHAPE = re.compile(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}")
_YEAR_RANGE = re.compile(r"(?:19|20)\d\d\s{0,2}[-\u2013\u2014/]\s{0,2}(?:(?:19|20)\d\d|\d\d)")
_RUPEE_BEFORE = re.compile(r"(?:₹|\brs\.?|\binr)\s*$", re.I)
_ORDINAL_AFTER = re.compile(r"(?:st|nd|rd|th)\b", re.I)
_RUPEE_AFTER = re.compile(r"\s*(?:rupees?|rs\b\.?|inr)", re.I)
_ORDINAL = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)\b", re.I)
def _collapse(w):
    return re.sub(r"(.)\1+", r"\1", w)


_COLL = {_collapse(x) for x in _WORDS if len(_collapse(x)) >= 3}
_BYLEN = {}
for _c in _COLL:
    if len(_c) >= 5:
        _BYLEN.setdefault(len(_c), []).append(_c)


def _edit1(a, b):
    """True if a and b differ by exactly one substitution, insertion or deletion."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    a, b = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def _fuzzy_digit_word(w):
    """A noisy spelling of a known number word: letters stretched (teeen, saaat, ettuu) or one edit away (ombattu / ombatthu) for words of 5+ letters. Short words must match exactly."""
    c = _collapse(w)
    if len(c) >= 4 and c in _COLL:
        return True
    if len(c) == 3 and len(w) > 3 and c in _COLL:            # 'teeen' -> 'ten': only when the letters really were stretched
        return True
    return len(c) >= 5 and any(_edit1(c, k) for n in (len(c) - 1, len(c), len(c) + 1) for k in _BYLEN.get(n, ()))


_MARKS = "".join(chr(c) for c in range(0x0300, 0x0E00) if unicodedata.category(chr(c)) in ("Mn", "Mc", "Me"))      # combining marks / Indic vowel signs: part of a word
_TOKEN = re.compile(rf"(?:[^\W\d_]|[{re.escape(_MARKS)}])+|\d+")                                                    # a token is a run of letters (with their marks) OR a run of digits; ANYTHING else separates
_FAMILY2 = [("PAN", re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])")),
            ("IFSC", re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])")),
            ("EMAIL", re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}\.[\w.]{1,63}")),                                   # bounded: no quantifier can scan to the end of the text from every start
            ("UPI", re.compile(r"(?<![\w.])[\w.-]{3,64}@[a-z]{2,}(?![\w.])")),
            ("IDLIKE", re.compile(r"[A-Za-z0-9/_-]{8,}"))]                                                     # a maximal run; kept only when it mixes letters and digits (checked in family2)
_HAS_LETTER, _HAS_DIGIT = re.compile(r"[A-Za-z]"), re.compile(r"\d")


def _is_amount_or_date(text, m):
    """True only for a number that REALLY looks like an amount/date and is short enough to be one (<= 8 digits)."""
    run = m.group()
    if len(re.sub(r"\D", "", run)) > 8:
        return False
    return bool(_AMOUNT_SHAPE.fullmatch(run) or _DATE_SHAPE.fullmatch(run) or _YEAR_RANGE.fullmatch(run) or
                _RUPEE_BEFORE.search(text, max(0, m.start() - 12), m.start()) or _RUPEE_AFTER.match(text, m.end()) or _ORDINAL_AFTER.match(text, m.end()))   # '4652 15th' is an amount then a day, not a 6-digit run


def _merge(ranges):
    ranges.sort()
    starts, ends = [], []
    for s, e in ranges:
        if ends and s <= ends[-1]:
            ends[-1] = max(ends[-1], e)
        else:
            starts.append(s); ends.append(e)
    return starts, ends


def _amount_ranges(text):
    out = [m.span() for m in _ORDINAL.finditer(text)]
    out += [m.span() for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text) if _is_amount_or_date(text, m)]
    return _merge(out)


def _hit(a, b, rng):
    """Does [a, b) overlap any of the (sorted, merged) ranges? One binary search instead of a scan over all of them."""
    starts, ends = rng
    i = bisect.bisect_left(starts, b) - 1
    return i >= 0 and ends[i] > a


def tripwire(text):
    c, idx = _clean(text)
    out = []
    for m in _DIGIT_RUN.finditer(c):
        if not _is_amount_or_date(c, m):
            a, b = _back(idx, m.start(), m.end())
            out.append({"label": "DIGITS", "start": a, "end": b, "source": "audit-tripwire"})
    excl = _amount_ranges(c)
    run, gap = [], 0
    for m in _TOKEN.finditer(c):
        w = m.group().casefold()
        if (w in _WORDS or w in _MULT or _fuzzy_digit_word(w)) and not _hit(m.start(), m.end(), excl):
            run.append((m.start(), m.end(), w, True))
            gap = 0
        elif w in _WEAK and run and not _hit(m.start(), m.end(), excl):
            run.append((m.start(), m.end(), w, False))             # a homophone after real digit words: part of the run, but never counted as one of the 4
            gap = 0
        elif w in _FILLERS and run and gap < 1:
            gap += 1                                   # one filler ("um") between digit words does not break the run
        else:
            _flush(run, out, idx)
            run, gap = [], 0
    _flush(run, out, idx)
    return out


def _flush(run, out, idx=None):
    if len([w for _, _, w, strong in run if strong and w not in _MULT]) >= 4:
        a, b = _back(idx, run[0][0], run[-1][1])
        out.append({"label": "DIGIT_WORDS", "start": a, "end": b, "source": "audit-tripwire"})


def family2(text):
    c, idx = _clean(text)
    excl = _amount_ranges(c)
    out = []
    for lab, p in _FAMILY2:
        for m in p.finditer(c):
            if lab == "IDLIKE" and not (_HAS_LETTER.search(m.group()) and _HAS_DIGIT.search(m.group())):
                continue
            if _hit(m.start(), m.end(), excl):
                continue
            a, b = _back(idx, m.start(), m.end())
            out.append({"label": lab, "start": a, "end": b, "source": "audit-family2"})
    return out


def scan(text, allowed=()):
    """tripwire + family2, minus values the review agent deliberately left (e.g. an order number)."""
    allowed = {a.strip() for a in allowed}
    return [f for f in tripwire(text) + family2(text) if text[f["start"]:f["end"]].strip() not in allowed]


def overmask_share(original_len, masked_chars):
    return masked_chars / original_len if original_len else 0.0


TOKEN = re.compile(r"<[A-Z_]+_\d+>")
UTILITY_MAX_SHARE = 0.85
UTILITY_MIN_LEN = 20


def utility(tokenised_text, max_share=UTILITY_MAX_SHARE, min_len=UTILITY_MIN_LEN):
    """Check 4, measured on the TOKENISED text the LLM would receive: placeholder characters / all characters. Short messages (<= 20 chars, e.g. just a phone number)
    are exempt. Returns {"share", "ok"}."""
    n = len(tokenised_text)
    share = sum(len(m.group()) for m in TOKEN.finditer(tokenised_text)) / n if n else 0.0
    return {"share": round(share, 3), "ok": not (n > min_len and share > max_share), "length": n}
