"""Written identifiers: regex + checksum shapes. Tuned for RECALL: long digit runs that match no known
format are masked too ('catch-all'), because a leaked partial ID is worse than an extra mask.
Amounts and ordinal dates are carved out first so they are never masked.

Span dicts: {label,start,end,source:'rules'} plus optional `strong` (True = exact format/checksum shape).
Labels produced here: PHONE AADHAAR ACCOUNT NUMBER_FRAGMENT PAN IFSC GSTIN VOTER_ID DRIVING_LICENCE
PASSPORT UPI_ID EMAIL DOB MASKED_ID ID_ALNUM ADDRESS LOAN(cue-based) ACCOUNT(cue-based)."""
import re

# things that look like numbers but are NOT PII
_EXCL = [
    re.compile(r"(?:₹|rs\.?|inr)\s?\d[\d,]*(?:\.\d+)?", re.I),
    re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:rupees?|rs\b\.?|inr)", re.I),
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)\b", re.I),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),                      # clock times
]
# >= 6 digits, single space/dash allowed; never inside an alphanumeric id ('LAN-2023-00123456', 'ABC1234567')
_NUM_RUN = re.compile(r"(?<![A-Za-z\d₹,.])(?<![A-Za-z][\-/_])\d(?:[ \-]?\d){5,}(?![\d]|[A-Za-z])")
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])")
_IFSC = re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])")
_GSTIN = re.compile(r"(?<![A-Za-z0-9])\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z](?![A-Za-z0-9])")
_VOTER = re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}\d{7}(?![A-Za-z0-9])")
_PASSPORT = re.compile(r"(?<![A-Za-z0-9])[A-PR-WY][1-9]\d{6}(?![A-Za-z0-9])")
_DL = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}[ -]?\d{2}[ -]?(?:19|20)\d{2}\d{7}(?![A-Za-z0-9])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_UPI = re.compile(r"(?<![\w.+-])[A-Za-z0-9._-]{2,}@[A-Za-z]{2,}(?![\w.-]*\.[A-Za-z])")
_MASKED = re.compile(r"(?<![A-Za-z0-9])(?:[Xx*•]{2,}[ \-]?){1,4}\d{2,6}(?!\d)|(?<![A-Za-z0-9])\d{2,6}[ \-]?(?:[Xx*•]{3,}[ \-]?)+")
_ID_SHAPE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{1,14}(?:[/_\-.][A-Za-z0-9]{1,14}){0,5}(?![A-Za-z0-9])")
_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_YEAR = r"(?:19[3-9]\d|200[0-8])"                                       # adults only: due dates are 2025/26
_DOB_TXT1 = re.compile(rf"(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\.?,?\s+{_YEAR}(?!\d)", re.I)
_DOB_TXT2 = re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+{_YEAR}(?!\d)", re.I)
_DOB_NUM4 = re.compile(rf"(?<!\d)(?:0?[1-9]|[12]\d|3[01])[/\-.](?:0?[1-9]|1[0-2])[/\-.]{_YEAR}(?!\d)")
_DOB_NUM2 = re.compile(r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])[/\-.](?:0?[1-9]|1[0-2])[/\-.]\d{2}(?!\d)")
_DOB_CUE = re.compile(r"\b(born|dob|birth|birthday|date of birth|janm)\b|जन्म|ಜನ್ಮ|பிறந்த", re.I)
_PASSPORT_CUE = re.compile(r"passport|पासपोर्ट", re.I)
# loan / account cue then the next token(s) containing a digit
_CUE = re.compile(r"\b(loan|lan|agreement|application|reference|ref|account|acct|a/c)\b", re.I)
_CONNECT = {"number", "no", "no.", "num", "is", "was", "of", "the", "id", "it", "its", "ends", "with", "last", "digits",
            "digit", "ending", "my", "your", "our", "ref", "reference", "account", "loan", "a/c", ":", "-", "#", "hai", "है"}
_LOAN_CUES = {"loan", "lan", "agreement", "application", "reference", "ref"}
_TOK = re.compile(r"\S+")


def excluded_ranges(text):
    return [m.span() for p in _EXCL for m in p.finditer(text)]


def overlaps(a, b, ranges):
    return any(a < e and b > s for s, e in ranges)


def _addresses(text, excl):
    """Pincode-anchored address: [house no.,] chunk, chunk ... PINCODE (needs at least one comma)."""
    out = []
    for m in re.finditer(r"(?<!\d)[1-9]\d{5}(?!\d)", text):
        if overlaps(m.start(), m.end(), excl):
            continue
        seg_start = max(text.rfind(c, 0, m.start()) for c in ".?!") + 1
        parts = text[seg_start:m.start()].split(",")
        if len(parts) < 2:
            continue
        first = parts[0].rstrip()
        word = first.split(" ")[-1] if first else ""
        if word and len(word) <= 5 and any(ch.isdigit() for ch in word):
            start = seg_start + len(first) - len(word)           # keep a leading house number
        else:
            start = seg_start + len(parts[0]) + 1                # drop 'It is ...' lead-in
            while start < m.start() and text[start] == " ":
                start += 1
        out.append(dict(label="ADDRESS", start=start, end=m.end(), source="rules", strong=True))
    return out


