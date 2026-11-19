"""Scoreboard.   Run from the project root:
    python -m layer0_data.scoreboard data/synth_dev.jsonl <detector> [limit]
detectors:  model  = Chitragupta only (baseline)
            rules  = our rules + spoken engine + cross-turn memory (no model)
            full   = union of all of the above (Layer 1)

A gold PII span counts as CAUGHT if >=50% of its characters are masked (whatever the label).
Also reported: over-masking of amounts/dates/company names, false alarms in PII-free calls,
latency, and WHICH layer caught each span (rules / spoken / model) to help troubleshooting.
"""
import collections
import json
import statistics
import sys
import time

THRESH, FULL = 0.5, 0.95
LABEL_MAP = {"PERSON": "NAME", "NAME": "NAME", "PHONE": "PHONE", "AADHAAR": "AADHAAR", "PAN": "PAN", "BANK_ACCOUNT": "ACCOUNT", "ACCOUNT": "ACCOUNT",
             "ADDRESS": "ADDRESS", "LOAN": "LOAN", "EMAIL": "EMAIL", "DOB": "DOB", "UPI": "UPI", "UPI_ID": "UPI"}


def turn_bases(row):
    """Global offset where each turn's TEXT starts inside row['text']."""
    bases, pos = [], 0
    for t in row["turns"]:
        prefix = f"[{t['speaker']}]: "
        bases.append(pos + len(prefix))
        pos += len(prefix) + len(t["text"]) + 1
    return bases


def covered(span, preds):
    """Fraction of the gold span's NON-whitespace characters inside the union of predicted spans."""
    val = span.get("value", "")
    idx = [i for i in range(span["start"], span["end"])
           if not (len(val) == span["end"] - span["start"] and val[i - span["start"]].isspace())]
    if not idx:
        return 1.0
    hit = sum(any(p["start"] <= i < p["end"] for p in preds) for i in idx)
    return hit / len(idx)


def run(det, rows):
    t0 = time.perf_counter()
    res = det.detect_calls([r["turns"] for r in rows])
    ms = (time.perf_counter() - t0) * 1000
    preds = []
    for r, per_turn in zip(rows, res):
        ps = []
        for base, spans in zip(turn_bases(r), per_turn):
            ps += [dict(s, start=s["start"] + base, end=s["end"] + base) for s in spans]
        preds.append(ps)
    n_turns = sum(len(r["turns"]) for r in rows)
    return preds, ms / n_turns


def score(rows, preds):
    cells = collections.defaultdict(lambda: [0, 0, 0, 0])
    neg_hit, neg_tot = collections.Counter(), collections.Counter()
    fp_free = free_calls = 0
    fp_labels = collections.Counter()
    by_source = collections.Counter()
    misses = []
    for r, ps in zip(rows, preds):
        for g in r["spans"]:
            c = covered(g, ps)
            overlap = [p for p in ps if p["start"] < g["end"] and p["end"] > g["start"]]
            strict = any(LABEL_MAP.get(p["label"]) == g["type"] for p in overlap)
            for key in (("ALL",), ("type", g["type"]), ("form", r["form"]), ("lang", r["lang"]),
                        ("type_form", g["type"], r["form"]), ("type_lang", g["type"], r["lang"])):
                m = cells[key]
                m[0] += 1; m[1] += c >= THRESH; m[2] += c >= FULL; m[3] += strict
            by_source["+".join(sorted({x for p in overlap for x in p["source"].split("+")})) if c >= THRESH else "MISSED"] += 1
            if c < THRESH:
                misses.append((r["id"], g["type"], r["form"], r["lang"], g["value"]))
        for n in r["negatives"]:
            neg_tot[n["type"]] += 1
            neg_hit[n["type"]] += covered(n, ps) > 0
        if r["primary_pii"] == "NONE":
            free_calls += 1
            fp_free += bool(ps)
            for p in ps:
                fp_labels[(p["source"], p["label"])] += 1
    return cells, neg_hit, neg_tot, fp_free, free_calls, fp_labels, by_source, misses


def pct(a, b):
    return f"{100 * a / b:5.1f}%" if b else "   n/a"


def report(res, ms_turn):
    cells, nh, nt, fpf, fc, fpl, src, misses = res
    print("\n=== RECALL (span masked >=50%) ===")
    for key in sorted(cells, key=lambda k: (len(k), str(k))):
        n, c, f, s = cells[key]
        if len(key) <= 2:
            print(f"  {' / '.join(key):<26} n={n:<4} {pct(c, n)}  full {pct(f, n)}  strict {pct(s, n)}")
    print("\n=== weakest (type x form) ===")
    tf = [(k, v) for k, v in cells.items() if k[0] == "type_form" and v[0] >= 5]
    for k, v in sorted(tf, key=lambda kv: kv[1][1] / kv[1][0])[:8]:
        print(f"  {k[1]:<8} {k[2]:<15} n={v[0]:<4} {pct(v[1], v[0])}")
    print("\n=== who caught the spans ===", dict(src))
    print("\n=== OVER-MASKING (want ~0%) ===")
    for t in nt:
        print(f"  {t:<7} touched {nh[t]}/{nt[t]} ({pct(nh[t], nt[t]).strip()})")
    print(f"  PII-free calls with any detection: {fpf}/{fc} ({pct(fpf, fc).strip()})  by (source,label): {dict(fpl)}")
    print(f"\n=== LATENCY: {ms_turn:.1f} ms per turn on average (batched; cached model results excluded if cached)")
    grp = collections.Counter((m[1], m[2]) for m in misses)
    print(f"\nmisses: {len(misses)}; biggest groups (type, form): {grp.most_common(6)}")
    for m in misses[:6]:
        print("  ", m)


