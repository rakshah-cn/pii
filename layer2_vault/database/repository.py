"""Persistence operations (all take an open Tx). Parameterised SQL only. Returns plain dicts; never logs values."""
import json
import time

from ..core.tokeniser import new_id


class Repository:
    # ---- borrower state (blocks writes / rehydration while erasure is pending or done)
    @staticmethod
    def _lock(tx):
        """PostgreSQL: lock the borrower_state row for the rest of the transaction so tokenise / rehydrate / erase are atomic ACROSS processes
        (SQLite already serialises writers with BEGIN IMMEDIATE). !! untested: no PostgreSQL server was available !!"""
        return " FOR UPDATE" if tx.kind == "postgres" else ""

    def ensure_borrower(self, tx, borrower_id):
        tx.execute("INSERT INTO borrower_state (borrower_id, state, updated_at) VALUES (?, 'ACTIVE', ?) ON CONFLICT (borrower_id) DO NOTHING", (borrower_id, time.time()))
        return tx.one("SELECT state FROM borrower_state WHERE borrower_id = ?" + self._lock(tx), (borrower_id,))["state"]

    def borrower_state(self, tx, borrower_id):
        r = tx.one("SELECT state FROM borrower_state WHERE borrower_id = ?" + self._lock(tx), (borrower_id,))
        return r["state"] if r else None

    def set_borrower_state(self, tx, borrower_id, state):
        tx.execute("INSERT INTO borrower_state (borrower_id, state, updated_at) VALUES (?, ?, ?) ON CONFLICT (borrower_id) DO UPDATE SET state = ?, updated_at = ?",
                   (borrower_id, state, time.time(), state, time.time()))

    # ---- keys
    def active_key(self, tx, borrower_id):
        return tx.one("SELECT * FROM borrower_keys WHERE borrower_id = ? AND key_status = 'ACTIVE'", (borrower_id,))

    def latest_key(self, tx, borrower_id):
        return tx.one("SELECT * FROM borrower_keys WHERE borrower_id = ? ORDER BY created_at DESC LIMIT 1", (borrower_id,))

    def insert_key(self, tx, key_id, borrower_id, wrapped, nonce, kek_version, dek_version=1, now=None):
        now = time.time() if now is None else now
        tx.execute("INSERT INTO borrower_keys (key_id, borrower_id, wrapped_dek, wrap_nonce, kek_version, dek_version, key_status, usage_count, created_at) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', 0, ?)",
                   (key_id, borrower_id, wrapped, nonce, kek_version, dek_version, now))
        tx.execute("INSERT INTO key_nonce_state (key_id, next_counter, created_at, updated_at) VALUES (?, 0, ?, ?)", (key_id, now, now))   # durable nonce counter (Phase 2)
        return key_id

    def bump_usage(self, tx, key_id, n):
        if n:
            tx.execute("UPDATE borrower_keys SET usage_count = usage_count + ? WHERE key_id = ?", (n, key_id))

    def allocate_nonces(self, tx, key_id, n, limit):
        """Atomically reserve `n` consecutive counters for this DEK and return the first one. The UPDATE locks the row, so concurrent writers
        (other threads, other processes) queue and can never be given the same counter. If the DEK would pass `limit`, nothing is reserved and
        key_usage_limit_reached is raised (the DEK must be rotated). The reservation commits or rolls back WITH the ciphertext it was taken for."""
        from ..core.errors import KeyStateError
        now = time.time()
        tx.execute("INSERT INTO key_nonce_state (key_id, next_counter, created_at, updated_at) SELECT key_id, usage_count, ?, ? FROM borrower_keys WHERE key_id = ? "
                   "ON CONFLICT (key_id) DO NOTHING", (now, now, key_id))                          # keys created by v1 start from what they already used
        r = tx.one("UPDATE key_nonce_state SET next_counter = next_counter + ?, updated_at = ? WHERE key_id = ? AND next_counter + ? <= ? RETURNING next_counter",
                   (n, now, key_id, n, limit))
        if r is None:
            raise KeyStateError("key_usage_limit_reached")
        tx.execute("UPDATE borrower_keys SET usage_count = usage_count + ? WHERE key_id = ?", (n, key_id))
        return r["next_counter"] - n

    def nonce_state(self, tx, key_id):
        return tx.one("SELECT * FROM key_nonce_state WHERE key_id = ?", (key_id,))

    def borrowers_with_active_keys(self, tx):
        return [r["borrower_id"] for r in tx.query("SELECT borrower_id FROM borrower_keys WHERE key_status = 'ACTIVE' ORDER BY borrower_id")]

    def retire_rotated_key(self, tx, key_id, now):
        """DEK rotation: the old DEK is destroyed (material overwritten) in the same transaction that re-encrypted its records."""
        return tx.execute("UPDATE borrower_keys SET key_status = 'DESTROYED', wrapped_dek = NULL, wrap_nonce = NULL, destroyed_at = ?, rotated_at = ?, destroy_reason = 'ROTATION' "
                          "WHERE key_id = ? AND key_status = 'ACTIVE'", (now, now, key_id)).rowcount

    def rekey_record(self, tx, vault_id, key_id, ciphertext, nonce):
        tx.execute("UPDATE vault_records SET key_id = ?, ciphertext = ?, nonce = ? WHERE vault_id = ?", (key_id, ciphertext, nonce, vault_id))

    def key_versions(self, tx):
        return tx.query("SELECT key_id, borrower_id, key_status, kek_version, dek_version, usage_count FROM borrower_keys")

    def set_key_status(self, tx, borrower_id, status, reason=None):
        if status == "DESTROYED":                      # the wrapped key and its nonce are overwritten, not just flagged
            n = tx.execute("UPDATE borrower_keys SET key_status = 'DESTROYED', wrapped_dek = NULL, wrap_nonce = NULL, destroyed_at = ?, destroy_reason = ? WHERE borrower_id = ? AND key_status != 'DESTROYED'", (time.time(), reason or "ERASURE", borrower_id)).rowcount
        else:
            n = tx.execute("UPDATE borrower_keys SET key_status = ? WHERE borrower_id = ? AND key_status = 'ACTIVE'", (status, borrower_id)).rowcount
        return n

    def key_state(self, tx):
        """{'kek': n, 'blind_index': n} for whatever a rotation has recorded (empty until the first one)."""
        return {r["name"]: r["version"] for r in tx.query("SELECT name, version FROM vault_key_state")}

    def set_key_state(self, tx, name, version, now):
        tx.execute("INSERT INTO vault_key_state (name, version, updated_at) VALUES (?, ?, ?) ON CONFLICT (name) DO UPDATE SET version = ?, updated_at = ?", (name, version, now, version, now))

    def keys_by_kek_version(self, tx):
        """{master version: number of keys still wrapped under it} (destroyed keys hold no wrapped key, so they do not count)."""
        out = {}
        for r in tx.query("SELECT kek_version, COUNT(*) AS n FROM borrower_keys WHERE wrapped_dek IS NOT NULL GROUP BY kek_version ORDER BY kek_version"):
            out[str(r["kek_version"])] = r["n"]
        return out

    def all_keys(self, tx):
        return tx.query("SELECT * FROM borrower_keys WHERE wrapped_dek IS NOT NULL")

    def rewrap_key(self, tx, key_id, wrapped, nonce, version):
        tx.execute("UPDATE borrower_keys SET wrapped_dek = ?, wrap_nonce = ?, kek_version = ? WHERE key_id = ?", (wrapped, nonce, version, key_id))

    # ---- records / mappings / indexes
    def find_by_digests_batch(self, tx, borrower_id, conversation_id, purpose_id, triples, chunk=100):
        """Batch form of find_by_digests: triples = [(pii_type, index_version, digest)] ALL inside ONE borrower / conversation / purpose scope (the scope is fixed by the
        three parameters, so a batch can never reach another borrower's rows; cross-borrower lookups do not exist). Returns {(pii_type, version, digest): row}."""
        found = {}
        triples = list(dict.fromkeys(triples))
        for i in range(0, len(triples), chunk):
            part = triples[i:i + chunk]
            cond = " OR ".join("(bi.pii_type = ? AND bi.index_version = ? AND bi.index_digest = ?)" for _ in part)
            params = [borrower_id, conversation_id, purpose_id] + [x for t in part for x in t]
            for r in tx.query("""SELECT m.token, m.vault_id, m.seq, bi.pii_type, bi.index_version, bi.index_digest, m.expires_at AS m_expires, r.expires_at AS r_expires
                                 FROM blind_indexes bi JOIN vault_records r ON r.vault_id = bi.vault_id JOIN token_mappings m ON m.vault_id = bi.vault_id
                                 WHERE bi.borrower_id = ? AND bi.conversation_id = ? AND bi.purpose_id = ? AND r.status = 'ACTIVE' AND m.status = 'ACTIVE' AND (""" + cond + ")", params):
                found[(r["pii_type"], r["index_version"], r["index_digest"])] = r
        return found

    def next_seqs(self, tx, conversation_id, types):
        """Highest token number in use per PII type for this conversation (0 if none): {type: max_seq}."""
        types = list(dict.fromkeys(types))
        out = {t: 0 for t in types}
        if types:
            for r in tx.query(f"SELECT pii_type, MAX(seq) AS n FROM token_mappings WHERE conversation_id = ? AND pii_type IN ({self._in(types)}) GROUP BY pii_type", [conversation_id] + types):
                out[r["pii_type"]] = r["n"]
        return out

    def insert_bundles(self, tx, bundles, now, expires_at):
        """Batch form of insert_bundle: bundles = [(rec, mapping, index)], three executemany calls inside the caller's transaction."""
        if not bundles:
            return
        tx.executemany("INSERT INTO vault_records (vault_id, borrower_id, conversation_id, token_id, pii_type, ciphertext, nonce, key_id, purpose_id, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
                       [(r["vault_id"], r["borrower_id"], r["conversation_id"], r["token_id"], r["pii_type"], r["ciphertext"], r["nonce"], r["key_id"], r["purpose_id"], now, expires_at) for r, _, _ in bundles])
        tx.executemany("INSERT INTO token_mappings (token_id, vault_id, token, borrower_id, conversation_id, pii_type, seq, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
                       [(m["token_id"], r["vault_id"], m["token"], r["borrower_id"], r["conversation_id"], r["pii_type"], m["seq"], now, expires_at) for r, m, _ in bundles])
        tx.executemany("INSERT INTO blind_indexes (index_id, vault_id, borrower_id, conversation_id, purpose_id, pii_type, index_version, index_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       [(new_id("bi"), r["vault_id"], r["borrower_id"], r["conversation_id"], r["purpose_id"], r["pii_type"], i["version"], i["digest"]) for r, _, i in bundles])

    def lock_conversation(self, tx, conversation_id):
        """Token numbers (<PHONE_3>) are allocated per conversation. Two borrowers writing into the SAME conversation at the same time would compute the same
        next number; PostgreSQL gets a transaction-scoped advisory lock so they queue (SQLite already serialises writers with BEGIN IMMEDIATE)."""
        if tx.kind == "postgres":
            tx.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", ("te03-conv:" + conversation_id,))

    def next_seq(self, tx, conversation_id, pii_type):
        return tx.one("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM token_mappings WHERE conversation_id = ? AND pii_type = ?", (conversation_id, pii_type))["n"]

    def find_by_digests(self, tx, borrower_id, conversation_id, purpose_id, pii_type, versions_digests):
        """versions_digests: [(index_version, digest)]. Only ACTIVE records with ACTIVE mappings count (scope is exact).
        The caller must still check `expires_at` (an expired-but-not-yet-cleaned hit is retired, never reused)."""
        out = []
        for ver, dg in versions_digests:
            r = tx.one("""SELECT m.token, m.vault_id, m.seq, bi.index_id, bi.index_version, m.expires_at AS m_expires, r.expires_at AS r_expires FROM blind_indexes bi
                          JOIN vault_records r ON r.vault_id = bi.vault_id JOIN token_mappings m ON m.vault_id = bi.vault_id
                          WHERE bi.borrower_id = ? AND bi.conversation_id = ? AND bi.purpose_id = ? AND bi.pii_type = ?
                            AND bi.index_version = ? AND bi.index_digest = ? AND r.status = 'ACTIVE' AND m.status = 'ACTIVE'""",
                       (borrower_id, conversation_id, purpose_id, pii_type, ver, dg))
            if r:
                out.append(r)
        return out

    def insert_bundle(self, tx, rec, mapping, index, now=None, expires_at=None):
        """record + mapping + blind index. created_at/expires_at are stamped identically on record and mapping (UTC epoch seconds)."""
        now = time.time() if now is None else now
        tx.execute("INSERT INTO vault_records (vault_id, borrower_id, conversation_id, token_id, pii_type, ciphertext, nonce, key_id, purpose_id, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
                   (rec["vault_id"], rec["borrower_id"], rec["conversation_id"], rec["token_id"], rec["pii_type"], rec["ciphertext"], rec["nonce"], rec["key_id"], rec["purpose_id"], now, expires_at))
        tx.execute("INSERT INTO token_mappings (token_id, vault_id, token, borrower_id, conversation_id, pii_type, seq, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
                   (mapping["token_id"], rec["vault_id"], mapping["token"], rec["borrower_id"], rec["conversation_id"], rec["pii_type"], mapping["seq"], now, expires_at))
        tx.execute("INSERT INTO blind_indexes (index_id, vault_id, borrower_id, conversation_id, purpose_id, pii_type, index_version, index_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (new_id("bi"), rec["vault_id"], rec["borrower_id"], rec["conversation_id"], rec["purpose_id"], rec["pii_type"], index["version"], index["digest"]))

    def mapping_with_record(self, tx, conversation_id, token):
        return tx.one("""SELECT m.token, m.status AS mapping_status, m.expires_at AS mapping_expires_at, r.expires_at AS record_expires_at,
                                m.pii_type, m.borrower_id, m.conversation_id, r.vault_id, r.ciphertext, r.nonce,
                                r.key_id, r.purpose_id, r.status AS record_status, r.created_at
                         FROM token_mappings m JOIN vault_records r ON r.vault_id = m.vault_id WHERE m.conversation_id = ? AND m.token = ?""", (conversation_id, token))

    def record_by_vault_id(self, tx, vault_id):
        return tx.one("""SELECT r.vault_id, r.borrower_id, r.conversation_id, r.pii_type, r.purpose_id, r.status, r.key_id, r.created_at, r.expires_at, m.token, m.status AS mapping_status
                         FROM vault_records r JOIN token_mappings m ON m.vault_id = r.vault_id WHERE r.vault_id = ?""", (vault_id,))

    def records_for(self, tx, borrower_id, conversation_id=None, with_cipher=False):
        cols = "r.vault_id, r.borrower_id, r.conversation_id, r.pii_type, r.purpose_id, r.status, r.key_id, r.created_at, r.expires_at, m.token, m.seq" + (", r.ciphertext, r.nonce" if with_cipher else "")
        sql = f"SELECT {cols} FROM vault_records r JOIN token_mappings m ON m.vault_id = r.vault_id WHERE r.borrower_id = ?"
        params = [borrower_id]
        if conversation_id is not None:
            sql += " AND r.conversation_id = ?"; params.append(conversation_id)
        return tx.query(sql + " ORDER BY r.created_at, m.seq", params)

    def active_records(self, tx):
        return tx.query("SELECT vault_id, borrower_id, conversation_id, pii_type, purpose_id, key_id, ciphertext, nonce FROM vault_records WHERE status = 'ACTIVE'")

    def tokens_for_conversation(self, tx, conversation_id, borrower_id=None):
        if borrower_id is None:
            return [r["token"] for r in tx.query("SELECT token FROM token_mappings WHERE conversation_id = ? AND status = 'ACTIVE' ORDER BY pii_type, seq", (conversation_id,))]
        return [r["token"] for r in tx.query("SELECT token FROM token_mappings WHERE conversation_id = ? AND borrower_id = ? AND status = 'ACTIVE' ORDER BY pii_type, seq", (conversation_id, borrower_id))]

    def borrowers_in_conversation(self, tx, conversation_id):
        return [r["borrower_id"] for r in tx.query("SELECT DISTINCT borrower_id FROM vault_records WHERE conversation_id = ? ORDER BY borrower_id", (conversation_id,))]      # a fixed order: callers lock these borrowers one by one (PostgreSQL), and two callers locking in different orders could deadlock

    def revoke_mapping(self, tx, conversation_id, token):
        r = tx.one("SELECT vault_id FROM token_mappings WHERE conversation_id = ? AND token = ? AND status = 'ACTIVE'", (conversation_id, token))
        if not r:
            return False
        tx.execute("UPDATE token_mappings SET status = 'REVOKED', revoked_at = ? WHERE conversation_id = ? AND token = ?", (time.time(), conversation_id, token))
        tx.execute("DELETE FROM blind_indexes WHERE vault_id = ?", (r["vault_id"],))
        return True

    def owners_with_open_jobs(self, tx, conversation_id):
        rows = tx.query("SELECT DISTINCT borrower_id FROM erasure_jobs WHERE status != 'COMPLETED' AND (conversation_id = ? OR borrower_id = ?)", (conversation_id, f"conv:{conversation_id}"))
        return [r["borrower_id"] for r in rows]

    def update_cipher(self, tx, vault_id, ciphertext, nonce):
        tx.execute("UPDATE vault_records SET ciphertext = ?, nonce = ? WHERE vault_id = ?", (ciphertext, nonce, vault_id))

    def replace_index(self, tx, vault_id, version, digest):
        tx.execute("UPDATE blind_indexes SET index_version = ?, index_digest = ? WHERE vault_id = ?", (version, digest, vault_id))

    def mark_pending(self, tx, borrower_id, conversation_id=None):
        sql, p = "UPDATE vault_records SET status = 'PENDING_ERASURE' WHERE borrower_id = ?", [borrower_id]
        if conversation_id is not None:
            sql += " AND conversation_id = ?"; p.append(conversation_id)
        return tx.execute(sql, p).rowcount

    # ---- shared conversation context (encrypted)
    def get_context(self, tx, borrower_id, conversation_id):
        return tx.one("SELECT * FROM vault_context WHERE borrower_id = ? AND conversation_id = ?", (borrower_id, conversation_id))

    def put_context(self, tx, borrower_id, conversation_id, key_id, ciphertext, nonce, version, now, expires_at):
        tx.execute("""INSERT INTO vault_context (borrower_id, conversation_id, key_id, ciphertext, nonce, version, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                      ON CONFLICT (borrower_id, conversation_id) DO UPDATE SET key_id = ?, ciphertext = ?, nonce = ?, version = ?, updated_at = ?, expires_at = ?""",
                   (borrower_id, conversation_id, key_id, ciphertext, nonce, version, now, expires_at, key_id, ciphertext, nonce, version, now, expires_at))

    def delete_contexts(self, tx, borrower_id, conversation_id=None):
        w, p = "borrower_id = ?", [borrower_id]
        if conversation_id is not None:
            w += " AND conversation_id = ?"; p.append(conversation_id)
        return tx.execute(f"DELETE FROM vault_context WHERE {w}", p).rowcount

    def delete_scope(self, tx, borrower_id, conversation_id=None, operation_id=None, now=None, tombstone_ttl=None):
        """Delete every row of the scope. A tombstone (id + status + time only: no PII, ciphertext, nonce or index) is written for each record first."""
        now = time.time() if now is None else now
        w, p = "borrower_id = ?", [borrower_id]
        if conversation_id is not None:
            w += " AND conversation_id = ?"; p.append(conversation_id)
        tx.execute(f"""INSERT INTO vault_record_tombstones (record_id, deletion_status, deleted_at, operation_id, reason, expires_at)
                       SELECT vault_id, 'DELETED', ?, ?, 'ERASURE', ? FROM vault_records WHERE {w} ON CONFLICT (record_id) DO NOTHING""",
                   [now, operation_id, None if tombstone_ttl is None else now + tombstone_ttl] + p)
        tx.execute(f"DELETE FROM vault_context WHERE {w}", p)                       # the encrypted conversation context goes with the rest (not counted: it is working state, not stored PII records)
        n_i = tx.execute(f"DELETE FROM blind_indexes WHERE {w}", p).rowcount
        n_m = tx.execute(f"DELETE FROM token_mappings WHERE {w}", p).rowcount
        n_r = tx.execute(f"DELETE FROM vault_records WHERE {w}", p).rowcount
        return {"records": n_r, "token_mappings": n_m, "blind_indexes": n_i}

    def count_scope(self, tx, borrower_id):
        return {t: tx.one(f"SELECT COUNT(*) AS n FROM {t} WHERE borrower_id = ?", (borrower_id,))["n"] for t in ("vault_records", "token_mappings", "blind_indexes")}

    # ---- retention / expiry
    def expire_bundle(self, tx, vault_id, now):
        """Retire ONE expired bundle: mapping + record -> EXPIRED, blind index removed (so the same value can get a fresh token)."""
        tx.execute("UPDATE token_mappings SET status = 'EXPIRED' WHERE vault_id = ? AND status = 'ACTIVE'", (vault_id,))
        tx.execute("UPDATE vault_records SET status = 'EXPIRED', expired_at = ? WHERE vault_id = ? AND status = 'ACTIVE'", (now, vault_id))
        tx.execute("DELETE FROM blind_indexes WHERE vault_id = ?", (vault_id,))

    def due_for_expiry(self, tx, now, limit):
        """ACTIVE records whose expiry has passed, for borrowers that are ACTIVE (records under erasure are PENDING_ERASURE and are skipped)."""
        lock = " FOR UPDATE OF r SKIP LOCKED" if tx.kind == "postgres" else ""
        return [r["vault_id"] for r in tx.query(
            "SELECT r.vault_id FROM vault_records r JOIN borrower_state b ON b.borrower_id = r.borrower_id "
            "WHERE r.status = 'ACTIVE' AND r.expires_at IS NOT NULL AND r.expires_at <= ? AND b.state = 'ACTIVE' ORDER BY r.expires_at LIMIT ?" + lock, (now, limit))]

    def due_for_deletion(self, tx, now, grace, limit):
        lock = " FOR UPDATE OF r SKIP LOCKED" if tx.kind == "postgres" else ""
        return tx.query(
            "SELECT r.vault_id, r.borrower_id, r.key_id FROM vault_records r JOIN borrower_state b ON b.borrower_id = r.borrower_id "
            "WHERE r.status = 'EXPIRED' AND r.expires_at IS NOT NULL AND r.expires_at + ? <= ? AND b.state = 'ACTIVE' ORDER BY r.expires_at LIMIT ?" + lock, (grace, now, limit))

    @staticmethod
    def _in(ids):
        return ",".join("?" for _ in ids)

    def mark_expired(self, tx, vault_ids, now):
        if not vault_ids:
            return 0
        q = self._in(vault_ids)
        tx.execute(f"UPDATE token_mappings SET status = 'EXPIRED' WHERE status = 'ACTIVE' AND vault_id IN ({q})", vault_ids)
        n = tx.execute(f"UPDATE vault_records SET status = 'EXPIRED', expired_at = ? WHERE status = 'ACTIVE' AND vault_id IN ({q})", [now] + list(vault_ids)).rowcount
        tx.execute(f"DELETE FROM blind_indexes WHERE vault_id IN ({q})", vault_ids)
        return n

    def delete_expired_records(self, tx, vault_ids, now, operation_id, tombstone_ttl):
        """Hard-delete expired records (ciphertext gone). The tombstone is written in the SAME transaction; mapping rows stay as EXPIRED (they hold no PII
        and keep the token number from being handed out again). Blind indexes were already removed when the record expired."""
        if not vault_ids:
            return 0
        q = self._in(vault_ids)
        tx.execute(f"""INSERT INTO vault_record_tombstones (record_id, deletion_status, deleted_at, operation_id, reason, expires_at)
                       SELECT vault_id, 'EXPIRED_DELETED', ?, ?, 'RETENTION', ? FROM vault_records WHERE status = 'EXPIRED' AND vault_id IN ({q}) ON CONFLICT (record_id) DO NOTHING""",
                   [now, operation_id, None if tombstone_ttl is None else now + tombstone_ttl] + list(vault_ids))
        tx.execute(f"DELETE FROM blind_indexes WHERE vault_id IN ({q})", vault_ids)
        return tx.execute(f"DELETE FROM vault_records WHERE status = 'EXPIRED' AND vault_id IN ({q})", vault_ids).rowcount

    def key_in_use(self, tx, key_id):
        """True if ANY record (whatever its status) still depends on this DEK."""
        return (tx.one("SELECT COUNT(*) AS n FROM vault_records WHERE key_id = ?", (key_id,))["n"]
                + tx.one("SELECT COUNT(*) AS n FROM vault_context WHERE key_id = ?", (key_id,))["n"]) > 0

    def retire_key(self, tx, key_id, now, reason):
        return tx.execute("UPDATE borrower_keys SET key_status = 'DESTROYED', wrapped_dek = NULL, wrap_nonce = NULL, destroyed_at = ?, destroy_reason = ? WHERE key_id = ? AND key_status = 'ACTIVE'",
                          (now, reason, key_id)).rowcount

    def purge_expired_metadata(self, tx, now, ops_ttl=None, limit=1000):
        out = {"tombstones": 0, "operations": 0, "idempotency": 0, "contexts": 0}
        out["contexts"] = tx.execute("DELETE FROM vault_context WHERE expires_at <= ?", (now,)).rowcount
        ids = [r["record_id"] for r in tx.query("SELECT record_id FROM vault_record_tombstones WHERE expires_at IS NOT NULL AND expires_at <= ? LIMIT ?", (now, limit))]
        if ids:
            out["tombstones"] = tx.execute(f"DELETE FROM vault_record_tombstones WHERE record_id IN ({self._in(ids)})", ids).rowcount
        if ops_ttl is not None:
            ids = [r["operation_id"] for r in tx.query("SELECT operation_id FROM vault_operations WHERE timestamp <= ? LIMIT ?", (now - ops_ttl, limit))]
            if ids:
                out["operations"] = tx.execute(f"DELETE FROM vault_operations WHERE operation_id IN ({self._in(ids)})", ids).rowcount
        out["idempotency"] = tx.execute("DELETE FROM vault_idempotency WHERE expires_at <= ?", (now,)).rowcount
        return out

    def live_vault_ids(self, tx, ids, now):
        """Which of these vault ids still have an ACTIVE, unexpired mapping AND record?"""
        if not ids:
            return set()
        rows = tx.query(f"SELECT m.vault_id FROM token_mappings m JOIN vault_records r ON r.vault_id = m.vault_id WHERE m.vault_id IN ({self._in(ids)}) "
                        "AND m.status = 'ACTIVE' AND r.status = 'ACTIVE' AND (m.expires_at IS NULL OR m.expires_at > ?) AND (r.expires_at IS NULL OR r.expires_at > ?)", list(ids) + [now, now])
        return {r["vault_id"] for r in rows}

    def open_borrower_jobs(self, tx, borrower_id, exclude_job=None):
        """Is a BORROWER-scope erasure job for this borrower still unfinished (pending / in progress / partially failed)? It alone may move the borrower to ERASED."""
        return tx.one("SELECT COUNT(*) AS n FROM erasure_jobs WHERE borrower_id = ? AND scope = 'BORROWER' AND status <> 'COMPLETED' AND job_id <> ?", (borrower_id, exclude_job or ""))["n"] > 0

    def job_statuses(self, tx, borrower_id, conversation_id):
        return {r["status"] for r in tx.query("SELECT status FROM erasure_jobs WHERE borrower_id = ? AND status <> 'COMPLETED' AND (scope = 'BORROWER' OR conversation_id = ?)", (borrower_id, conversation_id))}

    def tombstone(self, tx, record_id):
        return tx.one("SELECT * FROM vault_record_tombstones WHERE record_id = ?", (record_id,))

    # ---- operations log (safe metadata only) and erasure jobs
    def log_op(self, tx, op_type, borrower_id, request_id, caller, status, vault_id=None, error_code=None):
        tx.execute("INSERT INTO vault_operations (operation_id, operation_type, borrower_id, vault_id, request_id, caller, status, timestamp, error_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (new_id("op"), op_type, borrower_id, vault_id, request_id, caller, status, time.time(), error_code))

    def operations(self, tx, borrower_id=None):
        if borrower_id is None:
            return tx.query("SELECT * FROM vault_operations ORDER BY timestamp")
        return tx.query("SELECT * FROM vault_operations WHERE borrower_id = ? ORDER BY timestamp", (borrower_id,))

    def get_job(self, tx, job_id):
        return tx.one("SELECT * FROM erasure_jobs WHERE job_id = ?", (job_id,))

    def upsert_job(self, tx, job_id, borrower_id, scope, conversation_id, status, counts=None, error_code=None):
        now = time.time()
        tx.execute("""INSERT INTO erasure_jobs (job_id, borrower_id, scope, conversation_id, status, counts, error_code, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                      ON CONFLICT (job_id) DO UPDATE SET status = ?, counts = ?, error_code = ?, updated_at = ?""",
                   (job_id, borrower_id, scope, conversation_id, status, json.dumps(counts or {}), error_code, now, now, status, json.dumps(counts or {}), error_code, now))
