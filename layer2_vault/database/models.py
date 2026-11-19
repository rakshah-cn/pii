"""Schema v2. {BLOB}/{REAL} are substituted per dialect (SQLite: BLOB/REAL, PostgreSQL: BYTEA/DOUBLE PRECISION).
The database holds ONLY ciphertext, wrapped keys, blind-index digests, tokens and safe metadata - never plaintext PII or a plaintext key.
All timestamps are epoch seconds in UTC (REAL / DOUBLE PRECISION).

v1 -> v2 adds: retention (expires_at / expired_at / revoked_at, EXPIRED status), durable nonce state (key_nonce_state + UNIQUE(key_id, nonce)),
dek_version / rotated_at / destroy_reason on keys, tombstones, idempotency, and indexes for the real query patterns. See database/migrations.py.
v3 adds vault_context (shared encrypted conversation context). v4 adds vault_key_state (which master-secret versions the WHOLE deployment is on)."""

SCHEMA_VERSION = 4

TABLES = [
    """CREATE TABLE IF NOT EXISTS vault_meta (
        key TEXT NOT NULL PRIMARY KEY,
        value TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS borrower_state (
        borrower_id TEXT NOT NULL PRIMARY KEY,
        state TEXT NOT NULL CHECK (state IN ('ACTIVE','ERASURE_PENDING','ERASED')),
        updated_at {REAL} NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS borrower_keys (
        key_id TEXT NOT NULL PRIMARY KEY,
        borrower_id TEXT NOT NULL,
        wrapped_dek {BLOB},
        wrap_nonce {BLOB},
        kek_version INTEGER NOT NULL,
        dek_version INTEGER NOT NULL DEFAULT 1,
        key_status TEXT NOT NULL CHECK (key_status IN ('ACTIVE','REVOKED','DESTROYED')),
        usage_count BIGINT NOT NULL DEFAULT 0,
        created_at {REAL} NOT NULL,
        rotated_at {REAL},
        destroyed_at {REAL},
        destroy_reason TEXT,
        CHECK (key_status <> 'DESTROYED' OR (wrapped_dek IS NULL AND wrap_nonce IS NULL)))""",
    """CREATE TABLE IF NOT EXISTS key_nonce_state (
        key_id TEXT NOT NULL PRIMARY KEY,
        next_counter BIGINT NOT NULL DEFAULT 0,
        created_at {REAL} NOT NULL,
        updated_at {REAL} NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS vault_records (
        vault_id TEXT NOT NULL PRIMARY KEY,
        borrower_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        pii_type TEXT NOT NULL,
        ciphertext {BLOB} NOT NULL,
        nonce {BLOB} NOT NULL,
        key_id TEXT NOT NULL,
        purpose_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ACTIVE','PENDING_ERASURE','EXPIRED','DELETED')),
        created_at {REAL} NOT NULL,
        expires_at {REAL},
        expired_at {REAL})""",
    """CREATE TABLE IF NOT EXISTS token_mappings (
        token_id TEXT NOT NULL PRIMARY KEY,
        vault_id TEXT NOT NULL UNIQUE,
        token TEXT NOT NULL,
        borrower_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        pii_type TEXT NOT NULL,
        seq INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ACTIVE','EXPIRED','REVOKED')),
        created_at {REAL} NOT NULL DEFAULT 0,
        expires_at {REAL},
        revoked_at {REAL},
        UNIQUE (conversation_id, token),
        UNIQUE (conversation_id, pii_type, seq))""",
    """CREATE TABLE IF NOT EXISTS blind_indexes (
        index_id TEXT NOT NULL PRIMARY KEY,
        vault_id TEXT NOT NULL,
        borrower_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        purpose_id TEXT NOT NULL,
        pii_type TEXT NOT NULL,
        index_version INTEGER NOT NULL,
        index_digest TEXT NOT NULL,
        UNIQUE (borrower_id, conversation_id, purpose_id, pii_type, index_version, index_digest))""",
    """CREATE TABLE IF NOT EXISTS vault_operations (
        operation_id TEXT NOT NULL PRIMARY KEY,
        operation_type TEXT NOT NULL,
        borrower_id TEXT,
        vault_id TEXT,
        request_id TEXT,
        caller TEXT,
        status TEXT NOT NULL,
        timestamp {REAL} NOT NULL,
        error_code TEXT)""",
    """CREATE TABLE IF NOT EXISTS erasure_jobs (
        job_id TEXT NOT NULL PRIMARY KEY,
        borrower_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        conversation_id TEXT,
        status TEXT NOT NULL,
        counts TEXT,
        error_code TEXT,
        created_at {REAL} NOT NULL,
        updated_at {REAL} NOT NULL)""",
    # a minimal, NON-sensitive record that a vault record existed and how it ended: no PII, ciphertext, nonce or blind-index value
    """CREATE TABLE IF NOT EXISTS vault_record_tombstones (
        record_id TEXT NOT NULL PRIMARY KEY,
        deletion_status TEXT NOT NULL CHECK (deletion_status IN ('DELETED','EXPIRED_DELETED')),
        deleted_at {REAL} NOT NULL,
        operation_id TEXT,
        reason TEXT,
        expires_at {REAL})""",
    # v3: short-lived, ENCRYPTED conversation context (recent raw turns + name / number-piece memory) so several gateway processes can serve one conversation.
    # Encrypted under the borrower's DEK like every other value; expires on its own; deleted by erasure; unreadable once the key is destroyed.
    """CREATE TABLE IF NOT EXISTS vault_context (
        borrower_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        key_id TEXT NOT NULL,
        ciphertext {BLOB} NOT NULL,
        nonce {BLOB} NOT NULL,
        version BIGINT NOT NULL,
        updated_at {REAL} NOT NULL,
        expires_at {REAL} NOT NULL,
        PRIMARY KEY (borrower_id, conversation_id))""",
    # v4: a KEK / blind-index rotation done by ONE process must be seen by every other process, or a stale process keeps wrapping NEW borrower keys under the OLD master version
    # (and loses them when that secret is retired). A separate table, not vault_meta, so the running app role never needs to write the schema version.
    """CREATE TABLE IF NOT EXISTS vault_key_state (
        name TEXT NOT NULL PRIMARY KEY CHECK (name IN ('kek', 'blind_index')),
        version INTEGER NOT NULL CHECK (version >= 1),
        updated_at {REAL} NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS vault_idempotency (
        idempotency_key TEXT NOT NULL,
        operation TEXT NOT NULL,
        caller TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('IN_PROGRESS','COMPLETED','FAILED')),
        response_reference TEXT,
        created_at {REAL} NOT NULL,
        updated_at {REAL} NOT NULL,
        expires_at {REAL} NOT NULL,
        PRIMARY KEY (idempotency_key, operation, caller))""",
]

