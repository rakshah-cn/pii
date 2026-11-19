"""Run every Layer 2 test file and print a summary.   python -m layer2_vault.tests.run_all"""
import subprocess
import sys

FILES = ["test_encryption", "test_key_manager", "test_blind_index", "test_tokeniser", "test_rehydration", "test_erasure", "test_concurrency",
         "test_leakage", "test_rotation", "test_api", "test_lifecycle_extras", "test_review_fixes", "test_postgres", "test_integration", "test_retention", "test_crypto_lifecycle", "test_database_hardening", "test_api_hardening", "test_batching", "test_backup_restore", "test_security_regression", "test_context", "test_secure_defaults"]


def main():
    total = bad = skipped = 0
    for f in FILES:
        r = subprocess.run([sys.executable, "-m", f"layer2_vault.tests.{f}"], capture_output=True, text=True)
        last = [l for l in r.stdout.splitlines() if "passed" in l][-1:] or ["(no summary: crashed)"]
        n_ok = int(last[0].split()[0]) if last[0][0].isdigit() else 0
        n_bad = int(last[0].split(",")[1].split()[0]) if "failed" in last[0] else 1
        total += n_ok; bad += n_bad
        skipped += int(last[0].split("failed,")[1].split()[0]) if "skipped" in last[0] else 0
        print(f"{'PASS' if r.returncode == 0 else 'FAIL'}  {f:<24}{last[0]}")
        if r.returncode != 0:
            print(r.stdout[-1500:] + r.stderr[-800:])
    print(f"\nModular vault: {total} tests passed, {bad} failed" + (f", {skipped} skipped (need PostgreSQL / a scratch cluster)" if skipped else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
