"""Encrypted database backups and verified restores.

WHY: a database backup contains everything the vault stores. Ciphertext and wrapped keys are already encrypted, but a plain backup also holds token maps, blind-index
digests, borrower ids, key metadata and the operations log - and, if the master secret ever leaks, a plain backup is the whole vault. So backups are ENCRYPTED AGAIN,
with a key that is NOT the vault master secret, before they leave the database host.

FORMAT (one file)  MAGIC | header (JSON, authenticated, not secret) | chunks.   Every chunk is AES-256-GCM under a per-file key
  file key = HKDF-SHA256(backup key, salt = random 16 bytes in the header, info = "te03-vault/backup/file")  -> a fresh key for every backup, so nonces never repeat under one key
  nonce    = 4 zero bytes || chunk index (8 bytes)            AAD = header || chunk index || final flag
  Reordering, dropping, truncating, or editing any chunk or the header fails authentication; a file without the final chunk is rejected as truncated.
KEY: VAULT_BACKUP_KEY (hex/base64, >= 32 bytes), held by whoever runs backups/restores - NOT the vault. In production keep it in the same managed key store as the master
secret (see RUNBOOK.md) but as a different key.
WHAT A BACKUP DOES NOT DO: a restored backup is the state at backup time. Erasures done AFTER the backup are UNDONE by restoring it: replay them (RUNBOOK.md, 'Restoring a backup').
Tested: PostgreSQL (pg_dump custom format -> encrypt -> decrypt -> pg_restore into another database/cluster) and SQLite (online backup API)."""
import base64
import glob
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config.settings import decode_secret
from ..core.errors import ValidationFailed, VaultError

MAGIC = b"TE03BK1\n"
CHUNK = 1 << 20


class BackupError(VaultError):
    code = "backup_failed"


def backup_key(env=None) -> bytes:
    env = os.environ if env is None else env
    if not env.get("VAULT_BACKUP_KEY"):
        raise BackupError("backup_key_missing")                       # no key, no backup: an unencrypted backup is never written
    return decode_secret(env["VAULT_BACKUP_KEY"])


def _file_key(key: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"te03-vault/backup/file").derive(key)


def _nonce(i: int) -> bytes:
    return b"\x00" * 4 + i.to_bytes(8, "big")


def encrypt_stream(src, dst, key: bytes, meta: dict = None):
    """Read plaintext from `src`, write the encrypted backup to `dst`. Returns (plaintext_bytes, sha256 of the ciphertext file)."""
    if len(key) < 32:
        raise BackupError("backup_key_too_short")
    salt = os.urandom(16)
    header = json.dumps({"v": 1, "created": time.time(), "salt": salt.hex(), "chunk": CHUNK, **(meta or {})}, sort_keys=True).encode()
    fk, aes, h, total = None, None, hashlib.sha256(), 0
    fk = _file_key(key, salt); aes = AESGCM(fk)
    out = MAGIC + len(header).to_bytes(4, "big") + header
    dst.write(out); h.update(out)
    i, buf = 0, src.read(CHUNK)
    while True:
        nxt = src.read(CHUNK) if buf else b""
        final = 1 if not nxt else 0
        ct = aes.encrypt(_nonce(i), buf, header + i.to_bytes(8, "big") + bytes([final]))
        rec = bytes([final]) + len(ct).to_bytes(4, "big") + ct
        dst.write(rec); h.update(rec); total += len(buf); i += 1
        if final:
            return total, h.hexdigest()
        buf = nxt


def decrypt_stream(src, dst, key: bytes) -> dict:
    """Authenticate and decrypt a backup. Raises BackupError on ANY problem (wrong key, tampering, truncation, wrong file); returns the header."""
    try:
        if src.read(len(MAGIC)) != MAGIC:
            raise BackupError("not_a_backup_file")
        n = int.from_bytes(src.read(4), "big")
        if not 0 < n < 65536:
            raise BackupError("bad_backup_header")
        header = src.read(n)
        meta = json.loads(header)
        aes = AESGCM(_file_key(key, bytes.fromhex(meta["salt"])))
        i = 0
        while True:
            head = src.read(5)
            if len(head) < 5:
                raise BackupError("backup_truncated")
            final, ln = head[0], int.from_bytes(head[1:], "big")
            if final not in (0, 1) or ln > meta["chunk"] + 16 or ln < 16:
                raise BackupError("bad_backup_chunk")
            ct = src.read(ln)
            if len(ct) != ln:
                raise BackupError("backup_truncated")
            dst.write(aes.decrypt(_nonce(i), ct, header + i.to_bytes(8, "big") + bytes([final])))
            i += 1
            if final:
                if src.read(1):
                    raise BackupError("trailing_data_after_backup")
                return meta
    except BackupError:
        raise
    except (InvalidTag, ValueError, KeyError):
        raise BackupError("backup_authentication_failed")          # wrong key, edited, or reordered: one generic answer


