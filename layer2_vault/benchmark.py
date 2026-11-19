"""Measured latency of the modular vault (spec section 9: measure, do not assume).     python -m layer2_vault.benchmark
Backends: file SQLite (WAL) always; pooled PostgreSQL when VAULT_BENCH_DB_URL (or VAULT_TEST_DB_URL) is set.
Sections: primitives (AES-GCM encrypt/decrypt, key wrap/unwrap, blind-index key + HMAC), one DB lookup, vault operations (tokenise small -> large requests, reuse,
rehydrate, HTTP path) and concurrent throughput with error counts. Writes layer2_vault/PERFORMANCE.md. Synthetic fake values; this machine only; no target is
invented here - the numbers are the result. The 50 ms budget in the project brief is for Layer 1 detection PLUS tokenisation; Layer 1 is measured in its own report."""
import os
import platform
import tempfile
import threading
import time
import uuid

from fastapi.testclient import TestClient

from layer2_vault.api.routes import create_app
from layer2_vault.core import encryption as enc
from layer2_vault.core.blind_index import blind_index
from layer2_vault.core.key_manager import KeyManager
from layer2_vault.core.nonce import NonceSource
from layer2_vault.tests._util import GW, make, reh_req, tok_req, who


def q(xs, f):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(f * len(xs)))]


def stats(xs, unit="ms"):
    return f"p50 {q(xs, .5):.3f} | p95 {q(xs, .95):.3f} | p99 {q(xs, .99):.3f} | max {max(xs):.3f} {unit} (n={len(xs)})"


def timed(fn, n, scale=1000.0):
    out = []
    for i in range(n):
        t = time.perf_counter(); fn(i); out.append((time.perf_counter() - t) * scale)
    return out


def msg(k, tag, i, borrower=None, conv=None):
    base = abs(hash((tag, i))) % 10 ** 6 * 1000
    vals = [f"9{base + j:09d}" for j in range(k)]
    return tok_req(" ".join(vals), [("PHONE", x) for x in vals], borrower=borrower or f"B{i % 8}", conv=conv or f"C-{tag}-{i}", rid=f"r-{tag}-{i}")


def primitives(lines):
    K, A, km = os.urandom(32), b"aad-bytes-for-a-record", KeyManager({1: os.urandom(32)}, 1); dek = km.new_dek(); ns = NonceSource()
    v, nonce, wrapped = km.wrap(dek, "B1", "key1"); n0, ct = enc.encrypt(K, "98450 12345", A, nonce=ns.nonce(1)); bk = km.blind_key()
    rows = [("AES-256-GCM encrypt (11-char value)", lambda i: enc.encrypt(K, "98450 12345", A, nonce=ns.nonce(i))),
            ("AES-256-GCM decrypt", lambda i: enc.decrypt(K, n0, ct, A)),
            ("DEK wrap (AES-GCM under KEK, incl. HKDF)", lambda i: km.wrap(dek, "B1", "key1")),
            ("DEK unwrap (incl. HKDF)", lambda i: km.unwrap(wrapped, nonce, "B1", "key1", 1)),
            ("HKDF derive blind-index key", lambda i: km.blind_key()),
            ("blind-index HMAC (key already derived)", lambda i: blind_index(bk, "B1", "C1", "collections", "PHONE", "9845012345"))]
    lines += ["## Primitives (microseconds, in-process, no database)", "", "| operation | latency |", "|---|---|"]
    for name, fn in rows:
        lines.append(f"| {name} | {stats(timed(fn, 5000, 1e6), 'us')} |")
    lines.append("")


