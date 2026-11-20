"""Small labelled check of the disposition step.   .venv/bin/python -m layer9_voice.eval_disposition [model ...]     (needs SARVAM_API_KEY)

13 SYNTHETIC masked transcripts written by the developer (not independent, tiny): a smoke test of "does the model pick the right outcome and read amount/date", NOT evidence of accuracy.
Calls the model directly with the masked transcript only (no gateway), so it measures the model, not the sanitiser. Models: default sarvam-105b and deepseekv4.1-flash-1017."""
import sys
import time

import common.llm  # noqa: F401  (loads .env)
from layer9_voice import disposition
from layer9_voice.sarvam import SarvamClient, SarvamError

A = "Agent: Hello, this is a reminder call about your overdue loan instalment. Is this a good time to talk?\n"
# (name, transcript after the greeting, expected disposition, expected amount or None)
CASES = [
    ("ptp_en", "Customer: Yes. I will pay Rs 4,200 on the 7th.\nAgent: Thank you, goodbye.", "PROMISE_TO_PAY", 4200),
    ("ptp_hi", "Customer: हाँ, मैं सात तारीख को Rs 4,200 भर दूँगा।\nAgent: धन्यवाद, अलविदा।", "PROMISE_TO_PAY", 4200),
    ("ptp_mr", "Customer: हो, मी सात तारखेला Rs 4,200 भरीन.\nAgent: धन्यवाद, निरोप.", "PROMISE_TO_PAY", 4200),
    ("ptp_token", "Customer: My number is <PHONE_1>. I will pay Rs 2,000 by Friday.\nAgent: Thank you, goodbye.", "PROMISE_TO_PAY", 2000),
    ("ptp_partial", "Customer: I can only pay Rs 1,000 now, the rest next month.\nAgent: Understood, thank you.", "PROMISE_TO_PAY", 1000),
    ("refuse_en", "Customer: I am not paying this. Stop calling me.\nAgent: I understand. Goodbye.", "REFUSES_TO_PAY", None),
    ("refuse_hi", "Customer: मैं पैसे नहीं दूँगा, दोबारा फोन मत करना।\nAgent: ठीक है, अलविदा।", "REFUSES_TO_PAY", None),
    ("dispute_en", "Customer: This amount is wrong. I never took this much loan. I want it checked.\nAgent: I will note that.", "DISPUTES_DEBT", None),
    ("paid_en", "Customer: I already paid this last week through UPI.\nAgent: Thank you, we will check.", "ALREADY_PAID", None),
    ("callback_en", "Customer: I am driving right now. Please call me tomorrow evening.\nAgent: Sure, goodbye.", "REQUEST_CALLBACK", None),
    ("callback_mr", "Customer: मी आत्ता बोलू शकत नाही, उद्या फोन करा.\nAgent: ठीक आहे, निरोप.", "REQUEST_CALLBACK", None),
    ("wrong_en", "Customer: You have the wrong number. Nobody by that name lives here.\nAgent: Sorry for the trouble.", "WRONG_PERSON", None),
    ("vague_en", "Customer: I will try to pay sometime, maybe next week, I am not sure.\nAgent: Could you give a date?\nCustomer: Not really, we will see.", "UNCLEAR", None),
]


def run(model):
    c = SarvamClient(timeout=40)
    ok = parsed = amount_ok = amount_n = 0
    lat = []
    print(f"\n=== {model}")
    for name, body, want, want_amt in CASES:
        t = time.time()
        try:
            reply = c.chat([{"role": "system", "content": disposition.SYSTEM}, {"role": "user", "content": A + body}], model=model, max_tokens=1200, temperature=0.1, reasoning_effort="off")
            d = disposition.parse(reply)
        except (SarvamError, disposition.DispositionError) as e:
            print(f"  {name:12} ERROR {e}")
            continue
        lat.append(time.time() - t)
        parsed += 1
        hit = d["disposition"] == want
        ok += hit
        if want_amt is not None:
            amount_n += 1
            amount_ok += d["promised_amount"] == want_amt
        print(f"  {name:12} {'ok ' if hit else 'MISS'} got={d['disposition']:16} amount={d['promised_amount']} date={d['promised_date']!r}  ({lat[-1]:.1f}s)")
    print(f"  -> parsed {parsed}/{len(CASES)}, right outcome {ok}/{len(CASES)}, right amount {amount_ok}/{amount_n}, mean {sum(lat) / max(1, len(lat)):.1f}s")


if __name__ == "__main__":
    for m in (sys.argv[1:] or ["sarvam-105b", "deepseekv4.1-flash-1017"]):
        run(m)
