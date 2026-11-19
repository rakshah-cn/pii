"""Static facts for the demo console, copied from our own reports (with the file they came from). Nothing here is invented.
Every number carries a kind: measured (we ran it), recorded (last recorded run, not re-run today) or limit (a known gap).
The Layer 1 scoreboard is PARSED from layer1_detection/LAYER1_REPORT.md at request time, not retyped."""
import json
import os
import re

SUITES = {
    "L1": ["layer1_detection.test_layer1", "layer1_detection.test_chatter", "layer1_detection.test_memory_expand"],
    "L2": ["layer2_vault.tests.run_all"],
    "L3": ["layer3_gateway.test_gateway", "layer3_gateway.test_gateway_spec"],
    "L4": ["layer4_consent.test_consent", "layer4_consent.test_guard"],
    "L5": ["layer5_lineage.test_lineage"],
    "L6": ["layer6_erasure.test_erasure"],
    "L7": ["layer7_audit.test_audit"],
    "L8": ["layer8_demo.test_end_to_end"],
}


def _n(label, value, note="", kind="measured"):
    return {"label": label, "value": value, "note": note, "kind": kind}


LAYERS = [
    {"id": "L0", "name": "Data & scoreboard", "short": "Data",
     "job": "We had no labelled PII, and real call data must never leave the machine. So we generate fake conversations with known PII positions and score every layer against them.",
     "promise": "Every number in this console can be traced to a labelled, reproducible, holdout-clean test set.",
     "numbers": [_n("rows", "25,000", "dev 17,500 / test 3,750 / final 3,750 (final untouched)", "recorded"),
                 _n("language variants", "9", "English, Hindi, Marathi, Kannada, Tamil; native script + romanised", "recorded"),
                 _n("PII types", "10", "NAME PHONE AADHAAR ACCOUNT PAN LOAN UPI EMAIL DOB ADDRESS", "recorded"),
                 _n("quality gates", "24 / 24", "holdout clean, zero duplicates, zero name overlap (not re-run today)", "recorded")],
     "limits": ["All data is synthetic: scores are optimistic because the engine and the data share word lists.",
                "Marathi is our own choice of 5th language; the organisers have not confirmed it (Telugu is likely).",
                "PII-free share is 16.5% (target ~30%); native-language lines were never reviewed by a native speaker.",
                "Test and final cells are thin: 48 of 50 cells have under 300 spans."],
     "files": ["layer0_data/build_corpus.py", "layer0_data/audit_corpus.py", "data/corpus_manifest.json"]},
    {"id": "L1", "name": "PII detection", "short": "Detect",
     "job": "Finds PII in messy, code-mixed, spoken-style text: digits said as words, numbers split across turns, names in five languages.",
     "promise": "Three independent engines agree or add: rules, a spoken-number engine, and a multilingual transformer (Chitragupta, Apache-2.0).",
     "numbers": [_n("fully masked", "97.0%", "test split, 5,820 gold spans (>=95% of each span covered)"),
                 _n("PII-free false alarms", "2.9%", "20 of 694 clean calls (stoplist written after seeing failures: optimistic)"),
                 _n("names (weakest)", "92.8%", "fully masked; names are the hard type in every language"),
                 _n("latency (model)", "~10 ms", "p50 per message, int8 ONNX, synthetic, loaded CPU")],
     "limits": ["Synthetic score: expect the hidden corpus to score lower.",
                "Right-type label is 89.0% (PHONE only 70.5%): masking is right, the label can be wrong. Example seen live: a spoken phone number labelled PAN.",
                "Sakshi and GLiNER were compared and dropped (zero added recall, many false alarms)."],
     "files": ["layer1_detection/detector.py", "layer1_detection/spoken_engine.py", "layer1_detection/LAYER1_REPORT.md"]},
    {"id": "L2", "name": "Encrypted vault", "short": "Vault",
     "job": "Swaps every private value for a token (<PHONE_1>) and keeps the real value encrypted, under a key that belongs to that one borrower.",
     "promise": "The database never holds plaintext. Erasing a borrower = destroying their key (crypto-shredding): every copy of the ciphertext becomes unreadable.",
     "numbers": [_n("tests", "234", "pass on SQLite (242 on PostgreSQL); 9 skipped need a Postgres cluster", "recorded"),
                 _n("cipher", "AES-256-GCM", "per-borrower key, wrapped by a master-derived KEK (HKDF-SHA256)", "measured"),
                 _n("equality lookups", "HMAC-SHA256", "keyed blind index, scoped per conversation + type (never a plain hash)", "measured"),
                 _n("roadmap", "6 / 6 phases", "retention, nonce+rotation, pooling+idempotency, permissions, performance, runbook", "recorded")],
     "limits": ["Real AWS KMS is untested (only a fake client).", "No retention TTL is configured: it needs a policy decision.",
                "Phases 1-6 were not independently reviewed; the first build had 19 review findings (all fixed).",
                "Master key sits in .env here; production needs a KMS / HSM."],
     "files": ["layer2_vault/core/key_manager.py", "layer2_vault/services/vault_service.py", "layer2_vault/RUNBOOK.md"]},
    {"id": "L3", "name": "Gateway", "short": "Gateway",
     "job": "The single checkpoint between people and the LLM. A message is checked for consent, detected, tokenised, independently re-audited, and only then sent.",
     "promise": "Fail closed: any error, timeout or audit failure BLOCKS the message. Nothing raw reaches an LLM or a log.",
     "numbers": [_n("latency to the LLM", "p50 12-14 ms", "p95 17.5 ms (SQLite) / 20 ms (PG); budget 50 ms; 1,711 real messages"),
                 _n("end-to-end leak", "2.0%", "11 of 559 gold values; non-name values 0.7% (3/437)"),
                 _n("audit net, detector half-blind", "52.4% -> 16.1%", "leak with audit off vs on, when the detector misses 50% of spans"),
                 _n("spoken-number tripwire", "74-84%", "per language on the test split; 0 false alarms on 26,756 PII-free messages")],
     "limits": ["The audit cannot catch NAMES (no regex can) or PANs spelled letter by letter: if the detector misses them they pass.",
                "Over the 50 ms budget at 8+ concurrent clients on one process (CPU-bound; scale out is correct but needs cores).",
                "Layer 3 does not call Layer 5 itself; this console wires lineage in.",
                "Real Bedrock works (placeholders only) but was not load-tested."],
     "files": ["layer3_gateway/graph.py", "layer3_gateway/audit.py", "layer3_gateway/PROOF.md"]},
    {"id": "L4", "name": "Consent & purpose", "short": "Consent",
     "job": "Every stored record carries a tag: why it was collected, and what consent covers. A write whose purpose is not allowed for that store is refused.",
     "promise": "Purpose limitation, default deny: unknown store or purpose = rejected, and the rejection is written to the audit chain.",
     "numbers": [_n("tests", "5 + 11", "policy + guarded_write (no database call: microseconds)", "recorded"),
                 _n("stores covered", "3", "logs, vector_store, eval_sets (anything else is denied)", "measured"),
                 _n("explicit-consent purposes", "model_improvement", "needs consent_id AND explicit_consent=True", "measured")],
     "limits": ["There is no consent registry: consent_id is claimed, never checked against anything.",
                "The policy table is our reading of purpose limitation, not legal advice: it must be reviewed with legal.",
                "Older end_to_end.py still uses the previous WriteGuard (this console uses the new guarded_write)."],
     "files": ["layer4_consent/policy.py", "layer4_consent/guard.py"]},
    {"id": "L5", "name": "Lineage registry", "short": "Lineage",
     "job": "Remembers where every copy of a borrower's data went: log line, chunk, embedding, eval item, and what was derived from what.",
     "promise": "Erasure starts from a map instead of a guess. Names and values are stored only as keyed hashes.",
     "numbers": [_n("tests", "16", "incl. save/load round trip and borrower forgetting", "recorded"),
                 _n("copy kinds", "log, chunk, emb, eval", "with parent / child (derived_from, embedding_of)", "measured")],
     "limits": ["The registry only knows what was registered: that is exactly why Layer 6 has to search for the rest.",
                "Not called by the gateway code itself (wired here in the console)."],
     "files": ["layer5_lineage/registry.py"]},
    {"id": "L6", "name": "Erasure agent", "short": "Erasure",
     "job": "On an erasure request it hunts for EVERY copy, including ones nobody registered, deletes them, re-checks from scratch, shreds the key, and writes proof.",
     "promise": "The agent never sees raw values: it asks the vault yes/no questions (sealed matching). If anything survives, the status says so.",
     "numbers": [_n("tests", "19", "LangGraph: discover, plan, delete, verify, shred, audit", "recorded"),
                 _n("public mock requests", "187 / 187", "seeded copies removed (tuned on this set; held-out 20 not re-run)", "recorded"),
                 _n("final statuses", "3", "ERASED_VERIFIED, PARTIAL (a human decides), NOT_ERASED (survivors: escalates)", "measured")],
     "limits": ["Every store is a MOCK we generated: 'every copy erased' is unproven on real data.",
                "Embedding thresholds are tuned for a mock hashed bag-of-words, not a real embedding model.",
                "No write freeze while erasure runs; no trigger endpoint; backups and replicas are out of reach.",
                "Cannot see encrypted blobs, images, or stores outside the root it is given."],
     "files": ["layer6_erasure/agent.py", "layer6_erasure/tools.py", "layer6_erasure/LAYER6_CONTEXT.md"]},
    {"id": "L7", "name": "Tamper-evident audit", "short": "Audit",
     "job": "An append-only log where each record contains the hash of the one before it. Editing, deleting or reordering anything breaks every later hash.",
     "promise": "You can prove what the system did without the log being a copy of the data: borrower ids appear only as keyed hashes.",
     "numbers": [_n("tests", "5", "edit, delete, reorder detection + proof rendering", "recorded"),
                 _n("what is logged", "ids + counts", "never raw text, never the borrower id", "measured")],
     "limits": ["append() re-reads the whole file: 30 ms at 20k records, 160 ms at 100k. Fine for a demo, not for volume.",
                "A tamperer who rewrites the WHOLE file and every hash defeats a bare chain: the head hash must be anchored somewhere write-once. Not built.",
                "No lock and no fsync."],
     "files": ["layer7_audit/chain.py", "layer7_audit/proof.py"]},
    {"id": "L8", "name": "End to end", "short": "End to end",
     "job": "One borrower, one call, every layer: protect the conversation, store it under consent, then forget the borrower and prove it.",
     "promise": "The finish line is not a score. It is an independent file scan, not the agent, finding zero raw values and zero vault entries.",
     "numbers": [_n("whole-stack test", "passes", "ends CLEAN; a name part shares one token", "recorded")],
     "limits": ["Stores are mock; the real layout only changes the adapters.", "Synthetic, fake values throughout."],
     "files": ["layer8_demo/end_to_end.py", "layer8_demo/server.py"]},
]

