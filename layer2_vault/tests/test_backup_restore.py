"""Phase 6: encrypted backups, verified restores, and what a restore does and does not undo."""
import os
import shutil
import sqlite3
import tempfile
import uuid
from urllib.parse import urlsplit, urlunsplit

from layer2_vault.config.settings import Settings
from layer2_vault.ops import backup as B
from layer2_vault.schemas.vault_schemas import EraseRequest, RotateRequest
from layer2_vault.services import build_services
from layer2_vault.tests._util import ER, GW, KO, Skip, expect, make, need_postgres, reh_req, run, tok_req, who

PH, PH2 = "9845012345", "9811122233"
KEY = os.urandom(32)


def roundtrip(data, key=KEY, chunk=None):
    import io
    old = B.CHUNK
    if chunk: B.CHUNK = chunk
    try:
        src, enc_ = io.BytesIO(data), io.BytesIO(); n, digest = B.encrypt_stream(src, enc_, key, {"kind": "test"}); blob = enc_.getvalue()
    finally:
        B.CHUNK = old
    return blob, n, digest


def decrypt(blob, key=KEY):
    import io
    out = io.BytesIO(); meta = B.decrypt_stream(io.BytesIO(blob), out, key); return out.getvalue(), meta


def chunks(blob):
    """(header_end, [record bytes...]) of an encrypted backup."""
    hl = int.from_bytes(blob[8:12], "big"); pos = 12 + hl; recs = []
    while pos < len(blob):
        ln = int.from_bytes(blob[pos + 1:pos + 5], "big"); recs.append(blob[pos:pos + 5 + ln]); pos += 5 + ln
    return 12 + hl, recs


# ---------------------------------------------------------------- the encryption format
def test_a_backup_round_trips_across_many_chunks_and_hides_its_content():
    data = (b"SECRET-PHONE-9845012345 " * 400)
    blob, n, digest = roundtrip(data, chunk=1000)
    assert n == len(data) and len(chunks(blob)[1]) > 8 and decrypt(blob)[0] == data
    assert b"SECRET" not in blob and b"9845012345" not in blob and blob.startswith(B.MAGIC)
    assert roundtrip(b"")[1] == 0 and decrypt(roundtrip(b"")[0])[0] == b""
    blob2, _, _ = roundtrip(data, chunk=1000); assert blob2 != blob, "a fresh salt per backup: the same data never encrypts the same way twice"


def test_wrong_key_tampering_truncation_reordering_and_junk_are_all_rejected():
    data = os.urandom(5000); blob, _, _ = roundtrip(data, chunk=1000); start, recs = chunks(blob)
    bad = lambda b, key=KEY: expect(B.BackupError, lambda: decrypt(b, key))
    bad(blob, os.urandom(32))                                                                   # wrong key
    for pos in (0, 9, 20, start + 6, start + 100, len(blob) - 1):                              # magic, header, chunk bodies, the last tag byte
        t = bytearray(blob); t[pos] ^= 1; bad(bytes(t))
    bad(blob[:start + sum(len(r) for r in recs[:-1])])                                         # last chunk dropped at a chunk boundary -> truncated
    bad(blob[:-5]); bad(blob[:start + 3]); bad(blob[:10])
    swapped = blob[:start] + recs[1] + recs[0] + b"".join(recs[2:]); bad(swapped)             # reordered chunks
    bad(blob[:start] + recs[0] + recs[0] + b"".join(recs[1:]))                                  # duplicated chunk
    bad(blob + b"x"); bad(b"not a backup at all"); bad(b"")
    flipped_final = bytearray(blob); flipped_final[start] = 1; bad(bytes(flipped_final))        # claiming 'final' early
    e = expect(B.BackupError, lambda: decrypt(blob, os.urandom(32))); assert e.code == "backup_authentication_failed"
    expect(B.BackupError, lambda: roundtrip(b"x", key=b"short"), "backup_key_too_short")


