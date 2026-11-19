"""The EXISTING gateway (Layer 3) and erasure (Layer 6) suites, and the whole-stack demo, run on the MODULAR vault.
This is the proof that the new layer is a drop-in. Excluded: tests that read the legacy vault's private _rows (their intent -
encrypted at rest, borrower-scoped - is covered by test_tokeniser / test_leakage on the real interface)."""
import contextlib
import io

from layer2_vault.tests._util import run

LEGACY_INTERNALS = set()          # the gateway suite no longer reads the legacy vault's private _rows (that test was rewritten on the real interface)


def _run_suite(mod):
    names = [(k, v) for k, v in sorted(vars(mod).items()) if k.startswith("test_") and callable(v) and k not in LEGACY_INTERNALS]
    bad = []
    for name, fn in names:
        try:
            fn()
        except Exception as e:
            bad.append((name, type(e).__name__, str(e)[:90]))
    return len(names), bad


def test_gateway_suite_passes_on_the_modular_vault():
    """The gateway now runs on the modular vault natively (VaultClient is its default): just run its suites."""
    import layer3_gateway.test_gateway as T
    n, bad = _run_suite(T)
    assert n >= 31 and not bad, bad
def test_erasure_suite_passes_on_the_modular_vault():
    import layer6_erasure.evaluate as EV
    import layer6_erasure.test_erasure as TE
    import layer6_erasure.vault_adapter as VA
    from layer2_vault.compat import ModularVault
    old = VA.get_vault; VA.get_vault = lambda: (ModularVault(), "modular")
    saved = [(m, m.get_vault) for m in (TE, EV) if hasattr(m, "get_vault")]
    for m, _ in saved: m.get_vault = VA.get_vault
    try:
        n, bad = _run_suite(TE)
    finally:
        VA.get_vault = old
        for m, f in saved: m.get_vault = f
    assert n >= 13 and not bad, bad
def test_whole_stack_demo_ends_clean_on_the_modular_vault():
    import layer6_erasure.vault_adapter as VA
    import layer8_demo.end_to_end as E2E
    from layer2_vault.compat import ModularVault
    old = E2E.get_vault; E2E.get_vault = lambda: (ModularVault(), "modular")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            clean = E2E.main()
    finally:
        E2E.get_vault = old
    out = buf.getvalue()
    assert clean and "<PHONE_2>" not in out and "This is <PERSON_1> here" in out and "ERASED_VERIFIED" in out, out[-500:]

if __name__ == "__main__":
    run(globals())
