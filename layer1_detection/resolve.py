"""Resolve step: merge overlapping spans, give every span a meaningful TYPED label, join pieces of one number.

Input : per call -> normalised turn texts + per-turn raw spans from rules / spoken engine / model.
Output: per turn, non-overlapping spans {label,start,end,source,sources,conf,uncertain, + optional
        entity, part, joined_value, checksum_valid, partial}.

Evidence order for the label (a weaker guess NEVER overrides a stronger one):
  1. exact formats from rules (PAN, IFSC, GSTIN, voter ID, DL, UPI, email, DOB, masked values)
  2. checksum-valid numbers: Aadhaar (Verhoeff), card (Luhn)
  3. phone shape (10 digits starting 6-9, optional country code) when no cue says otherwise
  4. cue words in this turn / the previous 4 turns ('Aadhaar', 'mobile', 'PAN', 'account', 'loan' ...)
  5. shape fallback (rules label); spoken-only untyped runs stay SPOKEN_NUMBER (still masked)
Genuinely uncertain spans keep NUMBER_FRAGMENT / ID_ALNUM / ID_FRAGMENT so the gateway's review agent can see them.

`entity` ids are CALL-WINDOW-LOCAL counters ('e1', 'e2', ...): they are not derived from the value (no hash of a
phone number) and are only stable within one detect_calls() result.
"""
import re

from . import ids
from . import spoken_engine as se

UNCERTAIN = {"NUMBER_FRAGMENT", "ID_ALNUM", "ID_FRAGMENT"}
FREE = {"PERSON", "ADDRESS"}
STRONG_OURS = {"PAN", "IFSC", "EMAIL", "UPI_ID", "GSTIN", "VOTER_ID", "DRIVING_LICENCE", "DOB", "MASKED_ID", "PASSPORT"}
TARGET_LEN = {"PHONE": 10, "AADHAAR": 12, "PAN": 10}
JOINABLE = ("PHONE", "AADHAAR", "PAN", "ACCOUNT", "LOAN")
CUE_WINDOW = 6

_CUES = [
    ("LOAN", re.compile(r"loan\s+(?:account|acct|a/c)|\bloan\b|\blan\b|agreement|application\s+(?:no|number|id)|\breference\b|\bref\b|लोन|ऋण|एग्रीमेंट|ಸಾಲ|கடன்")),
    ("AADHAAR", re.compile(r"aadhaar|aadhar|adhar|uidai|आधार|ಆಧಾರ್|ஆதார்")),
    ("PAN", re.compile(r"\bpan\b|permanent account|पैन|ಪ್ಯಾನ್|பான்")),
    ("PHONE", re.compile(r"mobile|phone|contact|whatsapp|\byour number\b|alternate number|other number|registered number|which number|\bcall (?:me|you) on\b|मोबाइल|मोबाईल|फोन|संपर्क|ಮೊಬೈಲ್|ಫೋನ್|மொபைல்|தொலைபேசி|போன்")),
    ("ACCOUNT", re.compile(r"account|\ba/c\b|\bacct\b|bank|debit|खाता|अकाउंट|ಖಾತೆ|ಅಕೌಂಟ್|கணக்கு|அக்கவுண்ட்")),
]
_PARTIAL = re.compile(r"\b(last|ending|ends|end)\b|\blast\s+(?:4|four)\b|आखिरी|अंतिम|ಕೊನೆಯ|கடைசி")


def _cues(text):
    """[(start, end, label)] cue words in a lowercase turn; 'loan account' counts as LOAN only."""
    low = text.casefold()
    found = []
    for lab, rx in _CUES:
        for m in rx.finditer(low):
            if lab == "ACCOUNT" and any(l == "LOAN" and m.start() < e and m.end() > s for s, e, l in found):
                continue
            found.append((m.start(), m.end(), lab))
    return sorted(found)


# the word 'number' right after a cue word: 'mobile NUMBER', 'loan account NO.', 'फोन नंबर'
_NUMWORD = re.compile(r"\s{0,3}[:=#\-]?\s{0,3}(?:numbers?\b|no\b\.?|num\b|nambar|nambr|नंबर|नम्बर|नं(?![ऀ-ॿ])|क्रमांक|संख्या|ನಂಬರ್|ಸಂಖ್ಯೆ|எண்|நம்பர்)")
_CLAUSE_BREAK = re.compile(r"[.?!;,\n।]")


