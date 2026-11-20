"""The downstream task: read a finished call and say whether the customer will pay.

The LLM only ever sees the MASKED transcript (the gateway sends it; see calls.py). Its answer is parsed STRICTLY: anything malformed or self-contradicting is an error,
never a guess. There is deliberately no default disposition: a wrong 'will pay' on a collections case is worse than 'could not decide'."""
import json
import re

DISPOSITIONS = ("PROMISE_TO_PAY", "ALREADY_PAID", "REFUSES_TO_PAY", "DISPUTES_DEBT", "REQUEST_CALLBACK", "WRONG_PERSON", "UNCLEAR")
WILL_PAY = ("yes", "no", "unclear")
# which will_pay answers each disposition may carry (anything else means the model contradicted itself)
ALLOWED_WILL_PAY = {"PROMISE_TO_PAY": ("yes",), "REFUSES_TO_PAY": ("no",), "UNCLEAR": ("unclear",), "DISPUTES_DEBT": ("no", "unclear"), "WRONG_PERSON": ("no", "unclear"),
                    "ALREADY_PAID": WILL_PAY, "REQUEST_CALLBACK": WILL_PAY}
MAX_TRANSCRIPT_CHARS = 3500      # the gateway refuses messages over 4,000 characters (GATEWAY_MAX_MESSAGE_CHARS); keep a margin

SYSTEM = """You read the transcript of a loan-collections phone call between an Agent (a bot) and a Customer, and decide the call's outcome.
Words like <PHONE_1> or <PERSON_1> are placeholders for private values that were hidden on purpose. Never try to guess them and never write one in your answer.
Answer with ONE JSON object and nothing else, using exactly these keys:
{"disposition": one of PROMISE_TO_PAY | ALREADY_PAID | REFUSES_TO_PAY | DISPUTES_DEBT | REQUEST_CALLBACK | WRONG_PERSON | UNCLEAR,
 "will_pay": "yes" | "no" | "unclear",
 "promised_amount": the amount the customer committed to, written WITH the rupee marker like "Rs 4,200" (never a bare number), or null,
 "promised_date": the date or day the customer committed to, copied as they said it, or null,
 "reason": one short sentence,
 "evidence": a list of at most 2 short quotes from the Customer lines that support the decision}
Rules: PROMISE_TO_PAY needs will_pay "yes"; REFUSES_TO_PAY needs "no"; UNCLEAR needs "unclear". Only use PROMISE_TO_PAY when the customer clearly commits to paying.
If the customer is vague, says "maybe" or "I will try", use UNCLEAR. The transcript may be in English, Hindi or Marathi; write the JSON values in English."""


class DispositionError(Exception):
    pass


def build_transcript(turns, max_chars=MAX_TRANSCRIPT_CHARS, key="masked"):
    """turns = [{"speaker": "Agent"|"Customer", key: str}] -> (text, trimmed). Drops the OLDEST turns first when too long (the ending decides the outcome).
    key="text" builds the RAW transcript that the gateway then masks in one pass (re-sending already-masked text through the gateway garbles it: tested)."""
    lines = [f"{t['speaker']}: {t[key]}" for t in turns if t.get(key)]
    trimmed = False
    while len(lines) > 1 and len("\n".join(lines)) > max_chars:
        lines.pop(0)
        trimmed = True
    text = "\n".join(lines)
    if len(text) > max_chars:                                   # a single enormous turn: cut it, say so
        text, trimmed = text[-max_chars:], True
    return text, trimmed


_RUPEES = re.compile(r"(?:rs\.?|inr|₹)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)


def _amount(v):
    """None, a plain number, or a rupee-marked string ('Rs 4,200') -> a number. The marker matters: the gateway's reply sanitiser blanks a BARE number as possible PII
    but leaves a rupee-marked amount of up to 8 digits alone (measured), so the model is told to write it that way. '[redacted]' or anything else is an error."""
    if v is None:
        return None
    if isinstance(v, str):
        m = _RUPEES.fullmatch(v.strip())
        if not m:
            raise DispositionError("promised_amount_invalid")
        f = float(m.group(1).replace(",", ""))
        v = int(f) if f == int(f) else f
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 1e9:
        raise DispositionError("promised_amount_invalid")
    return v


def parse(reply):
    """The model's reply -> a clean dict with exactly the documented keys. Raises DispositionError on anything off-spec."""
    if not isinstance(reply, str):
        raise DispositionError("reply_not_text")
    start = reply.find("{")
    if start < 0:
        raise DispositionError("no_json_object")
    try:
        obj, _ = json.JSONDecoder().raw_decode(reply[start:])
    except ValueError:
        raise DispositionError("json_unreadable") from None
    if not isinstance(obj, dict):
        raise DispositionError("json_not_an_object")
    d, w = obj.get("disposition"), obj.get("will_pay")
    if d not in DISPOSITIONS:
        raise DispositionError("unknown_disposition")
    if w not in WILL_PAY:
        raise DispositionError("unknown_will_pay")
    if w not in ALLOWED_WILL_PAY[d]:
        raise DispositionError("disposition_contradicts_will_pay")
    date, reason, ev = obj.get("promised_date"), obj.get("reason"), obj.get("evidence", [])
    if date is not None and (not isinstance(date, str) or len(date) > 60):
        raise DispositionError("promised_date_invalid")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 400:
        raise DispositionError("reason_missing_or_too_long")
    if not isinstance(ev, list) or len(ev) > 3 or not all(isinstance(x, str) and len(x) <= 300 for x in ev):
        raise DispositionError("evidence_invalid")
    return {"disposition": d, "will_pay": w, "promised_amount": _amount(obj.get("promised_amount")), "promised_date": date,
            "reason": reason.strip(), "evidence": [x.strip() for x in ev]}


def generate(transcript_text, run_llm, retries=2):
    """run_llm(transcript_text) -> the model's raw reply. Two retries on a malformed answer (seen ~1 in 6 on one test case, not reproducible), then a loud failure (never a default)."""
    last = "no_attempt"
    for _ in range(retries + 1):
        try:
            return parse(run_llm(transcript_text))
        except DispositionError as e:
            last = str(e)
    raise DispositionError(f"{last} (after {retries + 1} attempts)")
