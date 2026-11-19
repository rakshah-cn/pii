# TE-03 PII vault: full project context, Layer 0 to Layer 8 (for replication)

Team "Looping In", CreditNirvana Agentathon, problem TE-03. Build window Thu 19 Nov 2026 9:35 to Fri 20 Nov 5:30pm.
Written for: an engineer who has to rebuild this project from scratch, or take it over.
Status checked 19 Nov 2026: every test suite listed in section 3 was re-run and passes (counts in section 3).
Every accuracy number below comes from OUR OWN SYNTHETIC data or MOCK stores. None is a score on the organisers' real data. Treat them as optimistic.

---------------------------------------------------------------------------------------------------

## 0. What the project is, in plain words

A lender's call-centre transcripts, chat messages and LLM prompts contain personal data (names, phone, Aadhaar, PAN, account numbers, addresses...). Under India's DPDP Act that data must not reach an LLM or a log in raw form, must be used only for the purpose it was collected for, and must be erasable on request, with proof.

So we built a pipeline. Every message passes through it BEFORE any LLM call or log write:

```
text -> L1 find PII -> L2 swap each value for a placeholder token (<PHONE_1>) and store the real value encrypted
     -> L3 gateway: independent re-check, send ONLY placeholders to the LLM, put real values back only for the borrower
     -> L4 consent tag decides which stores may receive the record
     -> L5 lineage registry remembers where every copy went
     -> L6 erasure agent finds and deletes every copy (even unregistered ones), destroys the borrower's key
     -> L7 hash-chained audit log proves what happened
     -> L8 end-to-end demo
L0 is not part of the runtime: it generates the fake data and the scoreboard used to measure L1 to L3.
```

Everything fails closed: on any error the message is blocked, never passed through.

---------------------------------------------------------------------------------------------------

## 1. Environment, dependencies, secrets (do this first)

- Python 3.12.3, a venv at `.venv`. Run everything from the project root as `.venv/bin/python -m <package.module>` (no notebooks).
- Pinned versions in use: langgraph 1.2.23, langchain-core 1.8.3, torch 2.15.0 (CPU), transformers 5.23.0, tokenizers 0.23.3, safetensors 0.8.0, onnx 1.23.2, onnxruntime 1.32.0, numpy 2.5.5, cryptography 50.0.2, fastapi 0.165.0, starlette 1.10.0, pydantic 2.14.3, httpx 0.28.1, boto3 1.44.6, psycopg 3.3.9 (+binary), psycopg-pool 3.3.3. Optional: PostgreSQL 16 for the vault's Postgres path.
- `.env` (never commit, never print) needs three names: `AWS_BEARER_TOKEN_BEDROCK`, `AWS_REGION` (default ap-southeast-2), `VAULT_MASTER_KEY`. Generate the vault master key yourself (32+ random bytes, encoded). Optional env: `AUDIT_HMAC_KEY`, `LINEAGE_HMAC_KEY` (production must set both; dev falls back to a random file key / a built-in DEV key with a warning), `GATEWAY_MODEL_THREADS`, `VAULT_RETENTION_POLICY`, `VAULT_TEST_DB_URL`, `VAULT_TEST_SUPER_URL`.
- `.gitignore`: `.env`, `.venv/`, `__pycache__/`, `transcript.csv`, the translations PDF, `data/`. So the corpus, the stores and the model files are NOT in git; a replicator regenerates the data (section L0) and re-downloads the models.
- Models (in `models/`, not in git):
  - `models/chitragupta`: Apache-2.0 token-classification PII model, base FacebookAI/xlm-roberta-large, Indic-language, covers Aadhaar/PAN/UPI/GSTIN. Used as-is, not retrained.
  - `models/chitragupta_onnx/model_int8.onnx`: produce it with `python -m layer1_detection.compare.latency_bench export` (exports fp32 ONNX, then `quantize_dynamic` to int8).
  - `models/sakshi` and `models/gliner_multi_pii` were downloaded for comparison only and DROPPED (0 added recall, many false alarms). A replicator does not need them.
- LLM for data validation and the gateway demo: Amazon Nova Pro on Bedrock, `apac.amazon.nova-pro-v1:0` (`common/llm.py`: tiny Bedrock Converse client, loads `.env`, never prints secrets). Claude models on that account were blocked until the Anthropic use-case form was submitted. Only placeholder skeletons (`{SLOT}` text) or tokenised text go to it.

Non-negotiable rules the team set (they shaped the code):
1. Be a validator: separate measured / assumed / untested. Synthetic scores are circular and optimistic.
2. Never send raw PII or real call data (`transcript.csv`, recordings, the translations PDF) to any external service or LLM.
3. Never print `.env`. Fail closed everywhere; rehydrated text never reaches a log.
4. Keep `data/corpus_final.jsonl` untouched until the very end (tune on dev only).
5. Never default to "approve" on an error (a fail-open LLM judge once hid a bug).
6. Headline recall = FULL coverage (>=95% of a span masked), not the lenient ">=50% masked" figure; report both.

---------------------------------------------------------------------------------------------------

## 2. Repository map

