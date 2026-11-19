"""Gateway under concurrent load.      python -m layer3_gateway.load_test [--convs 240] [--pg URL]
One gateway process (real detector with the model cache DEFEATED so every message pays the model cost, real vault, real consent, real audit), N client threads, each owning whole
conversations. Reports throughput, latency (entry -> LLM boundary and wall time per message), failures, and a CORRECTNESS check: detection is deterministic, so the masked output of every
message must be byte-identical at every concurrency level and on both databases - any difference would mean conversations interfered with each other.
Writes layer3_gateway/LOAD.md."""
import argparse
import hashlib
import json
import os
import platform
import tempfile
import threading
import time
import uuid

from layer1_detection.detector import Detector
from layer3_gateway.graph import Gateway, make_detector
from layer3_gateway.leakage_eval import leak
from layer3_gateway.leak_proof import load_rows
from layer3_gateway.vault_client import VaultClient

q = lambda xs, f: sorted(xs)[min(len(xs) - 1, int(f * len(xs)))] if xs else float("nan")


class NoHit(dict):
    """Layer 1's model cache that never reports a hit: every message runs the model (entries are still written/read back, as Layer 1 expects)."""
    def __contains__(self, k):
        return False


def run(label, vault, rows, clients, detector):
    gw = Gateway(detector=detector, audit_detector=Detector(use_rules=True, use_model=False), vault=vault, llm=lambda m: "ok")
    gw.handle("warm", "Customer", "hello there, my number is 98450 12345", borrower_id="Bw", purpose="loan_collection")
    pre, wall, reasons, digests, leaks, errors, lock = [], [], {}, {}, [0, 0], [], threading.Lock()

    def worker(mine):
        for ci, row in mine:
            outs = []
            for ti, t in enumerate(row["turns"]):
                t0 = time.perf_counter()
                try:
                    o = gw.handle(f"C{ci}", t["speaker"], t["text"], borrower_id=f"B{ci}", purpose="loan_collection", request_id=f"R{ci}-{ti}")
                except Exception as e:                                   # an exception escaping the gateway is a bug (it must always return a result)
                    with lock: errors.append(f"{type(e).__name__}")
                    outs.append(None); continue
                dt = (time.perf_counter() - t0) * 1000
                with lock:
                    wall.append(dt)
                    if "pre_llm_ms" in o: pre.append(o["pre_llm_ms"])
                    if o["status"] != "ok": reasons[(o.get("reason") or o["status"]).split(":")[0]] = reasons.get((o.get("reason") or o["status"]).split(":")[0], 0) + 1
                    digests[(ci, ti)] = hashlib.sha256((o.get("masked_text") or "<none>").encode()).hexdigest()[:16]
                outs.append(o.get("masked_text") if o["status"] == "ok" else None)
            for g in row["spans"]:
                _, partial = leak(g["value"], outs[g["turn"]])
                with lock: leaks[0] += 1; leaks[1] += partial
    mine = [[] for _ in range(clients)]
    for i, r in enumerate(rows): mine[i % clients].append((i, r))
    ts = [threading.Thread(target=worker, args=(m,)) for m in mine]
    t0 = time.perf_counter(); [t.start() for t in ts]; [t.join() for t in ts]; el = time.perf_counter() - t0
    fp = hashlib.sha256(json.dumps(sorted(digests.items())).encode()).hexdigest()[:16]
    r = {"clients": clients, "msgs": len(wall), "tput": len(wall) / el, "pre": (q(pre, .5), q(pre, .95), q(pre, .99)), "wall": (q(wall, .5), q(wall, .95), q(wall, .99), max(wall)),
         "over50": sum(1 for x in pre if x > 50), "n_pre": len(pre), "reasons": reasons, "errors": errors, "leak": tuple(leaks), "fp": fp}
    print(label, r, flush=True); return r


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--convs", type=int, default=240); ap.add_argument("--pg", default=os.environ.get("VAULT_BENCH_DB_URL")); a = ap.parse_args()
    rows = load_rows(a.convs, 11); det = make_detector(model=True); det.model.cache = NoHit()
    L = ["# Gateway under concurrent load", "", f"Machine: {platform.machine()}, {os.cpu_count()} CPUs. {len(rows)} conversations ({sum(len(r['turns']) for r in rows)} messages) from data/corpus_test.jsonl (synthetic), "
         "one gateway process, N client threads. Real detector with the model cache defeated (every message runs the model). LLM stubbed. 'pre-LLM' = entry -> LLM boundary (the 50 ms budget); 'wall' = time the caller waits per message incl. queueing.", ""]
    fps = {}
    backends = [("File SQLite (WAL)", lambda: (VaultClient(path=os.path.join(tempfile.mkdtemp(), "g.db"))))]
    if a.pg:
        from layer2_vault.config.settings import Settings
        backends.append(("PostgreSQL 16, pooled (min 4 / max 16)", lambda: VaultClient(settings=Settings.for_testing(db_url=a.pg + ("&" if "?" in a.pg else "?") + "pool_min=4&pool_max=16&schema=ld_" + uuid.uuid4().hex[:8]))))
    for label, mkv in backends:
        L += [f"## {label}", "", "| clients | msgs/s | pre-LLM p50 | p95 | p99 | wall p50 | p95 | p99 | max | pre-LLM > 50 ms | failed / blocked | exceptions | leaks (>=50%) | output fingerprint |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c in (1, 2, 4, 8, 16):
            r = run(label, mkv(), rows, c, det); fps.setdefault(label, set()).add(r["fp"])
            L.append(f"| {c} | {r['tput']:.0f} | {r['pre'][0]:.1f} | {r['pre'][1]:.1f} | {r['pre'][2]:.1f} | {r['wall'][0]:.1f} | {r['wall'][1]:.1f} | {r['wall'][2]:.1f} | {r['wall'][3]:.1f} | {r['over50']} of {r['n_pre']} | {r['reasons'] or 0} | {len(r['errors'])} | {r['leak'][1]}/{r['leak'][0]} | {r['fp']} |")
        L.append("")
    allfp = set().union(*fps.values())
    L += [f"**Correctness:** {len(allfp)} distinct output fingerprint(s) across every concurrency level and backend (1 = identical masked output everywhere, i.e. no cross-conversation interference).", ""]
    open(os.path.join(os.path.dirname(__file__), "LOAD.md"), "w").write("\n".join(L)); print("wrote LOAD.md")


if __name__ == "__main__":
    main()