def test_no_key_means_no_backup_and_the_command_line_says_so_without_writing_a_file():
    expect(B.BackupError, lambda: B.backup_key({}), "backup_key_missing")
    assert B.backup_key({"VAULT_BACKUP_KEY": KEY.hex()}) == KEY
    with tempfile.TemporaryDirectory() as d:
        old = os.environ.pop("VAULT_BACKUP_KEY", None)
        try: assert B.main(["backup", f"sqlite:///{d}/x.db", f"{d}/out.bak"]) == 1 and not os.path.exists(f"{d}/out.bak")
        finally:
            if old: os.environ["VAULT_BACKUP_KEY"] = old


# ---------------------------------------------------------------- SQLite
def test_sqlite_backup_is_encrypted_restores_exactly_and_never_overwrites():
    with tempfile.TemporaryDirectory() as d:
        s, v = make(db_url=f"sqlite:///{d}/live.db"); g = who(s, GW)
        v.tokenizer.tokenize(tok_req(f"call {PH}", [("PHONE", PH)]), g)
        info = B.backup_sqlite(f"{d}/live.db", f"{d}/b.bak", KEY)                                # taken while the vault is open and running
        blob = open(f"{d}/b.bak", "rb").read()
        assert oct(os.stat(f"{d}/b.bak").st_mode & 0o777) == "0o600" and b"SQLite format" not in blob and b"vault_records" not in blob and PH.encode() not in blob and info["sha256"]
        meta = B.restore_sqlite(f"{d}/b.bak", f"{d}/restored.db", KEY); assert meta["kind"] == "sqlite"
        s2 = Settings.for_testing(master_secrets=s.master_secrets, db_url=f"sqlite:///{d}/restored.db", registry=s.registry, rehydrate_policy=s.rehydrate_policy); v2 = build_services(s2)
        assert v2.rehydration.rehydrate(reh_req(["<PHONE_1>"]), who(s2, GW)).values == {"<PHONE_1>": PH}, "the restored vault opens with the same master secret and serves the same data"
        expect(B.BackupError, lambda: B.restore_sqlite(f"{d}/b.bak", f"{d}/restored.db", KEY), "destination_exists")
        t = bytearray(blob); t[len(blob) // 2] ^= 1; open(f"{d}/t.bak", "wb").write(bytes(t))
        expect(B.BackupError, lambda: B.restore_sqlite(f"{d}/t.bak", f"{d}/never.db", KEY)); assert not os.path.exists(f"{d}/never.db"), "a tampered backup creates nothing"
        expect(B.BackupError, lambda: B.restore_sqlite(f"{d}/b.bak", f"{d}/wrong.db", os.urandom(32))); assert not os.path.exists(f"{d}/wrong.db")
        assert not [f for f in os.listdir(tempfile.gettempdir()) if f.startswith("te03-sqlite")], "no plaintext temp copy is left behind"


def test_restoring_a_backup_undoes_later_erasures_until_they_are_replayed():
    """The honest limit of any backup: it is the past. Erasures made after it come back, so they MUST be re-applied from the erasure record (RUNBOOK.md)."""
    with tempfile.TemporaryDirectory() as d:
        s, v = make(db_url=f"sqlite:///{d}/live.db"); g, er = who(s, GW), who(s, ER)
        v.tokenizer.tokenize(tok_req(f"call {PH}", [("PHONE", PH)], borrower="GONE"), g); v.tokenizer.tokenize(tok_req(f"call {PH2}", [("PHONE", PH2)], borrower="STAY", rid="x"), g)
        B.backup_sqlite(f"{d}/live.db", f"{d}/pre-erasure.bak", KEY)
        assert v.deletion.erase(EraseRequest(job_id="J-gone", borrower_id="GONE"), er).status == "COMPLETED"
        B.restore_sqlite(f"{d}/pre-erasure.bak", f"{d}/restored.db", KEY)
        s2 = Settings.for_testing(master_secrets=s.master_secrets, db_url=f"sqlite:///{d}/restored.db", registry=s.registry, rehydrate_policy=s.rehydrate_policy); v2 = build_services(s2); g2, er2 = who(s2, GW), who(s2, ER)
        assert v2.rehydration.rehydrate(reh_req(["<PHONE_1>"], borrower="GONE"), g2).values == {"<PHONE_1>": PH}, "RISK: the erased borrower is back after the restore"
        r = v2.deletion.erase(EraseRequest(job_id="J-gone-replay", borrower_id="GONE"), er2); assert r.status == "COMPLETED" and r.key_destroyed
        expect(Exception, lambda: v2.rehydration.rehydrate(reh_req(["<PHONE_1>"], borrower="GONE"), g2))
        assert v2.rehydration.rehydrate(reh_req(["<PHONE_2>"], borrower="STAY", conv="C1"), g2).values == {"<PHONE_2>": PH2}


# ---------------------------------------------------------------- PostgreSQL: dump from one cluster, restore into ANOTHER
def _super_db(name):
    url = os.environ.get("VAULT_TEST_SUPER_URL")
    if not url: raise Skip("needs a scratch cluster where we are superuser (VAULT_TEST_SUPER_URL)")
    import psycopg
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name}"); c.execute(f"CREATE DATABASE {name}")
    sp = urlsplit(url); return urlunsplit(sp._replace(path=f"/{name}"))


