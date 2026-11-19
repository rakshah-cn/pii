"""Conversation-level leakage evaluation through the REAL gateway.

    python -m layer3_gateway.leakage_eval [data/hard_dev.jsonl] [limit] [--model]

Every conversation is replayed turn by turn through Gateway.handle (real vault encryption, real audit, real Layer-1
detector). We then ask, for every gold PII value, whether it is still visible in the text that would be SENT to an
LLM / written to a log (`masked_text`):
   intact leak  = the whole value survives          partial leak = >= 50% of its characters survive (longest
   contiguous run, after removing spaces/dashes)
Over-masking = amounts / dates / company names that no longer appear intact.

!! MEASURED ON OUR OWN SYNTHETIC DATA (data/hard_dev.jsonl was also used to tune layer 1). It says nothing about the
!! hidden corpus. Without --model the Chitragupta model is OFF, so NAMEs are expected to leak (only the model finds names).
"""
import collections
import difflib
import json
import re
import statistics
import sys

from layer1_detection.detector import Detector
from layer3_gateway.graph import Gateway


def _n(v):
    return re.sub(r"[\s\-,]", "", v).casefold()


def leak(value, outgoing):
    """(intact, partial) for one gold value against the outgoing text of its turn."""
    v, o = _n(value), _n(outgoing or "")
    if not v:
        return False, False
    if v in o:
        return True, True
    m = difflib.SequenceMatcher(None, v, o, autojunk=False).find_longest_match(0, len(v), 0, len(o))
    return False, m.size / len(v) >= 0.5


def pct(a, b):
    return f"{100 * a / b:5.1f}%" if b else "  n/a"


def run(path="data/hard_dev.jsonl", limit=None, use_model=False):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")][:limit]
    gw = Gateway(detector=Detector(use_rules=True, use_model=use_model),
                 audit_detector=Detector(use_rules=True, use_model=False))
    by_type = collections.defaultdict(lambda: [0, 0, 0])      # n, intact, partial
    by_form = collections.defaultdict(lambda: [0, 0, 0])
    neg = collections.defaultdict(lambda: [0, 0])
    ms, blocked, msgs, warned, leaked_conv, intact_conv, retried, leaked_nn = [], 0, 0, 0, 0, 0, 0, 0
    for row in rows:
        outs = []
        for t in row["turns"]:
            o = gw.handle(row["id"], t["speaker"], t["text"])
            msgs += 1
            ms.append(o["ms"])
            blocked += o["status"] == "blocked"
            warned += bool(o.get("warnings"))
            retried += o.get("retries", 0) > 0
            outs.append(o.get("masked_text") if o["status"] == "ok" else None)
        any_partial = any_intact = any_nn = False
        for g in row["spans"]:
            intact, partial = leak(g["value"], outs[g["turn"]])
            for k in ((by_type, g["type"]), (by_form, row["form"])):
                c = k[0][k[1]]
                c[0] += 1; c[1] += intact; c[2] += partial
            any_partial |= partial; any_intact |= intact
            any_nn |= partial and g["type"] != "NAME"
        for n in row["negatives"]:
            o = outs[n["turn"]]
            if o is None:
                continue
            c = neg[n["type"]]
            c[0] += 1
            c[1] += _n(n["value"]) not in _n(o)
        leaked_conv += any_partial
        leaked_nn += any_nn
        intact_conv += any_intact
    print(f"\nLEAKAGE THROUGH THE GATEWAY  |  {path}  |  {len(rows)} conversations, {msgs} messages  |  "
          f"detector: rules+spoken engine, Chitragupta {'ON' if use_model else 'OFF'}")
    print("MEASURED ON SYNTHETIC DATA (tuned on) - not a hidden-corpus number.\n")
    print(f"{'type':<9}{'n':>5}{'intact leak':>14}{'>=50% leak':>13}")
    for t, (n, i, p) in sorted(by_type.items()):
        note = "   <- model off: names are only found by Chitragupta" if t == "NAME" and not use_model else ""
        print(f"{t:<9}{n:>5}{pct(i, n):>14}{pct(p, n):>13}{note}")
    tot = [sum(v[i] for v in by_type.values()) for i in range(3)]
    print(f"{'ALL':<9}{tot[0]:>5}{pct(tot[1], tot[0]):>14}{pct(tot[2], tot[0]):>13}")
    non_name = [v for k, v in by_type.items() if k != "NAME"]
    nn = [sum(v[i] for v in non_name) for i in range(3)]
    print(f"{'ALL-NAME':<9}{nn[0]:>5}{pct(nn[1], nn[0]):>14}{pct(nn[2], nn[0]):>13}")
    print("\nby form:   " + "  ".join(f"{k} {pct(v[2], v[0]).strip()} ({v[0]})" for k, v in sorted(by_form.items())) + "   (>=50% leak)")
    print(f"\nconversations with any >=50% leak: {leaked_conv}/{len(rows)} ({pct(leaked_conv, len(rows)).strip()});"
          f" with any intact leak: {intact_conv}/{len(rows)} ({pct(intact_conv, len(rows)).strip()});"
          f" with any >=50% leak of a NON-name value: {leaked_nn}/{len(rows)} ({pct(leaked_nn, len(rows)).strip()})")
    print("over-masking (amount/date/org no longer intact): " +
          ", ".join(f"{k} {v[1]}/{v[0]} ({pct(v[1], v[0]).strip()})" for k, v in sorted(neg.items())))
    print(f"blocked messages: {blocked}/{msgs}   over-masking warnings: {warned}   messages needing an audit retry: {retried}")
    s = sorted(ms)
    q = lambda p: s[min(len(s) - 1, int(p * len(s)))]
    print(f"\nLATENCY per message, whole gateway (Chitragupta inference NOT included: "
          f"{'results come from the disk cache of earlier runs' if use_model else 'model is off'}): "
          f"p50 {statistics.median(s):.1f} ms  p95 {q(.95):.1f} ms  p99 {q(.99):.1f} ms  max {s[-1]:.1f} ms  (n={len(s)})")
    print("(includes LangGraph overhead, rules/spoken engine, AES-GCM vault writes, the audit re-scan; one process, CPU)")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    run(a[0] if a else "data/hard_dev.jsonl", int(a[1]) if len(a) > 1 else None, "--model" in sys.argv)