# Indexes follow the real query patterns (see docs in README): lookups by borrower / conversation / key, expiry sweeps, scoped blind-index
# lookups (covered by the UNIQUE constraint on blind_indexes), operation / tombstone / idempotency retention.
INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS one_active_key ON borrower_keys(borrower_id) WHERE key_status = 'ACTIVE'",
    "CREATE UNIQUE INDEX IF NOT EXISTS rec_key_nonce ON vault_records(key_id, nonce)",          # a nonce can never repeat under one DEK in stored data
    "CREATE INDEX IF NOT EXISTS keys_by_borrower ON borrower_keys(borrower_id)",
    "CREATE INDEX IF NOT EXISTS keys_by_status ON borrower_keys(key_status)",
    "CREATE INDEX IF NOT EXISTS rec_by_borrower ON vault_records(borrower_id)",
    "CREATE INDEX IF NOT EXISTS rec_by_conversation ON vault_records(conversation_id)",
    "CREATE INDEX IF NOT EXISTS rec_by_key ON vault_records(key_id)",
    "CREATE INDEX IF NOT EXISTS rec_by_expiry ON vault_records(status, expires_at)",
    "CREATE INDEX IF NOT EXISTS map_by_borrower ON token_mappings(borrower_id, conversation_id)",
    "CREATE INDEX IF NOT EXISTS map_by_expiry ON token_mappings(status, expires_at)",
    "CREATE INDEX IF NOT EXISTS bi_by_vault ON blind_indexes(vault_id)",
    "CREATE INDEX IF NOT EXISTS bi_by_borrower ON blind_indexes(borrower_id)",
    "CREATE INDEX IF NOT EXISTS ops_by_borrower ON vault_operations(borrower_id)",
    "CREATE INDEX IF NOT EXISTS ops_by_time ON vault_operations(timestamp)",
    "CREATE INDEX IF NOT EXISTS jobs_by_borrower ON erasure_jobs(borrower_id, status)",
    "CREATE INDEX IF NOT EXISTS tomb_by_time ON vault_record_tombstones(expires_at)",
    "CREATE INDEX IF NOT EXISTS idem_by_expiry ON vault_idempotency(expires_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ctx_key_nonce ON vault_context(key_id, nonce)",
    "CREATE INDEX IF NOT EXISTS ctx_by_expiry ON vault_context(expires_at)",
    "CREATE INDEX IF NOT EXISTS ctx_by_key ON vault_context(key_id)",
    "CREATE INDEX IF NOT EXISTS ctx_by_conversation ON vault_context(conversation_id)",
]