```
common/llm.py                       Bedrock client + .env loader
layer0_data/    data generation + scoreboard      layer1_detection/   detector (+ compare/ experiments)
layer2_vault/   encrypted token vault (modular)   layer3_gateway/     LangGraph gateway
layer4_consent/ purpose + consent policy          layer5_lineage/     where-did-the-data-go registry
layer6_erasure/ LangGraph erasure agent + mocks   layer7_audit/       hash-chained audit log
layer8_demo/    end-to-end + erasure demos        data/ models/       (gitignored artefacts)
```
Build order that worked: L0 data and scoreboard -> L1 detector (measured on the scoreboard) -> L2 vault -> L3 gateway -> L4/L5/L7 (small) -> L6 agent -> L8 demo. Then L2 and L3 were rebuilt to written specs, and L6 was hardened.

---------------------------------------------------------------------------------------------------

## 3. How to verify a rebuild (run these; expected counts as of 19 Nov 2026)

| Suite | Command | Result now |
|---|---|---|
| L1 | `python -m layer1_detection.test_layer1` / `.test_chatter` / `.test_memory_expand` | 18 / 7 / 18 pass |
| L2 modular vault | `python -m layer2_vault.tests.run_all` | 234 pass, 9 skipped (need Postgres / scratch cluster); 242 on Postgres |
| L3 | `python -m layer3_gateway.test_gateway` / `.test_gateway_spec` | 31 / 49 pass |
| L4 | `python -m layer4_consent.test_consent` / `.test_guard` | 5 / 11 pass |
| L5 | `python -m layer5_lineage.test_lineage` | 16 pass |
| L6 | `python -m layer6_erasure.test_erasure` | 19 pass |
| L7 | `python -m layer7_audit.test_audit` | 5 pass |
| L8 | `python -m layer8_demo.test_end_to_end` | passes (ends CLEAN) |
| L0 corpus | `python -m layer0_data.audit_corpus data/corpus` | 24 of 24 gates pass (last recorded; not re-run today) |
| L6 evaluation | `python -m layer6_erasure.evaluate public` | 187/187 seeded copies removed (last recorded; not re-run today) |

---------------------------------------------------------------------------------------------------

## LAYER 0: synthetic data and the scoreboard (`layer0_data/`)

**Why:** the team had no labelled PII data, and real transcripts must never leave the machine. So we generate fake conversations with known PII positions ("gold spans") and score the detector against them.

**Languages (9 variants):** en, hi, hi_rom, mr, mr_rom, kn, kn_rom, ta, ta_rom (native script + romanised for four languages, plus English). **Marathi is our own choice of 5th language** (easiest: Devanagari). The organisers have NOT confirmed it; Telugu is a likely real 5th. Adding a language = one table in `spoken.py` + name/address/skeleton packs.

**PII types (10):** NAME, PHONE, AADHAAR, ACCOUNT, PAN, LOAN, UPI, EMAIL, DOB, ADDRESS. Must-NOT-mask things: AMOUNT, DATE, ORG (measured as over-masking).

**Row recipe (a "cell" = language x PII type x form x scenario x channel):**
- `specs.py`: the coverage grid. Scenarios: emi_reminder, dispute, address_update, payment_promise. Forms: written, spoken, split_turns (a number said across two turns), spoken_fillers; PAN/LOAN spoken = letters spelled; names/addresses/etc. written only. Channels: call (speaker-labelled turns), message (chat/SMS style), prompt (an LLM prompt embedding borrower data). Hard cells are sampled more (weights: non-written +2, Kannada/Tamil +1, split +1). About 30% of calls are meant to be PII-free (target; achieved only 16.5%, see limits).
- `skeleton_lib.py` (+ `skeleton_ext.json`, `skeletons.py`): a template grammar. `(a|b|c)` alternation groups and `{SLOT}` placeholders expand into many sentences. Templates were written by Claude in-session. Roughly 590 base lines in 321 pools plus ~1,595 agent-written extension lines merged by `merge_ext.py` (validated by code, mostly NOT reviewed by a human or native speaker).
- `pii_gen.py`: makes fake values with code (never an LLM): valid-looking Aadhaar/PAN/phone/etc., name and address packs per language.
- `spoken.py`: digits <-> spoken words per language (`render_digits`, `parse_spoken`, `parse_spelled`). Used both to make spoken data and (in L1) to detect it. Word lists are written from model knowledge and are NOT native-checked.
- `convo.py`: builds one row from a cell + seed: picks a skeleton, fills slots, computes character offsets, splits numbers across turns.
- `checks.py`: deterministic row checks (unfilled slot, offset mismatch, digits/spoken/spelled mismatch, script check for the language, duplicates). Most quality control is plain code, not an LLM.
- `graph.py`: LangGraph loop `generate -> evaluate -> (approved: END | needs_improvement: optimize -> evaluate)`, max 3 rounds. `llm_steps.py` is the optional Bedrock Nova Pro judge/rewriter that sees skeletons only; it must fail loudly, never approve on error.
- `make_dataset.py`, `hard.py`: older dataset builders (synth_dev, hard_dev/hard_test, llm_hard, v2_*, corpus20k/25k kept for history).
- `build_corpus.py` (the current one): over-generates rows, keeps only `holdout_clean`, unique (blanked-text signature) and unseen-name rows, stratifies by (language family, channel, form, PII-free), tops up thin (family x type) cells, and writes `data/corpus_{dev,test,final}.jsonl` + `data/corpus_manifest.json`. Seed 300. Targets: dev 17,500 / test 3,750 / final 3,750 = **25,000 rows**.
- **Holdout design (the important idea):** test and final must not share templates or names with dev. Every template line falls in a deterministic hash bucket (dev 0-5, test 6-7, final 8-9; `skeleton_lib.zones`), and name WORDS are zoned the same way (`pii_gen.name_zone`), so leakage is prevented by construction and then re-checked.
- `audit_corpus.py`: 24 gates (100% holdout_clean, long-turn overlap with dev 0.2% / 2.0%, zero name overlap, zero duplicates).
- `scoreboard.py`: `python -m layer0_data.scoreboard <file> <detector: model|rules|full> [limit]`. A gold span counts as caught if >=50% of its characters are masked; "fully masked" = >=95%. Also reports per type / language variant / channel, right-label rate, false alarms on PII-free calls, over-masking of amounts/dates/orgs, latency, and which sub-detector caught each span. Flags THIN cells (<300 spans).
- `export_review.py` + `review/native_review.csv` (2,020 rows) + `review/pool_requests/`: material for a native-speaker review that has NOT happened.

