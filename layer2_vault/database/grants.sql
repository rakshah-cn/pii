-- Layer 2 vault: restricted PostgreSQL roles, part 2 of 2 (table and column grants). Run as vault_migration (the owner) or a superuser,
-- after the schema exists and after every migration. Idempotent. See roles.sql for what each role is for.

-- reset, then grant explicitly: nothing is inherited by accident
REVOKE ALL ON ALL TABLES IN SCHEMA vault FROM PUBLIC, vault_app, vault_readonly, vault_admin;

-- vault_app: row access only (no TRUNCATE, no REFERENCES, no TRIGGER, no DDL), and on each table ONLY what the code does to it (checked against every statement in
-- database/repository.py and the services, and exercised under this role by test_database_hardening). Review finding: it used to get UPDATE and DELETE everywhere.
GRANT SELECT, INSERT, UPDATE, DELETE ON
  vault.vault_records, vault.token_mappings, vault.blind_indexes, vault.vault_record_tombstones, vault.vault_idempotency, vault.vault_context
TO vault_app;
GRANT SELECT, INSERT, UPDATE ON vault.borrower_state, vault.erasure_jobs TO vault_app;             -- an ERASED borrower's row must stay: it is what keeps tokenise refused
GRANT SELECT, INSERT, UPDATE ON vault.borrower_keys, vault.key_nonce_state, vault.vault_key_state TO vault_app;   -- never DELETE: a DESTROYED key row proves the destruction and blocks minting a new key; counters must never be reset
GRANT SELECT, INSERT, DELETE ON vault.vault_operations TO vault_app;                               -- append-only log: no UPDATE; DELETE only for the retention purge
GRANT SELECT ON vault.vault_meta TO vault_app;                                                     -- the schema version is written by the migration role only (a forged value would defeat the version check)

-- vault_readonly: metadata only. Columns that hold secrets or searchable digests are simply not granted.
GRANT SELECT ON vault.vault_meta, vault.borrower_state, vault.key_nonce_state, vault.vault_key_state, vault.vault_operations, vault.erasure_jobs, vault.vault_record_tombstones TO vault_readonly;
GRANT SELECT (key_id, borrower_id, kek_version, dek_version, key_status, usage_count, created_at, rotated_at, destroyed_at, destroy_reason) ON vault.borrower_keys TO vault_readonly;           -- not wrapped_dek / wrap_nonce
GRANT SELECT (vault_id, borrower_id, conversation_id, token_id, pii_type, key_id, purpose_id, status, created_at, expires_at, expired_at) ON vault.vault_records TO vault_readonly;         -- not ciphertext / nonce
GRANT SELECT (token_id, vault_id, token, borrower_id, conversation_id, pii_type, seq, status, created_at, expires_at, revoked_at) ON vault.token_mappings TO vault_readonly;
GRANT SELECT (index_id, vault_id, borrower_id, conversation_id, purpose_id, pii_type, index_version) ON vault.blind_indexes TO vault_readonly;                                            -- not index_digest
GRANT SELECT (idempotency_key, operation, caller, status, created_at, updated_at, expires_at) ON vault.vault_idempotency TO vault_readonly;                                                -- not request_hash / response_reference

GRANT SELECT (borrower_id, conversation_id, key_id, version, updated_at, expires_at) ON vault.vault_context TO vault_readonly;                                                        -- not ciphertext / nonce

-- vault_admin: what readonly sees, plus the few columns an on-call engineer needs to un-stick a job. Still no secrets, no DDL, no record deletion.
-- (vault_admin is a member of vault_readonly: set in roles.sql, which needs the admin option)
GRANT UPDATE (state, updated_at) ON vault.borrower_state TO vault_admin;
GRANT UPDATE (status, counts, error_code, updated_at) ON vault.erasure_jobs TO vault_admin;
GRANT UPDATE (status, updated_at) ON vault.vault_idempotency TO vault_admin;
GRANT DELETE ON vault.vault_idempotency TO vault_admin;

-- tables created by later migrations get NO automatic access for the app role: add them to the lists above and re-run this file. (Earlier versions of this file granted
-- everything on new tables by default; the line below removes that default from a database that already has it.)
ALTER DEFAULT PRIVILEGES FOR ROLE vault_migration IN SCHEMA vault REVOKE ALL ON TABLES FROM vault_app;
