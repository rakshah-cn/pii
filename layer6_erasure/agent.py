"""LAYER 6: the erasure agent (LangGraph).

  discover -> plan -> delete -> verify --(zero left)--> shred -> audit -> END
                 ^                  \\--(survivors, passes < max)--> plan
                 (verify re-runs discovery;  survivors after max passes => NOT_ERASED, escalate)

WHAT MAKES IT AGENTIC (honest scope): discovery is ADAPTIVE. It starts from the lineage registry, then widens: it
sweeps by conversation, follows derivative links to a fixpoint, searches raw values (sealed), probes embeddings,
enumerates stores nobody registered, and every record it finds can reveal a new conversation or parent to follow
(snowball). Verification re-runs ALL of that and loops until clean. PLANNING is deterministic (no LLM needed);
nothing here depends on an LLM, and the agent never sees raw personal data (ids, counts, masked snippets only).

BLIND TO PII: the raw-value sweeps hand store text to the vault, which matches inside itself and returns only a category,
a score or a bool. The agent has no call that returns a borrower's values (there is no decrypt_for_erasure any more).
FAIL SAFE: any error (a store, the vault, the verify loop) ends in NOT_ERASED + escalate, never a crash; the key is kept.
ORDER MATTERS: the vault key is destroyed only AFTER verification, because the raw-value sweeps need the vault key.
STATUS: ERASED_VERIFIED | PARTIAL (ambiguous matches left for a human) | NOT_ERASED (survivors, or key not destroyed)
"""
import hashlib
import os
import re
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .adapters import StoreCatalog
from .tools import CONV_KEYS, SealedScanner, conversation_sweep, derivative_sweep, registry_sweep

DELETABLE = ("certain", "probabilistic")
ALL_METHODS = ["registry", "conversation", "raw", "indep", "derivative", "probe", "filegrep"]


class State(TypedDict, total=False):
    request_id: str
    borrower_id: str
    t0: float
    passes: int
    hits: list
    plan: list
    found: dict
    deleted: dict
    unresolved: list
    methods: list
    new_stores: list
    vault: dict
    status: str
    trace: list
    last_remaining: dict
    stores_seen: list
    probabilistic: int
    record: dict
    error: str


def _code(exc):
    """A short, PII-free error code: the exception class, plus its message only when it is a plain code like 'erasure_incomplete'."""
    msg = str(exc)
    return f"{type(exc).__name__}:{msg}" if re.fullmatch(r"[a-z0-9_]{1,60}", msg) else type(exc).__name__


def _job_id(request_id, borrower_id):
    """STABLE across retries of the same request, so a retry resumes the same vault erasure job (the vault runbook says so).
    The borrower hash keeps two borrowers with the same request id apart."""
    safe = re.sub(r"[^A-Za-z0-9_.:\-]", "_", str(request_id))[:80]
    return f"L6-{safe}-{hashlib.sha256(str(borrower_id).encode()).hexdigest()[:10]}"