**Row format (jsonl):** `turns` (list of {speaker,text}), `text` (flattened as `[Speaker]: text\n`), `spans` (type, start, end, value, form, rendering, ...), `lang`, `channel`, `form`, `primary_pii`, `split`. All values fake.

**Decision (Fri 20 Nov):** data generation is FROZEN. Do not spend more time on it. Rebuild with `python -m layer0_data.build_corpus` (the corpus is deterministic given the seed).

**Known limits:** PII-free share 16.5% (target 30%); name pools are small; no downstream masked-vs-unmasked task set; native-language lines unreviewed; test/final cells are thin (48 of 50 cells under 300 spans); `hard_test` and `corpus_test` have been looked at; `corpus_final` untouched.

---------------------------------------------------------------------------------------------------

## LAYER 1: PII detection (`layer1_detection/`)

**Job:** input = a call as an ordered list of `{speaker, text}` turns. Output = per turn, spans `{label, start, end, source}`. Budget: under 50 ms per message for detection + tokenising.

**Pipeline in `detector.py`** (a union, then cleanup):
1. `normalise.py`: text normalisation (zero-width characters etc.).
2. `rules.py`: written patterns (phone, Aadhaar, PAN, account, UPI, email, DOB, voter ID, GSTIN, DL, passport...) with validity checks; `ids.py` helpers.
3. `spoken_engine.py`: parses spoken digits/letters in all 9 variants (also English digit words in Indic script) with an "armed" state: cue words (number, aadhaar, "go on", नंबर, ನಂಬರ್, எண்...) make the next ~6 turns likely to contain an identifier, so fragments like "nine eight four five zero" are caught. Cross-turn memory joins pieces.
4. `model.py`: Chitragupta (XLM-R large) wrapper, with `OrtShim` to run the int8 ONNX session. Names, addresses and everything the rules miss. `suppress_orgs` drops company names; `suppress_chatter` drops greetings/honorifics in Indic scripts that the model tags as PERSON and grows partial-word spans to whole words (a stoplist written after seeing failures, so optimistic).
5. `expand.py`: absorbs `+91`, straggler digits and spoken edge words into a span.
6. `name_memory.py`: later mentions of parts of an already-detected name ("Kumar") in the same conversation.
7. `resolve.py`: merges overlapping spans, assigns types, joins numbers split across turns; offsets stay valid.

**Chosen fast config:** int8 ONNX, model run only on the NEWEST turn (`model_last_only=True`; earlier turns are history for cues/joining and covered by fast rules), 8 ORT threads by default (make it configurable: `make_detector(threads=)` / `GATEWAY_MODEL_THREADS`). Latency measured: p50 ~8.8 ms, p95 11.9 ms, p99 13.7 ms (short synthetic turns, loaded CPU).

**What was tried and dropped:** Sakshi, GLiNER (no added recall, many false alarms). Chitragupta alone was the baseline; rules + spoken engine + memory lifted it. The torch backend caches results on disk (`data/cache_chitragupta.json`, synthetic only); the int8 backend never uses the disk cache. FINDING: ModelDetector keeps an always-on in-memory cache, unbounded; the gateway trims it oldest-first.

**Measured on `corpus_test` (3,750 rows, 5,820 gold spans; synthetic):** masked (>=50%) 98.4%; FULLY masked (>=95%) **97.0%**; right type label too 89.0%; PII-free calls with a false alarm 20/694 = 2.9% (6.1% on dev); amounts/dates/orgs wrongly touched: 1/1843, 15/3060, 0/2809.
Per type fully masked: NAME 92.8% (weakest), ADDRESS 90.5%, PAN 97.0%, ACCOUNT 97.6%, AADHAAR 98.6%, LOAN 99.1%, PHONE 99.2%, UPI/DOB/EMAIL 100%. PHONE right-label only 70.5% (type confusion, still masked). By language variant 94.2% (ta_rom) to 99.2% (ta). Biggest miss groups: NAME and PAN in split_turns, NAME written/spoken.
Report generator: `python -m layer1_detection.final_report` (-> `LAYER1_REPORT.md`).

