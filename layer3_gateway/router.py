"""Sanitise and route the LLM's reply.
sanitise : (1) any placeholder that is not in the vault for THIS conversation (the model invented <AADHAAR_99>) becomes [unknown];
           (2) PII the model made up or echoed (digits, PAN, email ...) is replaced with [redacted] - found by two independent scanners.
route    : the reply goes two ways. log_entry = the tokenised reply (always). borrower_reply = the same reply with valid tokens rehydrated - ONLY when the audience is the borrower.
Rehydrated text is returned to the caller and nowhere else: it is never stored by this module."""
import re

from layer3_gateway import audit as au

TOKEN = re.compile(r"<[A-Z_]+_\d+>")


def _blank(text):
    return TOKEN.sub(lambda m: "•" * len(m.group()), text)          # placeholders contain words like NUMBER / the digit 1: blank them, same length


def sanitise(reply, known_tokens, audit_detector):
    known = set(known_tokens)
    unknown = 0

    def fix(m):
        nonlocal unknown
        if m.group() in known:
            return m.group()
        unknown += 1
        return "[unknown]"
    reply = TOKEN.sub(fix, reply)
    neutral = _blank(reply)
    found = list(audit_detector.detect_calls([[{"speaker": "x", "text": neutral}]])[0][0]) + au.scan(neutral)
    merged = []
    for f in sorted(found, key=lambda x: (x["start"], -x["end"])):
        if merged and f["start"] < merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], f["end"])
        else:
            merged.append({"start": f["start"], "end": f["end"]})
    for f in sorted(merged, key=lambda x: -x["start"]):
        reply = reply[:f["start"]] + "[redacted]" + reply[f["end"]:]
    return reply, {"unknown_tokens": unknown, "redacted_spans": len(merged)}


def route(reply_tokenised, audience, rehydrate_many):
    """-> (borrower_reply, log_entry). rehydrate_many(tokens) -> {token: value or None}; it may raise (the caller fails closed)."""
    if audience != "borrower":
        return reply_tokenised, reply_tokenised
    toks = list(dict.fromkeys(TOKEN.findall(reply_tokenised)))
    values = rehydrate_many(toks) if toks else {}
    out = reply_tokenised
    for t in toks:
        if values.get(t) is not None:
            out = out.replace(t, values[t])
    return out, reply_tokenised
