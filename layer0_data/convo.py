"""Builds one noisy conversation (call / chat messages / LLM prompt) from a coverage-grid cell.

Two steps:
  1. SKELETON = turns with slots like {NAME}, {PHONE}, {AMOUNT}. No real values. Written by Claude
     (layer0_data/skeleton_lib.py), expanded by a small grammar so lines are not repetitive.
  2. FILL     = code makes fake values and fills the slots, recording exact spans.

Most turns carry NO PII (like real calls). Amounts/dates/company are recorded as NEGATIVES:
things a detector must NOT mask.
"""
import random
import re

from . import hard as hard_mod, pii_gen, skeleton_lib as SL, spoken
from .specs import DIGIT_TYPES, SPELLED_TYPES

ORGS = ["Sunrise Capital", "Lakeview Finance", "Orchid Finserv", "Greenfield Credit", "Bluebird Finance", "Maple Housing Finance"]
SLOT_RE = re.compile(r"\{([A-Z0-9_]+)\}")
NUMERIC_TYPES = DIGIT_TYPES | SPELLED_TYPES
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def num_words(n):
    if n < 20: return _ONES[n]
    if n < 100: return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    if n < 1000: return _ONES[n // 100] + " hundred" + (" " + num_words(n % 100) if n % 100 else "")
    return num_words(n // 1000) + " thousand" + (" " + num_words(n % 1000) if n % 1000 else "")


def ordinal(n):
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


# ---------------------------------------------------------------- skeleton builders
def _ex(block, rng):
    return [(sp, SL.expand(tx, rng)) for sp, tx in block]


def _from(lang, getter, rng, part, cell, p_lang=0.6, packs=None):
    """Pick a block from the language pack (60%) or the English pack. If a pack's pool has NO line for this part
    (pools are small, so this is common for non-English packs) move on to the next pack instead of leaking a line from
    another part. Only if no pack has a line is the row flagged unclean (cell['_fb'])."""
    packs = packs or SL.CALL_PACKS
    seen = []
    for tag, pack, use in ((lang, packs.get(lang), rng.random() < p_lang), ("en", packs["en"], True)):
        if pack is None or not use:
            continue
        try:
            items = getter(pack)
        except (KeyError, TypeError):
            continue
        if not items:
            continue
        seen.append((tag, items))
        sel = SL.zone_items(items, part)
        if sel:
            cell.setdefault("_packs", []).append(tag)
            it = rng.choice(sel)
            if SL.is_ext(it):
                cell["_ext"] = cell.get("_ext", 0) + 1
            return _ex(it, rng)
    if not seen:                                                    # the language pack lacked this kind and English was skipped
        seen = [("en", getter(packs["en"]))]
    tag, items = seen[-1]
    cell.setdefault("_fb", []).append(1)
    cell.setdefault("_packs", []).append(tag)
    return _ex(rng.choice(items), rng)


def _kind(form, t, pack_ask):
    """split/partial only exist for numeric types. If a pack lacks the requested kind we raise, so the caller
    falls back to the English pack instead of pairing a full-number cue with a 4-digit value (review finding)."""
    want = "split" if form == "split_turns" else ("partial" if form == "partial" else "single")
    if t not in NUMERIC_TYPES or want == "single":
        return "single"
    if want not in pack_ask[t]:
        raise KeyError(want)
    return want


def _noise_turns(turns, lang, rng, hard):
    pack = SL.CALL_PACKS.get(lang)
    noise = SL.EN["noise"] + (pack["noise"] if pack else [])
    for _ in range(rng.randint(0, 4)):
        turns.insert(rng.randint(1, len(turns)), (rng.choice(["Agent", "Customer"]), rng.choice(noise)))
    if hard:                                       # customer repeats a number when asked again
        for i, (sp, tx) in enumerate(turns):
            if sp == "Customer" and re.fullmatch(r"\{(PHONE|AADHAAR|ACCOUNT|LOAN|PAN)\}", tx) and rng.random() < 0.4:
                turns[i + 1:i + 1] = [("Agent", "Can you repeat that once more?"), ("Customer", tx)]
                break
    if rng.random() < 0.08:                        # rare wrong speaker label (seen in real data)
        i = rng.randrange(len(turns))
        turns[i] = ("Agent" if turns[i][0] == "Customer" else "Customer", turns[i][1])
    return turns


def build_call(cell, pii_types, rng):
    lang, part, form = cell["lang"], cell.get("part"), cell["form"]
    blocks = []
    for t in pii_types:
        blocks.append(_from(lang, lambda p, t=t: p["ask"][t][_kind(form, t, p["ask"])], rng, part, cell))
    opening = _from(lang, lambda p: p["open"], rng, part, cell)
    intro = _from(lang, lambda p: p["intro"], rng, part, cell)
    due = _from(lang, lambda p: p["due"][cell["scenario"]], rng, part, cell)
    closing = _from(lang, lambda p: p["close"], rng, part, cell)
    rng.shuffle(blocks)
    body = [opening, intro] + blocks + [due, closing]
    if rng.random() < 0.3:
        body = [opening, intro, due] + blocks + [closing]
    if rng.random() < 0.25:
        body.remove(intro)
    turns = [t for b in body for t in b]
    return _noise_turns(turns, lang, rng, cell.get("hard"))


def _msg_pack(lang):
    if lang in SL.MSG_BY_LANG:
        return SL.MSG_BY_LANG[lang]
    return SL.MSG_NATIVE.get(lang), lang


def build_message(cell, pii_types, rng):
    lang, part, form = cell["lang"], cell.get("part"), cell["form"]
    own, own_tag = _msg_pack(lang)
    en = SL.MSG_EN

    def get(getter):
        seen = []
        for pack, tag in (([(own, own_tag), (en, "en")] if (own and rng.random() < 0.6) else [(en, "en")])):
            try:
                items = getter(pack)
            except (KeyError, TypeError):
                continue
            if not items:
                continue
            seen.append((tag, items))
            sel = SL.zone_items(items, part)
            if sel:
                cell.setdefault("_packs", []).append(tag)
                it = rng.choice(sel)
                if SL.is_ext(it):
                    cell["_ext"] = cell.get("_ext", 0) + 1
                return _ex(it, rng)
        if not seen:
            seen = [("en", getter(en))]
        tag, items = seen[-1]
        cell.setdefault("_fb", []).append(1)
        cell.setdefault("_packs", []).append(tag)
        return _ex(rng.choice(items), rng)
    turns = []
    if rng.random() < 0.6 or not pii_types:
        turns += get(lambda p: p["open"])
    for t in pii_types:
        turns += get(lambda p, t=t: p["ask"][t][_kind(form, t, p["ask"])])
    if rng.random() < 0.7:
        turns += get(lambda p: p["close"])
    if rng.random() < 0.06 and len(turns) > 1:      # wrong sender label
        i = rng.randrange(len(turns))
        turns[i] = ("Agent" if turns[i][0] == "Customer" else "Customer", turns[i][1])
    return turns


def build_prompt(cell, pii_types, rng):
    part, form = cell.get("part"), cell["form"]
    instr = SL.expand(SL.pick(SL.PROMPT_INSTR, rng, part, cell), rng)
    snippets, spoken_lines = [], []
    for t in pii_types:
        if form in ("spoken", "spoken_fillers") and t in NUMERIC_TYPES:
            spoken_lines.append(SL.expand(rng.choice(SL.PROMPT_SPOKEN), rng).replace("{CUE}", SL.PROMPT_CUE[t]).replace("{X}", "{%s}" % t))
        elif form == "partial" and t in NUMERIC_TYPES:
            snippets.append(f"(last 4 digits|ending) {{{t}}}".replace("(last 4 digits|ending)", rng.choice(["last 4 digits", "ending"])))
        else:
            snippets.append(SL.expand(SL.pick(SL.PROMPT_SNIP[t], rng, part, cell), rng))
    if not pii_types:
        snippets.append(rng.choice(["EMI {AMOUNT} due on the {DATE}, customer asked for time till the {DATE2}.",
                                    "Loan with {ORG}: {AMOUNT} overdue, promised to pay on the {DATE2}.",
                                    "Customer says payment was made on the {DATE2}, system shows {AMOUNT} pending."]))
    elif rng.random() < 0.5:
        snippets.append(rng.choice(["EMI {AMOUNT} due {DATE}", "overdue {AMOUNT}", "promised to pay on the {DATE2}"]))
    rng.shuffle(snippets)
    sep = rng.choice(["\n", "; ", " | ", "\n- "])
    body = (sep.join(snippets) if sep != "\n- " else "\n- " + sep.join(snippets)) if snippets else ""
    if body and any(x.startswith('"') for x in snippets) and rng.random() < 0.7:
        body = "{" + ", ".join(snippets) + "}"
    text = instr + ("\n" + body if body else "")
    if spoken_lines:
        text += "\nTranscript:\n" + "\n".join(spoken_lines)
    return [("User", text)]


def stub_skeleton(cell, pii_types, rng):
    ch = cell.get("channel", "call")
    return {"call": build_call, "message": build_message, "prompt": build_prompt}[ch](cell, pii_types, rng)


# ---------------------------------------------------------------- value making
def make_values(pii_types, cell, rng):
    """slot -> dict(text, type, digits, rendering, group). Split types create _P1/_P2."""
    lang, form, hard = cell["lang"], cell["form"], cell.get("hard", False)
    vals = {}
    for t in pii_types:
        if t == "NAME":
            vals["NAME"] = dict(text=pii_gen.fake_name(lang, rng, cell.get("part")), type="NAME")
        elif t == "ADDRESS":
            vals["ADDRESS"] = dict(text=pii_gen.fake_address(lang, rng), type="ADDRESS")
        elif t in ("UPI", "EMAIL", "DOB"):
            vals[t] = dict(text=(pii_gen.fake_upi(rng, cell.get("part")) if t == "UPI" else pii_gen.fake_email(rng, cell.get("part")) if t == "EMAIL" else pii_gen.fake_dob(rng)), type=t)
        else:
            raw = {"PHONE": pii_gen.fake_phone, "AADHAAR": pii_gen.fake_aadhaar, "ACCOUNT": pii_gen.fake_account,
                   "PAN": pii_gen.fake_pan, "LOAN": pii_gen.fake_loan}[t](rng)
            if t == "LOAN":     # written form keeps separators; spoken/split use the letters+digits only
                vals.update(_render_id(t, re.sub(r"[^A-Za-z0-9]", "", raw), form, lang, rng, hard, written=raw))
            else:
                vals.update(_render_id(t, raw, form, lang, rng, hard))
    return vals


def _render_one(t, raw, how, lang, rng, fillers=False, hard=False):
    """returns (text, rendering, noisy)"""
    if how == "digits":
        if t == "AADHAAR" and len(raw) == 12 and rng.random() < 0.5:
            return f"{raw[:4]} {raw[4:8]} {raw[8:]}", "digits", False
        if t == "PHONE" and rng.random() < 0.2:
            return f"{raw[:5]} {raw[5:]}", "digits", False
        return raw, "digits", False
    if t in SPELLED_TYPES:
        txt = spoken.render_spelled(raw, lang, rng)
        return (hard_mod.perturb(txt, lang, rng), "spelled", True) if hard else (txt, "spelled", False)
    if hard and lang == "en" and rng.random() < 0.4:
        return hard_mod.render_tens_en(raw, rng), "spoken", True
    txt = spoken.render_digits(raw, lang, rng, filler_prob=0.15 if fillers else 0.0)
    return (hard_mod.perturb(txt, lang, rng), "spoken", True) if hard else (txt, "spoken", False)


def _render_id(t, raw, form, lang, rng, hard=False, written=None):
    def one(r, how, **kw):
        txt, rend, noisy = _render_one(t, r, how, lang, rng, hard=hard, **kw)
        return dict(text=txt, type=t, digits=r, rendering=rend, **({"noisy": True} if noisy else {}))
    if form == "written":
        v = one(raw, "digits")
        if written:
            v["text"] = written
        return {t: v}
    if form in ("spoken", "spoken_fillers"):
        return {t: one(raw, "spoken", fillers=form == "spoken_fillers")}
    if form == "partial":                       # only the last 4 characters are said
        v = one(raw[-4:], "digits" if rng.random() < 0.5 else "spoken")
        v["partial"] = True
        return {t: v}
    k = rng.randint(3, len(raw) - 3)            # split_turns
    out = {}
    for name, part in (("P1", raw[:k]), ("P2", raw[k:])):
        v = one(part, "digits" if rng.random() < 0.4 else "spoken")
        v.update(group=f"{t}_1", part=1 if name == "P1" else 2)
        out[f"{t}_{name}"] = v
    return out


def negative_values(rng):
    amt = rng.choice([1850, 3184, 4652, 7200, 12500, 24999, 560, 9800, 45000])
    d1 = rng.randint(1, 28)
    d2 = rng.choice([d for d in range(1, 29) if d != d1])
    amount_txt = rng.choice([f"₹{amt:,}", f"{num_words(amt)} rupees", f"{amt} rupees", f"Rs. {amt:,}", f"Rs {amt}"])
    return {"AMOUNT": dict(text=amount_txt, type="AMOUNT", neg=True),
            "DATE": dict(text=ordinal(d1), type="DATE", neg=True),
            "DATE2": dict(text=ordinal(d2), type="DATE", neg=True),
            "ORG": dict(text=rng.choice(ORGS), type="ORG", neg=True)}


# ---------------------------------------------------------------- fill + offsets
def fill(turns, vals):
    """Fill slots; return (turn_dicts, text, spans, negatives). Offsets index into `text`."""
    lines, spans, negs, pos = [], [], [], 0
    out_turns = []
    for ti, (speaker, tpl) in enumerate(turns):
        prefix = f"[{speaker}]: "
        buf, cursor, local = "", 0, []
        for m in SLOT_RE.finditer(tpl):
            buf += tpl[cursor:m.start()]
            v = vals.get(m.group(1))
            if v is None:                       # unfilled slot -> checks.py will reject
                buf += m.group(0)
            else:
                local.append((len(buf), len(buf) + len(v["text"]), v))
                buf += v["text"]
            cursor = m.end()
        buf += tpl[cursor:]
        base = pos + len(prefix)
        for s, e, v in local:
            rec = {"type": v["type"], "turn": ti, "start": base + s, "end": base + e, "value": v["text"]}
            if v.get("neg"):
                negs.append(rec)
            else:
                for k in ("digits", "rendering", "group", "part", "noisy", "partial"):
                    if k in v:
                        rec[{"group": "group_id"}.get(k, k)] = v[k]
                spans.append(rec)
        out_turns.append({"speaker": speaker, "text": buf})
        lines.append(prefix + buf)
        pos += len(prefix) + len(buf) + 1       # +1 for "\n"
    return out_turns, "\n".join(lines), spans, negs


def build_row(cell, seed, skeleton_fn=stub_skeleton, skeleton=None):
    """Make one row. Pass `skeleton` to reuse/rewrite an existing skeleton (optimize step)."""
    rng = random.Random(seed)
    pii_types = [] if cell["pii"] == "NONE" else [cell["pii"]]
    if pii_types and "NAME" not in pii_types and rng.random() < 0.4:
        pii_types.append("NAME")               # names are common in real calls
    if pii_types and rng.random() < 0.12:       # sometimes a second identifier turns up in the same conversation
        extra = rng.choice(["PHONE", "ACCOUNT", "UPI", "EMAIL", "DOB", "ADDRESS"])
        if extra not in pii_types:
            pii_types.append(extra)
    vals = make_values(pii_types, cell, rng)
    vals.update(negative_values(rng))
    if skeleton is None:
        skeleton = skeleton_fn(cell, pii_types, rng)
    turns, text, spans, negs = fill(skeleton, vals)
    return {"lang": cell["lang"], "scenario": cell["scenario"], "form": cell["form"],
            "channel": cell.get("channel", "call"),
            "chatter_langs": sorted(set(cell.get("_packs", []))), "holdout_clean": not cell.get("_fb"), "ext_lines": cell.get("_ext", 0),
            "primary_pii": cell["pii"], "turns": turns, "text": text,
            "spans": spans, "negatives": negs, "skeleton": skeleton}