**Limits:** every score is synthetic; spoken tens and double/triple not covered for hi/mr/kn/ta; digit-word lists and ASR spellings unverified by native speakers; greeting stoplist and company-suffix list tuned on our own failures; the Marathi layer is our assumption.

---------------------------------------------------------------------------------------------------

## LAYER 2: PII vault (`layer2_vault/`)

**Job:** reversible tokenisation. Swap each detected value for a placeholder like `<PHONE_1>`; keep the real value encrypted; rehydrate only for allowed callers; support erasure by destroying a key.

**Two implementations exist.**
- `vault.py`: the first single-file prototype (24 tests), kept as legacy. Still has `decrypt_for_erasure` (nothing in L3 to L8 calls it). `LAYER2_REPORT.md` describes this one.
- **The modular vault (current; the gateway uses it through `VaultClient`):**

```
schemas (strict pydantic) -> PermissionEngine -> services -> core + database
  VaultService        tokenize: scope -> batched blind-index lookup -> reuse existing token OR new random token + AES-256-GCM -> ONE transaction
  RehydrationService  capability + purpose + borrower scope + policy -> audit "attempt" event -> key ACTIVE? -> unexpired? -> decrypt
  DeletionService     erase: pending -> tombstone + delete rows -> destroy DEK -> finalise (PARTIAL_FAILURE, never a false COMPLETED)
  RetentionService    expire -> delete (+ tombstone) -> retire idle DEKs -> purge old metadata
  KeyLifecycle        rotate KEK | BLIND_INDEX | DEK | ALL, revoke / destroy keys, revoke one mapping, reinstate borrower
  IdempotencyService  same key + same request = same result; different request = 409; stale claims recovered
  ContextService      shared conversation memory (vault_context table) for the gateway
  InternalOps         extend / scan_text / sealed erasure sweep
core/      encryption.py | key_manager.py | nonce.py | master_key_provider.py (env, AWS KMS) | blind_index.py | tokeniser.py | sealed_match.py | errors.py
database/  models (schema v3) | migrations | connection (SQLite or PostgreSQL pool) | repository | roles.sql + grants.sql | migrate CLI
ops/backup encrypted backups + verified restore
api/routes FastAPI: /vault/tokenize /rehydrate /records/{id} /records/{id}/lifecycle /erase /rotate-key /retention/cleanup /health
compat.py  ModularVault adapter so old callers (gateway, L6, L8) work unchanged
```

**Key hierarchy:** master secret (`VAULT_MASTER_KEY` env, or KMS-sealed) -> HKDF-SHA256 -> three independent keys: KEK (wraps data keys), blind-index key (deterministic keyed lookup so the same value gets the same token without storing it in plain), idempotency key. ONE random data key (DEK) per borrower. **Erasure = crypto-shredding**: destroy the borrower's DEK and every ciphertext becomes unreadable. The key store is separate from the rows.

**Notable design points:** nonce = process epoch + durable per-DEK counter (atomic allocation, UNIQUE(key_id, nonce) in the DB, exhaustion blocks encryption); KEK rotation only re-wraps (never decrypts PII); a DESTROYED key cannot be revived (DB CHECK + trigger); no default TTL (retention periods must come from an approved policy); expiry enforced on every read; unknown master version -> `VaultUnavailable('master_key_unavailable')` HTTP 503; unexpected API errors -> 500 `internal_error` with no body leak; 413 body cap; 429 auth-failure throttle; credential expiry/rotation/revocation; per-conversation advisory lock on PostgreSQL (real concurrency exposed a numbering race).
**Sealed erasure matching:** `core/sealed_match.py` matches inside the vault and returns only a category per text (strong / address / name_full / name_embedded / name_single), a score or a bool, never a value. `name_embedded` (20 Nov): the full name is there only inside a longer capitalised name ('A. Yadav' in 'Arjun A. Yadav'); Layer 6 never auto-deletes on it. (Replaced `decrypt_for_erasure`.)
**Roadmap done (6 phases, in order):** retention/TTL + migrations; nonce/rotation/KMS/final-destroy; pooling + idempotency + roles SQL; PermissionEngine + API hardening; batched performance; runbook + encrypted backups + restore test. Independent review of the first build found 19 issues (all fixed); phases 1 to 6 and the gateway had one independent review round on 20 Nov (three fresh reviewers): real defects found and fixed, a few listed as accepted limits (see `layer2_vault/README.md`, `layer3_gateway/README.md`).

