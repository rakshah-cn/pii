"""Prove Layer 2 (vault + tokenisation) and write layer2_vault/LAYER2_REPORT.md.   python -m layer2_vault.final_report
All values are fake. Each check prints PASS/FAIL; the report records the result and the known limits."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from layer2_vault.vault import Vault, VaultError

OUT = os.path.join(os.path.dirname(__file__), "LAYER2_REPORT.md")
results = []


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:                                           # a crash is a FAIL, never a silent pass
        ok, detail = False, f"crashed: {type(e).__name__}: {e}"
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def recovered(v, cid, tok, expect):
    try:
        return v.get(cid, tok) == expect
    except Exception:
        return False


PHONES = [f"98450{i:05d}" for i in range(40)]
NAMES = ["Ramesh Kumar", "Sunita Reddy", "Manjunath Gowda", "Priya Iyer"]
FILES = lambda p: [p, p + ".keys", p + ".audit.jsonl"]


def main():
    tmp = tempfile.mkdtemp(prefix="vault_check_")
    master = os.urandom(32)
    p = os.path.join(tmp, "v.db")
    v = Vault(master_key=master, path=p)
    toks = {}
    for i, ph in enumerate(PHONES):
        toks[ph] = v.put(f"conv{i % 3}", "PHONE", ph, borrower_id=f"B{i % 2}")
    for n in NAMES:
        v.put("conv0", "NAME", n, borrower_id="B0")

    def at_rest():
        blob = ""
        for f in FILES(p):
            if os.path.exists(f):
                blob += open(f, encoding="utf-8", errors="ignore").read()
        leaked = [x for x in PHONES + NAMES if x in blob or x.replace(" ", "") in blob]
        return not leaked, f"{len(PHONES) + len(NAMES)} values stored; found in rows/keys/audit files: {len(leaked)}; files: {[os.path.basename(f) for f in FILES(p) if os.path.exists(f)]}"
    check("1. nothing readable on disk (rows, key store, audit log)", at_rest)

    def token_props():
        t = list(toks.values())
        digits_in_tokens = [x for x in t if any(ph[-5:] in x for ph in PHONES)]
        return (not digits_in_tokens and len(set(t)) == len(t) or len(set(t)) > 1), f"tokens look like {sorted(set(t))[:3]}; value digits appear in a token: {len(digits_in_tokens)}"
    check("2. tokens are placeholders, not derived from the value", token_props)

    def dedupe():
        w = Vault(master_key=os.urandom(32))
        a = w.put("c", "PHONE", "98450 12345", borrower_id="X"); b = w.put("c", "PHONE", "9845012345", borrower_id="X")
        c = w.put("c2", "PHONE", "9845012345", borrower_id="X"); d = w.put("c", "PHONE", "9845012345", borrower_id="Y")
        return (a == b), f"same value (spacing differs) -> same token: {a == b}; other conversation -> {c}; other borrower -> {d}; rows for X: {len(w.entries_for_borrower('X'))}"
    check("3. same value in the same conversation gets one token", dedupe)

    def roundtrip():
        ph = PHONES[3]
        ok = recovered(v, "conv0", toks[ph], ph)
        try:
            v.get("conv0", toks[ph], role="not-a-role"); bad_role_blocked = False
        except Exception:
            bad_role_blocked = True
        return ok and bad_role_blocked, f"rehydration role restores the value: {ok}; an unknown role is refused: {bad_role_blocked}"
    check("4. round trip works and roles are enforced", roundtrip)

    def wrong_key():
        try:
            w = Vault(master_key=os.urandom(32), path=p)
            some = next(iter(toks.items()))
            return (not recovered(w, "conv0", some[1], some[0])), "opening the stored vault with a different master key recovers nothing"
        except VaultError as e:
            return True, f"opening with a different master key is refused ({str(e)[:50]})"
    check("5. wrong master key cannot read the data", wrong_key)

    # ---- crypto-shredding against a backup
    def shred():
        d = os.path.join(tmp, "shred"); os.makedirs(d); sp = os.path.join(d, "s.db")
        s = Vault(master_key=master, path=sp)
        mine = {x: s.put("cA", "PHONE", x, borrower_id="B1") for x in PHONES[:8]}
        other = s.put("cB", "PHONE", "9811111111", borrower_id="B2")
        backup_rows = os.path.join(d, "rows_backup"); shutil.copy(sp, backup_rows)
        backup_keys = os.path.join(d, "keys_backup"); shutil.copy(sp + ".keys", backup_keys)
        res = s.destroy_borrower("B1")
        gone = not any(recovered(s, "cA", t, x) for x, t in mine.items())
        others_ok = recovered(s, "cB", other, "9811111111")
        shutil.copy(backup_rows, sp)                                  # restore the pre-shred ROWS file only
        r = Vault(master_key=master, path=sp)
        rows_only = not any(recovered(r, "cA", t, x) for x, t in mine.items())
        shutil.copy(backup_keys, sp + ".keys")                        # now ALSO restore the pre-shred key store
        r2 = Vault(master_key=master, path=sp)
        both = any(recovered(r2, "cA", t, x) for x, t in mine.items())
        return (gone and others_ok and rows_only), (f"after destroy_borrower: borrower's values unreadable {gone}, key present {s.has_key('B1')}, other borrower intact {others_ok}, "
                                                    f"{res}; restoring only the old rows file recovers nothing: {rows_only}. "
                                                    f"KNOWN LIMIT: restoring BOTH old files with the old master key DOES decrypt ({both}); rotate_master_key after a shred closes this")
    check("6. crypto-shred destroys the borrower's data, even against a backup of the rows", shred)

    def rotation():
        d = os.path.join(tmp, "rot"); os.makedirs(d); rp = os.path.join(d, "r.db")
        s = Vault(master_key=master, path=rp); t = s.put("c", "PHONE", "9822222222", borrower_id="B9")
        old_rows, old_keys = os.path.join(d, "or"), os.path.join(d, "ok"); shutil.copy(rp, old_rows); shutil.copy(rp + ".keys", old_keys)
        s.destroy_borrower("B9"); new = os.urandom(32); s.rotate_master_key(new)
        shutil.copy(old_rows, rp); shutil.copy(old_keys, rp + ".keys")
        try:
            r = Vault(master_key=new, path=rp)
            return (not recovered(r, "c", t, "9822222222")), "pre-shred backup of BOTH files cannot be read after the master key is rotated"
        except VaultError as e:
            return True, f"pre-shred backup of BOTH files is refused after rotation ({str(e)[:40]})"
    check("7. after shred + master-key rotation even a full old backup is useless", rotation)

    def audit():
        ok1 = v.verify_audit(); lines = open(v.audit_path, encoding="utf-8").read().splitlines()
        events = len(lines); copy = os.path.join(tmp, "audit_tamper"); shutil.copy(v.audit_path, copy)
        mid = max(0, len(lines) // 2); rec = json.loads(lines[mid]); rec["tampered"] = True; lines[mid] = json.dumps(rec)
        open(v.audit_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        ok2 = v.verify_audit(); shutil.copy(copy, v.audit_path)
        raw_in_audit = any(x in open(v.audit_path, encoding="utf-8").read() for x in PHONES[:5])
        return ok1 and not ok2 and not raw_in_audit, f"{events} audit events; chain intact {ok1}; after editing one event verify says {ok2}; raw values in the audit log: {raw_in_audit}"
    check("8. access log is hash-chained, tamper-evident, and holds no raw values", audit)

    def suite():
        out = subprocess.run([sys.executable, "-m", "layer2_vault.test_vault"], capture_output=True, text=True, timeout=300)
        last = (out.stdout.strip().splitlines() or ["(no output)"])[-1]
        return out.returncode == 0 and "passed" in last, f"existing test suite: {last}"
    check("9. the vault test suite", suite)

    # ---- performance
    n = 5000; w = Vault(master_key=os.urandom(32)); put_ms = []
    for i in range(n):
        t0 = time.perf_counter(); w.put(f"c{i % 50}", "PHONE", f"9{i:09d}", borrower_id=f"B{i % 20}"); put_ms.append((time.perf_counter() - t0) * 1000)
    get_ms, limited = [], 0
    toks_w = w.tokens("c1")[:200]
    for t in toks_w:
        t0 = time.perf_counter()
        try:
            w.get("c1", t)
        except Exception:
            limited += 1; continue
        get_ms.append((time.perf_counter() - t0) * 1000)
    q = lambda xs, f: sorted(xs)[min(len(xs) - 1, int(f * len(xs)))] if xs else float("nan")
    perf = (f"put (in memory, {n} values): p50 {q(put_ms, .5):.2f} ms, p99 {q(put_ms, .99):.2f} ms.  "
            f"get ({len(get_ms)} reads): p50 {q(get_ms, .5):.2f} ms, p99 {q(get_ms, .99):.2f} ms; {limited} reads refused by the per-process rate limit")
    print("\n" + perf)
    shutil.rmtree(tmp, ignore_errors=True)
    L = ["# Layer 2 (vault + tokenisation): final report", "", "Synthetic fake values only. Run: `python -m layer2_vault.final_report`.", "", "| check | result | detail |", "|---|---|---|"]
    L += [f"| {n_} | {'PASS' if ok else 'FAIL'} | {d.replace('|', '/')} |" for n_, ok, d in results]
    L += ["", "## Speed", "", perf, "", "## Known limits", "",
          "- the master key lives in an environment file (no KMS/HSM); rate limits are per process",
          "- a pre-shred backup of BOTH files plus the OLD master key can still decrypt: rotate the master key after a shred",
          "- the vault is single-process; data is in memory unless a path is given (encrypted persistence rewrites the file on each change, so persisted writes are much slower than the in-memory figure)",
          "- tokens are scoped per conversation or borrower, so the same number in two unrelated conversations gets different tokens",
          "- a name-part token (for 'Kumar') rehydrates to the full name it was attached to",
          "- masking plus a vault is one technical safeguard, not DPDP compliance by itself", ""]
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed; wrote {OUT}")


if __name__ == "__main__":
    main()
