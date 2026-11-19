# Layer 6: Erasure agent (as built and re-run on 19 Nov 2026)

Status: built, 19/19 own tests pass, 10/10 public mock requests complete. Updated 19 Nov after the O1 (plaintext) and O3 (shred crash) fixes. Every number below comes from MOCK stores we generated
ourselves. None of it is a score on the organisers' real stores. Read section 9 before quoting anything in the pitch.

Run:
- tests:      `.venv/bin/python -m layer6_erasure.test_erasure`  (19)
- evaluation: `.venv/bin/python -m layer6_erasure.evaluate public`   (10 requests, the set we tuned on)
- held-out:   `.venv/bin/python -m layer6_erasure.evaluate heldout`  (20 requests; meant to be run once and reported. NOT re-run for this document)
- ablation:   `.venv/bin/python -m layer6_erasure.evaluate ablate`   (switches each discovery method off in turn)
- whole stack: `.venv/bin/python -m layer8_demo.test_end_to_end` (passes now; LAYER4_CONTEXT.md section 11 says it failed, that is out of date)

## 1. What Layer 6 is for

A borrower asks to be forgotten (DPDP right to erasure). Layer 6 must remove EVERY copy of their data from every place it
ended up, prove it, and keep proof that cannot be quietly edited. The hard part is not deleting the records we know about. It
is finding the copies nobody registered: a debug log with a raw phone number, a summary chunk derived from a deleted chunk,
an embedding row with no text, a CRM export in a folder nobody listed.

Why it is "the agentic layer": the agent does not run a fixed list of deletes. It looks, finds something new, widens where it
looks, deletes, then looks again from scratch and only stops when a full re-check finds nothing.

## 2. Decisions that are made (in code today)

| # | Decision | Where |
|---|---|---|
| D1 | Framework is LangGraph, six nodes: discover, plan, delete, verify, shred, audit | agent.py |
| D2 | Honest scope: discovery is adaptive, PLANNING IS DETERMINISTIC, NO LLM is used anywhere in Layer 6 | agent.py header |
| D3 | The agent is BLIND to raw personal data. It hands store text to the vault; the vault matches inside itself and returns only a category, a score or a bool. There is NO call that returns a borrower's values (`decrypt_for_erasure` was deleted). Evidence carries no snippets | tools.py, layer2_vault/core/sealed_match.py |
| D4 | Verify re-runs ALL discovery from scratch (not "did my delete return n") and loops back to plan until zero survivors, max 3 passes by default (the evaluator uses 4) | agent.py `verify`, `route` |
| D5 | Order matters: the borrower's vault key is destroyed only AFTER verification, because the raw-value sweep needs the vault | agent.py `shred` |
| D6 | Fail safe: survivors after the last pass, a vault shred that does not finish (`erasure_incomplete`), or ANY unexpected error in discover / plan / delete / verify => status NOT_ERASED, `escalate=True`, no crash. The key is kept and the lineage registry is kept so a retry still has it | agent.py `guarded`, `shred`, `audit_node` |
| D7 | Three evidence strengths. certain = delete. probabilistic = delete and flag in the audit record. ambiguous = NEVER auto-delete, hand to a human | tools.py |
| D8 | Name-only matches are weak evidence. A one-word name ("Sanjay") or a name shared by two borrowers is deleted only if something else ties the record to this borrower (same conversation id). Otherwise ambiguous, status PARTIAL | tools.py `raw_sweep` |
| D9 | Three final statuses: ERASED_VERIFIED, PARTIAL (ambiguous items left for a human), NOT_ERASED (survivors or key not destroyed) | agent.py `audit_node` |
| D10 | Delete order eval_sets, then vector_store, then logs, then anything else | agent.py `plan` |
| D11 | Stores are behind a `StoreAdapter` interface (records / delete / count / nearest). Real stores are plugged in by writing adapters. The agent talks only to that interface | adapters.py |
| D12 | The audit record carries only a keyed hash of the borrower id (HMAC), never the id, never raw values. It goes into the Layer 7 hash chain | agent.py, layer7_audit/chain.py |
| D13 | After the vault shred the lineage registry forgets the borrower and leaves no index residue, then saves | registry.forget_borrower |
| D14 | Vault is the real modular Layer 2 vault (`VaultClient`) when it loads; otherwise an UNENCRYPTED in-memory fake with the same sealed API (tests label which one ran) | vault_adapter.py |
| D16 | The vault shred uses a STABLE job id per request (`L6-<request_id>-<borrower hash>`). A retry resumes the same vault job instead of starting a new one | agent.py `_job_id` |
| D15 | The erasure evaluator is the ONLY code allowed to read `truth.json` (the seeded ground truth). The agent never reads it | evaluate.py |