LANGS = ["en", "hi", "hi_rom", "mr", "mr_rom", "kn", "kn_rom", "ta", "ta_rom"]
TYPES = ["PHONE", "AADHAAR", "ACCOUNT", "PAN", "LOAN", "NAME", "ADDRESS"]


def table(cells):
    """type x language matrices: masked (>=50%) and fully masked (>=95%)."""
    langs = [l for l in LANGS if ("lang", l) in cells]
    for title, idx in (("MASKED >=50%", 1), ("FULLY MASKED >=95%", 2), ("STRICT LABEL", 3)):
        print(f"\n=== {title}: type x language (recall%, n) ===")
        print(f"{'':9}" + "".join(f"{l:>11}" for l in langs) + f"{'ALL':>11}")
        for t in TYPES:
            line = f"{t:9}"
            for l in langs:
                v = cells.get(("type_lang", t, l))
                line += f"{100 * v[idx] / v[0]:6.0f}% ({v[0]:>2})" if v else f"{'-':>11}"
            v = cells.get(("type", t))
            line += f"{100 * v[idx] / v[0]:6.0f}% ({v[0]:>3})" if v else f"{'-':>11}"
            print(line)


FAMILY = {"en": "en", "hi": "hi", "hi_rom": "hi", "mr": "mr", "mr_rom": "mr", "kn": "kn", "kn_rom": "kn", "ta": "ta", "ta_rom": "ta"}
THIN = 300            # a language-family x type cell with fewer gold spans than this is reported as THIN (its % is weak evidence)


def slice_summary(rows, preds):
    """Fix #8: report the headline on honest slices: all rows, CLEAN-holdout only, noisy only, per language family,
    per channel. The CLEAN slice is the headline (rows that never borrowed a template line from another part)."""
    def one(name, idx):
        if not idx:
            print(f"  {name:<28} (no rows)"); return
        cells, nh, nt, fpf, fc, fpl, src, miss = score([rows[i] for i in idx], [preds[i] for i in idx])
        a = cells[("ALL",)]
        print(f"  {name:<28} rows {len(idx):>6} spans {a[0]:>6} | masked {pct(a[1], a[0])}  FULL {pct(a[2], a[0])}  strict {pct(a[3], a[0])} | PII-free flagged {fpf}/{fc}")
    n = range(len(rows))
    print("\n=== SLICES (headline = clean-holdout) ===")
    one("ALL rows", list(n))
    one("CLEAN-holdout rows  [HEADLINE]", [i for i in n if rows[i].get("holdout_clean", True)])
    one("noisy rows only", [i for i in n if rows[i].get("hard")])
    one("clean + noisy", [i for i in n if rows[i].get("holdout_clean", True) and rows[i].get("hard")])
    for fam in ("en", "hi", "mr", "kn", "ta"):
        one(f"family={fam}", [i for i in n if FAMILY.get(rows[i]["lang"]) == fam])
    for ch in ("call", "message", "prompt"):
        one(f"channel={ch}", [i for i in n if rows[i].get("channel", "call") == ch])
    cells = score(rows, preds)[0]
    cnt = collections.defaultdict(lambda: [0, 0]); thin = []
    for r, ps in zip(rows, preds):
        for g in r["spans"]:
            k = (FAMILY.get(r["lang"], r["lang"]), g["type"])
            cnt[k][0] += 1; cnt[k][1] += covered(g, ps) >= FULL
    for k, (nn, ok) in sorted(cnt.items()):
        if nn < THIN:
            thin.append(f"{k[0]}/{k[1]}(n={nn})")
    print(f"  THIN cells (< {THIN} gold spans; read their % with care): {len(thin)} of {len(cnt)}: {', '.join(thin[:14])}{' ...' if len(thin) > 14 else ''}")


def make_detector(name):
    from layer1_detection.detector import Detector
    if name in ("sakshi", "gliner", "full+sakshi", "full+gliner"):
        from layer1_detection.compare.adapters import make_adapter
        return make_adapter(name)
    return {"model": lambda: Detector(use_rules=False, use_model=True),
            "rules": lambda: Detector(use_rules=True, use_model=False),
            "full_int8": lambda: Detector(use_rules=True, use_model=True, model_backend="onnx_int8"),
            "full": lambda: Detector(use_rules=True, use_model=True)}[name]()


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    path, name = argv[0], argv[1]
    limit = int(argv[2]) if len(argv) > 2 else None
    rows = [json.loads(l) for l in open(path, encoding="utf-8")][:limit]
    preds, ms = run(make_detector(name), rows)
    res = score(rows, preds)
    report(res, ms)
    if "--table" in sys.argv:
        table(res[0])
    if "--slices" in sys.argv:
        slice_summary(rows, preds)