**Docs inside the folder:** `README.md`, `SPEC_REQUIREMENTS.md`, `LAYER2_ACCEPTANCE.md`, `RUNBOOK.md`, `PERFORMANCE.md`.
**Run:** `python -m layer2_vault.tests.run_all` (SQLite). PostgreSQL: set `VAULT_TEST_DB_URL='postgresql://USER@/DB?host=/var/run/postgresql'`; add `VAULT_TEST_SUPER_URL` (a scratch cluster where you are superuser) to also run the restricted-roles and cross-cluster restore tests, otherwise they are reported SKIPPED. Benchmark: `python -m layer2_vault.benchmark`.

**Measured:** 234 tests pass on SQLite (9 skipped), 242 on PostgreSQL. 50 values on PostgreSQL ~5 ms per request. At 16 concurrent clients on one process, tokenisation alone reached p99 43-84 ms.
**Limits:** real AWS KMS untested (fake client only); decrypted master secret sits in process memory; PostgreSQL tested locally only (unix socket, no TLS, no replication); roles SQL not DBA-reviewed; backup scheduling and key custody are the operator's job and a restore undoes later erasures until replayed from the L7 chain; nonce uniqueness after a DB restore is only probabilistically defended (rotate DEKs after a restore); auth is static bearer tokens (in-memory throttle, no mTLS/OIDC); conversation-scope erase is not a crypto-shred; Python cannot zero key memory.

---------------------------------------------------------------------------------------------------

## LAYER 3: gateway (`layer3_gateway/`), a LangGraph state machine

**Job:** the single fail-closed checkpoint between people and the LLM. Nothing raw reaches the LLM or a log; any error, timeout or audit failure blocks.

```
initialize (+ L4 consent) -> detect (L1) -> tokenise (L2, ONE batch) -> final_audit --clean--> llm_dispatch -> sanitise -> rehydrate_route -> END
   \-denied/invalid-> block    \-error-> block   \-vault error-> block   \-leak found: re-mask, retry max 2 -> tokenise   \-still dirty-> block
   (LLM timeout -> degraded generic reply, END)
```
API: `Gateway.process({request_id, borrower_id, conversation_id, purpose, text}) -> {status, borrower_reply, log_entry}`; older, richer `Gateway.handle(...)`.

| Module | Does |
|---|---|
| `graph.py` (551 lines) | the graph and node logic, bounded history, detector timeout / stuck-call protection, name memory, context sync (`_sync_in/_sync_out`) |
| `consent.py` | calls `layer4_consent.policy.decide("logs", purpose)`; default deny; alias map (`loan_collection` -> `collections`); L4 down = 503 |
| `vault_client.py` | `VaultClient(ModularVault)`: `tokenize_batch` (one transaction + idempotency key), `rehydrate_many`, scan/extend/erasure helpers |
| `audit.py` | independent final checks: digit tripwire, spoken-number tripwire, second pattern family (PAN/IFSC/email/UPI/id-like), utility (over-masking) check, plus a re-scan with L1's rules |
| `llm.py` | dispatch with hard timeout; Bedrock Converse adapter (client injectable); LLM-level idempotency cache |
| `router.py` | sanitise (unknown tokens -> `[unknown]`, invented PII -> `[redacted]`) and route (borrower reply rehydrated, log entry stays tokenised) |
| `events.py` | L7 events: request_id, keyed `borrower_hash`, purpose, latency; small codes only; flushed after the request; MemorySink or real `AuditLog` |
| `leak_proof.py`, `leakage_eval.py`, `load_test.py`, `scale_out.py`, `benchmark.py`, `demo.py` | measurement scripts; results in `PROOF.md`, `LOAD.md`, `SCALE.md`, `PERFORMANCE.md`; `gateway_graph.mmd` = diagram |

**Decisions that differ from the written spec (keep them, or consciously change them):**
1. The human-review node is DELETED: uncertain detections (number fragments, id-shaped guesses) are ALWAYS masked. Cost: an order number may get masked. 6 of the original 37 tests were retired, 4 rewritten.
2. Audit retry kept: re-mask what the audit found (max 2 retries, each with its own idempotency key), block if still present.
3. Two extra audit checks beyond the spec's four, including the L1 rules re-scan. Without the re-scan, leakage after a detector miss roughly doubles (16.1% -> 35.1%), so keep it.
4. Digit tripwire is broader than "6+ consecutive digits" (catches digits split by spaces, dashes, dots, zero-width chars) and excludes real-looking amounts/dates (`₹4,200`, `12/03/2024`). A bare 10-digit number can never hide behind a rupee sign.
5. Over-masking guard: placeholder characters / all characters on the tokenised text, length > 20, > 85% blocks. (An earlier version, "block only uncertain spans over 85%", then a literal 85% rule that dropped legitimate dense messages, were both revised.) `overmask_action="flag"` turns the block into a warning.
6. LLM timeout -> `degraded` generic reply; any other LLM error blocks.
7. Idempotency key = `gw:<sha256(request_id)[:32]>:t<attempt>`: a retry replays the stored tokens, same id with different text is refused.
8. Shared conversation memory in the vault (`vault_context`, schema v3): the gateway saves its last 6 raw turns, name parts and number pieces after each message and adopts a newer version written by another process. This fixed a real hole: a NEW gateway process every 2 turns leaked 5.2% (29/559), now 2.0% (11/559) = same as one process. Encrypted under the borrower key, versioned, TTL 1 h idle (operational default), erased with the borrower, dropped on DEK rotation. A failing context store never blocks a message.

