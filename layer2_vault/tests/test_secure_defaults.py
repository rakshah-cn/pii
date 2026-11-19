"""The vault used by the gateway must not silently become a throw-away one: no master key or no database means a refusal, not a random key and an in-memory database."""
import os
import tempfile

from layer2_vault.compat import ModularVault
from layer2_vault.core.errors import VaultError
from layer2_vault.tests._util import expect, run

KEY = os.urandom(32).hex()


class env:
    """Set / remove environment variables for a block, then restore them."""
    def __init__(self, **kv): self.kv, self.old = kv, {}
    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v


def refuses(msg_part, **kw):
    try:
        ModularVault(**kw); raise AssertionError("should have refused")
    except RuntimeError as e:
        assert msg_part in str(e), str(e); assert KEY not in str(e)
        return e


def test_without_a_master_key_the_vault_refuses_to_start():
    with env(VAULT_MASTER_KEY=None, VAULT_ALLOW_EPHEMERAL=None, VAULT_DB_PATH=None, VAULT_DB_URL=None):
        refuses("VAULT_MASTER_KEY")
        with tempfile.TemporaryDirectory() as d: refuses("VAULT_MASTER_KEY", path=f"{d}/v.db")           # a database alone is not enough


def test_without_a_database_the_vault_refuses_to_start_even_with_a_key():
    with env(VAULT_MASTER_KEY=KEY, VAULT_ALLOW_EPHEMERAL=None, VAULT_DB_PATH=None, VAULT_DB_URL=None):
        refuses("No vault database")


def test_the_unsafe_mode_must_be_asked_for_explicitly():
    with env(VAULT_MASTER_KEY=None, VAULT_ALLOW_EPHEMERAL=None, VAULT_DB_PATH=None, VAULT_DB_URL=None):
        assert ModularVault(ephemeral=True).svc.ctx.settings.db_url == "sqlite:///:memory:"
    with env(VAULT_MASTER_KEY=None, VAULT_ALLOW_EPHEMERAL="1", VAULT_DB_PATH=None, VAULT_DB_URL=None):
        assert ModularVault().svc.ctx.settings.db_url == "sqlite:///:memory:"
    with env(VAULT_MASTER_KEY=None, VAULT_ALLOW_EPHEMERAL="0", VAULT_DB_PATH=None, VAULT_DB_URL=None): refuses("VAULT_MASTER_KEY")


def test_a_configured_vault_survives_a_restart_and_a_different_key_cannot_read_it():
    with tempfile.TemporaryDirectory() as d, env(VAULT_MASTER_KEY=KEY, VAULT_ALLOW_EPHEMERAL=None, VAULT_DB_URL=None, VAULT_DB_PATH=f"{d}/v.db"):
        a = ModularVault(); tok = a.put("c", "PHONE", "9845012345", borrower_id="b1", purpose="collections")
        b = ModularVault(); assert b.get("c", tok) == "9845012345", "same key + same database after a 'restart': the data is still there"
        with env(VAULT_MASTER_KEY=os.urandom(32).hex()):
            c = ModularVault()
            try: assert c.get("c", tok) is None
            except VaultError: pass                                                         # a wrong master key must never yield the value


def test_the_database_url_and_path_are_both_honoured():
    with tempfile.TemporaryDirectory() as d, env(VAULT_MASTER_KEY=KEY, VAULT_ALLOW_EPHEMERAL=None, VAULT_DB_PATH=None, VAULT_DB_URL=f"sqlite:///{d}/u.db"):
        assert ModularVault().svc.ctx.settings.db_url == f"sqlite:///{d}/u.db"
    with tempfile.TemporaryDirectory() as d, env(VAULT_MASTER_KEY=KEY, VAULT_ALLOW_EPHEMERAL=None, VAULT_DB_PATH=None, VAULT_DB_URL=None):
        assert ModularVault(path=f"{d}/p.db").svc.ctx.settings.db_url == f"sqlite:///{d}/p.db"


def test_the_vault_no_longer_carries_the_guessable_test_service_tokens():
    v = ModularVault(ephemeral=True); reg = v.svc.ctx.settings.registry
    for t in ("tok-gateway-0000000000000000", "tok-erasure-0000000000000000", "tok-keyops-00000000000000000"): assert reg.authenticate(t) is None


def test_an_explicit_settings_object_is_used_as_given():
    from layer2_vault.config.settings import Settings
    with env(VAULT_MASTER_KEY=None, VAULT_ALLOW_EPHEMERAL=None, VAULT_DB_PATH=None, VAULT_DB_URL=None):
        s = Settings.for_testing(master_secrets={1: os.urandom(32)}); assert ModularVault(settings=s).svc.ctx.settings is s


if __name__ == "__main__":
    run(globals())
