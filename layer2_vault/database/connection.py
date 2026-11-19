"""Database access with safe transactions.

SQLite (stdlib) : one connection guarded by a lock, explicit BEGIN IMMEDIATE, so writes are
serialised and a unique-constraint race surfaces as ConflictError for the caller to retry.
PostgreSQL uses psycopg 3 with a CONNECTION POOL (psycopg_pool): one pool per Database object (= one per vault process), min/max size and acquire timeout
configurable (URL query: pool_min, pool_max, pool_timeout), connections health-checked when handed out and recycled (max lifetime / idle), no process-wide lock,
and every transaction rolls back on any exception before its connection returns to the pool. If the pool cannot give a connection in time the caller gets
VaultUnavailable('database_unavailable') (fail closed), never a hang. Same interface as SQLite. The PostgreSQL path is tested against a local server (see tests/test_postgres.py).
All SQL is parameterised ('?' placeholders; rewritten to %s for PostgreSQL). No string formatting of values into SQL."""
import re
import sqlite3
import threading
import weakref
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from contextlib import contextmanager

from ..core.errors import VaultUnavailable
from .models import INDEXES, TRIGGERS
from .migrations import migrate


class ConflictError(Exception):
    """A uniqueness constraint was violated (typically two concurrent requests for the same value)."""


