"""END-TO-END demo (all values fake; stores are MOCK).   .venv/bin/python -m layer8_demo.end_to_end

One borrower's conversation goes through every layer:
  detect + tokenise + vault (layers 1-3)  ->  consent-guarded store writes (4)  ->  lineage registry (5)
  ->  erasure agent finds EVERY copy incl. ones nobody registered (6)  ->  hash-chained audit proof (7)
and then an independent check (plain file scan, not the agent) proves nothing raw is left anywhere."""
import json
import os
import shutil

import common.llm  # noqa: F401  (loads .env: VAULT_MASTER_KEY etc.; secrets are never printed)
from layer3_gateway.graph import Gateway
from layer4_consent.write_guard import WriteGuard, new_tag
from layer5_lineage.registry import LineageRegistry, ref
from layer6_erasure.adapters import JsonlStore, StoreCatalog
from layer6_erasure.agent import build_agent, erase
from layer6_erasure.embed import embed
from layer6_erasure.vault_adapter import get_vault
from layer7_audit.chain import AuditLog
from layer7_audit.proof import render_proof

ROOT = "data/stores/e2e"
B, CID, NAME, PHONE, PAN = "B-E2E-001", "conv-e2e-1", "Ramesh Kumar", "9845012345", "ABCPK1234L"
CALL = [("Agent", "Hello sir, am I speaking with Ramesh Kumar?"),
        ("Customer", "Yes, speaking."),
        ("Agent", "Please confirm your registered mobile number."),
        ("Customer", "nine eight four five zero"),                       # the number is said in two pieces
        ("Agent", "Yes, go on."),
        ("Customer", "one two three four five"),
        ("Agent", "Your PAN card number please."),
        ("Customer", "ABCPK1234L. This is Kumar here."),                 # a later mention of a name part
        ("Agent", "Your EMI of ₹4,200 is due on the 5th. Thank you."),
        ("Customer", "Okay, I will pay by the 7th.")]


def count_raw(root, needles):
    """Independent check: plain text scan of every file under root. Prints counts only."""
    hits = {n: 0 for n in needles}
    for dp, _, files in os.walk(root):
        for f in files:
            if f.endswith((".jsonl", ".json")) and f not in ("audit_log.jsonl",):
                txt = open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read()
                for n in needles:
                    hits[n] += txt.count(n)
    return hits


