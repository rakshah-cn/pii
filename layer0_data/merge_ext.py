"""Validate agent-written template lines and merge the survivors into layer0_data/skeleton_ext.json.

  python -m layer0_data.merge_ext <workflow_output.json>

A proposed line is kept only if (a) no reviewer dropped it and (b) EVERY deterministic check passes:
  slots      required slots present, nothing outside required+optional; split halves in order in separate turns
  format     'A: .. ;; C: ..' with 1-6 turns (prompt pools: plain string)
  digits     no digit 0-9 anywhere (numbers/dates must be placeholders)
  grammar    alternation balanced, not nested; every expansion is free of stray ( ) | { }
  script     native packs mostly in the right script; Roman/English packs contain no Indic script
  duplicates not already in the pool, not repeated among the new lines
  length     each turn <= 160 chars
"""
import collections
import json
import random
import re
import sys

from . import skeleton_lib as SL

SLOT = re.compile(r"\{([A-Z0-9_]+)\}")
SCRIPTS = {"hi": (0x0900, 0x097F), "mr": (0x0900, 0x097F), "kn": (0x0C80, 0x0CFF), "ta": (0x0B80, 0x0BFF)}
INDIC = (0x0900, 0x0DFF)
_N = lambda s: re.sub(r"\s+", " ", s.strip().casefold())


def lang_of(group):
    g = group.replace("call_", "").replace("msgnative_", "").replace("msg_", "")
    return g


def script_check(group, line):
    letters = [c for c in SLOT.sub("", line) if c.isalpha()]
    if not letters:
        return True, ""
    ind = sum(INDIC[0] <= ord(c) <= INDIC[1] for c in letters)
    lang = lang_of(group)
    if group.startswith("msgnative") or (group.startswith("call_") and lang in SCRIPTS):
        lo, hi = SCRIPTS[lang]
        right = sum(lo <= ord(c) <= hi for c in letters)
        return (right / len(letters) >= 0.4), f"native-script share {right}/{len(letters)}"
    return (ind == 0), f"Indic script in a Roman/English pack ({ind} chars)"


def validate(group, pool, req, opt, line, existing_norm):
    errs = []
    if re.search(r"[0-9]", SLOT.sub("", line)):
        errs.append("digit")
    slots = set(SLOT.findall(line))
    if not set(req) <= slots:
        errs.append(f"missing_required:{sorted(set(req) - slots)}")
    if not slots <= set(req) | set(opt):
        errs.append(f"unknown_slot:{sorted(slots - set(req) - set(opt))}")
    stripped = re.sub(r"\(([^()]*)\)", "", line)
    if "(" in stripped or ")" in stripped:
        errs.append("unbalanced_or_nested_parentheses")
    if pool.startswith("prompt."):
        turns = [("U", line)]
    else:
        parts = line.split(" ;; ")
        turns = []
        for p in parts:
            m = re.match(r"^([AC]): (.+)$", p)
            if not m:
                errs.append("bad_turn_format"); break
            turns.append((m.group(1), m.group(2)))
        if not (1 <= len(turns) <= 6):
            errs.append("turn_count")
    if any(len(t) > 160 for _, t in turns):
        errs.append("turn_too_long")
    # split halves: separate customer turns, in order
    for r in req:
        if r.endswith("_P1"):
            base = r[:-3]
            i1 = [i for i, (_, t) in enumerate(turns) if "{" + base + "_P1}" in t]
            i2 = [i for i, (_, t) in enumerate(turns) if "{" + base + "_P2}" in t]
            if not i1 or not i2 or not (i1[0] < i2[0]):
                errs.append("split_halves_not_in_order_in_separate_turns")
    # the customer (not the agent) gives identifiers
    if ".ask." in pool and ".NAME." not in pool:
        for r in req:
            for sp, t in turns:
                if sp == "A" and "{" + r + "}" in t:
                    errs.append("agent_says_the_identifier")
    # every grammar expansion is clean
    for sd in range(6):
        ex = SL.expand(line, random.Random(sd))
        if re.search(r"[()|]", SLOT.sub("", ex)) or ex.count("{") != ex.count("}"):
            errs.append("bad_expansion"); break
    ok, why = script_check(group, line)
    if not ok:
        errs.append("script:" + why)
    if _N(line) in existing_norm:
        errs.append("duplicate")
    return errs


def main(path):
    d = json.load(open(path, encoding="utf-8"))
    result = d["result"] if isinstance(d, dict) and "result" in d else d
    reg = SL.pool_registry()
    existing = {n: {_N(x if isinstance(x, str) else SL.block_to_str(x)) for x in items} for n, items in reg.items()}
    req_files = {}
    import glob, os
    for f in glob.glob(os.path.join(os.path.dirname(__file__), "review", "pool_requests", "*.json")):
        j = json.load(open(f, encoding="utf-8"))
        for p in j["pools"]:
            req_files[p["pool"]] = p
    ext, rep, reasons = collections.defaultdict(list), [], collections.Counter()
    for g in result:
        key = g["key"]
        dropped = collections.defaultdict(set)
        for dr in g.get("drops", []):
            dropped[dr["pool"]].add(_N(dr["line"]))
        prop = drop = bad = ok_n = 0
        for add in g["additions"]:
            pool, lines = add["pool"], add["lines"]
            rq = req_files.get(pool)
            if rq is None or pool not in reg:
                continue
            seen = set(existing[pool])
            for line in lines:
                prop += 1
                if _N(line) in dropped[pool]:
                    drop += 1; continue
                errs = validate(key, pool, rq["required_slots"], rq["optional_slots"], line, seen)
                if errs:
                    bad += 1
                    for e in errs: reasons[e.split(":")[0]] += 1
                    continue
                seen.add(_N(line)); ext[pool].append(line); ok_n += 1
        rep.append((key, prop, drop, bad, ok_n, g.get("reviewers")))
    json.dump(ext, open(os.path.join(os.path.dirname(__file__), "skeleton_ext.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{'group':<14}{'proposed':>9}{'review-dropped':>16}{'failed checks':>15}{'accepted':>10}{'reviewers':>10}")
    for r in rep:
        print(f"{r[0]:<14}{r[1]:>9}{r[2]:>16}{r[3]:>15}{r[4]:>10}{str(r[5]):>10}")
    print("TOTAL proposed", sum(r[1] for r in rep), "| review-dropped", sum(r[2] for r in rep), "| failed checks", sum(r[3] for r in rep), "| accepted", sum(r[4] for r in rep))
    print("check failures by reason:", dict(reasons.most_common()))


if __name__ == "__main__":
    main(sys.argv[1])
