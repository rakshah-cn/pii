"""Layer 3 proof experiments: what actually leaks, and which parts of the gateway stop it.      python -m layer3_gateway.leak_proof [--rows 300] [--seed 7]
Data: data/corpus_test.jsonl (SYNTHETIC, fake values; the final split is never opened). Every conversation is replayed turn by turn through the REAL gateway (real detector,
real vault, real consent, real audit). A gold value 'leaks' if it (intact) or >= 50% of it survives in the text that would be sent to the LLM / written to a log.
Experiments:
  A  baseline                       one gateway, full detector
  B  restart every 2 turns          a NEW gateway process (same vault, empty memory) takes over the conversation every 2 turns
  C  two gateways alternating       turn 1 -> gateway 1, turn 2 -> gateway 2 ... (a load balancer without sticky routing)
  D  detector misses 50% of spans   the audit is the net; leave-one-out ablation of each audit check
  E  detector finds NOTHING         audit alone; each check alone
Writes layer3_gateway/PROOF.md."""
import argparse
import collections
import contextlib
import json
import os
import random
import re
import time

from layer1_detection.detector import Detector
from layer3_gateway import audit as au
from layer3_gateway.graph import Gateway, make_detector
from layer3_gateway.leakage_eval import leak
from layer3_gateway.vault_client import VaultClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pct = lambda a, b: f"{100 * a / b:.1f}%" if b else "n/a"


class NullDet:
    def detect_calls(self, calls):
        return [[[] for _ in c] for c in calls]


class Dropping:
    """Wraps a real detector and drops each detected span with probability p (deterministically): a detector that misses things."""
    def __init__(self, base, p, seed=1):
        self.base, self.p, self.seed = base, p, seed

    def detect_calls(self, calls):
        out = self.base.detect_calls(calls)
        for per_turn in out:
            last = per_turn[-1]
            per_turn[-1] = [s for s in last if random.Random(f"{self.seed}:{s['start']}:{s['end']}:{calls[0][-1]['text']}").random() >= self.p]
        return out

    @property
    def model(self):
        return getattr(self.base, "model", None)


@contextlib.contextmanager
def audit_cfg(gw, off):
    """Switch individual audit checks off (for the ablation). off: subset of {tripwire, family2, rescan, vault_scan}."""
    saved = (au.tripwire, au.family2, gw.audit_detector, gw.vault.scan_text)
    if "tripwire" in off: au.tripwire = lambda t: []
    if "family2" in off: au.family2 = lambda t: []
    if "rescan" in off: gw.audit_detector = NullDet()
    if "vault_scan" in off: gw.vault.scan_text = lambda *a, **k: []
    try:
        yield
    finally:
        au.tripwire, au.family2, gw.audit_detector, gw.vault.scan_text = saved


def load_rows(n, seed, path="data/corpus_test.jsonl"):
    rows = [json.loads(l) for l in open(os.path.join(ROOT, path), encoding="utf-8")]
    rows = [r for r in rows if r["spans"] and r.get("channel") == "call"]
    random.Random(seed).shuffle(rows)
    return rows[:n]


def replay(rows, detector, off=(), mode="single"):
    vault = VaultClient(ephemeral=True)
    mk = lambda: Gateway(detector=detector, audit_detector=Detector(use_rules=True, use_model=False), vault=vault, llm=lambda m: "ok")
    by_type, by_render = collections.defaultdict(lambda: [0, 0, 0]), collections.defaultdict(lambda: [0, 0, 0])
    msgs = blocked = retried = fam2 = rescan = 0
    for ri, row in enumerate(rows):
        cid, bid = f"C{ri}", f"B{ri}"
        gws = [mk()] if mode == "single" else [mk(), mk()] if mode == "alternate" else None
        outs, gw = [], None
        for ti, t in enumerate(row["turns"]):
            if mode == "restart2":
                if ti % 2 == 0: gw = mk()                      # a brand-new gateway process takes over: empty memory, same vault
            else:
                gw = gws[ti % len(gws)]
            with audit_cfg(gw, set(off)):
                o = gw.handle(cid, t["speaker"], t["text"], borrower_id=bid, purpose="loan_collection")
            msgs += 1; blocked += o["status"] == "blocked"; retried += o.get("retries", 0) > 0
            ar = o.get("audit_results") or {}
            fam2 += bool(ar.get("family2")); rescan += bool(ar.get("rules_rescan"))
            outs.append(o.get("masked_text") if o["status"] == "ok" else None)
        for g in row["spans"]:
            intact, partial = leak(g["value"], outs[g["turn"]])
            for c in (by_type[g["type"]], by_render[g.get("rendering", "n/a")]):
                c[0] += 1; c[1] += intact; c[2] += partial
    n = sum(v[0] for v in by_type.values()); i = sum(v[1] for v in by_type.values()); p = sum(v[2] for v in by_type.values())
    return {"values": n, "intact": i, "partial": p, "msgs": msgs, "blocked": blocked, "retried": retried, "fam2_msgs": fam2, "rescan_msgs": rescan,
            "by_type": dict(by_type), "by_render": dict(by_render)}


