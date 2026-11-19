"""Schema migrations. A fresh database is created straight at the current version; an existing v1 database is upgraded in place.
The upgrade is additive (new columns, new tables) plus WIDENING two CHECK constraints (status gains EXPIRED). It runs in ONE
transaction, so a failure leaves the old schema untouched. It never touches data other than adding defaults."""
import re

from ..core.errors import VaultUnavailable
from .models import SCHEMA_VERSION, V2_NEW_COLUMNS, V2_WIDENED_CHECKS, TABLES


def _columns(tx, table):
    if tx.kind == "sqlite":
        return {r["name"] for r in tx.query(f"PRAGMA table_info({table})")}
    return {r["column_name"] for r in tx.query("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?", (table,))}


def _table_exists(tx, table):
    if tx.kind == "sqlite":
        return tx.one("SELECT 1 AS x FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)) is not None
    return tx.one("SELECT 1 AS x FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?", (table,)) is not None


def current_version(tx):
    if not _table_exists(tx, "vault_meta"):
        return 0 if not _table_exists(tx, "vault_records") else 1          # tables without a meta row are the original v1 layout
    r = tx.one("SELECT value FROM vault_meta WHERE key = 'schema_version'")
    return int(r["value"]) if r else (1 if _table_exists(tx, "vault_records") else 0)


def _widen_check_sqlite(tx, table, ddl_sql):
    """SQLite cannot alter a CHECK: rebuild the table (rename old, create new, copy, drop old)."""
    old_cols = [r["name"] for r in tx.query(f"PRAGMA table_info({table})")]
    tx.execute(f"ALTER TABLE {table} RENAME TO {table}__old")
    tx.execute(ddl_sql)
    new_cols = [r["name"] for r in tx.query(f"PRAGMA table_info({table})")]
    common = ", ".join(c for c in old_cols if c in new_cols)
    tx.execute(f"INSERT INTO {table} ({common}) SELECT {common} FROM {table}__old")
    tx.execute(f"DROP TABLE {table}__old")


def migrate(tx, blob, real):
    """Bring the schema to SCHEMA_VERSION inside the caller's transaction. Returns (from_version, to_version)."""
    start = current_version(tx)
    if start > SCHEMA_VERSION:
        raise VaultUnavailable("schema_newer_than_this_code")      # never re-stamp a database written by NEWER code as if it were ours (a later strict open would then pass)
    sub = lambda s: s.replace("{BLOB}", blob).replace("{REAL}", real)
    if start == 0:
        for ddl in TABLES:
            tx.execute(sub(ddl))
    else:
        if start < 2:
            for table, col, ddl in V2_NEW_COLUMNS:
                if col not in _columns(tx, table):
                    tx.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sub(ddl)}")
            for ddl in TABLES:                                        # new tables (CREATE ... IF NOT EXISTS leaves existing ones alone)
                tx.execute(sub(ddl))
            for table, cname, check, marker in V2_WIDENED_CHECKS:
                if tx.kind == "postgres":
                    tx.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {cname}")
                    tx.execute(f"ALTER TABLE {table} ADD CONSTRAINT {cname} CHECK ({check})")
                else:
                    sql = tx.one("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))["sql"]
                    if marker not in sql:
                        ddl = next(sub(d) for d in TABLES if re.search(rf"CREATE TABLE IF NOT EXISTS {table}\b", d))
                        _widen_check_sqlite(tx, table, ddl.replace("IF NOT EXISTS ", ""))
    if 0 < start < 4:                                         # v3 and v4 added tables only (CREATE ... IF NOT EXISTS leaves existing ones alone)
        for ddl in TABLES:
            tx.execute(sub(ddl))
    tx.execute("INSERT INTO vault_meta (key, value) VALUES ('schema_version', ?) ON CONFLICT (key) DO UPDATE SET value = ?", (str(SCHEMA_VERSION), str(SCHEMA_VERSION)))
    return start, SCHEMA_VERSION