def main():
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(os.path.join(ROOT, "debug_dump"))
    vault, kind = get_vault()
    gw = Gateway(vault=vault)
    guard = WriteGuard(os.path.join(ROOT, "rejections.jsonl"))
    logs = JsonlStore(os.path.join(ROOT, "logs.jsonl"), "logs", "logs")
    vecs = JsonlStore(os.path.join(ROOT, "vectors.jsonl"), "vector_store", "vector_store")
    evals = JsonlStore(os.path.join(ROOT, "eval_sets.jsonl"), "eval_sets", "eval_sets")
    reg = LineageRegistry()
    reg.add_borrower(B, name=NAME)
    reg.add_conversation(B, CID)

    print(f"vault: {kind} (encrypted, per-borrower key)   stores: MOCK files under {ROOT}\n")
    print("STEP 1  every turn goes through the gateway BEFORE any log write or LLM call")
    masked_turns = []
    for i, (spk, text) in enumerate(CALL):
        out = gw.handle(CID, spk, text, audience="agent_dashboard", borrower_id=B, purpose="service")
        shown = out["masked_text"] if out["status"] == "ok" else f"(blocked: {out.get('reason')})"
        print(f"  [{spk:<8}] {text}\n      -> {shown}")
        if out["status"] != "ok":
            continue                                                       # fail closed: nothing is stored or sent
        masked_turns.append(out["masked_text"])
        lid = f"log-e2e-{i:02d}"
        rec = {"id": lid, "conversation_id": CID, "text": out["masked_text"], "consent": new_tag("service", note="loan servicing")}
        ok, why = guard.guarded_write("logs", logs, rec)
        reg.register(B, ref("log", lid), conversation_id=CID)
    chunk_text = " | ".join(masked_turns)
    print(f"\nvault holds {len(vault.entries_for_borrower(B))} encrypted entries for this borrower; tokens only: "
          f"{[e['token'] for e in vault.entries_for_borrower(B)]}")

    print("\nSTEP 2  consent / purpose tags decide which stores may receive derived data")
    ok, why = guard.guarded_write("eval_sets", evals, {"id": "eval-e2e-0", "conversation_id": CID, "prompt": chunk_text,
                                                        "expected": "intent=payment_promise", "consent": new_tag("service")})
    print(f"  eval item with purpose 'service'        -> written={ok}  ({why})")
    ok, why = guard.guarded_write("eval_sets", evals, {"id": "eval-e2e-1", "conversation_id": CID, "prompt": chunk_text,
                                                        "expected": "intent=payment_promise",
                                                        "consent": new_tag("model_improvement", consent_id="CN-E2E-1", explicit=True)})
    print(f"  eval item with explicit consent on file -> written={ok}  ({why})")
    if ok:
        reg.register(B, ref("eval", "eval-e2e-1"), conversation_id=CID)
    vec = {"id": "vec-e2e-0", "text": chunk_text, "conversation_id": CID, "embedding": [round(float(x), 4) for x in embed(chunk_text)],
           "consent": new_tag("service")}
    guard.guarded_write("vector_store", vecs, vec)
    reg.register(B, ref("chunk", "vec-e2e-0"), conversation_id=CID)
    reg.register(B, ref("emb", "vec-e2e-0"), parent=ref("chunk", "vec-e2e-0"), relation="embedding_of", conversation_id=CID)

    print("\nSTEP 3  copies the platform never mapped (the hard part of erasure): planted on purpose")
    summary = f"Summary: {NAME} promised to pay the EMI by the 7th. Reachable on {PHONE}."
    vecs.put({"id": "vec-e2e-derived", "text": summary, "conversation_id": None, "embedding": [round(float(x), 4) for x in embed(summary)],
              "consent": new_tag("service")})                                       # derived summary chunk, NOT in the registry
    logs.put({"id": "log-e2e-debug", "conversation_id": None, "consent": new_tag("service"),
              "text": f"DEBUG retry payload name={NAME} mobile={PHONE}"})            # an application debug log holding RAW values
    with open(os.path.join(ROOT, "debug_dump", "app_debug.jsonl"), "w", encoding="utf-8") as f:   # a file-based store nobody registered
        f.write(json.dumps({"id": "dbg-0", "text": f"crash dump: customer {NAME} pan {PAN}"}) + "\n")
    reg.save(os.path.join(ROOT, "registry.json"))
    cat = StoreCatalog(ROOT)
    before = {s.name: s.count() for s in cat.all()}
    print("  records per store before:", before)
    raw_before = count_raw(ROOT, [NAME, PHONE, PAN])
    print("  independent raw-value scan BEFORE erasure (name, phone, PAN occurrences):", list(raw_before.values()))

    print("\nSTEP 4  erasure request: the agent discovers, deletes, verifies, audits (it sees ids and counts, never raw text)")
    audit = AuditLog(os.path.join(ROOT, "audit_log.jsonl"))
    app = build_agent(ROOT, vault, reg, audit, registry_path=os.path.join(ROOT, "registry.json"))
    out = erase(app, {"request_id": "ER-E2E-001", "borrower_id": B})
    for t in out["trace"]:
        print("  -", t)
    print()
    ok, bad, why = audit.verify_chain()
    print(render_proof(out["record"], ok, why, head=audit.head()))

    print("\nSTEP 5  independent verification (a plain file scan, not the agent)")
    after = {s.name: s.count() for s in StoreCatalog(ROOT).all()}
    raw_after = count_raw(ROOT, [NAME, PHONE, PAN])
    print("  records per store after:", after)
    print("  raw-value occurrences left (name, phone, PAN):", list(raw_after.values()))
    print("  vault entries left for the borrower:", len(vault.entries_for_borrower(B)), "| key present:", vault.has_key(B))
    toks = [t for t in gw.vault.tokens(CID)]
    try:
        v = gw.vault.get(CID, toks[0]) if toks else None
    except Exception as e:                                                      # shredded: decrypt must fail
        v = f"decrypt failed ({type(e).__name__})"
    print("  vault.get on a former token ->", v)
    gw.forget(CID)
    print("  gateway in-memory state for the conversation dropped (history, name parts, joined-number state)")
    clean = sum(raw_after.values()) == 0 and not vault.entries_for_borrower(B)
    print("\nRESULT:", "CLEAN - no raw value, vault entry or key remains" if clean else "NOT CLEAN - see counts above")
    return clean


if __name__ == "__main__":
    main()