def row(label, r):
    return f"| {label} | {r['values']} | {r['intact']} ({pct(r['intact'], r['values'])}) | {r['partial']} ({pct(r['partial'], r['values'])}) | {r['blocked']} | {r['retried']} |"


HEAD = ["| configuration | gold values | intact leaks | >=50% leaks | blocked msgs | msgs needing retry |", "|---|---|---|---|---|---|"]


def spoken_recall(path="data/corpus_test.jsonl"):
    """Digit-type gold values written as words: does the spoken-number TRIPWIRE alone (no Layer 1) see them? Per language. Circularity caveat: word lists come from our own tables."""
    res = collections.defaultdict(lambda: [0, 0])
    for l in open(os.path.join(ROOT, path), encoding="utf-8"):
        r = json.loads(l)
        for s in r["spans"]:
            if s["type"] in ("NAME", "ADDRESS", "EMAIL", "UPI", "DOB") or any(c.isdigit() for c in s["value"]):
                continue
            hits = [f for f in au.tripwire(s["value"]) if f["label"] == "DIGIT_WORDS"]
            res[r["lang"]][0] += 1; res[r["lang"]][1] += bool(hits and sum(f["end"] - f["start"] for f in hits) >= 0.5 * len(s["value"]))
    return res


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--rows", type=int, default=300); ap.add_argument("--seed", type=int, default=7); a = ap.parse_args()
    rows = load_rows(a.rows, a.seed); det = make_detector(model=True); L = []
    n_msgs = sum(len(r["turns"]) for r in rows)
    L += ["# Layer 3 proof experiments", "", f"{len(rows)} random conversations ({n_msgs} messages) from data/corpus_test.jsonl (synthetic, fake values; final split untouched), seed {a.seed}. "
          "Every row is the REAL gateway: Layer 1 (rules + spoken engine + Chitragupta int8), Layer 4 consent, Layer 2 vault, 4-part audit. A value 'leaks' if it (intact) or >= 50% of it reaches the text sent to the LLM.", ""]
    t0 = time.time()
    base = replay(rows, det); print("A", base["intact"], base["partial"], round(time.time() - t0)); 
    L += ["## A-C. Does gateway memory matter? (full detector)", "", *HEAD, row("A  one gateway, whole conversation", base)]
    rs = replay(rows, det, mode="restart2"); L.append(row("B  a NEW gateway process every 2 turns (memory lost)", rs)); print("B", rs["intact"], rs["partial"], round(time.time() - t0))
    al = replay(rows, det, mode="alternate"); L.append(row("C  two gateways alternating turns (no sticky routing)", al)); print("C", al["intact"], al["partial"], round(time.time() - t0))
    L += ["", "Leaks by rendering, A vs B vs C (>= 50% leaks / values):", "", "| rendering | A | B | C |", "|---|---|---|---|"]
    for k in sorted(base["by_render"]):
        L.append(f"| {k} | " + " | ".join(f"{x['by_render'].get(k, [0, 0, 0])[2]}/{x['by_render'].get(k, [0, 0, 0])[0]}" for x in (base, rs, al)) + " |")
    L += ["", f"Extras on an INTACT detector (false-alarm cost): second pattern family fired on {base['fam2_msgs']} of {base['msgs']} messages, the Layer-1 rules re-scan on {base['rescan_msgs']}; messages needing an audit retry: {base['retried']}.", ""]
    for p in (0.5, 1.0):
        d = Dropping(det, p); L += [f"## {'D' if p == 0.5 else 'E'}. Detector {'misses 50% of its spans' if p == 0.5 else 'finds NOTHING (the audit alone)'}", "", *HEAD]
        cfgs = [("audit OFF (tripwire, family2, rescan, vault scan all off)", {"tripwire", "family2", "rescan", "vault_scan"}), ("audit FULL", set()),
                ("full minus digit/spoken tripwire", {"tripwire"}), ("full minus second pattern family", {"family2"}), ("full minus Layer-1 rules re-scan", {"rescan"}), ("full minus vault scan", {"vault_scan"}),
                ("ONLY tripwire", {"family2", "rescan", "vault_scan"}), ("ONLY Layer-1 rules re-scan", {"tripwire", "family2", "vault_scan"})]
        for label, off in cfgs:
            r = replay(rows, d, off); L.append(row(label, r)); print("D/E", p, label, r["intact"], r["partial"], round(time.time() - t0))
        L.append("")
    sr = spoken_recall()
    L += ["## F. Spoken-number tripwire alone, per language (gold values written as words)", "", "| language | values | seen by the tripwire (>= 50% covered) |", "|---|---|---|"]
    for k, (n, h) in sorted(sr.items()): L.append(f"| {k} | {n} | {h} ({pct(h, n)}) |")
    L += ["", "Caveat: the word lists the tripwire uses and the ones that generated this data are both ours; this measures coverage of OUR forms, not of real speakers' variants. Native-speaker review is still undone.", ""]
    open(os.path.join(os.path.dirname(__file__), "PROOF.md"), "w").write("\n".join(L)); print("wrote PROOF.md", round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