## 3. How one erasure runs

```
discover -> plan -> delete -> verify --(zero deletable left)--> shred -> audit -> END
               ^                 \--(survivors, passes < max)--> plan
                                  \--(survivors, passes = max)--> audit  (NOT_ERASED, escalate)
```

1. **discover** finds candidates with seven methods (section 4), widening in a loop: a record that reveals a new
   conversation id makes the agent sweep again (up to 3 rounds). Unregistered stores (any extra `*.jsonl` under the root) are
   probed too.
2. **plan** groups deletable hits by store, in the order D10.
3. **delete** calls the adapter's `delete(ids)` per store and counts what was removed.
4. **verify** is a full fresh discovery. It also feeds in ids already found, so a derived copy whose parent was just deleted
   is still caught through its parent link.
5. **shred** (only when verify found zero) destroys the borrower's vault entries and key with the stable job id, then checks the vault
   itself says the key is gone and no entries remain. If the vault raises, the error code is recorded and the status becomes NOT_ERASED.
6. **audit** writes one hash-chained record: status, passes, duration, per-store found / deleted / verified_zero, methods run,
   how many deletes were probabilistic, up to 50 unresolved items with reasons, unregistered stores that had hits.

## 4. The seven discovery methods

| Method | What it does | Raw values needed |
|---|---|---|
| registry | Ask Layer 5 what it knows for this borrower (ids only) | no |
| conversation sweep | Every record whose conversation id, or text, mentions one of the borrower's conversations | no |
| raw value sweep (sealed) | Search all store text for the borrower's real phone / PAN / Aadhaar / account / address / name, with phone-format variants (+91, spaces, dashes) | inside the vault |
| independent collapsed scan (sealed) | Second, different matcher: strip everything but letters and digits, then substring search on long identifiers (8 or more). Exists so a formatting gap in the first matcher does not hide a copy from both | inside the vault |
| derivative sweep | Follow parent / source links (`parent_chunk_id`, `source_record_id`, ...) to a fixpoint, so derived-of-derived copies are found | no |
| embedding probe (sealed) | Nearest-neighbour search from the borrower's own identifiers. Finds embedding rows that have no text at all. Above 0.50 similarity = probabilistic (deleted, flagged); 0.30 to 0.50 = ambiguous | inside the vault |
| independent file grep (sealed) | Byte-level search of every file under the root that no adapter manages (csv, txt, ...). Always ambiguous: the agent will not edit a file it cannot parse. A human must handle it | inside the vault |

## 5. What each method earns (ablation, re-run 19 Nov, public set, mock stores)

"Silent" = a seeded copy left behind that the audit record does NOT flag. This is the dangerous kind.

| Method switched off | Silent survivors | What was missed |
|---|---|---|
| none (full agent) | 0 | |
| registry | 0 | nothing (the other sweeps cover it on this mock) |
| conversation sweep | 43 | unregistered tokenised logs / chunks / eval items, analytics cache rows |
| raw value sweep | 2 | name-only eval prompts |
| derivative sweep | 12 | derived summary chunks, derived eval items |
| embedding probe | 3 | orphan embeddings (vector with no text) |
| independent collapsed scan | 0 | nothing on this mock (it is a safety net, not proven useful here) |
| file grep | 0 | nothing on this mock (the unmanaged-file case is covered by its own unit test) |
| raw sweep + collapsed scan | 15 | raw leaks in logs and vectors, CRM rows, name-only prompts |

KEY LESSON, say it plainly in the pitch: with a method switched off, the agent still reported ERASED_VERIFIED. The verify step
is only as good as the discovery methods behind it. "Verified" means "I looked with these methods and found nothing", not
"nothing exists".

