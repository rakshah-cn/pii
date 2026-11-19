"""Generate a synthetic dataset.
  python -m layer0_data.make_dataset N OUT SEED [hard=0.5] [llm] [part=dev|test|final] [workers=8]
hard=F   share of rows with ASR-style noise / partial ids (plain `hard` = 1.0)
llm      run every skeleton through the LLM judge + rewriter (Bedrock; skeletons only, placeholders)
part=    which holdout zone to draw template lines and NAME words from (dev/test/final), see skeleton_lib.zones
clean    drop rows that had to borrow a line from another part (holdout_clean=False). split == part when part is given
"""
import collections
import hashlib
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor

from . import checks, graph, specs


def split_of(conv_id):
    h = int(hashlib.md5(conv_id.encode()).hexdigest(), 16) % 100
    return "train" if h < 70 else ("dev" if h < 85 else "test")


def main(n=100, out="data/synth_dev.jsonl", seed=0, hard_share=0.0, llm=False, part=None, workers=8, clean_only=False):
    if llm:
        from . import llm_steps
        checks.JUDGE = llm_steps.judge
        app = graph.build_graph(rewrite_fn=llm_steps.rewrite)
    else:
        app = graph.build_graph()
    cells = specs.sample_cells(n, seed)
    rr = random.Random(seed + 99)
    for c in cells:
        c["hard"] = rr.random() < hard_share
        c["part"] = part
        if c["hard"] and c["pii"] in specs.DIGIT_TYPES and c["channel"] != "prompt" and rr.random() < 0.25:
            c["form"] = "partial"

    def one(i):
        try:
            return i, app.invoke({"cell": cells[i], "seed": seed * 100000 + i})
        except Exception as e:                          # an API failure drops THIS row, not the whole run
            return i, {"status": "error", "history": [[f"api_error:{type(e).__name__}"]], "rounds": 0}
    with ThreadPoolExecutor(workers if llm else 1) as ex:
        results = list(ex.map(one, range(n)))
    rows, fails = [], collections.Counter()
    for i, res in results:
        for hist in res["history"]:
            for e in hist:
                fails[e.split(":")[0] if e.startswith("unnatural") else e] += 1
        if res["status"] != "approved":
            fails["DISCARDED" if res["status"] != "error" else "ERROR"] += 1
            continue
        r = res["row"]
        cell = cells[i]
        if clean_only and not r.get("holdout_clean", True):
            fails["UNCLEAN_REJECTED"] += 1
            continue
        conv_id = f"c_{seed}_{i:05d}"
        r.update(id=f"s_{seed}_{i:05d}", conv_id=conv_id, split=(part if part else split_of(conv_id)), part=part or "all",
                 difficulty=specs.difficulty(cell), hard=bool(cell.get("hard")), rounds=res["rounds"], status="approved")
        r.pop("skeleton")
        rows.append(r)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)}/{n} rows -> {out}")
    print("PII-free:", sum(r["primary_pii"] == "NONE" for r in rows), "| hard:", sum(r["hard"] for r in rows))
    for k in ("channel", "form", "lang", "rounds"):
        print(f"by {k}:", dict(collections.Counter(r[k] for r in rows)))
    print("failure reasons seen (before fix):", dict(fails))


if __name__ == "__main__":
    a = sys.argv
    kw = {}
    for t in a[4:]:
        if t == "hard":
            kw["hard_share"] = 1.0
        elif t.startswith("hard="):
            kw["hard_share"] = float(t[5:])
        elif t == "llm":
            kw["llm"] = True
        elif t.startswith("part="):
            kw["part"] = t[5:]
        elif t.startswith("workers="):
            kw["workers"] = int(t[8:])
        elif t == "clean":
            kw["clean_only"] = True
    main(int(a[1]) if len(a) > 1 else 100, a[2] if len(a) > 2 else "data/synth_dev.jsonl", int(a[3]) if len(a) > 3 else 0, **kw)