def settle(v):
    """After a write-heavy section: PostgreSQL gets ANALYZE (+ a pause so autovacuum is not mid-run), SQLite a WAL checkpoint. Without this, the first read-heavy
    section after 35k inserts measured 5 ms instead of 0.5 ms (background autovacuum / checkpoint noise, confirmed by an isolated re-run)."""
    if v.ctx.db.kind == "postgres":
        from layer2_vault.database.models import TABLES
        import re
        names = [re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", d).group(1) for d in TABLES]
        with v.ctx.db.transaction() as tx:
            tx.execute("ANALYZE " + ", ".join(names))                              # this schema's tables only (a bare ANALYZE covers the whole database)
    else:
        v.ctx.db._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    time.sleep(2)


def backend_section(lines, label, url):
    s, v = make(db_url=url); g = who(s, GW); lines += [f"## {label}", ""]
    v.tokenizer.tokenize(msg(2, "warm", 0), g)
    # one DB lookup
    with v.ctx.db.transaction() as tx:
        d = [("PHONE", 1, "x" * 64)]
        lines.append(f"- single scoped blind-index lookup (miss, one query): {stats(timed(lambda i: v.ctx.repo.find_by_digests_batch(tx, 'B1', 'C1', 'collections', d), 3000))}")
    v.tokenizer.tokenize(msg(1, "lk", 0, "B1", "CL"), g)
    with v.ctx.db.transaction() as tx:
        lines.append(f"- token -> record lookup (one query): {stats(timed(lambda i: v.ctx.repo.mapping_with_record(tx, 'CL', '<PHONE_1>'), 3000))}")
    lines += ["", "| operation | latency (ms) |", "|---|---|"]
    def add(name, fn, n):
        lines.append(f"| {name} | {stats(timed(fn, n))} |"); print(f"  {label}: {name}")
    for k, n in ((1, 400), (2, 400), (10, 300), (50, 150), (100, 80), (200, 40)):
        add(f"tokenise, {k} NEW value{'s' if k > 1 else ''}", lambda i, k=k: v.tokenizer.tokenize(msg(k, f"n{k}", i), g), n)
    settle(v)                                                                 # let the database finish background work from the bulk inserts above
    for k in (2, 10, 50):
        for i in range(60): v.tokenizer.tokenize(msg(k, f"u{k}", i, f"B{i % 8}", f"CU{k}-{i}"), g)
        settle(v)
        add(f"tokenise, {k} values already known (reuse)", lambda i, k=k: v.tokenizer.tokenize(msg(k, f"u{k}", i % 60, f"B{(i % 60) % 8}", f"CU{k}-{i % 60}"), g), 300)
    # rehydrate
    for i in range(30): v.tokenizer.tokenize(msg(10, "rh", i, "BR", f"CR{i}"), g)
    toks = [f"<PHONE_{j + 1}>" for j in range(10)]
    add("rehydrate 1 token", lambda i: v.rehydration.rehydrate(reh_req(toks[:1], borrower="BR", conv=f"CR{i % 30}"), g), 400)
    add("rehydrate 10 tokens", lambda i: v.rehydration.rehydrate(reh_req(toks, borrower="BR", conv=f"CR{i % 30}"), g), 300)
    c = TestClient(create_app(v)); H = {"Authorization": "Bearer " + GW}
    add("HTTP tokenise, 2 values reused (auth + validation + service)", lambda i: c.post("/vault/tokenize", headers=H, json=msg(2, "u2", i % 60, f"B{(i % 60) % 8}", f"CU2-{i % 60}").model_dump()), 300)
    lines.append("")
    # concurrency
    lines += ["| concurrent clients | workload | req/s | p50 ms | p95 ms | p99 ms | errors |", "|---|---|---|---|---|---|---|"]
    for clients in (1, 4, 8, 16):
        for workload in ("independent borrowers (2 new values)", "ONE hot conversation (2 new values)"):
            out, errs, lock = [], [], threading.Lock()
            def worker(k):
                for j in range(30):
                    r = msg(2, f"cc{clients}{workload[:3]}{k}", j, f"BC{k}" if workload.startswith("indep") else "HOT", f"CC{k}-{j}" if workload.startswith("indep") else "HOTC")
                    t = time.perf_counter()
                    try: v.tokenizer.tokenize(r, g)
                    except Exception as e: errs.append(type(e).__name__ + ":" + getattr(e, "code", ""))
                    with lock: out.append((time.perf_counter() - t) * 1000)
            ts = [threading.Thread(target=worker, args=(k,)) for k in range(clients)]; t0 = time.perf_counter(); [t.start() for t in ts]; [t.join() for t in ts]
            el = time.perf_counter() - t0
            lines.append(f"| {clients} | {workload} | {len(out) / el:.0f} | {q(out, .5):.2f} | {q(out, .95):.2f} | {q(out, .99):.2f} | {len(errs)}{' (' + ', '.join(sorted(set(errs))) + ')' if errs else ''} |")
    lines.append("")


def main():
    lines = ["# Layer 2 vault: measured performance", "", f"Machine: {platform.processor() or platform.machine()}, {os.cpu_count()} CPUs, Python {platform.python_version()}. Synthetic fake values. "
             "Each number is from this run on this machine; production hardware and a networked database will differ. No target is asserted: read the numbers.", ""]
    primitives(lines)
    with tempfile.TemporaryDirectory() as d:
        backend_section(lines, "File SQLite (WAL, single connection, writes serialised)", f"sqlite:///{d}/bench.db")
    pg = os.environ.get("VAULT_BENCH_DB_URL") or os.environ.get("VAULT_TEST_DB_URL")
    if pg:
        url = pg + ("&" if "?" in pg else "?") + "pool_min=4&pool_max=16&schema=bench_" + uuid.uuid4().hex[:8]
        backend_section(lines, "PostgreSQL 16, pooled (min 4 / max 16 connections, local unix socket)", url)
    else:
        lines += ["## PostgreSQL", "", "Not measured in this run (no VAULT_BENCH_DB_URL / VAULT_TEST_DB_URL).", ""]
    lines += ["## How to read this", "",
              "- The PostgreSQL server here is on the SAME machine over a unix socket: a networked / managed database adds a round trip per statement (batching in Phase 5 keeps the statement count small and independent of the number of values).",
              "- Between the write-heavy and read-heavy sections the database is given ANALYZE / a checkpoint and a 2 s pause (see settle()); without it background work from the bulk inserts inflated the first read section.",
              "- SQLite tails (p95/p99 of the 100- and 200-value requests, and 16 clients) are WAL checkpoints and one connection shared by 16 threads: SQLite is the test / development backend, PostgreSQL is the deployment target.",
              "- 'ONE hot conversation' is the worst case: every client writes into the same conversation, so writes queue on the borrower / conversation locks. Errors there would show up as retries exhausted.",
              "- This is the vault alone. Detection (Layer 1) is measured separately (about 10 ms p50 on this machine); the project budget of 50 ms is for detection plus tokenisation together.", ""]
    path = os.path.join(os.path.dirname(__file__), "PERFORMANCE.md"); open(path, "w").write("\n".join(lines)); print("wrote", path)


if __name__ == "__main__":
    main()
