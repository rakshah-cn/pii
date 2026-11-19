# Layer 2: PII vault (modular implementation)

Secure, reversible tokenisation between detection (Layer 1) and the gateway (Layer 3). Requirements: `SPEC_REQUIREMENTS.md`. Final status table: `LAYER2_ACCEPTANCE.md`. Recovery procedures: `RUNBOOK.md`. Measured speed: `PERFORMANCE.md`.
Run all tests: `python -m layer2_vault.tests.run_all` (SQLite). PostgreSQL: `VAULT_TEST_DB_URL='postgresql://USER@/DB?host=/var/run/postgresql' python -m layer2_vault.tests.run_all`. Add `VAULT_TEST_SUPER_URL=...` (a scratch cluster where you are superuser) to also run the restricted-roles and cross-cluster restore tests; without it those tests are reported as SKIPPED, never as passed.
Latency: `python -m layer2_vault.benchmark`. Old prototype: `layer2_vault/vault.py` (kept; default for the gateway; `VAULT_BACKEND=modular` switches to this implementation).

```
Layer 1 spans ─► schemas (strict types, span text must match) ─► PermissionEngine ─► services ─► core + database
   tokenize   VaultService        scope -> batched blind-index lookup -> reuse OR random token + AES-256-GCM (counter nonce) -> ONE transaction
   rehydrate  RehydrationService  capability + purpose + borrower scope + policy -> audit attempt -> borrower/key ACTIVE -> unexpired -> decrypt (atomic)
   erase      DeletionService     pending -> tombstone + delete rows -> destroy DEK -> finalise (PARTIAL_FAILURE never COMPLETED)
   retention  RetentionService    expire -> delete (+ tombstone) -> optionally retire idle DEKs -> purge old metadata (batched, idempotent)
   lifecycle  KeyLifecycle        rotate KEK | BLIND_INDEX | DEK | ALL, revoke / destroy, revoke one mapping, reinstate borrower
   idempotency IdempotencyService same key + same request = same result; different request = 409; stale claims recovered
   internal   InternalOps         extend / scan_text / sealed erasure sweep
core/        encryption | key_manager (KEK / blind / idempotency keys, versions, revocation) | nonce (durable counter) | master_key_provider (env, AWS KMS) | blind_index | tokeniser | errors
database/    models (v2) | migrations | connection (SQLite; PostgreSQL pool) | repository | roles.sql + grants.sql | migrate (CLI)
ops/backup   encrypted backups + verified restore (PostgreSQL, SQLite)
api/routes   FastAPI: /vault/tokenize /rehydrate /records/{id} /records/{id}/lifecycle /erase /rotate-key /retention/cleanup /health
```
Key hierarchy: master secret (env, or KMS-sealed) -> HKDF-SHA256 -> KEK (wraps DEKs), blind-index key, idempotency key (three independent keys); one random DEK per borrower.

## What changed in the improvement roadmap (Phases 1-6)
1. **Data lifecycle:** schema v2 (expiry / revoked / destroyed timestamps, EXPIRED status, DB constraints), in-place migration from v1 (tested on both databases), retention policy (`VAULT_RETENTION_POLICY`; **no TTL is configured by default**: periods must come from an approved policy), expiry enforced on every read, cleanup job, PII-free tombstones.
2. **Crypto:** nonce = process epoch || durable per-DEK counter (atomic allocation, UNIQUE(key_id, nonce) in the database, exhaustion blocks encryption); KEK rotation (re-wrap only, never decrypts PII), blind-index rotation and DEK rotation are separate operations; key versions can be revoked; a DESTROYED key cannot be revived (CHECK + trigger); master secret can come from AWS KMS (tested against a fake client only).
3. **Database:** PostgreSQL connection pool (health-checked, timeouts, rollback on failure), per-conversation advisory lock (real concurrency exposed a numbering race), idempotency table, tombstones + lifecycle lookup, index review (every hot query tested to use an index), roles `vault_migration / vault_app / vault_readonly / vault_admin` (tested on a scratch cluster).
4. **API:** strict schemas, 413 body cap, 429 auth-failure throttle, 409 idempotency conflicts, 500 `internal_error` for the unexpected (no body leaks), credential expiry / rotation / revocation, central `PermissionEngine.authorize(identity, operation, borrower_id, purpose_id)` with purpose and borrower scope.
5. **Performance:** batched lookups, sequence numbers, nonce allocation and inserts: statements per request no longer grow with the number of values (50 values on PostgreSQL: 13 ms -> about 5 ms).
6. **Production readiness:** encrypted backups with verified restore, runbook, security regression scan of the whole database / logs / events / error bodies (with a negative control).

### Behaviour changes to know about
- An unknown / not-loaded master version is now `VaultUnavailable('master_key_unavailable')` (HTTP 503), not a 403 denial.
- Unexpected errors in the HTTP layer are HTTP 500 `internal_error` (database / dependency outages remain 503).
- Request schemas are strict (`"2"` is not `2`); error bodies include a safe `request_id`.
- Rotation `operation` defaults to `ALL` (the old behaviour).

## Where this still falls short (honest list)
- **Real AWS KMS is untested** (fake client only). The decrypted master secret still sits in process memory; per-DEK KMS wrapping is not built.
- **PostgreSQL** is tested locally (unix socket, no TLS, no replication, no managed service). The roles SQL was exercised on a scratch cluster but not reviewed by a DBA.
- **Backups:** format and restore are tested; scheduling, point-in-time recovery, and backup-key custody are the operator's job. A restore undoes later erasures until they are replayed (`RUNBOOK.md` 7).
- **Nonce uniqueness after a database restore** is defended by a random epoch (probabilistic), not proven: rotate DEKs after a restore.
- **Retention:** no TTL is set until the policy owner supplies one. EXPIRED mapping rows (no PII) are kept so token numbers are not reused; a conversation-scope erase deletes mapping rows, so numbers there can be reused.
- **Layer 4 / Layer 7 are interfaces, not wired** (`PolicyPermissions`, `InMemoryEvents` / `HashChainedEvents`). `decrypt_for_erasure` was DELETED from the modular vault (19 Nov); Layer 6 now uses `erasure_scan` / `erasure_probe` / `find_unmapped_copies`, which match inside the vault and return a category, scores or a bool (`core/sealed_match.py`). The legacy single-file `vault.py` still has the old call.
- **Authentication is static bearer tokens** (SHA-256 digests). The 429 throttle is in memory and per process. No mTLS / OIDC.
- Conversation-scope erasure is not a crypto-shred (one DEK per borrower). Pre-erasure / pre-rotation backups still hold old wrapped DEKs. Python cannot zero key memory.
- Speed numbers are from one machine with a local database; at 16 concurrent clients on one process, tokenisation p99 reached 43-84 ms by itself (`PERFORMANCE.md`), which would consume the 50 ms detection + tokenisation budget.

## Independent review (earlier phase)
19 findings survived verification and were fixed, each with a regression test (`tests/test_review_fixes.py`). The Phase 1-6 work has been tested but NOT independently reviewed by a second party.
`LAYER2_REPORT.md` describes the LEGACY single-file vault (`vault.py`), not this implementation.
