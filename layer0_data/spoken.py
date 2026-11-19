"""Spoken-number engine: digits <-> words, per language.

Used in two places:
  1. DATA GENERATION: render a fake number as spoken words (render_digits).
  2. DETECTION (later): parse spoken words back into digits (parse_spoken).
Having both lets us test that a rendering really means the claimed number.

!! NEEDS NATIVE-SPEAKER REVIEW: Kannada/Tamil/Hindi word lists below were written
!! from model knowledge. Mark them verified only after a native speaker checks.
"""
import random

# Each language: list of spellings per digit 0-9 (first = canonical, others = variants/ASR styles)
LEX = {
    "en": [["zero", "oh"], ["one"], ["two"], ["three"], ["four"], ["five"],
           ["six"], ["seven"], ["eight"], ["nine"]],
    "hi": [["शून्य", "ज़ीरो"], ["एक"], ["दो"], ["तीन"], ["चार"], ["पाँच", "पांच"],
           ["छह", "छः", "छे"], ["सात"], ["आठ"], ["नौ"]],
    "hi_rom": [["shunya", "zero"], ["ek"], ["do"], ["teen"], ["char"], ["paanch", "panch"],
               ["chhe", "chah"], ["saat"], ["aath"], ["nau"]],
    "kn": [["ಸೊನ್ನೆ"], ["ಒಂದು"], ["ಎರಡು"], ["ಮೂರು"], ["ನಾಲ್ಕು"], ["ಐದು"],
           ["ಆರು"], ["ಏಳು"], ["ಎಂಟು"], ["ಒಂಬತ್ತು"]],
    "kn_rom": [["sonne", "zero"], ["ondu"], ["eradu"], ["mooru"], ["naalku"], ["aidu"],
               ["aaru"], ["elu"], ["entu"], ["ombattu"]],
    "ta": [["பூஜ்யம்", "சைபர்"], ["ஒன்று"], ["இரண்டு"], ["மூன்று"], ["நான்கு"], ["ஐந்து"],
           ["ஆறு"], ["ஏழு"], ["எட்டு"], ["ஒன்பது"]],
    "ta_rom": [["poojyam", "zero"], ["ondru"], ["rendu"], ["moonu"], ["naalu"], ["anju"],
               ["aaru"], ["ezhu"], ["ettu"], ["onbadhu"]],
    # 5th language = Marathi (chosen as easiest: Devanagari, shares many words with Hindi). !! needs native review !!
    "mr": [["शून्य"], ["एक"], ["दोन"], ["तीन"], ["चार"], ["पाच"], ["सहा"], ["सात"], ["आठ"], ["नऊ"]],
    "mr_rom": [["shunya", "zero"], ["ek"], ["don"], ["teen"], ["char"], ["paach", "pach"], ["saha"],
               ["saat"], ["aath"], ["nau"]],
    # English digit words written in Indic script (code-mixed speech, ASR output). Detection only:
    # the generator never renders these. !! UNVERIFIED by native speakers (written from model knowledge) !!
    "hi_en": [["ज़ीरो", "जीरो"], ["वन"], ["टू"], ["थ्री"], ["फोर"], ["फाइव"], ["सिक्स"], ["सेवन"],
              ["एट", "एइट"], ["नाइन"]],
    "kn_en": [["ಜೀರೋ"], ["ವನ್"], ["ಟೂ"], ["ತ್ರೀ"], ["ಫೋರ್"], ["ಫೈವ್"], ["ಸಿಕ್ಸ್"], ["ಸೆವೆನ್"],
              ["ಎಯ್ಟ್", "ಎಟ್"], ["ನೈನ್"]],
    "ta_en": [["ஜீரோ"], ["வன்"], ["டூ"], ["த்ரி", "த்ரீ"], ["ஃபோர்", "போர்"], ["ஃபைவ்", "பைவ்"],
              ["சிக்ஸ்"], ["செவன்"], ["எய்ட்"], ["நைன்"]],
}
# If the organisers name a different 5th language (e.g. Telugu), add its entry here: one table.

