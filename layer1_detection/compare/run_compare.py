"""Compare detectors on one dataset.   python -m layer1_detection.compare.run_compare data/hard_dev.jsonl out.json
Runs: full (ours), sakshi, gliner, full+sakshi, full+gliner. Each model is loaded once and shared."""
import json
import sys
import time

from layer0_data import scoreboard as sb
from layer1_detection.compare.adapters import GlinerAdapter, SakshiAdapter, UnionWithExtra
from layer1_detection.detector import Detector

path, out = sys.argv[1], sys.argv[2]
names = sys.argv[3].split(",") if len(sys.argv) > 3 and sys.argv[3] != "all" else ["full", "sakshi", "gliner", "full+sakshi", "full+gliner"]
limit = int(sys.argv[4]) if len(sys.argv) > 4 else None
rows = [json.loads(l) for l in open(path, encoding="utf-8")][:limit]   # first N conversations (fixed subset)
print("rows:", len(rows), flush=True)
sak = SakshiAdapter() if any("sakshi" in n for n in names) else None
gli = GlinerAdapter() if any("gliner" in n for n in names) else None
dets = {"full": lambda: Detector(use_rules=True, use_model=True), "sakshi": lambda: sak, "gliner": lambda: gli,
        "full+sakshi": lambda: UnionWithExtra(sak), "full+gliner": lambda: UnionWithExtra(gli)}
results = {}
for n in names:
    t0 = time.perf_counter()
    preds, ms = sb.run(dets[n](), rows)
    cells, nh, nt, fpf, fc, fpl, src, misses = sb.score(rows, preds)
    def pc(k, i):
        v = cells.get(k)
        return round(100 * v[i] / v[0], 1) if v else None
    results[n] = {
        "masked": {t: pc(("type", t), 1) for t in sb.TYPES}, "full": {t: pc(("type", t), 2) for t in sb.TYPES},
        "strict": {t: pc(("type", t), 3) for t in sb.TYPES},
        "ALL": {"masked": pc(("ALL",), 1), "full": pc(("ALL",), 2), "strict": pc(("ALL",), 3)},
        "lang_full": {l: pc(("lang", l), 2) for l in sb.LANGS if ("lang", l) in cells},
        "overmask": {t: f"{nh[t]}/{nt[t]}" for t in nt}, "pii_free_false_alarm_calls": f"{fpf}/{fc}",
        "false_alarm_labels": {f"{a}:{b}": c for (a, b), c in fpl.items()},
        "ms_per_turn_batched": round(ms, 1), "wall_s": round(time.perf_counter() - t0, 1)}
    print(n, results[n]["ALL"], "pii-free FA", results[n]["pii_free_false_alarm_calls"], flush=True)
    json.dump(results, open(out, "w"), indent=1)
print("COMPAREDONE", flush=True)
