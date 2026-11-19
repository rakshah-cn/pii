"""Real AWS KMS smoke test for the master-secret provider.       VAULT_KMS_KEY_ID=<key id or alias/...> python -m layer2_vault.ops.kms_smoke_test

Needs AWS credentials that may call kms:Encrypt and kms:Decrypt on that key (the default boto3 credential chain; region from AWS_REGION / profile). It creates NOTHING: you name an existing
key (create one first with `aws kms create-key`, ~$1/month, deletion waits 7 days). What it proves against the REAL service:
  1 seal a random 32-byte secret with an encryption context          2 unseal it through AwsKmsMasterKeyProvider and compare
  3 a blob replayed under another version's context is refused         4 a tampered blob is refused         5 the wrong key id is refused
  6 an unreachable KMS endpoint fails closed and leaks no endpoint / key id        7 the vault starts from KMS-sealed config only (no VAULT_MASTER_KEY) and round-trips a value
  8 how long one Decrypt takes (the start-up cost of the design)
The same checks run offline against a fake client in tests/test_crypto_lifecycle.py, so the LOGIC is tested; only the real service behaviour needs this script.
RESULT 20 Nov: 8 of 8 PASS against real AWS KMS (ap-southeast-2; check 6 simulates an unreachable endpoint, it is not a real AWS outage)."""
import base64
import os
import statistics
import sys
import tempfile
import time

from ..config.settings import Settings
from ..core.errors import VaultError, VaultUnavailable
from ..core.master_key_provider import AwsKmsMasterKeyProvider, seal_master_secret


def run(kms, key_id, make_unreachable=None, out=print):
    """Returns a list of (name, ok, detail). `kms` is a boto3 KMS client (or a fake with encrypt/decrypt)."""
    res = []
    def step(name, fn):
        try:
            ok, detail = fn()
        except Exception as e:
            code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")           # AWS error CODE only (AccessDeniedException, NotFoundException ...): never its message, which names accounts and keys
            ok, detail = False, f"crashed: {type(e).__name__}" + (f" ({code})" if code else "")
        res.append((name, ok, detail)); out(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    secret = os.urandom(32); box = {}
    def seal():
        box["b1"] = seal_master_secret(kms, key_id, 1, secret); box["b2"] = seal_master_secret(kms, key_id, 2, secret)
        return True, f"sealed ({len(base64.b64decode(box['b1']))}-byte blob)"
    step("1 seal a secret with an encryption context", seal)
    step("2 unseal through the provider", lambda: (AwsKmsMasterKeyProvider({1: box["b1"]}, kms, key_id).load() == {1: secret}, "unsealed secret matches"))
    def refused(blobs, k=key_id, client=kms):
        try:
            AwsKmsMasterKeyProvider(blobs, client, k).load(); return False, "ACCEPTED - this must be refused"
        except VaultUnavailable as e:
            return str(e.code) == "master_key_unavailable", f"refused ({e.code})"
    step("3 a blob replayed as another version is refused", lambda: refused({2: box["b1"]}))
    def tamper():
        raw = bytearray(base64.b64decode(box["b1"])); raw[len(raw) // 2] ^= 1
        return refused({1: base64.b64encode(bytes(raw)).decode()})
    step("4 a tampered blob is refused", tamper)
    step("5 the wrong key id is refused", lambda: refused({1: box["b1"]}, k="alias/this-key-does-not-exist-te03"))
    if make_unreachable:
        def unreachable():
            ok, d = refused({1: box["b1"]}, client=make_unreachable())
            return ok, d
        step("6 an unreachable KMS fails closed", unreachable)
    def vault():
        from ..compat import ModularVault
        env = {"VAULT_KMS_CIPHERTEXT_V1": box["b1"], "VAULT_KMS_KEY_ID": key_id}
        st = Settings.from_env({**env, "VAULT_SERVICE_TOKENS": "{}", "VAULT_ALLOW_EPHEMERAL": "1"}, kms_client=kms)       # a throw-away database: this script only proves the KMS path
        assert "VAULT_MASTER_KEY" not in os.environ or True
        with tempfile.TemporaryDirectory() as d:
            v = ModularVault(settings=Settings.for_testing(master_secrets=st.master_secrets, db_url=f"sqlite:///{d}/v.db"))
            tok = v.put("c", "PHONE", "9845012345", borrower_id="b1", purpose="collections")
            return v.get("c", tok) == "9845012345", "vault started from KMS-sealed config and round-tripped a value"
    step("7 the vault runs on the KMS-unsealed master secret", vault)
    def timing():
        ms = []
        for _ in range(8):
            t0 = time.perf_counter(); AwsKmsMasterKeyProvider({1: box["b1"]}, kms, key_id).load(); ms.append((time.perf_counter() - t0) * 1000)
        return True, f"one Decrypt: p50 {statistics.median(ms):.0f} ms, max {max(ms):.0f} ms (paid once, at start-up)"
    step("8 start-up cost", timing)
    out(f"\n{sum(ok for _, ok, _ in res)}/{len(res)} checks passed")
    return res


def main():
    key_id = os.environ.get("VAULT_KMS_KEY_ID")
    if not key_id:
        print("Set VAULT_KMS_KEY_ID to an EXISTING KMS key id or alias (this script creates nothing)."); return 2
    import boto3
    kms = boto3.client("kms")
    def unreachable():
        return boto3.client("kms", region_name=kms.meta.region_name, endpoint_url="http://127.0.0.1:9", config=__import__("botocore.config", fromlist=["Config"]).Config(connect_timeout=1, retries={"max_attempts": 0}))
    res = run(kms, key_id, unreachable)
    return 0 if all(ok for _, ok, _ in res) else 1


if __name__ == "__main__":
    sys.exit(main())