**Measured (synthetic test split, real detector; fake values):**
- Real Bedrock Nova Pro call through the gateway works (placeholders only sent; a 50 ms timeout returned the degraded reply; a bad model id blocked with no value in the output).
- End-to-end leakage, 300 conversations: 11 of 559 gold values leak (2.0%): NAME 8/122, PAN 2/62, LOAN 1/38. Non-name values 3/437 (0.7%).
- Latency, entry -> LLM boundary: p50 12-14 ms, p95 17.5 ms (SQLite) / 20 ms (PostgreSQL), max 29 ms, 0 of 1,711 messages over 50 ms. The 85% rule blocked 1 of 1,711.
- Concurrency: byte-identical output at 1-16 threads and across 1-8 processes with non-sticky routing; ~120 msg/s per process; p95 stays under 50 ms only up to about 4 clients per process (8 clients 67-120 ms, 16 clients 130-227 ms). Capacity is CPU-bound (~60-120 msg/s on a 16-core machine). Set `GATEWAY_MODEL_THREADS` when running several processes (default 8 threads/process made 8 processes SLOWER than 1).
- Audit net: when the detector misses half its spans the leak is 52.4% with the audit off, 16.1% with it on (31.5% without the re-scan after the tripwire upgrade). Audit cannot catch names (no regex can) or PANs spelled letter by letter / split across turns.
- Spoken tripwire alone sees 74-84% per language on test; 0 false alarms on 26,756 PII-free messages.

**Limits:** real Bedrock tested only lightly; the LLM-level retry cache is per process and lost on restart; two processes writing the same conversation at the same moment: last writer wins; micro-batching of model calls not tried; spoken word lists not native-checked; `AuditLog.append` (L7) is O(n) and delays the reply at volume.

---------------------------------------------------------------------------------------------------

## LAYER 4: consent and purpose limitation (`layer4_consent/`)

**Job:** tag every stored record with the purpose and consent it was collected under, and refuse to store data where that purpose is not allowed. Example: loan-servicing data may go to logs but not into an eval set (model improvement needs explicit consent). Not scored on its own; it supports audit and erasure. Small and fast on purpose.

**Files:** `schema.py` (`ConsentTag`, `PURPOSES`, `LAWFUL_BASIS`), `policy.py` (`POLICY` table + `decide(store, tag) -> (ok, reason)`, the single source of truth), `guard.py` (hot-path `guarded_write(store, record, adapter, audit_logger)`), `write_guard.py` (older file-based `WriteGuard` + `new_tag(...)` helper, still used by L6 mocks and L8), tests.

**Tag fields:** purpose (service, collections, compliance, dispute, analytics, model_improvement; `collection` is an alias), consent_id, explicit_consent, lawful_basis (consent / contract / legal_obligation / legitimate_use), basis_note, collected_at, borrower_id (used only for a keyed hash, never logged raw).

**Policy table (default deny for unknown store / purpose / missing tag):**

| Store | service | collections | compliance | dispute | model_improvement | analytics |
|---|---|---|---|---|---|---|
| logs | allow | allow | allow | allow | no | no |
| vector_store | allow | allow | no | no | explicit consent | no |
| eval_sets | no | no | no | no | explicit consent | no |

"Explicit consent" = `consent_id` set AND `explicit_consent=True`. `analytics` is allowed nowhere (deliberate privacy-by-design). The table is our reading of purpose limitation, not legal advice.

**`guarded_write` flow:** record must be a dict -> coerce tag -> borrower hash (L7 keyed HMAC if available, else weaker SHA-256) -> reject if flagged `rehydrated` -> reject `explicit_consent` without `consent_id` -> `policy.decide` -> `adapter.put` (failure = event `write_failed`) -> emit `write_allowed`. Never raises to the caller; denial returns False so the gateway can still answer. Events carry no record body or raw PII (tested). Sink failures are counted in `guard.AUDIT_FAILURES`.

**Measured:** ~0.002 ms per call (in-memory). 16 tests.
**Limits:** no consent registry (a `consent_id` is only claimed, never checked for existence, owner, scope, expiry or withdrawal); no withdrawal propagation or purpose-scoped erasure; no retention on tags; the `rehydrated` guard relies on the caller setting the flag; the gateway checks purpose per turn but its store write is not yet routed through `guarded_write`; the old `WriteGuard` does not emit to L7.

---------------------------------------------------------------------------------------------------

## LAYER 5: lineage registry (`layer5_lineage/registry.py`)

**Job:** the family tree of where a borrower's data ended up: borrower -> conversation -> log record -> vector chunk -> embedding -> eval item -> vault entry. Refs look like `log:L12`, `chunk:V7`, `emb:V7`, `eval:E3`, `vault:<PHONE_1>@c_B0001_1`.

**Key idea:** the tree is deliberately INCOMPLETE in practice (pipelines forget to register things), so the registry also keeps INVERTED INDEXES (by token, by conversation, by name-hash) that the erasure agent uses to find records the tree never saw. It stores only ids and keyed hashes, never raw values.