def test_pg_a_backup_of_the_vault_schema_restores_into_another_cluster_and_serves_the_same_data():
    need_postgres(); target = _super_db("te03_restore_test")
    s, v = make(); g, ko = who(s, GW), who(s, KO); schema = v.ctx.db._schema
    v.tokenizer.tokenize(tok_req(f"call {PH} or {PH2}", [("PHONE", PH), ("PHONE", PH2)]), g); v.deletion.erase(EraseRequest(job_id="J-pg", borrower_id="ERASED-ALREADY"), who(s, ER))
    with tempfile.TemporaryDirectory() as d:
        info = B.backup_postgres(s.db_url, f"{d}/pg.bak", KEY); blob = open(f"{d}/pg.bak", "rb").read()
        assert oct(os.stat(f"{d}/pg.bak").st_mode & 0o777) == "0o600" and info["schema"] == schema
        assert b"PGDMP" not in blob and b"vault_records" not in blob and PH.encode() not in blob, "nothing readable: not even the dump's own header"
        t = bytearray(blob); t[len(blob) // 2] ^= 1; open(f"{d}/t.bak", "wb").write(bytes(t))
        expect(B.BackupError, lambda: B.restore_postgres(f"{d}/t.bak", target, KEY))
        import psycopg
        with psycopg.connect(target) as c: assert c.execute("select count(*) from pg_namespace where nspname = %s", (schema,)).fetchone()[0] == 0, "the tampered file never reached the database"
        meta = B.restore_postgres(f"{d}/pg.bak", target, KEY); assert meta["kind"] == "postgres" and meta["schema"] == schema
        assert not [f for f in os.listdir(tempfile.gettempdir()) if f.startswith("te03-restore-")], "the decrypted dump was removed"
    s2 = Settings.for_testing(master_secrets=s.master_secrets, db_url=target + ("&" if "?" in target else "?") + f"schema={schema}&pool_min=0&pool_max=4", registry=s.registry,
                              rehydrate_policy=s.rehydrate_policy, require_non_superuser_db_role=False)        # the scratch cluster's login is a superuser; production would not be
    v2 = build_services(s2); g2 = who(s2, GW)
    assert v2.rehydration.rehydrate(reh_req(["<PHONE_1>", "<PHONE_2>"]), g2).values == {"<PHONE_1>": PH, "<PHONE_2>": PH2}
    assert v2.tokenizer.tokenize(tok_req(f"again {PH}", [("PHONE", PH)], rid="n"), g2).mappings[0].token == "<PHONE_1>", "blind indexes came back too: the same value still reuses its token"
    r = v2.lifecycle.rotate(RotateRequest(target_key_version=1, operation="DEK"), who(s2, KO)); assert r.deks_rotated == 1 and r.records_reencrypted == 2, "after a restore: rotate the DEKs (new nonce sequence)"
    assert v2.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g2).values == {"<PHONE_1>": PH}
    assert v2.deletion.erase(EraseRequest(job_id="J-pg", borrower_id="ERASED-ALREADY"), who(s2, ER)).status == "COMPLETED"


if __name__ == "__main__":
    run(globals())
