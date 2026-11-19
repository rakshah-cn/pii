"""Build the held-out synthetic corpus.     python -m layer0_data.build_corpus [--dev N --test N --final N --seed S --out data/corpus]

For each part (dev first, then test, then final) it over-generates rows with the same pipeline as make_dataset
(deterministic checks, no LLM) and KEEPS only rows that are
  * holdout_clean     every template line came from this part's own zone (fix #3)
  * unique            blanked-text signature not seen in this part, and for test/final not seen in dev (fix #6)
  * unseen names      test/final: no NAME word occurs in dev (fix #5). Names are zoned BY WORD in pii_gen, so this
                      holds by construction; it is also checked here as a belt-and-braces filter.
Then it selects the target number of rows stratified by (language family, channel, form, PII-free) matching the
sampler's own proportions, and tops up THIN (language family x PII type) cells (fix #7).  split == part (fix #4).
Writes <out>_dev/_test/_final.jsonl and <out>_manifest.json (gate metrics).  All values are fake."""
import argparse
import collections
import json
import random
import re

from . import graph, specs

FAMILY = {"en": "en", "hi": "hi", "hi_rom": "hi", "mr": "mr", "mr_rom": "mr", "kn": "kn", "kn_rom": "kn", "ta": "ta", "ta_rom": "ta"}
MIN_CELL = {"dev": 300, "test": 60, "final": 60}      # spans per (family, type) cell we try to reach by top-up


def make_cells(n, seed, part, hard_share=0.5):
    cells = specs.sample_cells(n, seed)
    rr = random.Random(seed + 99)
    for c in cells:
        c["hard"] = rr.random() < hard_share
        c["part"] = part
        if c["hard"] and c["pii"] in specs.DIGIT_TYPES and c["channel"] != "prompt" and rr.random() < 0.25:
            c["form"] = "partial"
    return cells


def stratum_of_cell(c):
    return (FAMILY[c["lang"]], c["channel"], c["form"], c["pii"] == "NONE")


def stratum(r):
    return (FAMILY[r["lang"]], r["channel"], r["form"], r["primary_pii"] == "NONE")


def signature(r):
    t = r["text"]
    for a, b, ty in sorted([(s["start"], s["end"], s["type"]) for s in r["spans"]] + [(n["start"], n["end"], n["type"]) for n in r["negatives"]], reverse=True):
        t = t[:a] + f"<{ty}>" + t[b:]
    return re.sub(r"\d+", "0", t)


def name_words(r):
    out = set()
    for s in r["spans"]:
        if s["type"] == "NAME":
            out |= {w.casefold() for w in re.split(r"[\s.,]+", s["value"]) if len(w) > 1}
    return out


def generate(part, n, seed):
    app = graph.build_graph()
    cells = make_cells(n, seed, part)
    rows = []
    for i, c in enumerate(cells):
        res = app.invoke({"cell": c, "seed": seed * 100000 + i})
        if res["status"] != "approved":
            continue
        r = res["row"]; r.pop("skeleton")
        r.update(conv_id=f"c_{part}_{seed}_{i:06d}", split=part, part=part, difficulty=specs.difficulty(c), hard=bool(c.get("hard")),
                 rounds=res["rounds"], status="approved")
        rows.append(r)
    return rows


def span_cells(r):
    return [(FAMILY[r["lang"]], s["type"]) for s in r["spans"]]


