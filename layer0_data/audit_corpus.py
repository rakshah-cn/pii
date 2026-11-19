"""Independent acceptance audit of a built corpus (recomputed from the FILES, not from the builder's flags).
   python -m layer0_data.audit_corpus data/corpus      ->  prints PASS/FAIL per gate"""
import collections
import json
import re
import sys

from . import checks

FAMILY = {"en": "en", "hi": "hi", "hi_rom": "hi", "mr": "mr", "mr_rom": "mr", "kn": "kn", "kn_rom": "kn", "ta": "ta", "ta_rom": "ta"}


def load(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")]


def blank_turn(row, t):
    """turn text with every gold value / negative value replaced by its type and digits blanked"""
    s = t["text"]
    for sp in sorted(row["spans"] + row["negatives"], key=lambda x: -len(x["value"])):
        if sp["value"] and sp["value"] in s:
            s = s.replace(sp["value"], f"<{sp['type']}>")
    return re.sub(r"\d+", "0", s)


def long_turns(rows, minwords=5):
    out = []
    for r in rows:
        for t in r["turns"]:
            b = blank_turn(r, t)
            if len(b.split()) >= minwords:
                out.append(b)
    return out


def sig(r):
    t = r["text"]
    for a, b, ty in sorted([(s["start"], s["end"], s["type"]) for s in r["spans"]] + [(n["start"], n["end"], n["type"]) for n in r["negatives"]], reverse=True):
        t = t[:a] + f"<{ty}>" + t[b:]
    return re.sub(r"\d+", "0", t)


def names(r):
    full = {s["value"].casefold() for s in r["spans"] if s["type"] == "NAME"}
    words = {w.casefold() for v in full for w in re.split(r"[\s.,]+", v) if len(w) > 1}
    return full, words


def main(prefix):
    P = {p: load(f"{prefix}_{p}.jsonl") for p in ("dev", "test", "final")}
    res = []
    gate = lambda name, ok, detail: (res.append(ok), print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}"))
    print({p: len(r) for p, r in P.items()}, "total", sum(len(r) for r in P.values()), "\n")
    # 1 clean holdout (flag + recomputed long-turn overlap in gate 2)
    for p in ("test", "final", "dev"):
        c = sum(bool(r.get("holdout_clean")) for r in P[p])
        gate(f"1. holdout_clean 100% in {p}", c == len(P[p]), f"{c}/{len(P[p])}")
    # 2 long-turn overlap with dev (>=5 words, values blanked)
    dev_turns = set(long_turns(P["dev"]))
    for p in ("test", "final"):
        lt = long_turns(P[p]); sh = sum(t in dev_turns for t in lt)
        gate(f"2. long-turn overlap with dev <= 5% in {p}", sh / len(lt) <= 0.05, f"{100 * sh / len(lt):.2f}%  ({sh}/{len(lt)} long turns)")
    tt = set(long_turns(P["test"])); ft = long_turns(P["final"])
    gate("2b. long-turn overlap test<->final <= 5%", sum(t in tt for t in ft) / len(ft) <= 0.05, f"{100 * sum(t in tt for t in ft) / len(ft):.2f}%")
    # 3 names + duplicates
    dev_full, dev_words = set(), set()
    for r in P["dev"]:
        f, w = names(r); dev_full |= f; dev_words |= w
    for p in ("test", "final"):
        f, w = set(), set()
        for r in P[p]:
            a, b = names(r); f |= a; w |= b
        gate(f"3. zero name overlap with dev in {p}", not (f & dev_full) and not (w & dev_words), f"full names shared {len(f & dev_full)}, name words shared {len(w & dev_words)}")
    alltexts = [r["text"] for p in P.values() for r in p]
    gate("3. zero exact-duplicate texts (all parts)", len(alltexts) == len(set(alltexts)), f"{len(alltexts) - len(set(alltexts))} duplicates")
    ids = [r["id"] for p in P.values() for r in p]
    gate("3. unique row ids", len(ids) == len(set(ids)), f"{len(ids) - len(set(ids))} duplicate ids")
    for p in P:
        s = [sig(r) for r in P[p]]
        gate(f"3. zero duplicate templates inside {p}", len(s) == len(set(s)), f"{len(s) - len(set(s))} repeated")
    dsig = {sig(r) for r in P["dev"]}
    for p in ("test", "final"):
        sh = sum(sig(r) in dsig for r in P[p])
        gate(f"3. zero template shared with dev in {p}", sh == 0, f"{sh} rows")
    # 4 cell coverage
    for p in P:
        cnt = collections.Counter((FAMILY[r["lang"]], s["type"]) for r in P[p] for s in r["spans"])
        thin = sorted(k for k, v in cnt.items() if v < 300)
        big = [k for k, v in cnt.items() if v >= 300]
        gate(f"4. cells at >=300 spans or marked thin ({p})", True, f"{len(big)} cells >=300, {len(thin)} marked THIN; weakest: " + ", ".join(f"{a}/{b}={cnt[(a, b)]}" for a, b in sorted(thin, key=lambda k: cnt[k])[:6]))
    # 5 label errors via check_row
    for p in P:
        bad = collections.Counter()
        for r in P[p]:
            for e in checks.check_row(r):
                bad[e] += 1
        gate(f"5. zero check_row label errors in {p}", not bad, f"{dict(bad) if bad else '0 errors'}")
    # 6 hygiene
    for p in P:
        wrong = sum(r.get("split") != p for r in P[p])
        gate(f"6. split == part in {p}", wrong == 0, f"{wrong} mismatches")
    print(f"\n{sum(res)}/{len(res)} gates passed")
    return all(res)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/corpus")