def build_agent(root, vault, registry, audit, max_passes=3, registry_path=None, disable=()):
    """`disable` turns discovery methods off (ablation / testing only): registry, conversation, raw, indep, derivative, probe, filegrep."""
    catalog = StoreCatalog(root)
    scanner = SealedScanner(vault, registry)

    def stores():
        """Fresh adapters each time (files may have changed). Registered + any store nobody registered."""
        c = StoreCatalog(root)
        return c.registered, c.discover_unregistered()

    def run_discovery(b, history=frozenset()):
        """`history` = ids already found/deleted in earlier passes: derived copies whose parent is gone still point at them."""
        reg, unreg = stores()
        allA = reg + unreg
        methods, hits = [], {}

        def add(hs, m):
            if hs:
                methods.append(m)
            for h in hs:
                key = (h["store"], h["id"])
                prev = hits.get(key)
                if prev is None or (prev["strength"] == "ambiguous" and h["strength"] in DELETABLE):
                    hits[key] = h

        convs = set(registry.conversations_of(b)) | {e["conversation_id"] for e in vault.entries_for_borrower(b)}
        if "registry" not in disable:
            add(registry_sweep(reg, registry, b), "registry")
        for _ in range(3):                                         # snowball: new conversations found => sweep again
            before = len(convs)
            if "conversation" not in disable:
                add(conversation_sweep(allA, convs), "conversation_sweep")
            if "raw" not in disable:
                add(scanner.raw_sweep(allA, b, convs), "raw_value_sweep(sealed)")
            for a in allA:
                for rid, text, meta in a.records():
                    h = hits.get((a.name, rid))
                    if h and h["strength"] in DELETABLE:
                        for k in CONV_KEYS:
                            if meta.get(k):
                                convs.add(meta[k])
            if len(convs) == before:
                break
        if "indep" not in disable:
            add(scanner.collapse_scan(allA, b), "independent_collapsed_scan(sealed)")
        known = registry.known_for(b)
        pivots = ({h["id"] for h in hits.values() if h["strength"] in DELETABLE} | set(history)
                  | {i for ids in known.values() for i in ids})
        if "derivative" not in disable:
            add(derivative_sweep(allA, pivots), "derivative_sweep")
        if "probe" not in disable:
            add(scanner.vector_probe(allA, b), "embedding_probe(sealed)")
        if "filegrep" not in disable:
            managed = {os.path.abspath(a.path) for a in allA}
            add(scanner.file_grep(root, b, managed, StoreCatalog.SKIP | {"requests_public.json", "requests_heldout.json"}),
                "independent_file_grep(sealed)")
        new_stores = sorted({a.name for a in unreg if any(h["store"] == a.name for h in hits.values())})
        if unreg:
            methods.append("unmapped_store_discovery")
        return list(hits.values()), sorted(set(methods)), new_stores, [a.name for a in allA]

    def discover(s: State):
        hits, methods, new_stores, seen = run_discovery(s["borrower_id"])
        d = [h for h in hits if h["strength"] in DELETABLE]
        return {"hits": hits, "methods": methods, "new_stores": new_stores, "stores_seen": seen, "passes": 0,
                "found": {}, "deleted": {}, "t0": s.get("t0") or time.time(),
                "trace": s.get("trace", []) + [f"discover: {len(d)} deletable, "
                                               f"{len(hits) - len(d)} ambiguous, stores probed={len(seen)}, "
                                               f"unregistered stores with hits={new_stores}"]}

    def plan(s: State):
        order = {"eval_sets": 0, "vector_store": 1, "logs": 2}
        by_store = {}
        for h in s["hits"]:
            if h["strength"] in DELETABLE:
                by_store.setdefault(h["store"], []).append(h["id"])
        p = [{"store": k, "ids": sorted(v)} for k, v in sorted(by_store.items(), key=lambda kv: (order.get(kv[0], 3), kv[0]))]
        prob = sum(1 for h in s["hits"] if h["strength"] == "probabilistic")
        return {"plan": p, "probabilistic": max(s.get("probabilistic", 0), prob),
                "trace": s["trace"] + [f"plan: {sum(len(x['ids']) for x in p)} records in {len(p)} stores"]}

    def delete(s: State):
        reg, unreg = stores()
        by_name = {a.name: a for a in reg + unreg}
        found, deleted = {k: list(v) for k, v in s.get("found", {}).items()}, dict(s.get("deleted", {}))
        for item in s["plan"]:
            a = by_name.get(item["store"])
            n = a.delete(item["ids"]) if a else 0
            deleted[item["store"]] = deleted.get(item["store"], 0) + n
            found[item["store"]] = sorted(set(found.get(item["store"], [])) | set(item["ids"]))
        return {"found": found, "deleted": deleted,
                "trace": s["trace"] + [f"delete: removed {sum(deleted.values())} records so far"]}

    def verify(s: State):
        hist = {i for ids in s.get("found", {}).values() for i in ids}
        hits, methods, new_stores, seen = run_discovery(s["borrower_id"], hist)
        d = [h for h in hits if h["strength"] in DELETABLE]
        remaining = {}
        for h in d:
            remaining[h["store"]] = remaining.get(h["store"], 0) + 1
        return {"hits": hits, "passes": s["passes"] + 1, "last_remaining": remaining,
                "methods": sorted(set(s["methods"]) | set(methods)),
                "new_stores": sorted(set(s["new_stores"]) | set(new_stores)),
                "trace": s["trace"] + [f"verify pass {s['passes'] + 1}: {len(d)} deletable survivors "
                                       f"{remaining if remaining else ''}"]}

    def route(s: State):
        if s.get("error"):
            return "audit"
        if not s["last_remaining"]:
            return "shred"
        return "plan" if s["passes"] < max_passes else "audit"

    def shred(s: State):
        """Destroy the borrower's vault entries and key. Runs only after verify found zero. If the vault cannot finish
        (e.g. 'erasure_incomplete') nothing here crashes: the outcome is recorded and the status becomes NOT_ERASED."""
        b = s["borrower_id"]
        err = None
        try:
            res = vault.destroy_borrower(b, job_id=_job_id(s["request_id"], b))
            res = dict(res or {})
        except Exception as e:                           # noqa: BLE001 - the vault's failure must not crash the agent
            res, err = {}, _code(e)
        try:
            remaining = len(vault.entries_for_borrower(b))
            key_gone = not vault.has_key(b)
        except Exception as e:                           # noqa: BLE001 - cannot confirm => assume NOT erased
            remaining, key_gone, err = -1, False, err or _code(e)
        res = {"entries_deleted": res.get("entries_deleted", 0), "key_destroyed": bool(res.get("key_destroyed")) and key_gone and err is None,
               "entries_remaining": remaining}
        if err:
            res["error"] = err
        if res["key_destroyed"] and remaining == 0:      # only forget the lineage once the vault really is clean (a retry needs it otherwise)
            registry.forget_borrower(b)
            if registry_path:
                registry.save(registry_path)
        return {"vault": res, "trace": s["trace"] + [f"shred: vault entries deleted={res['entries_deleted']}, key destroyed={res['key_destroyed']}"
                                                     + (f", ERROR {err}" if err else "")]}

    def audit_node(s: State):
        b = s["borrower_id"]
        err = s.get("error")
        hits = s.get("hits", [])
        vres = s.get("vault")
        if vres is None:                                 # shred never ran (survivors, or an earlier error)
            try:
                vres = {"entries_deleted": 0, "key_destroyed": False, "entries_remaining": len(vault.entries_for_borrower(b))}
            except Exception:                            # noqa: BLE001
                vres = {"entries_deleted": 0, "key_destroyed": False, "entries_remaining": -1}
        unresolved = [{"store": h["store"], "id": h["id"], "reason": h["evidence"]} for h in hits if h["strength"] == "ambiguous"]
        last_remaining = s.get("last_remaining")
        survivors = bool(last_remaining) or last_remaining is None      # None = verify never completed: cannot claim clean
        if err or survivors or not vres["key_destroyed"] or vres["entries_remaining"]:
            status = "NOT_ERASED"
        elif unresolved:
            status = "PARTIAL"
        else:
            status = "ERASED_VERIFIED"
        names = sorted(set(s.get("stores_seen", [])) | set(s.get("found", {})))
        rows = [{"store": n, "found": len(s.get("found", {}).get(n, [])), "deleted": s.get("deleted", {}).get(n, 0),
                 "verified_zero": n not in (last_remaining or {}) and last_remaining is not None} for n in names]
        rec = audit.append({"type": "erasure", "request_id": s["request_id"], "borrower_hash": audit.hash_borrower(b),
                            "status": status, "passes": s.get("passes", 0), "duration_s": round(time.time() - (s.get("t0") or time.time()), 2),
                            "stores": rows, "vault": vres, "methods": s.get("methods", []),
                            "methods_run": [m for m in ALL_METHODS if m not in disable],
                            "probabilistic": s.get("probabilistic", 0), "unresolved": unresolved[:50],
                            "unresolved_count": len(unresolved), "unregistered_stores_with_hits": s.get("new_stores", []),
                            "error": err, "escalate": status != "ERASED_VERIFIED"})
        return {"record": rec, "status": status, "unresolved": unresolved,
                "trace": s.get("trace", []) + [f"audit: status={status}"]}

    def guarded(name, fn):
        """Any error in a node becomes state['error'] and the graph jumps to audit (NOT_ERASED, escalate). It never crashes."""
        def run(s: State):
            try:
                return fn(s)
            except Exception as e:                       # noqa: BLE001 - deliberate: fail safe, report, escalate
                return {"error": f"{name}:{_code(e)}", "trace": s.get("trace", []) + [f"{name}: FAILED {_code(e)}"]}
        return run

    def after(nxt):
        return lambda s: "audit" if s.get("error") else nxt

    g = StateGraph(State)
    for name, fn in (("discover", discover), ("plan", plan), ("delete", delete), ("verify", verify)):
        g.add_node(name, guarded(name, fn))
    g.add_node("shred", shred)                          # shred handles its own errors (it must record the vault outcome)
    g.add_node("audit", audit_node)
    g.add_edge(START, "discover")
    g.add_conditional_edges("discover", after("plan"), {"plan": "plan", "audit": "audit"})
    g.add_conditional_edges("plan", after("delete"), {"delete": "delete", "audit": "audit"})
    g.add_conditional_edges("delete", after("verify"), {"verify": "verify", "audit": "audit"})
    g.add_conditional_edges("verify", route, {"shred": "shred", "plan": "plan", "audit": "audit"})
    g.add_edge("shred", "audit")
    g.add_edge("audit", END)
    return g.compile()


def erase(app, request):
    return app.invoke({"request_id": request["request_id"], "borrower_id": request["borrower_id"],
                       "t0": time.time(), "trace": []})