**API (used by L6):** `add_borrower`, `add_conversation`, `register`, `register_write` (the one call the gateway should make after a successful L4 write), `known_for`, `conversations_of`, `borrowers_sharing_name`, `forget_borrower` (leaves no index residue, via reverse maps), `save`/`load` (atomic JSON, format version 2), purpose/consent metadata per ref from L4, O(1) per-borrower lookup, thread lock. HMAC key from `LINEAGE_HMAC_KEY` (DEV key + warning if unset).
**Measured:** 16 tests. **Limits:** not connected to the gateway (nothing auto-registers); in our runs it used the DEV key.

---------------------------------------------------------------------------------------------------

## LAYER 6: erasure agent (`layer6_erasure/`), a LangGraph agent (the "agentic" layer)

**Job:** a borrower asks to be forgotten. Remove EVERY copy from every place it ended up, prove it, keep tamper-evident proof. The hard part is the unregistered copies: a debug log with a raw phone, a summary chunk derived from a deleted chunk, an embedding with no text, a CRM export nobody listed.

```
discover -> plan -> delete -> verify --(zero left)--> shred -> audit -> END
              ^                \--(survivors, passes < max)--> plan
                                \--(survivors at max passes)--> audit (NOT_ERASED, escalate)
```
Discovery is adaptive (widens when a record reveals a new conversation id), planning is deterministic, **no LLM anywhere in L6 (decided)**.

