"""Erasure demo (MOCK stores).   .venv/bin/python -m layer8_demo.erasure_demo

Seeds the mock stores, shows what the consent guard refused, runs one erasure request end to end, prints the audit
proof, then proves tamper detection by editing a copy of the audit log."""
import json
import os
import shutil

from layer5_lineage.registry import LineageRegistry
from layer6_erasure import mock_data
from layer6_erasure.agent import build_agent, erase
from layer6_erasure.evaluate import PRISTINE, WORK, present
from layer6_erasure.vault_adapter import get_vault
from layer7_audit.chain import AuditLog
from layer7_audit.proof import render_proof


def main(borrower_index=0):
    if not os.path.exists(PRISTINE):
        mock_data.seed(PRISTINE)
    mock_data.make_work_copy(PRISTINE, WORK)
    truth = json.load(open(os.path.join(WORK, "truth.json"), encoding="utf-8"))
    vault, kind = get_vault()
    mock_data.seed_vault(vault, truth)
    reg_path = os.path.join(WORK, "registry.json")
    registry = LineageRegistry.load(reg_path)
    audit = AuditLog(os.path.join(WORK, "audit_log.jsonl"))
    rq = json.load(open(os.path.join(WORK, "requests_public.json")))[borrower_index]
    b = rq["borrower_id"]

    print(f"MOCK stores (the real layout is not available).  vault in use: {kind}")
    pres = present(WORK)
    print("store sizes before:", {k: len(v) for k, v in pres.items()})
    rej = [json.loads(l) for l in open(os.path.join(WORK, "rejections.jsonl"))]
    print(f"consent guard refused {len(rej)} writes while seeding (e.g. purpose 'service' -> eval_sets); "
          f"sample: {rej[0]['reason']}\n")

    print(f"ERASURE REQUEST {rq['request_id']}  (borrower id is internal, never shown in the proof)")
    app = build_agent(WORK, vault, registry, audit, registry_path=reg_path)
    out = erase(app, rq)
    print("agent trace (ids and counts only; no raw values):")
    for t in out["trace"]:
        print("  -", t)
    print("\nstore sizes after: ", {k: len(v) for k, v in present(WORK).items()})
    print()
    ok, bad, why = audit.verify_chain()
    print(render_proof(out["record"], ok, why, head=audit.head()))

    tmp = os.path.join(WORK, "audit_tampered_copy.jsonl")
    shutil.copy(audit.path, tmp)
    lines = open(tmp).read().splitlines()
    r = json.loads(lines[0]); r["stores"][0]["found"] += 5
    lines[0] = json.dumps(r)
    open(tmp, "w").write("\n".join(lines) + "\n")
    t = AuditLog(tmp)
    print("\nTAMPER TEST: edited one number in a COPY of the audit log ->", t.verify_chain())
    os.remove(tmp)


if __name__ == "__main__":
    main()