# "double 5" / "triple 5" -- common in English & Hindi-speaking callers
MULT = {
    "en": {2: "double", 3: "triple"},
    "hi": {2: "डबल", 3: "ट्रिपल"},
    "hi_rom": {2: "double", 3: "triple"},
    "mr": {2: "डबल", 3: "ट्रिपल"},
    "mr_rom": {2: "double", 3: "triple"},
}

FILLERS = {
    "en": ["um", "uh", "okay", "so"],
    "hi": ["अम्म", "तो", "हाँ"],
    "hi_rom": ["umm", "toh", "haan"],
    "kn": ["ಅಂದ್ರೆ", "ಹಾ"],
    "kn_rom": ["andre", "haa"],
    "ta": ["அது", "ஆமா"],
    "ta_rom": ["adhu", "aama"],
    "mr": ["म्हणजे", "हो"],
    "mr_rom": ["mhanje", "ho"],
}


def render_digits(digits, lang, rng=None, use_multipliers=True, filler_prob=0.0,
                  variants=True):
    """'9876500123' -> spoken words. Returns the text AND nothing hidden:
    the caller already knows the digits, so labels stay exact."""
    rng = rng or random
    words, i = [], 0
    while i < len(digits):
        d = int(digits[i])
        run = 1
        while i + run < len(digits) and digits[i + run] == digits[i]:
            run += 1
        spelled = LEX[lang][d]
        pick = lambda: rng.choice(spelled) if variants else spelled[0]
        if use_multipliers and lang in MULT and run in (2, 3) and rng.random() < 0.7:
            words.append(f"{MULT[lang][run]} {pick()}")
            i += run
        else:
            words.append(pick())
            i += 1
        if filler_prob and rng.random() < filler_prob and i < len(digits):
            words.append(rng.choice(FILLERS[lang]))
    return " ".join(words)


def split_for_turns(digits, rng=None):
    """Cut a digit string into two parts (for 'number split across two turns')."""
    rng = rng or random
    k = rng.randint(3, len(digits) - 3)
    return digits[:k], digits[k:]


# ---------- parser (inverse) ----------
def _build_reverse():
    rev = {}
    for lang, rows in LEX.items():
        for d, spellings in enumerate(rows):
            for s in spellings:
                rev.setdefault(s, set()).add(str(d))
    return rev

_REV = _build_reverse()
_MULT_REV = {}
for _l, _m in MULT.items():
    for _n, _w in _m.items():
        _MULT_REV[_w] = _n


def parse_spoken(text):
    """Extract digits from spoken text. Ignores non-digit words (fillers).
    Returns the digit string. Words matching several digits in different
    languages (e.g. Tamil 'aaru'=6 vs Kannada 'aaru'=6 agree; clashes are
    reported by find_clashes) take the first listed."""
    toks = text.replace(",", " ").replace(".", " ").lower().split()
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t in _MULT_REV and i + 1 < len(toks) and toks[i + 1] in _REV:
            out.append(sorted(_REV[toks[i + 1]])[0] * _MULT_REV[t])
            i += 2
            continue
        if t in _REV:
            out.append(sorted(_REV[t])[0])
        i += 1
    return "".join(out)


def find_clashes():
    """Words that mean different digits in different languages (ambiguity risk)."""
    return {w: sorted(d) for w, d in _REV.items() if len(d) > 1}


# ---------- spelled-out letters+digits (PAN, loan numbers): "A B W N J 0 3 H" ----------
def render_spelled(value, lang, rng=None, digit_word_prob=0.3):
    """Letters said one by one; digits either as digit characters or as words."""
    rng = rng or random
    out = []
    for ch in value:
        if ch.isalpha():
            out.append(ch.upper())
        elif rng.random() < digit_word_prob:
            out.append(rng.choice(LEX[lang][int(ch)]))
        else:
            out.append(ch)
    return " ".join(out)


def parse_spelled(text):
    """Inverse of render_spelled: single letters, digit chars, digit words."""
    out = []
    for t in text.split():
        if len(t) == 1 and t.isalpha():
            out.append(t.upper())
        elif t.isdigit():
            out.append(t)
        elif t.lower() in _REV:
            out.append(sorted(_REV[t.lower()])[0])
    return "".join(out)
