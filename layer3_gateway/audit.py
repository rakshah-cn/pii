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
"""
import re

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
_ZW = {ord(c): " " for c in "​‌‍⁠﻿ "}      # same length: offsets stay valid
_SEP = r"[ \t\-.,/()+_]{1,2}"
_DIGIT_RUN = re.compile(rf"(?<!\d)\d(?:(?:{_SEP})?\d){{5,}}(?!\d)")
_AMOUNT_SHAPE = re.compile(r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?")
_DATE_SHAPE = re.compile(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}")
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


_TOKEN = re.compile(r"[^\s\-/_.,;:!?()+\[\]{}|।…\"']+")
_FAMILY2 = [("PAN", re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])")),
            ("IFSC", re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])")),
            ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
            ("UPI", re.compile(r"(?<![\w.])[\w.-]{3,}@[a-z]{2,}(?![\w.])")),
            ("IDLIKE", re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9/_-]*[A-Za-z])(?=[A-Za-z0-9/_-]*\d)[A-Za-z0-9/_-]{8,}(?![A-Za-z0-9])"))]


def _prep(text):
    return text.translate(_ZW)


def _is_amount_or_date(text, m):
    """True only for a number that REALLY looks like an amount/date and is short enough to be one (<= 8 digits)."""
    run = m.group()
    if len(re.sub(r"\D", "", run)) > 8:
        return False
    return bool(_AMOUNT_SHAPE.fullmatch(run) or _DATE_SHAPE.fullmatch(run) or
                _RUPEE_BEFORE.search(text[:m.start()]) or _RUPEE_AFTER.match(text, m.end()) or _ORDINAL_AFTER.match(text, m.end()))   # '4652 15th' is an amount then a day, not a 6-digit run


def _amount_ranges(text):
    out = [m.span() for m in _ORDINAL.finditer(text)]
    out += [m.span() for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text) if _is_amount_or_date(text, m)]
    return out


def _hit(a, b, ranges):
    return any(a < e and b > s for s, e in ranges)


def tripwire(text):
    text = _prep(text)
    out = [{"label": "DIGITS", "start": m.start(), "end": m.end(), "source": "audit-tripwire"}
           for m in _DIGIT_RUN.finditer(text) if not _is_amount_or_date(text, m)]
    excl = _amount_ranges(text)
    run, gap = [], 0
    for m in _TOKEN.finditer(text):
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
            _flush(run, out)
            run, gap = [], 0
    _flush(run, out)
    return out


def _flush(run, out):
    if len([w for _, _, w, strong in run if strong and w not in _MULT]) >= 4:
        out.append({"label": "DIGIT_WORDS", "start": run[0][0], "end": run[-1][1], "source": "audit-tripwire"})


def family2(text):
    text = _prep(text)
    excl = _amount_ranges(text)
    return [{"label": lab, "start": m.start(), "end": m.end(), "source": "audit-family2"}
            for lab, p in _FAMILY2 for m in p.finditer(text) if not _hit(m.start(), m.end(), excl)]


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