# ---------------------------------------------------------------- PostgreSQL
def _pg_bin(tool):
    p = shutil.which(tool) or next(iter(sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{tool}"), reverse=True)), None) or os.path.join(os.environ.get("PG_BIN", ""), tool)
    if not p or not os.path.exists(p):
        raise BackupError(f"{tool}_not_found")
    return p


def split_url(url):
    """Vault URL -> (libpq URL without the vault-only parameters, schema)."""
    sp = urlsplit(url); q = dict(parse_qsl(sp.query)); schema = q.pop("schema", None)
    for k in ("pool_min", "pool_max", "pool_timeout"):
        q.pop(k, None)
    return urlunsplit(sp._replace(query=urlencode(q))), schema


def backup_postgres(db_url, out_path, key, schema=None):
    """pg_dump (custom format) of the vault schema, piped straight into the encryptor: the plaintext dump never touches the disk."""
    libpq, url_schema = split_url(db_url); schema = schema or url_schema
    if not schema:
        raise ValidationFailed("schema_required")
    cmd = [_pg_bin("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", f"--schema={schema}", f"--dbname={libpq}"]
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as dst:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            n, digest = encrypt_stream(p.stdout, dst, key, {"kind": "postgres", "schema": schema})
            err = p.stderr.read(); rc = p.wait()
        if rc != 0 or n == 0:
            raise BackupError("pg_dump_failed")
    except BaseException:
        if os.path.exists(out_path):
            os.remove(out_path)                                        # never leave a partial backup behind
        raise
    return {"bytes_plain": n, "sha256": digest, "schema": schema}


def restore_postgres(in_path, target_url, key, schema=None, clean=False):
    """Decrypt + authenticate the whole file FIRST (into a private temp file), only then run pg_restore. A tampered file never reaches the database."""
    libpq, url_schema = split_url(target_url)
    fd, tmp = tempfile.mkstemp(prefix="te03-restore-", suffix=".dump"); os.fchmod(fd, 0o600)
    try:
        with os.fdopen(fd, "wb") as plain, open(in_path, "rb") as src:
            meta = decrypt_stream(src, plain, key)
        if meta.get("kind") != "postgres":
            raise BackupError("not_a_postgres_backup")
        cmd = [_pg_bin("pg_restore"), "--no-owner", "--no-privileges", f"--dbname={libpq}"] + (["--clean", "--if-exists"] if clean else []) + [tmp]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            raise BackupError("pg_restore_failed")
        return meta
    finally:
        try:
            with open(tmp, "r+b") as f:                                # best-effort: overwrite before unlinking (does not defeat SSD wear levelling / journaling)
                f.write(b"\x00" * os.path.getsize(tmp))
        except OSError:
            pass
        os.remove(tmp)


# ---------------------------------------------------------------- SQLite
def backup_sqlite(db_path, out_path, key):
    """Consistent online copy via SQLite's backup API (safe while the vault is running), encrypted straight to the output file."""
    fd, tmp = tempfile.mkstemp(prefix="te03-sqlite-", suffix=".db"); os.close(fd); os.chmod(tmp, 0o600)
    try:
        src = sqlite3.connect(db_path); dst = sqlite3.connect(tmp); src.backup(dst); dst.close(); src.close()
        with open(tmp, "rb") as plain, os.fdopen(os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as out:
            n, digest = encrypt_stream(plain, out, key, {"kind": "sqlite"})
        return {"bytes_plain": n, "sha256": digest}
    finally:
        os.remove(tmp)


def restore_sqlite(in_path, dest_path, key):
    if os.path.exists(dest_path):
        raise BackupError("destination_exists")                         # a restore never silently overwrites a live database
    fd, tmp = tempfile.mkstemp(prefix="te03-sqlite-r-", suffix=".db"); os.chmod(tmp, 0o600)
    try:
        with os.fdopen(fd, "wb") as plain, open(in_path, "rb") as src:
            meta = decrypt_stream(src, plain, key)
        if meta.get("kind") != "sqlite":
            raise BackupError("not_a_sqlite_backup")
        chk = sqlite3.connect(tmp); ok = chk.execute("PRAGMA integrity_check").fetchone()[0]; chk.close()
        if ok != "ok":
            raise BackupError("restored_database_corrupt")
        shutil.move(tmp, dest_path); tmp = None
        return meta
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def main(argv=None):
    """python -m layer2_vault.ops.backup backup  <db-url> <out-file>      |     restore <in-file> <target-db-url | sqlite-path>        (key: VAULT_BACKUP_KEY)"""
    a = sys.argv[1:] if argv is None else argv
    if len(a) != 3 or a[0] not in ("backup", "restore"):
        print(main.__doc__); return 2
    try:
        key = backup_key()
        if a[0] == "backup":
            info = backup_sqlite(a[1][len("sqlite:///"):], a[2], key) if a[1].startswith("sqlite:///") else backup_postgres(a[1], a[2], key)
        else:
            info = restore_postgres(a[1], a[2], key) if a[2].startswith(("postgres://", "postgresql://")) else restore_sqlite(a[1], a[2], key)
        print(json.dumps({k: v for k, v in info.items() if k in ("kind", "schema", "bytes_plain", "sha256", "created")})); return 0
    except VaultError as e:
        print(f"failed: {e.code}"); return 1


if __name__ == "__main__":
    sys.exit(main())
