# Layer 2 vault: recovery runbook

For whoever is on call. Every scenario says **what the vault does by itself** (it always fails closed: no raw text goes onward, no value is returned), **what you do**, **how you check it worked**, and **which test exercises it**. Where something is NOT automatic or NOT tested, it says so.

Two rules apply to every scenario:
1. **Never "fix" an outage by generating a new master secret, minting a new key, or restoring a key row by hand.** Every borrower's data is unreadable without the master secret it was wrapped under. A new one makes that data permanently unreadable. The database refuses to revive a destroyed key (trigger `destroyed_key_is_final`).
2. **Never mark an erasure complete anywhere else until the vault says `COMPLETED`.** Retry the same `job_id`.

Useful checks (all read-only):
- `GET /vault/health` (capability `health`): `status`, `database`, `key_material`, `db_role_safe`.
- `GET /vault/records/{vault_id}/lifecycle` (capability `metadata`): NEVER_EXISTED / ACTIVE / EXPIRED / PENDING_DELETION / PARTIALLY_FAILED / DELETED / EXPIRED_DELETED.
- SQL as `vault_readonly`: `SELECT job_id, borrower_id, status, error_code FROM erasure_jobs WHERE status <> 'COMPLETED';`  `SELECT key_status, kek_version, count(*) FROM borrower_keys GROUP BY 1,2;`  `SELECT key_id, next_counter FROM key_nonce_state ORDER BY next_counter DESC LIMIT 5;`  `SELECT * FROM vault_idempotency WHERE status = 'IN_PROGRESS';`

---

## 1. PostgreSQL is down, failing over, or unreachable
**Vault:** every operation that needs the database raises `database_unavailable` -> HTTP 503; tokenise returns no masked text, so the gateway must not forward the raw message. A transaction that was running is rolled back as a whole (record + mapping + blind index + nonce reservation + idempotency row together). The connection pool health-checks each connection when it is handed out, so dead connections are replaced automatically once the server is back; an exhausted pool times out (default 5 s) instead of hanging. `health.status` = `degraded`.
**You:** restore the database (failover / restart). Check `GET /vault/health` returns `ok`, `database: true`, `db_role_safe: true`. If you promoted a replica or restored a backup, go to scenario 7. Look for work that was in flight: erasure jobs not COMPLETED (scenarios 4 and 6), `IN_PROGRESS` idempotency rows (they are taken over automatically once older than `idempotency_stale_seconds`, default 120 s, when the caller retries).
**Verify:** one tokenise + rehydrate round trip on a test borrower; no job stuck.
**Tests:** `test_pg_a_closed_pool_means_database_unavailable...`, `test_pg_a_connection_killed_by_the_server_is_replaced_by_the_health_check`, `test_pg_a_failed_transaction_is_rolled_back...`, `test_pg_an_exhausted_pool_fails_closed...`, `test_a_database_disconnect_is_503_with_no_text...`.
**Not covered:** TLS / remote connections, replication lag, split-brain. Tested only against a local server over a unix socket.

## 2. Master key unavailable (KMS unreachable, secret missing, key version not loaded)
**Vault:** at start-up, `Settings.from_env` raises `master_key_unavailable` and the process does not come up. At runtime, any operation that must wrap or unwrap a DEK raises `master_key_unavailable` (503); `health.key_material` = `false`. Nothing is stored while the key is missing (tested). A **revoked** key version raises `key_version_revoked` (403).
**You:** restore access to the key (KMS key policy / IAM role / the secret in the environment) and restart. **Do not** create a replacement secret.
**Verify:** `GET /vault/health` -> `key_material: true`; rehydrate a known token.
**Tests:** `test_missing_master_key_blocks_encryption_and_rehydration_and_health_says_so`, `test_the_kms_provider_unseals_the_master_secret_and_fails_closed_otherwise`, `test_a_revoked_key_version_fails_closed...`, `test_a_restored_backup_with_an_unloaded_master_version_stays_unreadable`.
**Not covered:** real AWS KMS (the provider is tested against a fake client only: key policy, encryption-context condition, throttling and regional failover are unverified).

