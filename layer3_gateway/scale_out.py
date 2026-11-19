"""Scale-out: does adding gateway PROCESSES add capacity, with NON-sticky routing?     python -m layer3_gateway.scale_out --pg URL [--convs 160]
P worker processes (each its own detector + gateway + vault connection pool; one shared PostgreSQL vault and one shared master secret). A pool hands each TURN of a conversation
to whichever worker is free, so consecutive turns of one conversation land on DIFFERENT processes (the situation that used to leak). K driver threads each walk one conversation at a time.
Checks: throughput and latency per P, and that the masked output of every message is byte-identical to the one-process run (shared conversation context makes routing irrelevant).
Writes layer3_gateway/SCALE.md."""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
import platform
import threading
import time
import uuid

from layer3_gateway.leak_proof import load_rows

q = lambda xs, f: sorted(xs)[min(len(xs) - 1, int(f * len(xs)))] if xs else float("nan")
_gw = None


class _NoHit(dict):
    def __contains__(self, k):
        return False


def _init(url, key_hex, threads):
    global _gw
    from layer1_detection.detector import Detector
    from layer2_vault.config.settings import Settings
    from layer3_gateway.graph import Gateway, make_detector
    from layer3_gateway.vault_client import VaultClient
    det = make_detector(model=True, threads=threads); det.model.cache = _NoHit()
    vault = VaultClient(settings=Settings.for_testing(master_secrets={1: bytes.fromhex(key_hex)}, db_url=url))
    _gw = Gateway(detector=det, audit_detector=Detector(use_rules=True, use_model=False), vault=vault, llm=lambda m: "ok")
    _gw.handle("warm", "Customer", "hello there my number is 98450 12345", borrower_id="Bwarm", purpose="loan_collection")


def _turn(task):
    cid, ti, speaker, text = task
    t0 = time.perf_counter()
    o = _gw.handle(cid, speaker, text, borrower_id="B" + cid, purpose="loan_collection", request_id=f"{cid}-{ti}")
    return (o.get("pre_llm_ms"), (time.perf_counter() - t0) * 1000, o["status"], hashlib.sha256((o.get("masked_text") or "<none>").encode()).hexdigest()[:16], os.getpid())


def run(procs, rows, url, key_hex, threads):
    ctx = mp.get_context("spawn")
    with ctx.Pool(procs, initializer=_init, initargs=(url, key_hex, threads)) as pool:
        pre, wall, digests, pids, lock, bad = [], [], {}, set(), threading.Lock(), []
        per_conv_pids = {}
        todo = list(enumerate(rows)); tl = threading.Lock()

        def driver():
            while True:
                with tl:
                    if not todo: return
                    ci, row = todo.pop()
                for ti, t in enumerate(row["turns"]):
                    r = pool.apply(_turn, ((f"S{procs}-{ci}", ti, t["speaker"], t["text"]),))
                    with lock:
                        if r[0] is not None: pre.append(r[0])
                        wall.append(r[1]); digests[(ci, ti)] = r[3]; pids.add(r[4]); per_conv_pids.setdefault(ci, set()).add(r[4])
                        if r[2] == "blocked": bad.append(ci)
        K = max(2, procs * 2)
        ts = [threading.Thread(target=driver) for _ in range(K)]
        t0 = time.perf_counter(); [t.start() for t in ts]; [t.join() for t in ts]; el = time.perf_counter() - t0
    fp = hashlib.sha256(json.dumps(sorted(digests.items())).encode()).hexdigest()[:16]
    moved = sum(1 for s in per_conv_pids.values() if len(s) > 1)
    return {"procs": procs, "msgs": len(wall), "tput": len(wall) / el, "pre": (q(pre, .5), q(pre, .95), q(pre, .99)), "wall": (q(wall, .5), q(wall, .95)), "over50": sum(1 for x in pre if x > 50), "n": len(pre),
            "blocked": len(bad), "fp": fp, "moved": moved, "convs": len(per_conv_pids)}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--pg", required=True); ap.add_argument("--convs", type=int, default=160); ap.add_argument("--threads", default="auto", help="'auto' = cores / processes (>= 1), 'default' = Layer 1's own 8, or a number"); a = ap.parse_args()
    rows = load_rows(a.convs, 11); key_hex = os.urandom(32).hex()
    L = ["# Gateway scale-out with non-sticky routing", "", f"Machine: {platform.machine()}, {os.cpu_count()} CPUs. {len(rows)} conversations ({sum(len(r['turns']) for r in rows)} messages), synthetic. P gateway PROCESSES (own detector, own vault pool), one shared PostgreSQL vault, "
         "each TURN sent to whichever process is free (so a conversation's turns spread over processes). 2P client threads. LLM stubbed. 'pre-LLM' = entry -> LLM boundary (50 ms budget).", "",
         "| processes | model threads each | msgs/s | pre-LLM p50 | p95 | p99 | over 50 ms | wall p50 | wall p95 | conversations that moved between processes | blocked convs | output fingerprint |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    fps = set()
    for p in (1, 2, 4, 8):
        url = a.pg + ("&" if "?" in a.pg else "?") + f"pool_min=1&pool_max=4&schema=so_{uuid.uuid4().hex[:8]}"
        th = None if a.threads == "default" else max(1, (os.cpu_count() or 8) // p) if a.threads == "auto" else int(a.threads)
        r = run(p, rows, url, key_hex, th); fps.add(r["fp"]); r["threads"] = th; print(r, flush=True)
        L.append(f"| {p} | {r['threads'] or 'Layer 1 default (8)'} | {r['tput']:.0f} | {r['pre'][0]:.1f} | {r['pre'][1]:.1f} | {r['pre'][2]:.1f} | {r['over50']} of {r['n']} | {r['wall'][0]:.1f} | {r['wall'][1]:.1f} | {r['moved']} of {r['convs']} | {r['blocked']} | {r['fp']} |")
    L += ["", f"**Correctness:** {len(fps)} distinct output fingerprint(s) across 1, 2, 4 and 8 processes (1 = every message masked byte-for-byte the same however it was routed).", ""]
    open(os.path.join(os.path.dirname(__file__), "SCALE.md"), "w").write("\n".join(L)); print("wrote SCALE.md")


if __name__ == "__main__":
    main()