## 6. Current results (mock stores, public set of 10)

- 187 seeded copies, 187 removed, 100% completeness, 0 survivors, 0 collateral deletions (no other borrower's data and no decoy touched).
- Re-run after the vault-side matching change: identical results (187/187, 9 ERASED_VERIFIED + 1 PARTIAL, ablation table unchanged). Time rose from 0.11-0.16 s to 0.16-0.20 s per request (extra vault calls).
- Statuses: 9 ERASED_VERIFIED, 1 PARTIAL (borrower B0031, whose name is the single word "Sanjay": by design a human decides).
- 1 pass each, 0.11 to 0.16 s per request (the brief's erasure budget is 15 minutes per RUNBOOK.md; real-store time is unknown).
- Audit chain verifies intact after the run.
- Own tests (19) cover: full erasure with no collateral, key destroyed and registry forgets, no raw value in agent output or audit,
  fail-safe when deletes do nothing, verify loop when the first delete is partial, single-word name kept and PARTIAL, shared name
  kept without corroboration, phone format variants, unmanaged file flagged and left untouched, unregistered store cleaned,
  audit tamper detected, works with the fallback vault, consent rejections carry no record bodies, and (new) the agent is blind (a spy
  on every vault return value finds no raw value; `decrypt_for_erasure` does not exist), `find_unmapped_copies` is a bool, a vault
  `erasure_incomplete` does not crash (NOT_ERASED, key and registry kept), a retry reuses the same vault job and finishes, an
  unexpected store error ends NOT_ERASED, and exactly one hash-chained `erasure` record is written per request.

## 7. How Layer 6 connects to the others

- **Layer 2 vault**: `destroy_borrower(borrower, job_id)`, `has_key`, `entries_for_borrower`, `access_log` and the SEALED sweep: `erasure_scan` (category per text), `erasure_probe` (scores; the query vector is built and kept inside the vault), `find_unmapped_copies` (a bool). Every sealed call writes an audit 'attempt' event before decrypting.
  `destroy_borrower` runs the vault's own erasure job (it ends COMPLETED or raises `erasure_incomplete`; the vault never
  reports complete before the key action succeeded).
- **Layer 5 lineage**: reads `known_for`, `conversations_of`, `borrowers_sharing_name`; calls `forget_borrower` after shred.
- **Layer 4 consent**: Layer 6 does not enforce consent. The mock stores were built through the old `WriteGuard`, so eval_sets only
  hold items from borrowers who consented.
- **Layer 7 audit**: one `type: erasure` record per request.
- **Layer 3 gateway**: no trigger exists. Nothing calls `erase()` except tests and the demo. There is no endpoint, queue or job id (see O5).
- **Layer 8 demo**: the end-to-end test runs a real gateway turn, writes through the guard, registers lineage, erases, and an
  independent file scan confirms no raw value is left.

## 8. Open decisions (NOT decided; need Raksha's call)

| # | Open item | Why it matters / recommendation |
|---|---|---|
| O1 | RESOLVED 19 Nov: `decrypt_for_erasure` deleted from the modular vault (InternalOps and ModularVault). Replaced by `erasure_scan`, `erasure_probe`, `find_unmapped_copies`. Deviation from the pasted spec: the scan returns a CATEGORY per text (strong / address / name_full / name_single), not only Hit/Miss, because the name rules (D8) need to tell a full name from a one-word name. A category carries no value. | Still open: the LEGACY single-file `layer2_vault/vault.py` (and its own 24 tests) still has `decrypt_for_erasure`. Nothing in Layers 3 to 8 calls it. Delete it only with its tests rewritten; ask before doing so. |
| O2 | Is there any LLM in Layer 6? No, by design (D2); the team's pasted brief repeats this as a rule. | Decided: no LLM. Say so on the slide. |
| O3 | RESOLVED 19 Nov: `shred` catches vault failures, uses a stable job id, keeps key and registry on failure; node errors route to audit. | |
| O4 | The real store layout is not known. Everything uses JSONL mock stores. | Only the adapters change when real layouts arrive. Ask the organisers, or state the assumption on the slide. |
| O5 | No freeze during erasure. Between discovery and shred, a new message from the same borrower could be written. There is no "erasure pending" block in Layer 3 / 4 (the vault blocks its own writes once its job starts, but the agent's multi-pass loop begins earlier). | Needed for a real deployment; for the demo, mention it. Also no trigger endpoint or job queue. |
| O6 | Purpose-scoped erasure (withdraw consent for one purpose only) is not built. Layer 4 notes the same. | Out of scope unless the brief asks. |

## 9. Honest limits (do not overclaim)

0. **Vault-side matching is still our code.** Moving the matcher into the vault removes the plaintext route from Layer 6, but the vault still decrypts every borrower value for the sweep, in process. Real KMS / separate-process isolation is untested.
1. **Circular test.** The mock stores were generated by us with copy types the agent was designed to find. 100% on the mock
   shows the agent handles those 15 seeded copy types, not that it will find what a real system hides.
2. **Tuned on the public set.** The embedding thresholds (0.50 / 0.30) were tuned on the 10 public requests. The held-out 20
   were not re-run for this document.
3. **The embedding is a mock** (hashed bag-of-words, `embed.py`). A real learned embedding model behaves differently; there the probe
   is a candidate generator only (the code already treats it that way: probabilistic hits are flagged).
4. **Names are the weak spot.** Name-only copies are deleted only with corroboration. A real borrower with a common name will
   often finish PARTIAL. That is intended, and it needs a human.
5. **Verify is blind to anything the methods cannot see** (section 5): an encrypted blob, an image, a screenshot, a format no
   adapter reads (only flagged if raw bytes match), a store outside the root directory.
6. **Backups and replicas are out of reach.** A database backup taken before the erasure still holds the old wrapped key; with
   the old master secret it still decrypts (vault RUNBOOK sections 4 and 7). A restore undoes erasures until they are replayed
   from the Layer 7 audit chain. Layer 6 does not touch backups.
7. **LineageRegistry in these runs uses the DEV HMAC key** (a warning prints). Production must set `LINEAGE_HMAC_KEY`.
8. **Audit chain limit:** whoever can rewrite the whole file and recompute the hashes defeats a bare chain. The latest hash
   (`head()`) must be anchored somewhere the writer cannot edit. Also `AuditLog.append` is O(n) (160 ms per append at 100k records).
9. **Single process, file-backed.** Adapters rewrite whole JSONL files on delete. Fine for a demo, not for large stores or concurrent erasures.
10. **Probabilistic deletes can be wrong.** They are flagged in the audit record (`probabilistic` count), but a flagged record may
    have belonged to someone else.

## 10. Suggested next steps (my proposal, not agreed)

1. Run the held-out 20 ONCE, report it separately from the public 10, and say which set the thresholds came from.
2. Decide the legacy `vault.py` (O1 leftover): delete its `decrypt_for_erasure` and rewrite 8 of its tests, or mark the file retired.
3. Build the demo story: one borrower, registry-only view versus what the agent found, then the audit proof. The ablation table (section 5) is the strongest slide.
4. Write the unknowns on the slide: real store layout (O4), no write freeze (O5), backups (limit 6).
5. Not done from the pasted spec, on purpose: the skeleton `agent.py` was NOT pasted over the working agent (it would drop snowball discovery, evidence strengths, ambiguous handling and the per-store verify record). Its two fixes (stable job id, shred fails safe) were applied to the existing agent. Discovery methods run one after another (about 0.2 s in total), not in parallel.

## 11. Files

| File | Role |
|---|---|
| agent.py | the LangGraph agent (6 nodes), `build_agent`, `erase` |
| tools.py | discovery tools; `SealedScanner` sends text to the vault and receives categories / scores / bools only |
| ../layer2_vault/core/sealed_match.py | the matcher that runs INSIDE the vault (moved from tools.py) |
| adapters.py | `StoreAdapter` interface, JSONL mock stores, `StoreCatalog` (registered plus unregistered discovery) |
| vault_adapter.py | picks the modular vault, else an unencrypted fake |
| embed.py | mock hashed bag-of-words embedding |
| mock_data.py | generates 50 fake borrowers, their copies, decoys, requests and `truth.json` |
| evaluate.py | scoring (completeness, survivors by kind, silent survivors, collateral) and the ablation |
| test_erasure.py | 19 tests |