PROBLEM = {
    "title": "A PII vault and a DPDP-compliant LLM data path",
    "pitch": "Lenders want LLMs on collections calls. Those calls are full of Aadhaar, PAN, phone numbers and names, said out loud in five languages. "
             "DPDP gives the borrower the right to be forgotten. So: protect before the LLM, keep a key per borrower, and be able to find and prove the deletion of every copy.",
    "flow": ["Borrower message", "Detect", "Tokenise + encrypt", "Audit", "LLM sees tokens only", "Store under consent", "Register lineage", "Erase on request", "Prove it"],
}


def _l1_report(root):
    p = os.path.join(root, "layer1_detection", "LAYER1_REPORT.md")
    try:
        txt = open(p, encoding="utf-8").read()
        sec = txt.split("## test", 1)[1].split("## dev", 1)[0]
    except Exception:                                                  # noqa: BLE001
        return None
    head = re.search(r"masked \(>=50% of span\): \*\*([\d.]+)%\*\*\s+fully masked \(>=95%\): \*\*([\d.]+)%\*\*\s+right type label too: \*\*([\d.]+)%\*\*", sec)
    fa = re.search(r"false alarm: \*{0,2}(\d+)/(\d+) \(([\d.]+)%\)", sec)
    rows = int(re.search(r"(\d+) rows, (\d+) gold PII spans", sec).group(1)) if re.search(r"(\d+) rows, (\d+) gold PII spans", sec) else None
    types = [{"name": a, "spans": int(b), "masked": float(c), "full": float(d), "label": float(e)}
             for a, b, c, d, e in re.findall(r"^\| ([A-Z]+) \| (\d+) \| ([\d.]+)% \| ([\d.]+)% \| ([\d.]+)% \|", sec, re.M)]
    langs = [{"name": a, "spans": int(b), "full": float(c)} for a, b, c in re.findall(r"^\| ([a-z_]+) \| (\d+) \| ([\d.]+)% \|", sec, re.M)]
    if not (head and types and langs):
        return None
    return {"split": "test", "rows": rows, "masked50": float(head.group(1)), "full": float(head.group(2)), "label": float(head.group(3)),
            "false_alarm": {"n": int(fa.group(1)), "of": int(fa.group(2)), "pct": float(fa.group(3))} if fa else None, "types": types, "langs": langs,
            "caveat": "synthetic data; the engine and the data share word lists, so this is optimistic"}


def build(root):
    manifest = {}
    try:
        manifest = json.load(open(os.path.join(root, "data", "corpus_manifest.json"), encoding="utf-8"))
    except Exception:                                                  # noqa: BLE001
        pass
    return {"problem": PROBLEM, "layers": LAYERS, "suites": {k: len(v) for k, v in SUITES.items()}, "l1_report": _l1_report(root),
            "corpus": {"targets": manifest.get("targets"), "seed": manifest.get("seed")}}
