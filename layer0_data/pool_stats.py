"""Enumerate every template POOL (a list of lines a builder draws one from) and report how many lines fall in each
holdout zone. Run:  python -m layer0_data.pool_stats"""
import collections

from . import skeleton_lib as SL


def pools():
    return list(SL.pool_registry().items())


def zone_counts(zone_fn):
    tot = collections.Counter(); empty = collections.Counter(); one = collections.Counter(); P = pools()
    for name, items in P:
        z = zone_fn(items)
        for part in ("dev", "test", "final"):
            n = len(z[part]); tot[part] += n
            empty[part] += n == 0; one[part] += n == 1
    return len(P), tot, empty, one


def old_zones(items):
    b = lambda x: SL._bucket(x)
    return {"dev": [x for x in items if b(x) < 6], "test": [x for x in items if 6 <= b(x) < 8], "final": [x for x in items if b(x) >= 8]}


if __name__ == "__main__":
    for label, fn in (("OLD zones (hash bucket 0-5 / 6-7 / 8-9)", old_zones),
                      ("NEW zones", getattr(SL, "zones", None))):
        if fn is None:
            continue
        n, tot, empty, one = zone_counts(fn)
        print(f"{label}: {n} pools")
        print("   lines per zone      :", dict(tot))
        print("   pools with NO line  :", dict(empty))
        print("   pools with 1 line   :", dict(one))