## 3. KEK rotation fails (or you want to migrate to a new master secret version)
Procedure (all versions you need must be loaded: `VAULT_MASTER_KEY_V1`, `VAULT_MASTER_KEY_V2`):
1. `POST /vault/rotate-key {"target_key_version": 2, "operation": "KEK"}` - re-wraps every DEK under version 2. **No PII value is decrypted** (tested). Then `{"operation": "BLIND_INDEX"}` (decrypts values to recompute lookup digests), or `"ALL"` for both in one transaction. Use an `Idempotency-Key` header so a retry returns the first answer.
2. Verify: the rotate response carries `keys_by_kek_version` (e.g. `{"2": 41}`: how many keys are wrapped under each master version AFTER the call); retire the old master secret only when its count is gone. Or by hand: `SELECT kek_version, count(*) FROM borrower_keys WHERE wrapped_dek IS NOT NULL GROUP BY 1;` shows only version 2, `SELECT index_version, count(*) FROM blind_indexes GROUP BY 1;` only version 2, and `SELECT name, version FROM vault_key_state;` shows `kek` / `blind_index` = 2. **A rotation is recorded in the database (`vault_key_state`), so every other process wraps NEW borrower keys under the new version at once, before it restarts** (found by review: a process started before the rotation kept using the old version, and the keys it created were lost when the old secret was retired). A process that does not hold the recorded version refuses to create keys (`master_key_unavailable`) instead of using an older one. If a key shows up under the old version after the rotation (a key created while the rotation was running), run the rotation again: it re-wraps stragglers.
3. Set `VAULT_KEY_VERSION=2`, restart, then revoke version 1 (`VAULT_REVOKED_KEY_VERSIONS=[1]`). **Order matters:** revoking before migrating makes the not-yet-migrated data unreadable (the vault refuses, loudly).
**If it fails halfway:** KEK / BLIND_INDEX / ALL run in ONE transaction: nothing changed, and the in-memory active version is restored. Fix the cause (usually a missing master version) and run it again.
**Tests:** `test_kek_rotation_rewraps_dek_without_decrypting_any_pii...`, `test_blind_index_rotation_moves_only_the_digests`, `test_rotation_rewraps_reindexes_and_survives_retiring_the_old_secret`, `test_a_failed_rotation_restores_the_active_key_version_and_changes_nothing`, `test_rotate_is_idempotent...`.
**Not covered:** rotation of a very large database in one transaction (long lock; measured only on small data). If the table is large, rotate off-peak.

## 4. DEK destruction fails (erasure of a borrower)
**Vault:** the erasure job ends `PARTIAL_FAILURE`, never `COMPLETED`. The borrower stays `ERASURE_PENDING`, so tokenise and rehydrate are already refused. `lifecycle` of their records reports `PARTIALLY_FAILED`.
**You:** retry **the same `job_id`** (or the same `Idempotency-Key`): it resumes from the failed step; steps already done are not repeated.
**Verify:** job `COMPLETED`, `key_destroyed: true`; `borrower_keys` row has `key_status = 'DESTROYED'`, `wrapped_dek IS NULL`; a tombstone exists per deleted record.
**Remember:** destroying the DEK does not delete copies elsewhere: database backups and replicas taken before the destruction still hold the wrapped key. With the old master secret they still decrypt. Retire those backups, and rotate the master secret (scenario 3) after a batch of erasures. Layer 6 is responsible for logs, vector stores and other downstream copies.
**Tests:** `test_a_failing_step_gives_PARTIAL_FAILURE_never_COMPLETED...`, `test_a_partial_failure_releases_the_key_for_a_retry_that_resumes_the_job`, `test_a_records_lifecycle_distinguishes...`.

## 5. Retention cleanup was interrupted
**Vault:** expiry is enforced at read time from the stored timestamps, so an expired token is already unusable whether or not the job ran (tested). The job is housekeeping: batched (one transaction per batch), idempotent, and reports `PARTIAL_FAILURE` with `error` if a batch fails; the failed batch rolls back, committed batches stay. Records of borrowers under erasure are skipped.
**You:** `POST /vault/retention/cleanup` (capability `retention`) again. Repeating it is harmless.
**Verify:** result `status: COMPLETED`; `SELECT count(*) FROM vault_records WHERE status = 'EXPIRED';` falls to 0.
**Policy:** no retention period is configured by default (nothing auto-expires). Set `VAULT_RETENTION_POLICY` from the approved policy; the vault does not invent one. Idle-key retirement is off unless `destroy_idle_keys` is true.
**Tests:** the eight required retention tests in `test_retention.py` (active rehydrates, expired denied, denied before cleanup, no reactivation by retry, blind index removed, needed DEK kept, repeated cleanup, failed cleanup retryable).

