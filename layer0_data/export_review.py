"""Export every non-English string we authored into one CSV for a native speaker to review.
Columns: language, kind, text, status (blank -> ok / fix), correction.   Run:  python -m layer0_data.export_review"""
import csv
import re

from . import hard, pii_gen, skeleton_lib as SL, spoken

rows = []
add = lambda lang, kind, text: rows.append((lang, kind, text, "", ""))
for lang, table in spoken.LEX.items():
    if lang == "en":
        continue
    for d, words in enumerate(table):
        add(lang, f"digit_word_{d}", " | ".join(words))
for lang, alts in hard._ALT.items():
    for w, vs in alts.items():
        add(lang, "asr_variant", f"{w} -> {', '.join(vs)}")
for lang, (first, last) in pii_gen._N.items():
    if lang != "en":
        add(lang, "first_names", " ".join(first)); add(lang, "last_names", " ".join(last))
for lang, a in pii_gen._AD.items():
    if lang == "roman":
        continue
    for k in ("house", "bld", "street", "loc", "city"):
        add(lang, f"address_{k}", " | ".join(a[k]))
import json as _json
_status = _json.load(open("layer0_data/review/ext_review_status.json", encoding="utf-8"))
for name, items in SL.pool_registry().items():
    fam, _, rest = name.partition(".")
    kind, lang = fam.split(":")[0], fam.split(":")[-1]
    if lang == "en" or fam == "prompt":
        continue
    group = {"call": "call_", "msg": "msg_", "msgnative": "msgnative_"}[kind] + lang
    for it in items:
        origin = "agent-written" if SL.is_ext(it) else "hand-written"
        rev = ("machine-reviewed x%d" % _status.get(group, 0)) if (origin == "agent-written" and _status.get(group, 0)) else ("NOT machine-reviewed" if origin == "agent-written" else "n/a")
        rows.append((lang, f"{name} [{origin}; {rev}]", SL.block_to_str(it), "", ""))
add_note = None
with open("layer0_data/review/native_review.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f); w.writerow(["language", "kind", "text", "status (ok/fix)", "correction"]); w.writerows(rows)
import collections
print(len(rows), "rows ->", "layer0_data/review/native_review.csv")
print(dict(collections.Counter(r[0] for r in rows)))
