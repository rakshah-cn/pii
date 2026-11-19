"""End-to-end Layer 1 latency per message, gateway style: the current turn + up to 5 earlier turns.
Rules+spoken+resolve only, and full (with Chitragupta, cache OFF), p50/p95/p99.  Synthetic data."""
import json
import statistics
import sys
import time

import torch

from layer1_detection.detector import Detector

torch.set_num_threads(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
rows = [json.loads(l) for l in open("data/hard_dev.jsonl", encoding="utf-8")][:60]
msgs = []
for r in rows:
    for i in range(len(r["turns"])):
        msgs.append(r["turns"][max(0, i - 5):i + 1])
msgs = msgs[:300]


def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def run(det, label, clear=False):
    for m in msgs[:5]:
        det.detect_calls([m])
    lat = []
    for m in msgs:
        if clear:
            det.model.cache.clear()
        t0 = time.perf_counter(); det.detect_calls([m]); lat.append((time.perf_counter() - t0) * 1000)
    print(f"{label:<44} n={len(lat)} p50 {pct(lat,50):7.1f} p95 {pct(lat,95):7.1f} p99 {pct(lat,99):7.1f} mean {statistics.mean(lat):7.1f} ms", flush=True)


run(Detector(use_rules=True, use_model=False), "rules + spoken + resolve (no model)")
full = Detector(use_rules=True, use_model=True)
full.model.use_cache = False
run(full, f"full incl. Chitragupta torch fp32 ({torch.get_num_threads()} thr)", clear=True)