def select(cands, target, part, rng, ref_share):
    """Stratified pick of `target` rows, then top up thin (family, type) cells without exceeding the target."""
    by = collections.defaultdict(list)
    for r in cands:
        by[stratum(r)].append(r)
    for v in by.values():
        rng.shuffle(v)
    chosen, used = [], set()
    for st, share in ref_share.items():
        q = round(target * share)
        for r in by.get(st, [])[:q]:
            chosen.append(r); used.add(id(r))
    rest = [r for r in cands if id(r) not in used]
    rng.shuffle(rest)
    while len(chosen) < target and rest:                 # strata that ran short are filled from whatever is left
        r = rest.pop(); chosen.append(r); used.add(id(r))
    # top up thin cells: add candidate rows that contain spans for under-quota cells, then drop non-contributing rows
    need = MIN_CELL[part]
    for _ in range(3):
        cnt = collections.Counter(c for r in chosen for c in span_cells(r))
        deficit = {c for c, k in cnt.items() if k < need} | {(f, t) for f in ("en", "hi", "mr", "kn", "ta") for t in ("PHONE", "AADHAAR", "ACCOUNT", "PAN", "LOAN", "NAME") if cnt[(f, t)] < need}
        if not deficit:
            break
        extra = [r for r in cands if id(r) not in used and any(c in deficit for c in span_cells(r))]
        rng.shuffle(extra)
        for r in extra:
            if not any(cnt[c] < need for c in span_cells(r)):
                continue
            chosen.append(r); used.add(id(r))
            for c in span_cells(r): cnt[c] += 1
        # trim back to target by removing rows that contribute to no deficit cell and no scarce cell
        cnt = collections.Counter(c for r in chosen for c in span_cells(r))
        removable = [r for r in chosen if all(cnt[c] > need for c in span_cells(r))]
        rng.shuffle(removable)
        while len(chosen) > target and removable:
            r = removable.pop(); chosen.remove(r)
    return chosen[:target] if len(chosen) > target else chosen


def main(dev=17500, test=3750, final=3750, seed=100, out="data/corpus", over=3.0):
    rng = random.Random(seed)
    targets = {"dev": dev, "test": test, "final": final}
    ref = collections.Counter()
    for c in make_cells(60000, seed + 7, "dev"):
        ref[stratum_of_cell(c)] += 1
    ref_share = {k: v / 60000 for k, v in ref.items()}
    out_rows, manifest = {}, {"seed": seed, "targets": targets, "parts": {}}
    seen_sig, dev_words, dev_sigs = set(), set(), set()
    for k, part in enumerate(("dev", "test", "final")):
        n_gen = int(targets[part] * over / 0.8)
        cands, rej = [], collections.Counter()
        sigs = set()
        for rows in (generate(part, n_gen, seed + 10 * k + 1),):
            for r in rows:
                if not r.get("holdout_clean", True):
                    rej["unclean"] += 1; continue
                sg = signature(r)
                if sg in sigs or (part != "dev" and sg in dev_sigs):
                    rej["duplicate_template"] += 1; continue
                if part != "dev" and (name_words(r) & dev_words):
                    rej["name_seen_in_dev"] += 1; continue
                sigs.add(sg); cands.append(r)
        chosen = select(cands, targets[part], part, rng, ref_share)
        for i, r in enumerate(chosen):
            r["id"] = f"{part}_{i:06d}"
        out_rows[part] = chosen
        if part == "dev":
            dev_words = set().union(*[name_words(r) for r in chosen])
            dev_sigs = {signature(r) for r in chosen}
        with open(f"{out}_{part}.jsonl", "w", encoding="utf-8") as f:
            for r in chosen:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        cnt = collections.Counter(c for r in chosen for c in span_cells(r))
        thin = sorted(f"{a}/{b}(n={v})" for (a, b), v in cnt.items() if v < 300)
        manifest["parts"][part] = {"generated": n_gen, "candidates_kept": len(cands), "rejected": dict(rej), "rows": len(chosen),
                                   "spans": sum(len(r["spans"]) for r in chosen), "clean_pct": round(100 * sum(r["holdout_clean"] for r in chosen) / max(1, len(chosen)), 2),
                                   "thin_cells_under_300_spans": thin}
        print(f"{part:<6} rows {len(chosen):>6}/{targets[part]} | candidates {len(cands)} of {n_gen} generated | rejected {dict(rej)} | clean {manifest['parts'][part]['clean_pct']}% | thin cells {len(thin)}")
    json.dump(manifest, open(f"{out}_manifest.json", "w", encoding="utf-8"), indent=1)
    return out_rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", type=int, default=17500); ap.add_argument("--test", type=int, default=3750); ap.add_argument("--final", type=int, default=3750)
    ap.add_argument("--seed", type=int, default=100); ap.add_argument("--out", default="data/corpus"); ap.add_argument("--over", type=float, default=3.0)
    a = ap.parse_args()
    main(a.dev, a.test, a.final, a.seed, a.out, a.over)
