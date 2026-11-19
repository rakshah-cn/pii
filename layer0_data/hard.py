"""'Hard mode' perturbations: make spoken numbers look like noisy ASR output.
These variants are NOT in the detector's lexicon on purpose (held-out), so the hard set tests
generalisation. !! Spellings are plausible ASR variants written from model knowledge, not verified
by native speakers. !!"""
import random

_ALT = {
    "en": {"two": ["to", "too"], "four": ["for"], "eight": ["ate"], "one": ["won"], "zero": ["nil"]},
    "hi_rom": {"shunya": ["siffar", "sunya"], "ek": ["ik"], "do": ["doh"], "teen": ["tin", "theen"],
               "char": ["chaar"], "paanch": ["panj", "paach", "panch"], "chhe": ["chhah", "cheh"],
               "saat": ["sat", "saath"], "aath": ["aat", "ath"], "nau": ["nao", "nou"]},
    "kn_rom": {"sonne": ["sunne", "sonna"], "ondu": ["ondhu", "onde"], "eradu": ["yeradu", "eradhu"],
               "mooru": ["muru", "moru"], "naalku": ["nalku", "naalk"], "aidu": ["ayidu", "aydu"],
               "aaru": ["aru"], "elu": ["yelu", "aelu"], "entu": ["yentu", "entthu"],
               "ombattu": ["ombhattu", "ombatthu"]},
    "ta_rom": {"poojyam": ["pujyam", "poojiyam"], "ondru": ["ondhru", "onru", "onnu"],
               "rendu": ["irandu", "renndu"], "moonu": ["moondru", "munu", "moonru"],
               "naalu": ["nangu", "naangu"], "anju": ["ainthu", "aanju"], "aaru": ["aru"],
               "ezhu": ["yezhu"], "ettu": ["yettu", "ettuu"], "onbadhu": ["onbathu", "ombadhu"]},
}
_CONFUSABLE = {"B": "D", "D": "B", "P": "T", "T": "P", "V": "B", "M": "N", "N": "M", "F": "S", "S": "F"}


def _edit(w, rng):
    """one-character slip: drop an inner char, swap two neighbours, or double a char."""
    if len(w) < 4:
        return w
    i = rng.randint(1, len(w) - 2)
    op = rng.choice(["drop", "swap", "dup"])
    if op == "drop":
        return w[:i] + w[i + 1:]
    if op == "swap":
        return w[:i] + w[i + 1] + w[i] + w[i + 2:]
    return w[:i] + w[i] + w[i:]


def perturb(text, lang, rng, p_variant=0.35, p_edit=0.15, p_drop=0.04, p_letter=0.06):
    known = set(w for ws in spoken.LEX.get(lang, []) for w in ws)
    out = []
    for t in text.split():
        if t in known:
            r = rng.random()
            if r < p_drop:
                continue                                   # ASR swallowed a digit
            alts = _ALT.get(lang, {}).get(t)
            if alts and rng.random() < p_variant:
                t = rng.choice(alts)
            elif rng.random() < p_edit:
                t = _edit(t, rng)
        elif len(t) == 1 and t in _CONFUSABLE and rng.random() < p_letter:
            t = _CONFUSABLE[t]                             # B heard as D, etc.
        out.append(t)
    return " ".join(out) or text


_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _pair_words(n):
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])


def render_tens_en(digits, rng):
    """English 'ninety eight seven six five' style: some digit pairs said as one number."""
    out, i = [], 0
    while i < len(digits):
        if i + 1 < len(digits) and rng.random() < 0.5 and digits[i] != "0":
            out.append(_pair_words(int(digits[i:i + 2]))); i += 2
        else:
            out.append(_ONES[int(digits[i])] if digits[i] != "0" else rng.choice(["zero", "oh"])); i += 1
    return " ".join(out)


from . import spoken  # noqa: E402  (placed last: spoken has no dependency on this module)
