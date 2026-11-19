"""Gateway latency: graph entry -> LLM dispatch boundary (initialize + consent, detect, tokenise, final audit). Budget from the brief: < 50 ms p95.
    python -m layer3_gateway.benchmark [--convs 120] [--pg URL]
REAL components: Layer 1 detector (rules + spoken engine + Chitragupta int8 ONNX, model on the newest turn), Layer 4 consent, the modular Layer 2 vault (file SQLite, and pooled
PostgreSQL if --pg / VAULT_BENCH_DB_URL), the 4-part audit, in-memory Layer 7 events. The LLM is a stub and is NOT timed. Model cache cleared before every message (otherwise repeated chatter lines are free). Messages are the turns of real conversations from the
SYNTHETIC dev corpus (fake values), sent in order so the detector's history window behaves as in production. Also reports what the new utility rule blocked.
Writes layer3_gateway/PERFORMANCE.md."""
import argparse
import collections
import json
import os
import platform
import tempfile
import time
import uuid

from layer1_detection.detector import Detector
from layer3_gateway.graph import Gateway, make_detector
from layer3_gateway.vault_client import VaultClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
q = lambda xs, f: sorted(xs)[min(len(xs) - 1, int(f * len(xs)))] if xs else float("nan")


def run_backend(label, vault, rows, detector):
    gw = Gateway(detector=detector, audit_detector=Detector(use_rules=True, use_model=False), vault=vault, llm=lambda m: "ok")
    gw.handle("warm", "Customer", "hello there, my number is 98450 12345", borrower_id="Bw", purpose="loan_collection")     # warm-up (model, caches, connections)
    gw.events.sink.events.clear()
    pre, det, tok, aud, total = [], [], [], [], []
    status, reasons, blocked_over = collections.Counter(), collections.Counter(), []
    n = 0
    for ci, r in enumerate(rows):
        cid, bid = f"C{ci}", f"B{ci}"
        for ti, t in enumerate(r["turns"]):
            if not isinstance(t.get("text"), str) or not t["text"].strip():
                continue
            if detector.model: detector.model.cache.clear()        # Layer 1 caches model results in memory by default: clear it so EVERY message pays the real model cost
            out = gw.handle(cid, t["speaker"], t["text"], borrower_id=bid, purpose="loan_collection", request_id=f"R{ci}-{ti}-{uuid.uuid4().hex[:6]}")
            n += 1; status[out["status"]] += 1
            ev = gw.events.sink.events; d = next((e["ms"] for e in ev if e["event"] == "L1_DETECT_COMPLETE"), None); k = next((e["ms"] for e in ev if e["event"] == "L2_TOKENISE_COMPLETE"), None)
            ev.clear()
            if out["status"] == "blocked":
                reasons[out.get("reason", "?").split(":")[0]] += 1
                if out.get("reason") == "over_masking": blocked_over.append(len(t["text"]))
            if "pre_llm_ms" in out:
                pre.append(out["pre_llm_ms"]); total.append(out["ms"])
                if d is not None: det.append(d)
                if k is not None: tok.append(k)
                if d is not None and k is not None: aud.append(max(0.0, out["pre_llm_ms"] - d - k))
        gw.forget(cid)
    L = [f"## {label}", "", f"{n} messages from {len(rows)} conversations; {len(pre)} reached the LLM boundary (clean audit).", "",
         "| step | p50 ms | p95 ms | p99 ms | max ms |", "|---|---|---|---|---|"]
    for name, xs in (("**entry -> LLM boundary (the 50 ms budget)**", pre), ("detect (Layer 1)", det), ("tokenise (Layer 2 batch)", tok), ("audit + consent + overhead (remainder)", aud), ("whole request incl. stub LLM + rehydrate", total)):
        L.append(f"| {name} | {q(xs, .5):.2f} | {q(xs, .95):.2f} | {q(xs, .99):.2f} | {max(xs):.2f} |")
    over = sum(1 for x in pre if x > 50)
    L += ["", f"- messages over 50 ms before the LLM: {over} of {len(pre)} ({100 * over / max(1, len(pre)):.1f}%)", f"- outcomes: {dict(status)}", f"- block reasons: {dict(reasons)}",
          f"- blocked by the 85% utility rule: {len(blocked_over)} of {n} messages ({100 * len(blocked_over) / max(1, n):.1f}%); lengths of those messages: {sorted(blocked_over)[:12]}", ""]
    print("\n".join(L)); return L, (q(pre, .95), over, len(pre))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--convs", type=int, default=120); ap.add_argument("--pg", default=os.environ.get("VAULT_BENCH_DB_URL")); a = ap.parse_args()
    rows = []
    with open(os.path.join(ROOT, "data", "corpus_dev.jsonl"), encoding="utf-8") as f:           # dev split only: the final split is never touched
        for i, line in enumerate(f):
            r = json.loads(line)
            if r.get("channel") == "call": rows.append(r)
            if len(rows) >= a.convs: break
    detector = make_detector(model=True)
    L = ["# Layer 3 gateway: latency to the LLM boundary", "", f"Machine: {platform.machine()}, {os.cpu_count()} CPUs. Detector = the gateway's own configuration (rules + spoken engine + Chitragupta int8, model on the newest turn). "
         "Synthetic dev conversations, fake values. LLM stubbed and not timed. Budget: p95 < 50 ms.", ""]
    with tempfile.TemporaryDirectory() as d:
        l, s1 = run_backend("File SQLite (WAL)", VaultClient(path=f"{d}/gw.db", ephemeral=True), rows, detector); L += l
    if a.pg:
        from layer2_vault.config.settings import Settings
        url = a.pg + ("&" if "?" in a.pg else "?") + "pool_min=4&pool_max=16&schema=gwb_" + uuid.uuid4().hex[:8]
        l, s2 = run_backend("PostgreSQL 16, pooled (local socket)", VaultClient(settings=Settings.for_testing(db_url=url)), rows, detector); L += l
    open(os.path.join(os.path.dirname(__file__), "PERFORMANCE.md"), "w").write("\n".join(L)); print("wrote PERFORMANCE.md")


if __name__ == "__main__":
    main()