class Tx:
    def __init__(self, conn, kind):
        self.conn, self.kind = conn, kind

    def _sql(self, sql):
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    def execute(self, sql, params=()):
        try:
            return self.conn.execute(self._sql(sql), tuple(params))
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):          # ONLY a uniqueness race is retryable; NOT NULL / CHECK violations are bugs and must surface
                raise ConflictError("unique")
            raise
        except Exception as e:
            if type(e).__name__ == "UniqueViolation":          # psycopg: SQLSTATE 23505
                raise ConflictError("unique")
            raise

    def executemany(self, sql, seq):
        seq = [tuple(p) for p in seq]
        if not seq:
            return
        try:
            if self.kind == "postgres":
                with self.conn.cursor() as cur:                       # psycopg: executemany lives on the cursor (and runs as one pipelined batch)
                    cur.executemany(self._sql(sql), seq)
            else:
                self.conn.executemany(self._sql(sql), seq)
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                raise ConflictError("unique")
            raise
        except Exception as e:
            if type(e).__name__ == "UniqueViolation":
                raise ConflictError("unique")
            raise

    @staticmethod
    def _row(row):
        if row is None:
            return None
        d = dict(row)
        return {k: (bytes(v) if isinstance(v, memoryview) else v) for k, v in d.items()}

    def query(self, sql, params=()):
        return [self._row(r) for r in self.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        return self._row(self.execute(sql, params).fetchone())


class Database:
    def __init__(self, url="sqlite:///:memory:", migrate=True):
        """migrate=False: the running vault connects as a role that may NOT change the schema (vault_app): it only checks the schema version and never issues DDL.
        Migrations then run separately, as vault_migration:  python -m layer2_vault.database.migrate"""
        self.url, self.auto_migrate = url, migrate
        self._lock = threading.RLock()
        if url.startswith("sqlite:///"):
            self.kind = "sqlite"
            path = url[len("sqlite:///"):] or ":memory:"
            self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA secure_delete=ON")         # freed / overwritten content (a destroyed wrapped key, an erased ciphertext) is zero-filled, not just unlinked
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
        elif url.startswith(("postgresql://", "postgres://")):
            self.kind = "postgres"
            self._conn = None
            sp = urlsplit(url)
            q = dict(parse_qsl(sp.query))
            self._schema = q.pop("schema", None)                  # optional: run inside one PostgreSQL schema (used to isolate tests)
            if self._schema is not None and not re.fullmatch(r"[A-Za-z0-9_]{1,63}", self._schema):
                raise VaultUnavailable("bad_schema_name")
            try:
                self.pool_min, self.pool_max, self.pool_timeout = int(q.pop("pool_min", 1)), int(q.pop("pool_max", 10)), float(q.pop("pool_timeout", 5))
            except ValueError:
                raise VaultUnavailable("bad_pool_settings")
            if not (0 <= self.pool_min <= self.pool_max and self.pool_max >= 1 and self.pool_timeout > 0):
                raise VaultUnavailable("bad_pool_settings")
            self._pgurl = urlunsplit(sp._replace(query=urlencode(q)))
            self._pool = None
        else:
            raise VaultUnavailable("unsupported_database_url")
        self.create_schema()
        if self.kind == "postgres":
            self._open_pool()

    def _open_pool(self):
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        kw = {"row_factory": dict_row, "connect_timeout": 5}
        if self._schema:
            kw["options"] = f"-csearch_path={self._schema}"
        self._pool = ConnectionPool(self._pgurl, min_size=self.pool_min, max_size=self.pool_max, timeout=self.pool_timeout, kwargs=kw, open=False,
                                    check=ConnectionPool.check_connection, max_lifetime=1800, max_idle=300, name="te03-vault")
        self._pool.open()
        weakref.finalize(self, self._pool.close)                  # connections are returned even if nobody calls close()
        if self.pool_min > 0:
            try:
                self._pool.wait(timeout=self.pool_timeout)
            except Exception:
                raise VaultUnavailable("database_unavailable")

    @contextmanager
    def transaction(self):
        if self.kind == "sqlite":
            try:
                with self._lock:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        yield Tx(self._conn, "sqlite")
                        self._conn.execute("COMMIT")
                    except BaseException:
                        self._conn.execute("ROLLBACK")
                        raise
            except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                raise VaultUnavailable("database_unavailable") from e
            return
        import psycopg
        import psycopg_pool
        down = (psycopg.OperationalError, psycopg.InterfaceError, psycopg_pool.PoolClosed, psycopg_pool.PoolTimeout)
        try:
            with self._pool.connection() as conn:               # commits on a clean exit, ROLLS BACK on any exception, then returns the connection to the pool
                yield Tx(conn, "postgres")
        except down as e:
            raise VaultUnavailable("database_unavailable") from e

    @contextmanager
    def _setup_tx(self):
        """Schema setup runs before the pool exists: PostgreSQL uses one short-lived connection (all DDL in one transaction)."""
        if self.kind == "sqlite":
            with self.transaction() as tx:
                yield tx
            return
        import psycopg
        from psycopg.rows import dict_row
        opts = {"options": f"-csearch_path={self._schema}"} if self._schema else {}
        try:
            with psycopg.connect(self._pgurl, row_factory=dict_row, connect_timeout=5, **opts) as conn:
                yield Tx(conn, "postgres")
        except (psycopg.OperationalError, psycopg.InterfaceError) as e:
            raise VaultUnavailable("database_unavailable") from e

    def create_schema(self):
        if not self.auto_migrate:
            with self._setup_tx() as tx:
                from .migrations import current_version
                from .models import SCHEMA_VERSION
                found = current_version(tx)
            if found != SCHEMA_VERSION:
                raise VaultUnavailable("schema_version_mismatch")      # run the migration first, as the migration role
            self.migrated_from = found
            return
        if self.kind == "postgres" and self._schema:
            import psycopg
            try:
                with psycopg.connect(self._pgurl, connect_timeout=5) as conn:
                    # check first: CREATE SCHEMA IF NOT EXISTS needs database-level CREATE privilege even when the schema exists (the migration role may lack it)
                    if conn.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (self._schema,)).fetchone() is None:
                        conn.execute(f'CREATE SCHEMA "{self._schema}"')
            except psycopg.OperationalError as e:
                raise VaultUnavailable("database_unavailable") from e
        blob, real = ("BLOB", "REAL") if self.kind == "sqlite" else ("BYTEA", "DOUBLE PRECISION")
        with self._setup_tx() as tx:
            self.migrated_from, _ = migrate(tx, blob, real)          # fresh database -> create v2; v1 -> upgrade in place (one transaction)
            for ddl in INDEXES + TRIGGERS[self.kind]:
                tx.execute(ddl)

    def pool_stats(self) -> dict:
        return self._pool.get_stats() if self.kind == "postgres" and self._pool else {}

    def role_info(self) -> dict:
        """Which database role is the vault using, and is it dangerously privileged? (PostgreSQL only.)"""
        if self.kind != "postgres":
            return {"role": None, "superuser": False, "createdb": False, "createrole": False}
        with self.transaction() as tx:
            r = tx.one("SELECT current_user AS role, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user")
        return {"role": r["role"], "superuser": bool(r["rolsuper"]), "createdb": bool(r["rolcreatedb"]), "createrole": bool(r["rolcreaterole"])}

    def ping(self) -> bool:
        try:
            with self.transaction() as tx:
                tx.one("SELECT 1 AS ok")
            return True
        except Exception:
            return False

    def scrub(self):
        """Make the database FILES match what was just erased / overwritten. Call after a commit that destroyed or replaced key material or ciphertext.
        SQLite: secure_delete (set at open) zero-fills the freed content in the pages, but the write-ahead log still holds the earlier page images until a checkpoint copies the new
        pages over the old ones and empties the log; this runs that checkpoint (TRUNCATE). Returns True when it completed, False when a reader blocked it (it then happens at the next
        checkpoint). PostgreSQL: returns None - nothing here can do it (dead row versions stay until VACUUM; old page images stay in the WAL segments until they are recycled or
        archived): see RESIDUAL_RISK in services/key_lifecycle.py and RUNBOOK.md."""
        if self.kind != "sqlite" or self._conn is None:
            return None
        if self.url.rstrip("/") in ("sqlite://", "sqlite:///:memory:", "sqlite:///"):
            return True                                              # in-memory: nothing on disk
        try:
            with self._lock:                                         # never between another thread's BEGIN and COMMIT on this connection
                busy = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
            return busy == 0
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return False

    def close(self):
        if self.kind == "sqlite" and self._conn is not None:
            self._conn.close()
        elif self.kind == "postgres" and self._pool is not None:
            self._pool.close()
