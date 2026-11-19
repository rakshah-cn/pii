"""Freeze Layer 1: score the detector and write layer1_detection/LAYER1_REPORT.md.

  python -m layer1_detection.final_report                      # test + dev (default)
  python -m layer1_detection.final_report --parts test         # one part
  python -m layer1_detection.final_report --limit 200          # quick trial
  python -m layer1_detection.final_report --final              # ALSO score corpus_final (do this ONCE, last)

Detector configuration = exactly what the gateway uses: rules + spoken engine + Chitragupta (int8 ONNX), resolver,
span expansion, name memory, chatter suppression. All numbers are on SYNTHETIC data with gold labels."""
import argparse
import collections
import json
import os
import time

from layer0_data import scoreboard as sb
from layer1_detection.detector import Detector

OUT = os.path.join(os.path.dirname(__file__), "LAYER1_REPORT.md")
pct = lambda a, b: f"{100 * a / b:.1f}%" if b else "n/a"


def section(part, rows, preds):
    cells, nh, nt, fpf, fc, fpl, src, miss = sb.score(rows, preds)
    a = cells[("ALL",)]
    L = [f"## {part}: {len(rows)} rows, {a[0]} gold PII spans", "",
         f"- masked (>=50% of span): **{pct(a[1], a[0])}**   fully masked (>=95%): **{pct(a[2], a[0])}**   right type label too: **{pct(a[3], a[0])}**",
         f"- PII-free calls with a false alarm: **{fpf}/{fc} ({pct(fpf, fc)})**",
         "- must-NOT-mask things touched: " + ", ".join(f"{t} {nh[t]}/{nt[t]}" for t in sorted(nt)), "",
         "| type | spans | masked | fully masked | right label |", "|---|---|---|---|---|"]
    for k, v in sorted(cells.items(), key=lambda kv: -kv[1][0]):
        if k[0] == "type":
            L.append(f"| {k[1]} | {v[0]} | {pct(v[1], v[0])} | {pct(v[2], v[0])} | {pct(v[3], v[0])} |")
    L += ["", "| language variant | spans | fully masked |", "|---|---|---|"]
    for k, v in sorted(cells.items()):
        if k[0] == "lang":
            L.append(f"| {k[1]} | {v[0]} | {pct(v[2], v[0])} |")
    L += ["", "| channel | rows | spans | fully masked |", "|---|---|---|---|"]
    for ch in ("call", "message", "prompt"):
        idx = [i for i, r in enumerate(rows) if r.get("channel", "call") == ch]
        if idx:
            c = sb.score([rows[i] for i in idx], [preds[i] for i in idx])[0][("ALL",)]
            L.append(f"| {ch} | {len(idx)} | {c[0]} | {pct(c[2], c[0])} |")
    cnt = collections.Counter((sb.FAMILY.get(r["lang"], r["lang"]), g["type"]) for r in rows for g in r["spans"])
    thin = sorted(f"{f}/{t}={n}" for (f, t), n in cnt.items() if n < sb.THIN)
    grp = collections.Counter((m[1], m[2]) for m in miss).most_common(6)
    L += ["", f"- THIN cells (< {sb.THIN} gold spans, read their % with care): {len(thin)} of {len(cnt)}",
          f"- biggest miss groups (type, form): {grp}", ""]
    return L, a


def latency(rows, det):
    """One message at a time, gateway style: the current turn plus up to 5 earlier turns, model cache OFF."""
    W = []
    for r in rows[:80]:
        t = r["turns"]
        for i in range(len(t)):
            W.append([{"speaker": x["speaker"], "text": x["text"]} for x in t[max(0, i - 5):i + 1]])
    W = W[:160]
    g = Detector(use_rules=True, use_model=True, model_last_only=True, model_backend="onnx_int8")
    g.detect_calls([W[0]])
    ms = []
    for w in W:
        g.model.cache.clear()
        t0 = time.perf_counter(); g.detect_calls([w]); ms.append((time.perf_counter() - t0) * 1000)
    ms.sort()
    p = lambda q: ms[min(len(ms) - 1, int(q * len(ms)))]
    return [f"## Latency (one message at a time, gateway configuration, n={len(ms)})", "",
            f"p50 **{p(.5):.1f} ms**, p95 {p(.95):.1f} ms, p99 {p(.99):.1f} ms (budget 50 ms). Measured on short synthetic turns on this machine; "
            "real, longer messages will be slower.", ""]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default="test,dev"); ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--final", action="store_true"); ap.add_argument("--prefix", default="data/corpus")
    a = ap.parse_args()
    parts = [p for p in a.parts.split(",") if p] + (["final"] if a.final else [])
    det = Detector(use_rules=True, use_model=True, model_backend="onnx_int8")
    L = ["# Layer 1 (detection): final report", "",
         "Detector: rules + spoken-number engine (cross-turn memory) + Chitragupta (int8) + resolver (types, joined numbers) + "
         "span expansion + name memory + chatter suppression. Synthetic data, gold labels; **optimistic for real data**.", ""]
    first = None
    for part in parts:
        rows = [json.loads(l) for l in open(f"{a.prefix}_{part}.jsonl", encoding="utf-8")][:a.limit]
        t0 = time.time(); preds, _ = sb.run(det, rows)
        sec, tot = section(part, rows, preds); L += sec
        print(f"{part}: {len(rows)} rows scored in {time.time() - t0:.0f}s | fully masked {pct(tot[2], tot[0])} | masked {pct(tot[1], tot[0])}")
        first = first or rows
    L += latency(first, det)
    L += ["## Known limitations", "",
          "- names (about 93% fully masked) and addresses (about 90%) are the weak types; number types are 97-99%",
          "- every score is on our own synthetic data; real transcripts will score lower by an unknown amount",
          "- spoken tens and double/triple are not covered for Hindi/Marathi/Kannada/Tamil; digit-word lists and ASR spellings are unverified by native speakers",
          "- the greeting stoplist and company-suffix list were tuned on our failures", ""]
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
