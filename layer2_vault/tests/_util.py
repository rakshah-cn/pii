"""Shared helpers for the Layer 2 test files (plain-python style, no pytest: each file runs with `python -m layer2_vault.tests.<file>`)."""
import sys
import traceback

from layer2_vault.config.settings import Settings
from layer2_vault.schemas.vault_schemas import DetectedEntity, EraseRequest, RehydrateRequest, TokenizeRequest
from layer2_vault.services import build_services

RT = "tok-retention-0000000000000"
import os as _os
_os.environ.setdefault("VAULT_ALLOW_EPHEMERAL", "1")                # the test suites use throw-away vaults on purpose (test_secure_defaults checks the refusal)
GW, ER, LI, KO, AU = ("tok-gateway-0000000000000000", "tok-erasure-0000000000000000", "tok-lineage-0000000000000000",
                      "tok-keyops-00000000000000000", "tok-audit-00000000000000000")


def make(permissions=None, events=None, db=None, clock=None, **kw):
    import os, uuid
    if os.environ.get("VAULT_TEST_DB_URL") and "db_url" not in kw:           # run the same tests on PostgreSQL, each in its own schema
        kw["db_url"] = os.environ["VAULT_TEST_DB_URL"] + ("&" if "?" in os.environ["VAULT_TEST_DB_URL"] else "?") + "pool_min=0&pool_max=4&schema=t_" + uuid.uuid4().hex[:12]
    s = Settings.for_testing(**kw)
    return s, build_services(s, db=db, permissions=permissions, events=events, clock=clock)


def break_db(v):
    """Simulate a database outage on whichever backend is in use."""
    if v.ctx.db.kind == "sqlite":
        v.ctx.db._conn.close()
    else:
        v.ctx.db._pool.close()                       # the pool can no longer give out a connection: same as the server going away


def who(s, tok):
    return s.registry.authenticate(tok)


def tok_req(text, ents, borrower="B1", conv="C1", purpose="collections", rid="r1"):
    """ents: [(type, value)] - offsets are computed from the text so a test can never mis-count."""
    out, pos = [], 0
    for t, v in ents:
        i = text.index(v, pos); pos = i + len(v)
        out.append(DetectedEntity(type=t, value=v, start=i, end=i + len(v)))
    return TokenizeRequest(request_id=rid, borrower_id=borrower, conversation_id=conv, purpose_id=purpose, text=text, detected_entities=out)


def reh_req(tokens, borrower="B1", conv="C1", purpose="collections", dest="borrower_reply", rid="h1"):
    return RehydrateRequest(request_id=rid, borrower_id=borrower, conversation_id=conv, purpose_id=purpose, destination=dest, tokens=tokens)


def expect(exc_type, fn, code=None):
    try:
        fn()
    except exc_type as e:
        if code is not None:
            assert getattr(e, "code", None) == code, f"expected code {code!r}, got {getattr(e, 'code', None)!r}"
        return e
    raise AssertionError(f"expected {exc_type.__name__}")


class Skip(Exception):
    """Raised by a test that cannot run in this environment (e.g. needs PostgreSQL). Reported as SKIP, never as a pass."""


def need_postgres():
    import os
    if not os.environ.get("VAULT_TEST_DB_URL"):
        raise Skip("needs PostgreSQL (VAULT_TEST_DB_URL)")


def run(namespace):
    tests = [(k, v) for k, v in sorted(namespace.items()) if k.startswith("test_") and callable(v)]
    bad = skipped = 0
    for name, fn in tests:
        try:
            fn(); print("PASS", name)
        except Skip as e:
            skipped += 1; print("SKIP", name, f"({e})")
        except Exception:
            bad += 1; print("FAIL", name); traceback.print_exc()
    print(f"\n{len(tests) - bad - skipped} passed, {bad} failed" + (f", {skipped} skipped" if skipped else ""))
    sys.exit(1 if bad else 0)