def _pick(cues, low):
    """The cue that names the number being asked for. With several cues in an EARLIER turn ('the mobile number linked to your loan') the cue directly followed by the word 'number'
    ('mobile number') is the head of the phrase, not the last cue word - but only when no clause break (. ? ! ; , newline) lies between it and the last cue ('Your loan number is on file.
    Please confirm your mobile.' is a mobile question). `low` None (cues in the number's own turn, or text unknown): the last cue, as before."""
    if low is not None:
        heads = [c for c in cues if _NUMWORD.match(low, c[1])]
        if heads and not _CLAUSE_BREAK.search(low, heads[-1][1], cues[-1][0]):
            return heads[-1]
    return cues[-1]


def _cue_and_age(cues_by_turn, turn, s, e, texts=None):
    """-> (cue label or None, age): age = how many turns back the cue sits (0 = this turn). `texts` (lowercased inside) enables the head-of-phrase choice for earlier turns. A cue that comes
    AFTER the number in the same turn ('9876543210 is my loan number') beats a stale one from an earlier turn."""
    low = (lambda t: texts[t].casefold()) if texts else (lambda t: None)
    before = [c for c in cues_by_turn[turn] if c[1] <= s]
    if before:
        return _pick(before, None)[2], 0
    after = [c for c in cues_by_turn[turn] if c[0] >= e]
    for t in range(turn - 1, max(-1, turn - 1 - CUE_WINDOW), -1):
        if cues_by_turn[t]:
            if after and turn - t >= STALE_CUE_AGE:
                return after[0][2], 0
            return _pick(cues_by_turn[t], low(t))[2], turn - t
    return (after[0][2] if after else None), 0


def _cue_for(cues_by_turn, turn, s, e, texts=None):
    return _cue_and_age(cues_by_turn, turn, s, e, texts)[0]


def _partial_intent(texts, turn):
    return any(_PARTIAL.search(texts[t].casefold()) for t in range(max(0, turn - 1), turn + 1))


def _clusters(spans):
    out = []
    for sp in sorted(spans, key=lambda x: (x["start"], -x["end"])):
        if out and sp["start"] < out[-1]["end"]:
            out[-1]["end"] = max(out[-1]["end"], sp["end"])
            out[-1]["spans"].append(sp)
        else:
            out.append({"start": sp["start"], "end": sp["end"], "spans": [sp]})
    return out


STALE_CUE_AGE = 2        # a loan / account cue this many turns back (or more) no longer outvotes the shape of a mobile number


def type_span(value, cue, rule_labels, partial=False, cue_age=0):
    """-> (label, conf, checksum_valid or None).  `value` = digits/letters ('?' = unreadable word).  cue_age: how many turns back the cue was found (0 = this turn)."""
    isdig = value.isdigit()
    isnum = bool(re.fullmatch(r"[0-9?]+", value))        # digits, possibly with unreadable words ('?')
    n = len(value)
    for lab in rule_labels:
        if lab in STRONG_OURS:
            return lab, "high", None
    if isdig:
        if ids.aadhaar_valid(value):
            return "AADHAAR", "high", True
        if ids.luhn_valid(value) and cue not in ("ACCOUNT", "LOAN", "PHONE"):
            return "CARD_NUMBER", "high", True
        if ids.phone_shape(value) and cue in (None, "PHONE"):
            return "PHONE", "high", None
        if n == 10 and ids.phone_shape(value) and cue in ("ACCOUNT", "LOAN") and cue_age >= STALE_CUE_AGE:
            return "PHONE", "medium", None            # a valid mobile shape; the loan / account cue is several turns old and nothing in between mentioned it
    if ids.PAN_RE.fullmatch(value) and cue in (None, "PAN"):
        return "PAN", "high", None
    if cue and n >= 3 and (isnum or cue in ("PAN", "LOAN")):
        cs = (ids.aadhaar_valid(value) if (cue == "AADHAAR" and isdig and n == 12) else None)
        return cue, "medium", cs
    if not isnum and "ID_ALNUM" in rule_labels:                       # letters+digits token: an id, not a digit run
        return "ID_ALNUM", "medium", None
    for lab in ("PHONE", "AADHAAR", "ACCOUNT", "NUMBER_FRAGMENT", "ID_ALNUM", "ADDRESS"):   # shape from rules
        if lab in rule_labels:
            return lab, "medium", (False if lab == "AADHAAR" and isdig and n == 12 else None)
    for lab in ("LOAN", "ACCOUNT"):
        if lab in rule_labels:
            return lab, "medium", None
    if "ID_FRAGMENT" in rule_labels:
        return "ID_FRAGMENT", "low", None
    # spoken-only, untyped
    if isnum and n == 10:
        return "PHONE", "low", None
    if isnum and n == 12:
        return "AADHAAR", "low", None
    if isnum and 9 <= n <= 19:
        return "ACCOUNT", "low", None
    if not isnum and n >= 8 and any(c.isdigit() for c in value):
        return "LOAN", "low", None
    return "SPOKEN_NUMBER", "low", None


