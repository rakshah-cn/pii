"""Deterministic checks on one generated row. Returns a list of problems (empty = pass).
These are the 'evaluate' step: most quality control is plain code, not an LLM."""
import re

from . import pii_gen, spoken

SCRIPT_RANGES = {"hi": (0x0900, 0x097F), "mr": (0x0900, 0x097F), "kn": (0x0C80, 0x0CFF), "ta": (0x0B80, 0x0BFF)}


def _in_script(s, lang):
    lo, hi = SCRIPT_RANGES[lang]
    return any(lo <= ord(c) <= hi for c in s)


def check_row(row, seen_hashes=None):
    errs, text = [], row["text"]
    if re.search(r"\{[A-Z0-9_]+\}", text):
        errs.append("unfilled_slot")
    spans = sorted(row["spans"], key=lambda s: s["start"])
    for s in spans:
        if text[s["start"]:s["end"]] != s["value"]:
            errs.append(f"offset_mismatch:{s['type']}")
            continue
        r, d = s.get("rendering"), s.get("digits")
        if r == "digits" and re.sub(r"\D", "", s["value"]) != re.sub(r"\D", "", d) and s["type"] not in ("PAN", "LOAN"):
            errs.append(f"digits_mismatch:{s['type']}")
        if r == "spoken" and not s.get("noisy") and spoken.parse_spoken(s["value"]) != d:
            errs.append(f"spoken_mismatch:{s['type']}")
        if r == "spelled" and not s.get("noisy") and spoken.parse_spelled(s["value"]) != d:
            errs.append(f"spelled_mismatch:{s['type']}")
    # full-number validity (using the group's complete digits when split)
    full = {}
    for s in row["spans"]:
        if "digits" in s and not s.get("partial"):
            full.setdefault(s.get("group_id", id(s)), []).append(s)
    for grp in full.values():
        grp.sort(key=lambda s: s.get("part", 1))
        t, whole = grp[0]["type"], "".join(s["digits"] for s in grp)
        if t == "AADHAAR" and not (len(whole) == 12 and pii_gen.verhoeff_valid(whole)):
            errs.append("aadhaar_invalid")
        if t == "PHONE" and not (len(whole) == 10 and whole[0] in "6789"):
            errs.append("phone_invalid")
        if t == "PAN" and not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", whole):
            errs.append("pan_invalid")
        if len(grp) == 2 and not (grp[0]["turn"] < grp[1]["turn"]):
            errs.append("split_not_across_turns")
    # no overlaps between spans, or between spans and negatives
    allsp = sorted(row["spans"] + row["negatives"], key=lambda s: s["start"])
    for a, b in zip(allsp, allsp[1:]):
        if b["start"] < a["end"]:
            errs.append("overlap")
    # native-script names really are in the claimed script
    lang = row["lang"]
    if lang in SCRIPT_RANGES:
        for s in spans:
            if s["type"] == "NAME" and not _in_script(s["value"], lang):
                errs.append("name_wrong_script")
    # expected PII present for the cell
    if row["primary_pii"] != "NONE" and not any(s["type"] == row["primary_pii"] for s in spans):
        errs.append("missing_primary_pii")
    if row["primary_pii"] == "NONE" and spans:
        errs.append("pii_in_pii_free")
    if seen_hashes is not None:               # near-duplicate (exact after normalising digits/names)
        h = hash(re.sub(r"\W+", "", text.lower()))
        if h in seen_hashes:
            errs.append("duplicate")
        seen_hashes.add(h)
    return sorted(set(errs))


JUDGE = None   # set to llm_steps.judge to turn the LLM naturalness judge on


def llm_judge_naturalness(row):
    """LLM naturalness judge on the SKELETON (placeholders only, no PII). Off unless JUDGE is set."""
    if JUDGE is None or "skeleton" not in row:
        return True, ""
    return JUDGE(row["skeleton"])