**Seven discovery methods:** registry (L5); conversation sweep; raw-value sweep (sealed, inside the vault, with phone-format variants); independent collapsed scan (letters+digits only, ids of 8+ chars; a second, different matcher); derivative sweep (follow parent/source links to a fixpoint); embedding probe (nearest neighbour from the borrower's identifiers: >0.50 probabilistic = delete and flag; 0.30 to 0.50 ambiguous); independent file grep (bytes of unmanaged files; always ambiguous, never auto-edited).

**Key decisions (D1-D16 in `LAYER6_CONTEXT.md`):**
- The agent is BLIND to raw data: store text goes to the vault, which matches inside and returns only a category/score/bool. `decrypt_for_erasure` was deleted from the modular vault.
- Verify re-runs ALL discovery from scratch and loops until zero survivors (max 3 passes; the evaluator uses 4). The borrower's vault key is destroyed only AFTER verify (the raw sweep needs the vault).
- Evidence strengths: certain = delete; probabilistic = delete and flag; ambiguous = never auto-delete, hand to a human. Name-only matches are weak: a one-word or shared name is deleted only with corroboration (same conversation id), else PARTIAL.
- Final statuses: ERASED_VERIFIED, PARTIAL (ambiguous items for a human), NOT_ERASED (survivors, or key not destroyed). Any unexpected error -> NOT_ERASED, escalate, no crash; key and registry kept so a retry works. Stable job id `L6-<request_id>-<hash>` makes a retry resume the same vault job.
- Delete order: eval_sets, vector_store, logs, then others. After shred the registry forgets the borrower.
- Stores sit behind a `StoreAdapter` interface (records / delete / count / nearest), so real stores need only new adapters. The audit record carries a keyed borrower hash only, goes into the L7 chain.

**Files:** `agent.py` (graph, `build_agent`, `erase`), `tools.py` (discovery tools, `SealedScanner`), `adapters.py` (`StoreAdapter`, JSONL mock stores, `StoreCatalog`), `vault_adapter.py` (modular vault, else an UNENCRYPTED in-memory fake, tests label which ran), `embed.py` (mock hashed bag-of-words embedding), `mock_data.py` (50 fake borrowers, copies, decoys, requests, `truth.json`), `evaluate.py` (`public` 10 requests, `heldout` 20, `ablate`; the ONLY code allowed to read `truth.json`), `test_erasure.py`, `LAYER6_CONTEXT.md`.

**Measured (MOCK stores, public set of 10):** 187 seeded copies, 187 removed, 100% completeness, 0 survivors, 0 collateral deletions; 9 ERASED_VERIFIED + 1 PARTIAL (a one-word name, by design); 1 pass each, 0.16-0.20 s per request. Ablation (switch a method off): silent survivors 43 without the conversation sweep, 12 without the derivative sweep, 3 without the embedding probe, 2 without the raw sweep, 15 without raw sweep + collapsed scan. **Key lesson to say plainly: with a method off the agent still reported ERASED_VERIFIED. "Verified" means "I looked with these methods and found nothing", not "nothing exists".**
**Limits:** circular test (we generated the mocks with the copy types the agent was built to find); thresholds tuned on the public 10 and the held-out 20 not re-run; embedding is a mock (a real model behaves differently); real store layout unknown (only adapters change); no write freeze during erasure; no trigger endpoint or job queue; backups/replicas out of reach; verify cannot see encrypted blobs, images, unreadable formats, stores outside the root; legacy `vault.py` still has `decrypt_for_erasure`; JSONL adapters rewrite whole files (demo scale only); purpose-scoped erasure not built.

---------------------------------------------------------------------------------------------------

## LAYER 7: audit log (`layer7_audit/`)

`chain.py` `AuditLog`: append-only, hash-chained JSONL (`data/stores/audit_log.jsonl`). `hash_i = SHA256(hash_{i-1} + canonical_json(record_i))` with each record carrying `seq`, `ts`, `prev_hash`, `hash`. `verify_chain()` returns `(ok, first_bad_seq, reason)` (reasons: `sequence_gap_or_reorder`, `prev_hash_mismatch`, `record_altered`, `chain_intact`). Borrower ids are stored only as an HMAC-SHA256 keyed hash (`hash_borrower`; key from `AUDIT_HMAC_KEY` or a random mode-600 file `data/stores/.audit_key`). There is no update/delete API. `head()` is the value to anchor externally. `proof.py` `render_proof()` prints a human-readable erasure proof and states both WHAT IT PROVES and WHAT IT DOES NOT.
Receives: gateway events (L3), consent write events (L4), erasure records (L6).
**Measured:** 5 tests.
**Limits:** a tamperer who can rewrite the WHOLE file and recompute hashes defeats a bare chain (anchor `head()` in WORM storage / an external timestamp); **`append` is O(n)** (re-reads the file: 1.4 ms at 1k records, 30 ms at 20k, 160 ms at 100k) and is NOT fixed (needs a stored tail hash); single-process, file-backed.

---------------------------------------------------------------------------------------------------

## LAYER 8: end-to-end demo (`layer8_demo/`)

`end_to_end.py` (`python -m layer8_demo.end_to_end`) runs ONE fake borrower (Ramesh Kumar) through everything:
1. A 10-turn call, each turn through the gateway (the phone number is said in two pieces; a PAN; a later "Kumar" mention).
2. Consent-guarded writes to logs / vector store / eval sets (an eval item with purpose `service` is refused; one with explicit consent is written), lineage registration.
3. Plants unregistered copies on purpose: a derived summary chunk, a debug log holding raw name+phone, and an unregistered `debug_dump/app_debug.jsonl` file.
4. Runs the erasure agent, prints the hash-chained proof.
5. Independent verification with a plain file scan (not the agent) plus checks that the vault key is gone and a former token cannot be decrypted. Final line: `CLEAN - no raw value, vault entry or key remains`.

`erasure_demo.py` seeds the L6 mock stores, shows what L4 refused, runs one request, prints the proof, then proves tamper detection by editing a copy of the audit log. `test_end_to_end.py` asserts the whole stack ends clean and that a name part shares one token. Mock stores live under `data/stores/e2e` and `layer6_erasure` work folders. Suggested pitch story: one borrower, registry-only view versus what the agent found, then the audit proof, then the ablation table.

---------------------------------------------------------------------------------------------------

## 4. Cross-layer open items and honest limits (say these out loud in the pitch)

1. All numbers are synthetic or mock. Hidden-corpus scores will be lower by an unknown amount. Names (92.8% fully masked) and addresses (90.5%) are the weakest types; 2.0% of gold values leak end to end (names 8 of 11 leaks).
2. The 5th language is our guess (Marathi); word lists and templates are not native-reviewed.
3. Erasure stores and the embedding are MOCKS; the real layout was never provided.
4. Real AWS KMS, real Bedrock at volume, and real PostgreSQL deployment (TLS, replication, DBA review) are untested.
5. The 50 ms budget holds at about 4 concurrent clients per process on 16 cores; beyond that add cores or machines (output stays identical across processes).
6. L4 has no consent registry; L5 is not wired into the gateway; L7 `append` is O(n); no write freeze or trigger endpoint for erasure; backups undo erasures until replayed.
7. Synthetic data caveats: PII-free share 16.5% (target 30%), thin test/final cells, `corpus_final` still untouched (keep it that way until the final measurement).
8. Org Claude spend limit was hit on Fri 20 Nov (workflow reviewers failed); avoid launching many agents. The Bedrock judge budget is separate.

## 5. Replication checklist

1. Create the venv, install the pinned packages (section 1), create `.env` with the three names, set a random `VAULT_MASTER_KEY`.
2. Download Chitragupta into `models/chitragupta`; export int8 with `python -m layer1_detection.compare.latency_bench export`.
3. Build data: `python -m layer0_data.build_corpus` then `python -m layer0_data.audit_corpus data/corpus` (expect 24/24 gates).
4. Measure L1: `python -m layer0_data.scoreboard data/corpus_dev.jsonl full` (dev only; run test once for the report; leave final alone). Expect ~97% fully masked.
5. Run suites for L2 to L8 (section 3). Optionally set up PostgreSQL and `VAULT_TEST_DB_URL`.
6. Run `python -m layer6_erasure.evaluate public` and `python -m layer8_demo.end_to_end`.
7. Read, in order: `layer2_vault/README.md`, `layer3_gateway/README.md` and `PROOF.md`, `layer4_consent/LAYER4_CONTEXT.md`, `layer6_erasure/LAYER6_CONTEXT.md`, `layer1_detection/LAYER1_REPORT.md`.