# columns added to v1 tables by the v2 migration: (table, column, ddl)
V2_NEW_COLUMNS = [
    ("borrower_keys", "dek_version", "INTEGER NOT NULL DEFAULT 1"),
    ("borrower_keys", "rotated_at", "{REAL}"),
    ("borrower_keys", "destroy_reason", "TEXT"),
    ("vault_records", "expires_at", "{REAL}"),
    ("vault_records", "expired_at", "{REAL}"),
    ("token_mappings", "created_at", "{REAL} NOT NULL DEFAULT 0"),
    ("token_mappings", "expires_at", "{REAL}"),
    ("token_mappings", "revoked_at", "{REAL}"),
]
# tables whose CHECK constraint on status was widened in v2 (name, constraint name used by PostgreSQL, new check)
V2_WIDENED_CHECKS = [          # (table, PostgreSQL constraint name, check, text that is present in the table's SQL once the check exists)
    ("vault_records", "vault_records_status_check", "status IN ('ACTIVE','PENDING_ERASURE','EXPIRED','DELETED')", "'EXPIRED'"),
    ("token_mappings", "token_mappings_status_check", "status IN ('ACTIVE','EXPIRED','REVOKED')", "'EXPIRED'"),
    ("borrower_keys", "borrower_keys_destroyed_check", "key_status <> 'DESTROYED' OR (wrapped_dek IS NULL AND wrap_nonce IS NULL)", "key_status <> 'DESTROYED'"),
]

# A DESTROYED key is final. The database itself refuses to move it back (so an application bug, a restore script or hand-written SQL cannot quietly revive it).
TRIGGERS = {
    "sqlite": ["""CREATE TRIGGER IF NOT EXISTS destroyed_key_is_final BEFORE UPDATE OF key_status ON borrower_keys
                  WHEN OLD.key_status = 'DESTROYED' AND NEW.key_status <> 'DESTROYED'
                  BEGIN SELECT RAISE(ABORT, 'destroyed_key_is_final'); END"""],
    "postgres": ["""CREATE OR REPLACE FUNCTION vault_destroyed_key_is_final() RETURNS trigger AS $$
                    BEGIN
                      IF OLD.key_status = 'DESTROYED' AND NEW.key_status <> 'DESTROYED' THEN RAISE EXCEPTION 'destroyed_key_is_final'; END IF;
                      RETURN NEW;
                    END; $$ LANGUAGE plpgsql""",
                 "DROP TRIGGER IF EXISTS destroyed_key_is_final ON borrower_keys",
                 """CREATE TRIGGER destroyed_key_is_final BEFORE UPDATE OF key_status ON borrower_keys
                    FOR EACH ROW EXECUTE FUNCTION vault_destroyed_key_is_final()"""],
}
