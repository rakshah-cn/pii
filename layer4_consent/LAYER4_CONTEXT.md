# Layer 4: Consent and purpose limitation (as built, 19 Nov 2026)

Status: built and tested on its own (16 tests: 5 in test_consent.py, 11 in test_guard.py).
Run:  `.venv/bin/python -m layer4_consent.test_consent` and `.venv/bin/python -m layer4_consent.test_guard`

## 1. Purpose

Requirement from the brief: tag every stored record with the purpose and consent it was collected under, and refuse to
store data where that purpose does not allow it. Example: loan-servicing data may go in logs, but not into an eval set
(that is a model-improvement use and needs explicit consent).
Layer 4 is not scored on its own. It supports the audit trail and erasure scoring, so it is kept small and fast.

## 2. Files

| File | Role |
|---|---|
| schema.py | `ConsentTag` dataclass (slots=True), `PURPOSES`, `LAWFUL_BASIS` |
| policy.py | `POLICY` table and `decide(store, tag) -> (ok, reason)`. The single source of truth. |
| guard.py | NEW hot-path hook `guarded_write(store, record, adapter, audit_logger) -> bool` |
| write_guard.py | Older `WriteGuard` class (rejections to a JSONL file) and the `new_tag(...)` helper. Still used by the Layer 6 mocks and the Layer 8 demo. |
| test_consent.py / test_guard.py | tests |

Two entry points coexist on purpose: `WriteGuard.guarded_write(store, adapter, record)` (old, file-based rejection log) and
`guard.guarded_write(store, record, adapter, audit_logger)` (new, emits to Layer 7). Both call the same `policy.decide`,
so they cannot disagree.

## 3. The consent tag

Attached to every record under the key `consent` (an object or a dict).

| Field | Meaning |
|---|---|
| purpose | service, collections, compliance, dispute, analytics, model_improvement. `collection` is accepted as an alias for `collections`. |
| consent_id | id of the consent artefact; None = no explicit consent on file |
| explicit_consent | True only if the borrower consented to THIS purpose |
| lawful_basis | consent, contract, legal_obligation, legitimate_use (default contract) |
| basis_note | free text, e.g. "loan servicing under agreement" |
| collected_at | ISO timestamp |
| borrower_id | optional. Used only to make a keyed hash for the audit event. Never logged raw. |

Build one with `new_tag("service", borrower_id="B-0001")`; it returns a dict.

## 4. Policy table (store -> purpose -> rule)

| Store | service | collections | compliance | dispute | model_improvement | analytics |
|---|---|---|---|---|---|---|
| logs | allow | allow | allow | allow | no | no |
| vector_store | allow | allow | no | no | explicit consent | no |
| eval_sets | no | no | no | no | explicit consent | no |

- `explicit consent` means BOTH `consent_id` is set AND `explicit_consent=True`.
- Default deny: an unknown store, an unknown purpose, or a missing tag is rejected.
- `analytics` is allowed nowhere. This is a deliberate privacy-by-design choice; say so in the pitch.
- ASSUMPTION: the table is our reading of purpose limitation, not legal advice. Edit it with legal.

## 5. Decision flow of `guarded_write`

1. Record must be a dict, else reject `record_not_a_dict`.
2. Tag is coerced (object or dict). Missing or no purpose: reject `missing_or_invalid_consent_tag`.
3. Borrower hash: Layer 7's keyed HMAC if the logger has `hash_borrower` (first 16 hex chars), else unkeyed SHA-256 (weaker, guessable for small id spaces).
4. Record flagged `rehydrated` (truthy): reject `rehydrated_data_must_not_be_stored`.
5. `explicit_consent` true but no `consent_id`: reject `explicit_consent_without_consent_id`.
6. `policy.decide`: reject with `unknown_store:X`, `purpose_not_allowed:P->S` or `explicit_consent_required:P->S`.
7. Write via `adapter.put(record)` (or `adapter.write`). If it raises: event `write_failed`, return False.
8. Emit `write_allowed`, return True.

It never raises to the caller. A denied write returns False, so the gateway can still answer the borrower.
Any exception in steps 1-6 is turned into a rejection (fail closed).

## 6. Audit events (sent to `audit_logger.emit(event)`, or `.append(event)` for the Layer 7 `AuditLog`)

| kind | fields |
|---|---|
| write_allowed | store, record_id, purpose, consent_id, borrower_hash |
| write_rejected | store, record_id, purpose, borrower_hash, reason |
| write_failed | store, record_id, purpose, borrower_hash, reason (exception class name only) |

Events never contain the record body or raw PII (a test checks this). `record_id` is taken from `record_id` or `id`.
Layer 7 adds `seq`, `ts`, `prev_hash`, `hash` when it appends. If the sink itself fails, the conversation is not
interrupted; the failure is counted in `guard.AUDIT_FAILURES` (surface this in monitoring, because a lost rejection
event is a gap in the audit trail).

## 7. Interfaces

- Layer 3 (gateway): after masking, build the record with `consent=new_tag(purpose, consent_id=..., explicit=..., borrower_id=...)` and call
  `guarded_write("logs", record, log_store, audit_log)`. If False, drop the write and still return the reply.
  The gateway already calls `policy.decide` in `layer3_gateway/consent.py` before processing a turn (purpose gate), and
  maps its own purpose names to Layer 4's names there. The store write itself is not yet routed through `guarded_write`.
- Layer 5 (lineage): reads `purpose` and `consent_id` from `write_allowed` events (or from the stored record's `consent`).
- Layer 6 (erasure): unchanged. Mock stores are still built through the old `WriteGuard`.
- Layer 7 (audit): receives the events above. Test `test_layer7_chain_integration_keyed_hash` shows the chain still verifies.
- Layer 8 (demo): uses the old `WriteGuard` for its store writes and `rejections.jsonl`.

## 8. Measured

- `guarded_write` about 0.002 ms per call with an in-memory sink and store (target: under 1 ms). No database calls.

## 9. Deliberate departures from the first context.md

1. model_improvement keeps the explicit-consent rule (consent_id AND explicit_consent=True). The pasted table allowed it with no check.
2. Borrower hash uses Layer 7's keyed HMAC when available (the pasted version was unkeyed SHA-256 only).
3. Accepts both `put` and `write` adapters, and both tag objects and dicts, so existing code keeps working.

## 10. Scope cuts and honest limits

- No consent registry. The tag only CLAIMS a consent_id; nothing checks that it exists, belongs to this borrower, covers
  this purpose, is unexpired, or was withdrawn. We trust the gateway's authenticated state.
- No withdrawal propagation or purpose-scoped erasure. Erasure is per borrower, via Layer 6.
- No retention or expiry on tags.
- No check that the tag's borrower matches the record's borrower.
- The `rehydrated` guard relies on the caller setting the flag; Layer 4 cannot see whether text was rehydrated.
- The unkeyed SHA-256 fallback is guessable for small id spaces.
- The old `WriteGuard` still writes its own rejection file and does not emit to Layer 7.

## 11. Status of the other suites when this was written

Layer 3 gateway 31 pass, Layer 5 5 pass, Layer 6 13 pass, Layer 7 5 pass.
Layer 8 end-to-end FAILS (NOT CLEAN). Cause is not Layer 4: the rewritten Layer 3 graph calls `vault.tokenize_batch`, which
only the new `VaultClient` has, and `layer8_demo/end_to_end.py` `get_vault()` still returns the old `Vault`, so every turn with
PII is blocked with `vault_error:AttributeError`. Fix is in the demo or Layer 3 wiring, not here.