def _cue_ids(text, excl):
    """'loan number is HL-2231', 'account number 20318822': mask the next token(s) that contain a digit."""
    out = []
    toks = [(m.start(), m.end(), m.group()) for m in _TOK.finditer(text)]
    for i, (s, e, w) in enumerate(toks):
        cm = _CUE.fullmatch(w.strip(".,;:!?").lower()) if w.strip(".,;:!?") else None
        if not cm:
            continue
        cue = cm.group(1).lower()
        j, skipped = i + 1, 0
        while j < len(toks) and skipped <= 4:
            ts, te, tw = toks[j]
            core = tw.strip(".,;:!?()[]\"'")
            if core.lower() in _CONNECT or core == "":
                j += 1; skipped += 1
                continue
            if any(ch.isdigit() for ch in core) and len(core) >= 3 and not overlaps(ts, te, excl):
                cs = ts + (len(tw) - len(tw.lstrip(".,;:!?()[]\"'")))
                out.append(dict(label="LOAN" if cue in _LOAN_CUES else "ACCOUNT", start=cs, end=cs + len(core),
                                source="rules", cue=cue))
            break
    return out


def find(text, excl):
    spans = _addresses(text, excl)
    # structured identifiers first (strong shapes)
    for pat, lab in ((_PAN, "PAN"), (_IFSC, "IFSC"), (_GSTIN, "GSTIN"), (_VOTER, "VOTER_ID"), (_DL, "DRIVING_LICENCE")):
        for m in pat.finditer(text):
            spans.append(dict(label=lab, start=m.start(), end=m.end(), source="rules", strong=True))
    for m in _EMAIL.finditer(text):
        spans.append(dict(label="EMAIL", start=m.start(), end=m.end(), source="rules", strong=True))
    for m in _UPI.finditer(text):
        if not any(m.start() < s["end"] and m.end() > s["start"] for s in spans):
            spans.append(dict(label="UPI_ID", start=m.start(), end=m.end(), source="rules", strong=True))
    for m in _MASKED.finditer(text):
        spans.append(dict(label="MASKED_ID", start=m.start(), end=m.end(), source="rules", strong=True))
    has_dob_cue = bool(_DOB_CUE.search(text))
    for pat in (_DOB_TXT1, _DOB_TXT2, _DOB_NUM4) + ((_DOB_NUM2,) if has_dob_cue else ()):
        for m in pat.finditer(text):
            spans.append(dict(label="DOB", start=m.start(), end=m.end(), source="rules", strong=True))
    has_passport = bool(_PASSPORT_CUE.search(text))
    for m in _PASSPORT.finditer(text):
        spans.append(dict(label="PASSPORT" if has_passport else "ID_ALNUM", start=m.start(), end=m.end(),
                          source="rules", strong=has_passport))
    # numeric runs (>= 6 digits)
    for m in _NUM_RUN.finditer(text):
        if overlaps(m.start(), m.end(), excl):
            continue
        d = re.sub(r"\D", "", m.group())
        if len(d) == 10 and d[0] in "6789":
            lab = "PHONE"
        elif (len(d) == 12 and d.startswith("91") and d[2] in "6789") or \
             (len(d) == 11 and d.startswith("0") and d[1] in "6789"):
            lab = "PHONE"            # with country code / leading zero
        elif len(d) == 12:
            lab = "AADHAAR"          # checksum decided later (resolver); mask either way (recall first)
        elif 9 <= len(d) <= 19:
            lab = "ACCOUNT"
        else:
            lab = "NUMBER_FRAGMENT"
        spans.append(dict(label=lab, start=m.start(), end=m.end(), source="rules"))
    # generic id-shape: any alphanumeric token mixing letters and digits (loan / agreement numbers vary a lot:
    # 'AB12345678', 'LAN-2023-00123456', 'PL/BLR/2021/004567', 'HL0012345678')
    for m in _ID_SHAPE.finditer(text):
        tok = m.group()
        alnum = re.sub(r"[^A-Za-z0-9]", "", tok)
        nd, nl = sum(c.isdigit() for c in alnum), sum(c.isalpha() for c in alnum)
        if len(alnum) < 7 or nd < 3 or nl < 1:
            continue
        if overlaps(m.start(), m.end(), excl) or any(
                s.get("strong") and m.start() < s["end"] and m.end() > s["start"] for s in spans):
            continue                      # weaker digit-run spans inside the id are fine: the resolver merges them
        if re.fullmatch(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}", tok):          # plain numeric date
            continue
        spans.append(dict(label="ID_ALNUM", start=m.start(), end=m.end(), source="rules"))
    spans += _cue_ids(text, excl)
    return spans
