"""Evaluator: the ONLY code that reads truth.json (the seeded ground truth). The agent never does.

  .venv/bin/python -m layer6_erasure.evaluate public     # 10 public requests (the set we tuned on)
  .venv/bin/python -m layer6_erasure.evaluate heldout    # 20 held-out requests (run once, report only)
Reports completeness (seeded copies removed), survivors by kind, collateral damage (other borrowers' data or
decoys deleted by mistake), passes and wall time per request. MOCK stores: results do not transfer to real ones.
"""
import collections
import json
import os
import sys

from layer5_lineage.registry import LineageRegistry
from layer7_audit.chain import AuditLog

from . import mock_data
from .adapters import StoreCatalog
from .agent import build_agent, erase
from .vault_adapter import get_vault

PRISTINE, WORK = "data/stores/pristine", "data/stores/work"


def present(root):
    return {a.name: {rid for rid, _, _ in a.records()} for a in StoreCatalog(root).all()}


def run(which="public", max_passes=4, verbose=True, vault=None, fresh=True, disable=()):
    if fresh:
        mock_data.make_work_copy(PRISTINE, WORK)
    truth = json.load(open(os.path.join(WORK, "truth.json"), encoding="utf-8"))
    vault_kind = "given"
    if vault is None:
        vault, vault_kind = get_vault()
    mock_data.seed_vault(vault, truth)
    reg_path = os.path.join(WORK, "registry.json")
    registry = LineageRegistry.load(reg_path)
    audit = AuditLog(os.path.join(WORK, "audit_log.jsonl"))
    app = build_agent(WORK, vault, registry, audit, max_passes=max_passes, registry_path=reg_path, disable=disable)
    reqs = json.load(open(os.path.join(WORK, f"requests_{which}.json")))
    erased, results = set(), []
    for rq in reqs:
        b = rq["borrower_id"]
        out = erase(app, rq)
        erased.add(b)
        pres = present(WORK)
        T = truth["borrowers"][b]["copies"]
        surv = [(s, i, k) for s, i, k in T if i in pres.get(s, set())]
        collateral = [(s, i) for ob, OT in truth["borrowers"].items() if ob not in erased
                      for s, i, _ in OT["copies"] if i not in pres.get(s, set())]
        collateral += [(s, i) for s, i in truth["decoys"] if i not in pres.get(s, set())]
        rec = out["record"]
        flagged = {(u["store"], u["id"]) for u in rec["unresolved"]}
        silent = [(s_, i, k) for s_, i, k in surv if (s_, i) not in flagged]
        results.append({"request": rq["request_id"], "borrower": b, "status": rec["status"], "passes": rec["passes"],
                        "duration_s": rec["duration_s"], "seeded": len(T), "survivors": surv, "silent": silent, "collateral": len(collateral),
                        "unresolved": rec["unresolved_count"], "probabilistic": rec["probabilistic"]})
        if verbose:
            print(f"{rq['request_id']} {b} {rec['status']:<16} copies={len(T):<3} survivors={len(surv):<2} "
                  f"flagged={len(surv) - len(silent)} SILENT={len(silent)} collateral={len(collateral)} "
                  f"passes={rec['passes']} {rec['duration_s']}s"
                  + (f"  survivor kinds={dict(collections.Counter(k for _, _, k in surv))}" if surv else ""))
    tot = sum(r["seeded"] for r in results)
    surv_all = sum(len(r["survivors"]) for r in results)
    summary = {"vault": vault_kind, "requests": len(results), "seeded_copies": tot, "removed": tot - surv_all,
               "completeness_pct": round(100 * (tot - surv_all) / tot, 1) if tot else None,
               "statuses": dict(collections.Counter(r["status"] for r in results)),
               "survivor_kinds": dict(collections.Counter(k for r in results for _, _, k in r["survivors"])),
               "survivors_flagged_for_human": sum(len(r["survivors"]) - len(r["silent"]) for r in results),
               "survivors_SILENT": sum(len(r["silent"]) for r in results),
               "collateral_total": results[-1]["collateral"] if results else 0,
               "max_duration_s": max((r["duration_s"] for r in results), default=0),
               "max_passes": max((r["passes"] for r in results), default=0),
               "chain": audit.verify_chain()}
    if verbose:
        print("\nSUMMARY:", json.dumps(summary, default=str))
    return results, summary


def ablate():
    """Switch each discovery method off in turn. Shows what each earns, and that a blind verify still says VERIFIED."""
    print(f"{'method disabled':<16}{'status counts':<46}{'silent survivors':<18}by kind")
    for m in ("none", "registry", "conversation", "raw", "indep", "derivative", "probe", "filegrep", "raw+indep"):
        res, s = run("public", verbose=False, disable=() if m == "none" else tuple(m.split("+")))
        print(f"{m:<16}{str(s['statuses']):<46}{s['survivors_SILENT']:<18}{s['survivor_kinds']}")


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "public"
    ablate() if a == "ablate" else run(a)