def _norm_written(slice_):
    return re.sub(r"[^0-9A-Za-z]", "", slice_).upper()


def resolve_call(texts, speakers, per_turn):
    """texts: normalised turn texts; per_turn: list of raw span lists. -> list of final span lists."""
    cues_by_turn = [_cues(t) for t in texts]
    items = []                       # typed numeric-ish clusters, in order, for joining
    final = [[] for _ in texts]
    for ti, (text, spans) in enumerate(zip(texts, per_turn)):
        for cl in _clusters(spans):
            s, e = cl["start"], cl["end"]
            srcs = []
            for sp in cl["spans"]:
                if sp["source"] not in srcs:
                    srcs.append(sp["source"])
            srcs.sort(key=lambda x: ("rules", "spoken", "model").index(x) if x in ("rules", "spoken", "model") else 9)
            ours = [sp for sp in cl["spans"] if sp["source"] in ("rules", "spoken")]
            base = dict(start=s, end=e, source="+".join(srcs), sources=srcs)
            if not ours:                                              # model-only: keep its label (names, addresses ...)
                best = max(cl["spans"], key=lambda sp: sp["end"] - sp["start"])
                final[ti].append(dict(base, label=best["label"], conf="model", uncertain=False))
                continue
            rule_labels = [sp["label"] for sp in ours if sp["source"] == "rules"]
            spoken_hit = any(sp["source"] == "spoken" for sp in ours)
            if "ADDRESS" in rule_labels:
                final[ti].append(dict(base, label="ADDRESS", conf="high", uncertain=False))
                continue
            slice_ = text[s:e]
            has_digit_signal = any(ch.isdigit() for ch in slice_) or spoken_hit
            if not has_digit_signal:                                  # e.g. letters-only id fragments
                value = _norm_written(slice_)
            elif spoken_hit:
                value = se.span_value(text, s, e) or _norm_written(slice_)
            else:
                value = _norm_written(slice_)
            cue, cue_age = _cue_and_age(cues_by_turn, ti, s, e, texts)
            partial = _partial_intent(texts, ti) and 1 <= len(value) <= 6
            label, conf, cs = type_span(value, cue, rule_labels, partial, cue_age)
            sp_out = dict(base, label=label, conf=conf, uncertain=label in UNCERTAIN)
            if cs is not None:
                sp_out["checksum_valid"] = cs
            if partial and label in JOINABLE:
                sp_out["partial"] = True
            final[ti].append(sp_out)
            items.append(dict(turn=ti, ref=sp_out, value=value, cue=cue, partial=partial, label=label, cue_age=cue_age))

    # ---- join pieces of one number said across turns -------------------------------------------------
    open_, groups = {}, []
    for it in items:
        lab = it["label"] if it["label"] in JOINABLE else it["cue"] if it["cue"] in JOINABLE else None
        n = len(it["value"])
        target = TARGET_LEN.get(lab)
        is_fragment = (lab in JOINABLE and not it["partial"] and
                       ((target is not None and n < target) or (target is None and n <= 8)))
        if lab and is_fragment and it["ref"]["label"] != "ADDRESS":
            o = open_.get(lab)
            if o and it["turn"] > o["last"] and it["turn"] - o["last"] <= CUE_WINDOW and \
                    (target is None or o["len"] < target):
                o["items"].append(it); o["last"] = it["turn"]; o["len"] += n
                if target is not None and o["len"] >= target:
                    groups.append(o["items"]); del open_[lab]
            else:
                if o:
                    groups.append(o["items"])
                open_[lab] = {"items": [it], "last": it["turn"], "len": n}
        elif lab:
            if lab in open_:
                groups.append(open_.pop(lab)["items"])
            groups.append([it])
        else:
            groups.append([it])
    groups += [o["items"] for o in open_.values()]

    counter = 0
    for g in sorted(groups, key=lambda g: (g[0]["turn"], g[0]["ref"]["start"])):
        counter += 1
        joined = "".join(i["value"] for i in g)
        if len(g) > 1:
            cue = g[0]["cue"] if g[0]["cue"] in JOINABLE else g[0]["label"]
            label, conf, cs = type_span(joined, cue, [], False, min(i["cue_age"] for i in g))
            for i in g:
                i["ref"].update(label=label, conf=conf, uncertain=label in UNCERTAIN)
                if cs is not None:
                    i["ref"]["checksum_valid"] = cs
        for k, i in enumerate(g, 1):
            i["ref"].update(entity=f"e{counter}", part=k, joined_value=joined)
    return final