## 6. Deletion (erasure) was interrupted (process died, database dropped mid-job)
**Vault:** each step is its own transaction and the job row records progress. A job left `IN_PROGRESS` or `PARTIAL_FAILURE` is **not** complete. A conversation-scope erase can never re-open a borrower whose erasure is pending or finished (a bug found by review and fixed).
**You:** find the jobs (`erasure_jobs WHERE status <> 'COMPLETED'`) and retry each with its original `job_id`. If the caller used an `Idempotency-Key` and the process died holding it, the claim is taken over automatically after the stale window; before that, a retry gets `idempotency_in_progress` (409).
**Tests:** `test_a_failure_while_deleting_rows_is_also_partial_and_resumable`, `test_a_stale_in_progress_claim_is_recovered_and_a_fresh_one_is_not_stolen`, `test_resumed_job_returns_the_same_public_counts_as_a_replay`.

## 7. Restoring a backup (disaster recovery or bad data)
A backup is the past. **Restoring it undoes every erasure made after it** - tested and demonstrated (`test_restoring_a_backup_undoes_later_erasures_until_they_are_replayed`). Do this, in order:
1. **Restore** with `VAULT_BACKUP_KEY` set: `python -m layer2_vault.ops.backup restore <file> <postgresql-url | sqlite-path>`. The file is authenticated and decrypted first; a wrong key, a tampered, reordered or truncated file is rejected before anything touches the database. PostgreSQL restore goes into a database you name: restore to a NEW database, check it, then switch the vault over.
2. **Make sure the master secret version(s) the backup was written under are still loadable.** If that version was retired, the data is permanently unreadable (fails closed; tested).
3. **Replay every erasure made since the backup**, from the erasure record (Layer 6's job log / Layer 7 audit chain - not from the restored database, which does not know about them). Use a NEW `job_id` per borrower; erasing an already-empty borrower is harmless and idempotent.
4. **Rotate the DEKs** (`operation: DEK`): the restored nonce counters are older than reality (`core/nonce.py` explains the residual risk); a new DEK starts a fresh nonce sequence.
5. Run retention cleanup (scenario 5): anything that expired between the backup and now is retired.
6. Verify: health `ok`; rehydrate a known token; `lifecycle` of a record you know was erased reports `DELETED`/`NEVER_EXISTED`, not `ACTIVE`.
**Backups themselves:** encrypt (the tool refuses to write an unencrypted one), keep `VAULT_BACKUP_KEY` somewhere other than the vault host and other than the master secret, keep backups no longer than your retention policy allows, and remember backups are copies of personal data for erasure purposes.
**Tests:** `test_a_backup_of_the_vault_schema_restores_into_another_cluster_and_serves_the_same_data` (dump from one PostgreSQL cluster, restore into another, rehydrate, DEK-rotate, replay an erasure), the SQLite variants, and the format tests (wrong key / tamper / truncate / reorder / junk).
**Not covered:** scheduled backups, point-in-time recovery (WAL archiving), backup of a live PostgreSQL under heavy write load, restore time for a large database, backup integrity monitoring.

## 8. Schema migration (new release)
`VAULT_DB_URL=postgresql://vault_migration@...?schema=vault python -m layer2_vault.database.migrate` (run as the **migration role**; the running vault is `vault_app` with `VAULT_DB_AUTO_MIGRATE=false` and refuses to start on a schema-version mismatch). v1 -> v2 is additive and runs in one transaction (new columns, new tables, two widened CHECKs); existing rows keep their data (tested on SQLite and PostgreSQL). Re-run `grants.sql` after every migration. Take a backup first. **v4 (this release) adds the table `vault_key_state`; `grants.sql` no longer gives the app role automatic access to new tables, so the migration is not finished until `grants.sql` has been re-run.** The vault now refuses to open a database written by NEWER code (`schema_newer_than_this_code`) instead of overwriting its schema version. `grants.sql` is also tighter: the app role can no longer change `vault_meta` (the schema version), delete key / counter / state rows, or update the operations log (all exercised under that role in `test_database_hardening`).

## 8b. After an erasure: what is physically left in the database files
- **SQLite:** `secure_delete` is on (freed and overwritten content is zero-filled) and a TRUNCATE checkpoint runs after every erasure, key rotation and retention run, so neither the destroyed wrapped key nor the erased ciphertext remains in the main file or the write-ahead log (`test_after_an_erasure_nothing_recoverable_is_left_in_the_sqlite_files` reads the raw files). If a reader blocks the checkpoint the erase response says so; it completes at the next checkpoint.
- **PostgreSQL: not done by the vault.** Deleted and overwritten rows stay as dead row versions until VACUUM, and old page images stay in the WAL segments until they are recycled or their archive expires. Run `VACUUM` on `borrower_keys` and `vault_records` after an erasure window (the destroyed key row is the one that matters: with the master secret it still unwraps), and keep WAL archive retention short or encrypted. Backups taken earlier are covered in section 7.

## 9. Service credentials: rotation, revocation, suspected leak
- **Rotate:** add the new credential next to the old (same service name, same rights), roll the callers over, then give the old one an `expires_at` or revoke it. Both work during the overlap.
- **Revoke one service now:** add it to `VAULT_REVOKED_SERVICES` and restart (or `registry.revoke_identity(name)` in-process): refused immediately with 401, indistinguishable from an unknown credential.
- Repeated failures from one address get HTTP 429 (default 10 per 60 s). The counter is per process and in memory; behind a proxy all clients share the proxy's address - rate-limit at the proxy as well.
**Tests:** `test_expired_and_not_yet_valid_credentials_are_401...`, `test_a_revoked_identity_or_credential_is_refused_immediately...`, `test_repeated_authentication_failures_lock_the_client_out...`.

## 10. Suspected compromise of a master secret or a DEK
- **Master secret leaked:** migrate to a new master secret (scenario 3, operation ALL), revoke the old version, retire every backup written under it, and rotate DEKs (`operation: DEK`) - wrapped DEKs alone are useless without the master, but the data keys protect the ciphertext if the database and the old master both leaked.
- **One borrower's DEK suspected:** `operation: DEK` re-encrypts all borrowers' records under new DEKs (one transaction per borrower; failures are reported per borrower and retryable with the same `target_dek_version`). Backups taken before still hold the old DEK wrapped under the old master.

---

## Managed key protection: plan for AWS KMS (built and tested only against a fake KMS client)
**What exists:** `AwsKmsMasterKeyProvider`. The master secret is stored only as a KMS-encrypted blob (`VAULT_KMS_CIPHERTEXT_V<n>`, base64) bound to an encryption context (`app`, `purpose`, `version`), so a blob cannot be replayed as another version. At start-up the process calls `kms:Decrypt`; if KMS is unreachable the vault does not start.
**Steps to adopt:**
1. Create a symmetric customer-managed KMS key; key policy lets the vault's IAM role `kms:Decrypt` only, with a condition on the encryption context; operators get `kms:Encrypt` for provisioning only.
2. Run `seal_master_secret(kms_client, key_id, version, secret)` once; store the output as `VAULT_KMS_CIPHERTEXT_V1` and set `VAULT_KMS_KEY_ID`; **remove `VAULT_MASTER_KEY`** from the environment and from every file.
3. Do the same for `VAULT_BACKUP_KEY` with a different KMS key.
4. Turn on CloudTrail for the key; alert on Decrypt from any other principal.
5. Rotate: add `VAULT_KMS_CIPHERTEXT_V2`, follow scenario 3.
**Limits of this design:** the decrypted master secret lives in process memory (Python cannot zero it), and DEKs are still wrapped by a KEK derived from it. The stronger design - every DEK wrapped by KMS directly (`GenerateDataKey` / `Decrypt` per borrower, no master secret in the process) - removes the in-memory master but costs a KMS call per unwrap (tens of ms, plus KMS rate limits). It is not built; decide it against the 50 ms budget.

## What to alert on
Health `degraded` (any reason) - 503 rate - erasure jobs not COMPLETED after 15 minutes (the brief's erasure budget) - retention cleanup `PARTIAL_FAILURE` - `IN_PROGRESS` idempotency rows older than the stale window - `next_counter` of any key above 50% of `key_usage_limit` (rotate that DEK) - 429s or 401 spikes - database role reported superuser (`db_role_safe: false`).
