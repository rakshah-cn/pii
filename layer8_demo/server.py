"""Layer 8 demo server: a thin web layer over the REAL layers 1-7.   .venv/bin/python -m layer8_demo.server   ->  http://127.0.0.1:8808

Nothing here re-implements a layer. Each endpoint calls the layer's own code and returns what it did, so the front end can show
the same message as every layer saw it. Honest scope (the UI says the same):
  * all values typed or generated are FAKE; the stores (logs / vectors / eval sets) are MOCK JSONL files under data/stores/ui/
  * the LLM is a stub unless the user switches "live LLM" on (then only TOKENISED text goes to Bedrock Nova Pro)
  * /api/message returns the raw text the user just typed (to draw the highlights). The production gateway never returns it.
"""
import collections
import contextvars
import hashlib
import secrets
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:                                              # lets this file run directly (VS Code "Run Python File") as well as with -m
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)                                                        # the layers use project-relative paths (data/..., models/...)

import common.llm  # noqa: F401,E402  (loads .env; secrets are never printed or returned)

os.environ.setdefault("LINEAGE_HMAC_KEY", uuid.uuid4().hex)           # demo process only: avoids the dev-key warning

from fastapi import FastAPI, HTTPException                              # noqa: E402
from fastapi.responses import FileResponse                              # noqa: E402
from fastapi.staticfiles import StaticFiles                             # noqa: E402
from pydantic import BaseModel                                          # noqa: E402

from layer3_gateway import llm as llmmod                                # noqa: E402
from layer3_gateway.events import Events, Layer7Sink                    # noqa: E402
from layer3_gateway.graph import TOKEN, Gateway, make_detector, merge_spans   # noqa: E402
from layer4_consent import policy                                       # noqa: E402
from layer4_consent.guard import guarded_write                          # noqa: E402
from layer4_consent.write_guard import new_tag                          # noqa: E402
from layer5_lineage.registry import LineageRegistry, ref                # noqa: E402
from layer6_erasure.adapters import JsonlStore, StoreCatalog            # noqa: E402
from layer6_erasure.agent import build_agent, erase                     # noqa: E402
from layer6_erasure.embed import embed                                  # noqa: E402
from layer6_erasure.vault_adapter import get_vault                      # noqa: E402
from layer7_audit.chain import AuditLog                                 # noqa: E402
from layer7_audit.proof import render_proof                             # noqa: E402

from layer8_demo import ui_content                                     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(HERE, "web")
UI_ROOT = os.path.join(ROOT, "data", "stores", "ui")
PORT = int(os.environ.get("DEMO_PORT", "8808"))
BEDROCK_MODEL = "apac.amazon.nova-pro-v1:0"
LOCK = threading.RLock()                                                # one conversation at a time: the gateway keeps per-conversation state


# ---------------------------------------------------------------- detector wrapper (records what Layer 1 saw; can be blinded)
class RecordingDetector:
    """Wraps the real Layer-1 detector. blind=True returns NO spans (simulates a detector miss) so the audit net can be shown working."""
    def __init__(self, inner):
        self.inner, self.blind, self.last, self.last_ms = inner, False, [], 0.0

    def detect_calls(self, calls):
        t = time.perf_counter()
        if self.blind:
            res = [[[] for _ in turns] for turns in calls]
        else:
            res = self.inner.detect_calls(calls)
        self.last_ms = (time.perf_counter() - t) * 1000
        self.last = [dict(s) for s in res[0][-1]]
        return res

    def __getattr__(self, name):
        return getattr(self.inner, name)


DET = {"det": None}
LLM = {"live": False, "calls": 0, "last_error": None}
METRICS = {"lat": collections.deque(maxlen=300), "t": collections.deque(maxlen=300), "messages": 0}   # real per-message timings, for the console's gauges (nothing here holds text)


def _llm(masked_text):
    """Whatever the gateway sends here is already tokenised. The stub never leaves the machine; live mode calls Bedrock (Nova Pro)."""
    if LLM["live"]:
        LLM["calls"] += 1
        try:
            return llmmod.make_bedrock_llm(BEDROCK_MODEL, region=common.llm.REGION)(masked_text)
        except Exception as e:                                          # noqa: BLE001  (gateway decides: timeout -> degraded, other -> blocked)
            LLM["last_error"] = type(e).__name__
            raise
    return Gateway._stub_llm(masked_text)


# ---------------------------------------------------------------- one demo session = one borrower + one conversation
class Workspace:
    """Everything shared by all borrowers: ONE vault (a key per borrower), ONE audit chain, ONE lineage registry, ONE set of mock stores, ONE gateway."""
    def __init__(self):
        self.id = "WS" + uuid.uuid4().hex[:6]
        self.root = os.path.join(UI_ROOT, self.id)
        os.makedirs(os.path.join(self.root, "debug_dump"), exist_ok=True)
        self.vault, self.vault_kind = get_vault()
        self.audit = EnrichedAuditLog(os.path.join(self.root, "audit_log.jsonl"))
        self.events = Events(sink=Layer7Sink(self.audit))
        self.gw = Gateway(detector=DET["det"], vault=self.vault, events=self.events, llm=_llm)
        self.reg = LineageRegistry()
        self.logs = JsonlStore(os.path.join(self.root, "logs.jsonl"), "logs", "logs")
        self.vecs = JsonlStore(os.path.join(self.root, "vectors.jsonl"), "vector_store", "vector_store")
        self.evals = JsonlStore(os.path.join(self.root, "eval_sets.jsonl"), "eval_sets", "eval_sets")
        self.adapters = {"logs": self.logs, "vector_store": self.vecs, "eval_sets": self.evals}
        self.borrowers = collections.OrderedDict()
        self.privacy = []                                                      # privacy requests (maker-checker workflow), all borrowers
        self.next_case, self.last = 1041, None

    def add(self, name, profile=None):
        s = Session(self, name, profile)
        self.borrowers[s.sid] = s
        self.last = s
        return s

    def reload_stores(self):                                                   # the erasure agent edits the files directly: refresh the in-memory copies
        for a in self.adapters.values():
            a._load()


class Session:
    """One borrower case. Shared things (vault, audit, registry, stores, gateway, root) are read from the workspace."""
    def __init__(self, ws, name, profile=None):
        self.ws = ws
        self.profile = profile or {}
        self.sid = "C" + uuid.uuid4().hex[:6]
        self.case = ws.next_case
        ws.next_case += 1
        self.name = name
        self.B, self.CID = f"B-{self.sid}", f"conv-{self.sid}"
        ws.reg.add_borrower(self.B, name=name)
        ws.reg.add_conversation(self.B, self.CID)
        self.turns, self.values, self.n = [], collections.OrderedDict(), 0     # values: raw -> label (server memory only; never returned)
        self.planted, self.erased, self.last_erasure = False, False, None
        self.requests, self.planted_items, self.chunks, self.rejections = [], [], [], 0
        self.history = []                                                      # message responses, for the conversation view

    def __getattr__(self, name):
        if name in ("vault", "vault_kind", "audit", "events", "gw", "reg", "logs", "vecs", "evals", "adapters", "root"):
            return getattr(self.ws, name)
        raise AttributeError(name)

    def reg_path(self):
        return os.path.join(self.root, "registry.json")


WS = {"ws": None}
CASE = contextvars.ContextVar("case", default=None)


REQ = contextvars.ContextVar("req", default=None)
USERS = {
    "asha": {"name": "Asha Rao", "role": "agent", "title": "Collections agent", "type": "user"},
    "rohit": {"name": "Rohit Mehta", "role": "privacy_officer", "title": "Privacy officer", "type": "user"},
    "meera": {"name": "Meera Iyer", "role": "dpo", "title": "Data protection officer", "type": "user"},
    "vikram": {"name": "Vikram Shah", "role": "auditor", "title": "Internal auditor", "type": "user"},
    "erasure-agent": {"name": "Erasure agent", "role": "service", "title": "Layer 6 service", "type": "service"},
}
AUDIT_READ = ("auditor", "privacy_officer", "dpo")


class CaseMiddleware:
    """Pure ASGI. Builds the request context every audit record is stamped with: user, source IP (x-forwarded-for first, as the KB's login table does), user agent, session id, W3C trace id."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            h = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
            ip = (h.get("x-forwarded-for") or "").split(",")[0].strip() or (scope.get("client") or ("", 0))[0]
            parts = h.get("traceparent", "").split("-")
            trace = parts[1] if len(parts) == 4 and len(parts[1]) == 32 else secrets.token_hex(16)
            ctx = {"user": h.get("x-user") or "asha", "ip": ip, "ua": h.get("user-agent", "")[:120], "session": h.get("x-session", ""), "trace_id": trace, "path": scope.get("path"), "method": scope.get("method")}
            t1, t2 = CASE.set(h.get("x-case")), REQ.set(ctx)
            try:
                await self.app(scope, receive, send)
            finally:
                CASE.reset(t1)
                REQ.reset(t2)
        else:
            await self.app(scope, receive, send)


# event name -> (action, category, service, outcome)
ACTIONS = {
    "L3_STARTED": ("gateway.request.received", "gateway", "layer3_gateway", "success"), "L1_DETECT_COMPLETE": ("detection.run", "gateway", "layer1_detection", "success"),
    "L2_TOKENISE_COMPLETE": ("vault.tokenise", "gateway", "layer2_vault", "success"), "L3_AUDIT_PASSED": ("gateway.audit.passed", "gateway", "layer3_gateway", "success"),
    "L3_AUDIT_FAILED": ("gateway.audit.failed", "gateway", "layer3_gateway", "failure"), "LLM_RESPONDED": ("llm.respond", "gateway", "layer3_gateway", "success"),
    "LEAK_PREVENTED": ("gateway.leak.prevented", "gateway", "layer3_gateway", "blocked"), "L3_BLOCKED": ("gateway.request.blocked", "gateway", "layer3_gateway", "blocked"),
    "CONSENT_DENIED": ("consent.decision", "consent", "layer4_consent", "denied"), "write_allowed": ("store.write", "consent", "layer4_consent", "success"),
    "write_rejected": ("store.write", "consent", "layer4_consent", "denied"), "write_failed": ("store.write", "consent", "layer4_consent", "failure"),
    "erasure": ("erasure.execute", "erasure", "layer6_erasure", "success"),
    "PRIVACY_REQUEST_INITIATED": ("privacy.request.initiate", "privacy", "layer8_app", "success"), "PRIVACY_REQUEST_RECOMMENDED": ("privacy.request.recommend", "privacy", "layer8_app", "success"),
    "PRIVACY_REQUEST_APPROVED": ("privacy.request.approve", "privacy", "layer8_app", "success"), "PRIVACY_REQUEST_REJECTED": ("privacy.request.reject", "privacy", "layer8_app", "success"),
    "MAKER_CHECKER_VIOLATION": ("privacy.request.approve", "privacy", "layer8_app", "denied"), "ACCESS_DENIED": ("access.check", "access", "layer8_app", "denied"),
    "AUDIT_VIEW": ("audit.record.view", "audit_access", "layer7_audit", "success"), "AUDIT_VERIFY": ("audit.chain.verify", "audit_access", "layer7_audit", "success"),
    "AUDIT_EXPORT": ("audit.export", "audit_access", "layer7_audit", "success"), "AUDIT_TAMPER_TEST": ("audit.tamper_test", "audit_access", "layer7_audit", "success"),
}


class EnrichedAuditLog(AuditLog):
    """Layer 7's hash chain, with the envelope a production audit record needs (see the KB's audit gaps): who (actor, role), from where (IP, session), when (explicit UTC),
    what (action, resource), outcome, and a W3C trace id so one request can be followed across services. The envelope is inside the hashed body."""
    def append(self, record):
        ctx = REQ.get()
        rec = dict(record)
        kind = rec.get("event") or rec.get("kind") or rec.get("type") or ""
        action, cat, svc, outcome = ACTIONS.get(kind, (kind.lower(), "system", "layer7_audit", "success"))
        if kind == "erasure" and rec.get("status") != "ERASED_VERIFIED":
            outcome = "failure"
        if "outcome" in rec:                                                              # the gateway's own field: keep it, but never let it shadow the audit outcome
            rec["gateway_outcome"] = rec.pop("outcome")
        u = USERS.get(ctx["user"]) if ctx else None
        rec.setdefault("actor", {"type": u["type"], "id": ctx["user"], "name": u["name"], "role": u["role"]} if u else {"type": "service", "id": "seed-loader", "name": "Seed loader", "role": "system"})
        rec.setdefault("schema", "audit.v2")
        rec.setdefault("action", action)
        rec.setdefault("category", cat)
        rec.setdefault("service", svc)
        rec.setdefault("outcome", outcome)
        rec.setdefault("env", "demo")
        rec.setdefault("span_id", secrets.token_hex(8))
        if ctx:
            rec.setdefault("source", {"ip": ctx["ip"], "user_agent": ctx["ua"], "session_id": ctx["session"], "channel": "web"})
            rec.setdefault("trace_id", ctx["trace_id"])
        rec["ts_utc"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        return super().append(rec)


def cur_user():
    ctx = REQ.get() or {}
    uid = ctx.get("user") or "asha"
    return uid, USERS.get(uid)


def require(*roles):
    """Server-side role check. A refusal is itself written to the audit trail."""
    uid, u = cur_user()
    if not u or u["role"] not in roles:
        try:
            WS["ws"].audit.append({"event": "ACCESS_DENIED", "request_id": (REQ.get() or {}).get("trace_id", "")[:16], "resource": {"type": "endpoint", "id": (REQ.get() or {}).get("path")}, "required_roles": list(roles), "role": u["role"] if u else None})
        except Exception:                                                              # noqa: BLE001
            pass
        raise HTTPException(403, f"{u['title'] if u else 'This user'} cannot do this (requires: {', '.join(roles)})")
    return uid, u


def sess():
    ws = WS["ws"]
    return ws.borrowers.get(CASE.get()) or ws.last


def new_session(name="Borrower"):
    return WS["ws"].add(name)


SEEDS = [
    ("Ramesh Kumar", [("Agent", "Hello sir, am I speaking with Ramesh Kumar?"), ("Customer", "Yes, speaking."), ("Agent", "Please confirm your registered mobile number."), ("Customer", "nine eight four five zero"),
                      ("Agent", "Yes, go on."), ("Customer", "one two three four five"), ("Agent", "Your PAN card number please."), ("Customer", "ABCPK1234L. This is Kumar here."),
                      ("Agent", "Your EMI of \u20b94,200 is due on the 5th. Thank you."), ("Customer", "Okay, I will pay by the 7th.")]),
    ("Priya Nair", [("Agent", "\u0b89\u0b99\u0bcd\u0b95\u0bb3\u0bcd \u0bae\u0bca\u0baa\u0bc8\u0bb2\u0bcd \u0b8e\u0ba3\u0bcd\u0ba3\u0bc8\u0b9a\u0bcd \u0b9a\u0bca\u0bb2\u0bcd\u0bb2\u0bc1\u0b99\u0bcd\u0b95\u0bb3\u0bcd."), ("Customer", "\u0b8e\u0ba9\u0bcd \u0ba8\u0bae\u0bcd\u0baa\u0bb0\u0bcd \u0b92\u0ba9\u0bcd\u0baa\u0ba4\u0bc1 \u0b8e\u0b9f\u0bcd\u0b9f\u0bc1 \u0ba8\u0bbe\u0ba9\u0bcd\u0b95\u0bc1 \u0b90\u0ba8\u0bcd\u0ba4\u0bc1"),
                    ("Agent", "\u0ba4\u0bca\u0b9f\u0bb0\u0bc1\u0b99\u0bcd\u0b95\u0bb3\u0bcd."), ("Customer", "\u0baa\u0bc2\u0b9c\u0bcd\u0baf\u0bae\u0bcd \u0b92\u0ba9\u0bcd\u0bb1\u0bc1 \u0b87\u0bb0\u0ba3\u0bcd\u0b9f\u0bc1 \u0bae\u0bc2\u0ba9\u0bcd\u0bb1\u0bc1 \u0ba8\u0bbe\u0ba9\u0bcd\u0b95\u0bc1 \u0b90\u0ba8\u0bcd\u0ba4\u0bc1. \u0b8e\u0ba9\u0bcd \u0baa\u0bc6\u0baf\u0bb0\u0bcd \u0baa\u0bbf\u0bb0\u0bbf\u0baf\u0bbe \u0ba8\u0bbe\u0baf\u0bb0\u0bcd.")]),
    ("Suresh Patil", [("Agent", "Please share your Aadhaar number."), ("Customer", "\u092e\u093e\u091d\u093e \u0928\u0902\u092c\u0930 \u0928\u090a \u0906\u0920 \u091a\u093e\u0930 \u092a\u093e\u091a \u0936\u0942\u0928\u094d\u092f \u090f\u0915 \u0926\u094b\u0928 \u0924\u0940\u0928 \u091a\u093e\u0930 \u092a\u093e\u091a \u0906\u0939\u0947. \u092e\u093e\u091d\u0947 \u0928\u093e\u0935 \u0938\u0941\u0930\u0947\u0936 \u092a\u093e\u091f\u0940\u0932 \u0906\u0939\u0947.")]),
    ("Lakshmi Devi", [("Agent", "Please confirm your email address."), ("Customer", "My email is lakshmi.devi@example.com"), ("Agent", "Thank you. Your EMI is due on the 5th."), ("Customer", "I will pay through UPI lakshmi@okbank")]),
]


SEED_PROFILES = [
    {"product": "Personal loan", "loan_ref": "\u2022\u20224821", "dpd": 32, "outstanding": 48200, "language": "English", "channel": "Call", "assignee": "Asha Rao"},
    {"product": "Two-wheeler loan", "loan_ref": "\u2022\u20227730", "dpd": 12, "outstanding": 21450, "language": "Tamil", "channel": "Call", "assignee": "Asha Rao"},
    {"product": "Business loan", "loan_ref": "\u2022\u20221904", "dpd": 58, "outstanding": 186000, "language": "Marathi", "channel": "Call", "assignee": "Asha Rao"},
    {"product": "Gold loan", "loan_ref": "\u2022\u20226657", "dpd": 5, "outstanding": 9800, "language": "English", "channel": "WhatsApp", "assignee": "Asha Rao"},
]


def seed_workspace():
    ws = WS["ws"]
    for (name, turns), prof in zip(SEEDS, SEED_PROFILES):
        s = ws.add(name, prof)
        for who, text in turns:
            _message(s, Msg(text=text, speaker=who))
    ws.last = next(iter(ws.borrowers.values()))


@asynccontextmanager
async def lifespan(app):
    t = time.perf_counter()
    DET["det"] = RecordingDetector(make_detector(model=True))
    WS["ws"] = Workspace()
    DET["det"].inner.detect_calls([[{"speaker": "Customer", "text": "warm up my number is nine eight four five"}]])     # first call is slow (model load)
    seed_workspace()
    url = f"http://127.0.0.1:{PORT}"
    print(f"\n{'=' * 64}\n  READY in {time.perf_counter() - t:.1f}s (vault: {WS['ws'].vault_kind}, {len(WS['ws'].borrowers)} sample cases)\n  Open  {url}   in your browser (Ctrl+C here to stop)\n{'=' * 64}\n", flush=True)
    if not os.environ.get("DEMO_NO_BROWSER"):
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:                                               # noqa: BLE001  (no browser available: the URL above is enough)
            pass
    yield


app = FastAPI(title="TE-03 demo console", lifespan=lifespan)
app.add_middleware(CaseMiddleware)


# ---------------------------------------------------------------- helpers
def _records(S_):
    return S_.audit.records()


def _slim_event(r):
    drop = {"prev_hash", "borrower_hash", "request_id"}
    out = {k: v for k, v in r.items() if k not in drop}
    out["hash"] = r["hash"][:12]
    out["prev"] = r["prev_hash"][:12]
    out["borrower"] = (r.get("borrower_hash") or "")[:10]
    out["request"] = r.get("request_id")
    out["kind_name"] = r.get("event") or r.get("kind") or r.get("type") or "record"
    if r.get("type") == "erasure":                                      # keep the chain view small
        out = {k: out[k] for k in ("seq", "ts", "hash", "prev", "borrower", "request", "kind_name", "status", "passes") if k in out}
    return out


def vault_rows(S_):
    """What the DATABASE holds for this borrower: ciphertext, never plaintext. (read straight from the vault tables)"""
    db = S_.vault.svc.ctx.db
    with db.transaction() as tx:
        cur = tx.execute("SELECT m.token, m.pii_type, r.ciphertext, r.nonce, r.key_id, r.purpose_id, r.status, r.created_at "
                         "FROM token_mappings m JOIN vault_records r ON r.vault_id = m.vault_id WHERE m.borrower_id = ? ORDER BY m.created_at, m.seq", (S_.B,))
        rows = [tuple(r) for r in cur.fetchall()]
        k = tx.execute("SELECT key_id, kek_version, dek_version, key_status, usage_count FROM borrower_keys WHERE borrower_id = ?", (S_.B,)).fetchall()
    return ([{"token": r[0], "label": r[1], "ciphertext": bytes(r[2]).hex(), "nonce": bytes(r[3]).hex(), "key_id": r[4][:10], "purpose": r[5],
              "status": r[6], "created": r[7]} for r in rows],
            [{"key_id": x[0][:10], "kek_version": x[1], "dek_version": x[2], "status": x[3], "uses": x[4]} for x in k])


def store_snapshot(S_):
    """This borrower's records in every store (other borrowers' records are not shown)."""
    known = S_.reg.known_for(S_.B)
    known_ids = {i for ids in known.values() for i in ids}
    planted_ids = {x.rsplit(":", 1)[-1] for x in S_.planted_items}
    out = []
    for a in StoreCatalog(S_.root).all():
        ids = [i for i, _, _ in a.records()]
        mine = [i for i in ids if i in known_ids or i in planted_ids]
        if not a.registered and not mine:
            continue
        rel = os.path.relpath(a.path, ROOT) if getattr(a, "path", None) else a.name
        out.append({"store": a.name, "path": rel, "registered_store": bool(a.registered), "count": len(mine),
                    "records": [{"id": i, "in_registry": i in known_ids} for i in mine[:300]],
                    "unregistered_records": sum(1 for i in mine if i not in known_ids)})
    return out


def lineage_graph(S_):
    reg = S_.reg
    refs = set(reg.borrower_refs.get(S_.B, ()))
    for r in list(refs):
        refs |= reg.descendants(r)
    nodes = []
    for r in sorted(refs):
        kind, ident = r.split(":", 1) if ":" in r else ("?", r)
        md = reg.ref_metadata.get(r, {})
        nodes.append({"id": r, "kind": kind, "label": ident, "purpose": md.get("purpose"), "consent_id": md.get("consent_id"),
                      "parents": sorted(reg.parents.get(r, ())), "children": sorted(reg.children.get(r, ())), "tokens": sorted(reg.ref_tokens.get(r, ())),
                      "conversations": sorted(reg.ref_conv.get(r, ()))})
    edges = [{"from": p, "to": c, "relation": reg.relation.get((p, c), "derived_from")} for p in refs for c in reg.children.get(p, ()) if c in refs]
    return {"borrower": "borrower (name kept only as keyed hash)", "conversation": S_.CID, "nodes": nodes, "edges": edges}


def raw_scan(S_, needles):
    """Independent check: plain text scan of every store file (not the agent). Returns COUNTS only."""
    hits = {n: 0 for n in needles}
    for dp, _, files in os.walk(S_.root):
        for f in files:
            if f.endswith((".jsonl", ".json")) and f not in ("audit_log.jsonl", "registry.json"):
                txt = open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read()
                for n in needles:
                    hits[n] += txt.count(n)
    return sum(hits.values())


# ---------------------------------------------------------------- API: status / content
@app.get("/api/status")
def status():
    s = sess()
    return {"session": s.sid, "vault": s.vault_kind, "detector": "rules + spoken engine + Chitragupta (XLM-R, int8 ONNX)", "llm_live": LLM["live"], "llm_model": BEDROCK_MODEL,
            "stores": "MOCK (JSONL files; real layout not provided)", "turns": s.n, "chain_len": len(_records(s)), "planted": s.planted, "erased": s.erased,
            "borrower_name": s.name, "conversation": s.CID, "borrower": s.B, "borrower_hash": s.audit.hash_borrower(s.B)[:16], "case": s.case, "cases": len(WS["ws"].borrowers), "user": {"id": cur_user()[0], **(cur_user()[1] or {})}, "users": [{"id": k, **v} for k, v in USERS.items() if v["type"] == "user"]}


@app.get("/api/content")
def content():
    return ui_content.build(ROOT)


class LlmToggle(BaseModel):
    live: bool


@app.post("/api/llm")
def llm_toggle(b: LlmToggle):
    LLM["live"], LLM["last_error"] = b.live, None
    return {"live": LLM["live"]}


class NewSession(BaseModel):
    name: str = "Borrower"
    product: str = "Personal loan"
    loan_last4: str = ""
    language: str = "English"


def case_summary(s):
    last = s.history[-1] if s.history else None
    ok = [h for h in s.history if h.get("status") not in ("blocked", "erased")]
    return {"sid": s.sid, "case": s.case, "hash": s.audit.hash_borrower(s.B)[:16], "messages": s.n, "state": "ERASED" if s.erased else "ACTIVE",
            "last_ts": last["ts"] if last else None, "preview": (ok[-1]["l3"].get("llm_saw") if ok and not s.erased else None), "planted": s.planted,
            "key_present": s.vault.has_key(s.B), "values": len(s.vault.entries_for_borrower(s.B)), "conversation": s.CID, "borrower": s.B,
            "profile": s.profile, "initials": "".join(w[0] for w in s.name.split()[:2]).upper()}


@app.get("/api/cases")
def cases():
    return [case_summary(x) for x in WS["ws"].borrowers.values()]


def consent_by_purpose(s):
    by = {}
    h = s.audit.hash_borrower(s.B)
    for r in s.audit.records():
        if (r.get("borrower_hash") or "")[:16] == h[:16] and (r.get("kind") in ("write_allowed", "write_rejected") or r.get("event") == "CONSENT_DENIED"):
            d = by.setdefault(r.get("purpose") or "service", {"allowed": 0, "refused": 0})
            d["allowed" if r.get("kind") == "write_allowed" else "refused"] += 1
    return by


def redact(h):
    """Only the agent role sees the conversation; every other role gets the protected form (tokens), never the typed text."""
    if h.get("status") == "erased":
        return h
    h = json.loads(json.dumps(h))
    h["text"] = ""
    for sp in h["l1"]["spans"]:
        sp["text"] = ""
    l3 = h["l3"]
    l3["reply_borrower"] = ""
    if l3.get("escaped"):
        l3["escaped"] = [{"label": e["label"], "text": "[withheld]"} for e in l3["escaped"]]
    return h


@app.get("/api/case/{sid}")
def case_detail(sid: str):
    s = WS["ws"].borrowers.get(sid)
    if not s:
        raise HTTPException(404, "no such case")
    uid, u = cur_user()
    hist = s.history if u and u["role"] == "agent" else [redact(h) for h in s.history]
    return {"summary": case_summary(s), "history": hist, "consent": consent_by_purpose(s), "redacted": not (u and u["role"] == "agent")}


@app.get("/api/stats")
def stats():
    ws = WS["ws"]
    recs = ws.audit.records()
    ok, bad, why = ws.audit.verify_chain()
    k = lambda x: sum(1 for r in recs if (r.get("event") or r.get("kind") or r.get("type")) == x)
    return {"records": len(recs), "chain_ok": ok, "writes_allowed": k("write_allowed"), "writes_refused": k("write_rejected") + k("CONSENT_DENIED"), "messages": len({r.get("request_id") for r in recs if r.get("event") == "L3_STARTED"}),
            "blocked": k("L3_BLOCKED"), "leaks_prevented": k("LEAK_PREVENTED"), "denied_access": k("ACCESS_DENIED"), "requests": len(ws.privacy), "head": ws.audit.head()}


@app.post("/api/session/new")
def session_new(b: NewSession):
    with LOCK:
        last4 = (b.loan_last4 or "".join(random.choice("0123456789") for _ in range(4)))[:4]
        s = WS["ws"].add(b.name.strip() or "Borrower", {"product": b.product, "loan_ref": "\u2022\u2022" + last4, "dpd": 0, "outstanding": 0, "language": b.language, "channel": "Call", "assignee": cur_user()[1]["name"] if cur_user()[1] else "Unassigned"})
    return case_summary(s)


# ---------------------------------------------------------------- API: one message through layers 1-5 and 7
class Msg(BaseModel):
    text: str
    speaker: str = "Customer"
    purpose: str = "service"
    detector: str = "normal"            # normal | blind
    consent_id: str | None = None
    llm_live: bool | None = None


@app.post("/api/message")
def message(m: Msg):
    require("agent")
    return _message(sess(), m)


def _message(s, m):
    with LOCK:
        if s.erased:
            raise HTTPException(409, "this borrower has been erased")
        before = len(_records(s))
        DET["det"].blind = (m.detector == "blind")
        if m.llm_live is not None:
            LLM["live"] = m.llm_live
        t0 = time.perf_counter()
        try:
            out = s.gw.handle(s.CID, m.speaker, m.text, audience="borrower", borrower_id=s.B, purpose=m.purpose, consent_id=m.consent_id, internal=True)
        finally:
            DET["det"].blind = False
        total_ms = (time.perf_counter() - t0) * 1000
        s.n += 1
        ok = out.get("status") in ("ok", "degraded")
        retries = out.get("retries", 0)
        masked = out.get("masked_text") if ok else None

        # --- layer 1: what the detector found on the ORIGINAL text
        spans = out.get("spans", []) if retries == 0 else merge_spans(DET["det"].last)
        spans = [sp for sp in spans if 0 <= sp["start"] < sp["end"] <= len(m.text)]
        toks = TOKEN.findall(masked) if masked else []
        l1 = {"spans": [], "ms": round(DET["det"].last_ms, 1), "blind": m.detector == "blind"}
        for i, sp in enumerate(spans):
            raw = sp.get("joined_value") or m.text[sp["start"]:sp["end"]]
            l1["spans"].append({"start": sp["start"], "end": sp["end"], "label": sp["label"], "sources": sp.get("sources", [sp.get("source")]), "conf": sp.get("conf"),
                                "uncertain": bool(sp.get("uncertain")), "text": m.text[sp["start"]:sp["end"]],
                                "token": toks[i] if len(toks) == len(spans) and retries == 0 else None})
            if ok:
                s.values[raw] = sp["label"]

        # --- layer 7: the events this request wrote to the hash chain
        recs = _records(s)
        new_events = [_slim_event(r) for r in recs[before:]]

        # --- layer 4 + 5: store the MASKED text through the consent guard, register lineage
        l4 = {"purpose": m.purpose, "gateway_decision": "denied" if str(out.get("reason", "")).startswith("consent") else "allowed", "writes": []}
        l5 = {"added": []}
        if ok and masked:
            rid = f"log-{s.case}-{s.n:03d}"
            tag = new_tag(m.purpose, consent_id=m.consent_id, explicit=False, note="loan servicing", borrower_id=s.B)
            written = guarded_write("logs", {"id": rid, "conversation_id": s.CID, "speaker": m.speaker, "text": masked, "consent": tag}, s.logs, s.audit)
            l4["writes"].append({"store": "logs", "id": rid, "allowed": written})
            if written:
                s.reg.register_write(s.B, "logs", rid, masked_text=masked, conversation_id=s.CID, purpose=m.purpose)
                l5["added"].append(ref("log", rid))
            cid_ = f"vec-{s.case}-{s.n:03d}"
            vrec = {"id": cid_, "conversation_id": s.CID, "text": masked, "embedding": [round(float(x), 4) for x in embed(masked)], "consent": tag}
            written = guarded_write("vector_store", vrec, s.vecs, s.audit)
            l4["writes"].append({"store": "vector_store", "id": cid_, "allowed": written})
            if written:
                r1 = s.reg.register_write(s.B, "vector_store", cid_, masked_text=masked, conversation_id=s.CID, purpose=m.purpose)
                l5["added"].append(r1)
                s.reg.register(s.B, ref("emb", cid_), parent=ref("chunk", cid_), relation="embedding_of", conversation_id=s.CID)
                l5["added"].append(ref("emb", cid_))
                s.chunks.append(cid_)
            recs = _records(s)
            new_events = [_slim_event(r) for r in recs[before:]]

        events = {e["kind_name"]: e for e in new_events}
        l3 = {"status": out.get("status"), "reason": out.get("reason"), "trace": out.get("trace", []), "retries": retries, "warnings": out.get("warnings", []),
              "audit": out.get("audit_results"), "masked_share": out.get("masked_share"), "pre_llm_ms": out.get("pre_llm_ms"), "ms": round(total_ms, 1),
              "llm_live": LLM["live"], "llm_error": LLM["last_error"],
              "stage_ms": {"detect": (events.get("L1_DETECT_COMPLETE") or {}).get("ms"), "tokenise": (events.get("L2_TOKENISE_COMPLETE") or {}).get("ms")}}
        l3["net_found"] = sum(int(e.get("values", 0)) for e in new_events if e["kind_name"] == "L2_TOKENISE_COMPLETE" and int(e.get("attempt", 0)) >= 1)
        if ok and m.detector == "blind":                      # ground truth: what the REAL detector would have found, and did any of it reach the LLM unmasked?
            oracle = DET["det"].inner.detect_calls([[{"speaker": m.speaker, "text": m.text}]])[0][-1]
            l3["escaped"] = [{"label": sp["label"], "text": m.text[sp["start"]:sp["end"]]} for sp in oracle if m.text[sp["start"]:sp["end"]] in (masked or "")]
        if ok:
            l3.update(llm_saw=masked, reply_logged=out.get("reply_masked"), reply_borrower=out.get("reply"), log_entry=out.get("log_entry"))
        if ok and masked:
            s.turns.append({"speaker": m.speaker, "masked": masked})
        if ok:
            METRICS["lat"].append(float(out.get("pre_llm_ms") or total_ms))
            METRICS["t"].append(time.time())
            METRICS["messages"] += 1
        entries, keys = vault_rows(s)
        l2 = {"tokens_this_message": sorted(set(toks)), "entries": entries, "keys": keys, "vault": s.vault_kind}
        res = {"turn": s.n, "speaker": m.speaker, "text": m.text, "status": out.get("status"), "reason": out.get("reason"),
                "request_id": out.get("request_id"), "ts": time.time(), "conversation_id": s.CID, "borrower_hash": s.audit.hash_borrower(s.B)[:16], "purpose": m.purpose,
                "canonical_purpose": out.get("purpose"), "consent_id": m.consent_id, "message_id": f"msg-{s.n:03d}",
                "l1": l1, "l2": l2, "l3": l3, "l4": l4, "l5": l5, "l7": {"events": new_events, "chain_len": len(recs), "head": recs[-1]["hash"][:16] if recs else None},
                "demo_note": "the raw text is returned only so the console can draw highlights; the production gateway never returns it"}
        s.history.append(res)
        return res


# ---------------------------------------------------------------- API: layer 2 (vault)
class VaultTry(BaseModel):
    token: str
    role: str                           # borrower_reply | agent_dashboard | log_pipeline | engineer


@app.post("/api/vault/attempt")
def vault_attempt(b: VaultTry):
    """Try to read a token as different callers. Only the borrower-reply path may rehydrate; everything else is denied by the vault itself."""
    s = sess()
    with LOCK:
        if b.role == "borrower_reply":
            try:
                got = s.vault.rehydrate_many(s.CID, [b.token])
                val = got.get(b.token) if isinstance(got, dict) else None
            except Exception as e:                                                          # noqa: BLE001
                return {"role": b.role, "allowed": False, "result": f"denied ({type(e).__name__})"}
            return {"role": b.role, "allowed": val is not None, "result": ("value returned to the borrower's own reply" if val is not None else "no value (token unknown or key destroyed)"),
                    "length": len(val) if val else 0}
        try:
            s.vault.get(s.CID, b.token, role=b.role)
        except Exception as e:                                                              # noqa: BLE001
            return {"role": b.role, "allowed": False, "result": f"denied by the vault ({getattr(e, 'code', type(e).__name__)})"}
        return {"role": b.role, "allowed": False, "result": "denied"}


# ---------------------------------------------------------------- API: layer 4 (consent)
@app.get("/api/consent/matrix")
def consent_matrix():
    stores = list(policy.POLICY)
    purposes = ["service", "collections", "compliance", "dispute", "analytics", "model_improvement"]
    cells = []
    for st in stores:
        for p in purposes:
            rule = policy.POLICY[st].get(p)
            cells.append({"store": st, "purpose": p, "rule": rule or "deny"})
    return {"stores": stores + ["crm_export"], "purposes": purposes, "cells": cells + [{"store": "crm_export", "purpose": p, "rule": "deny"} for p in purposes]}


class TryWrite(BaseModel):
    store: str
    purpose: str
    consent_id: str | None = None
    explicit: bool = False


@app.post("/api/consent/try")
def consent_try(b: TryWrite):
    require("privacy_officer", "dpo")
    """A real guarded_write against the session's mock store, with the real Layer 7 audit event. Reason comes from the event it wrote."""
    s = sess()
    with LOCK:
        if s.erased:
            raise HTTPException(409, "session already erased")
        adapter = s.adapters.get(b.store) or JsonlStore(os.path.join(s.root, f"{b.store}.jsonl"), b.store, "other")
        masked = (s.turns[-1]["masked"] if s.turns else "Please call back about <LOAN_1>.")
        rid = f"try-{uuid.uuid4().hex[:6]}"
        rec = {"id": rid, "conversation_id": s.CID, "text": masked, "consent": new_tag(b.purpose, consent_id=b.consent_id or None, explicit=b.explicit, borrower_id=s.B)}
        if b.store == "vector_store":
            rec["embedding"] = [round(float(x), 4) for x in embed(masked)]
        ok = guarded_write(b.store, rec, adapter, s.audit)
        last = _records(s)[-1]
        if ok and b.store in s.adapters:
            kind_store = b.store
            r = s.reg.register_write(s.B, kind_store, rid, masked_text=masked, conversation_id=s.CID, purpose=b.purpose, consent_id=b.consent_id or None)
        else:
            r = None
        if not ok:
            s.rejections += 1
        return {"allowed": ok, "reason": last.get("reason", "ok"), "event": _slim_event(last), "registered": r}


# ---------------------------------------------------------------- API: layer 5 (lineage) + stores
@app.get("/api/lineage")
def lineage():
    s = sess()
    t = time.perf_counter()
    g = lineage_graph(s)
    ms = (time.perf_counter() - t) * 1000
    return {"graph": g, "stores": store_snapshot(s), "planted": s.planted, "lookup_ms": round(ms, 2)}


@app.post("/api/plant")
def plant():
    """Simulate the real-world mess: copies the platform never registered. Values come from what the user actually typed in this session (fake)."""
    s = sess()
    with LOCK:
        if s.erased:
            raise HTTPException(409, "session already erased")
        if not s.values:
            raise HTTPException(400, "send a message that contains a name / phone / PAN first (or play the demo call)")
        by = {}
        for raw, label in s.values.items():                          # longest value per type: a number said in pieces is planted whole
            if len(raw) > len(by.get(label, "")):
                by[label] = raw
        name = by.get("PERSON") or by.get("NAME")
        phone = by.get("PHONE")
        pan = by.get("PAN")
        pieces = [f"{k}={v}" for k, v in (("name", name), ("mobile", phone), ("pan", pan)) if v] or [f"value={next(iter(s.values))}"]
        summary = "Summary: " + ", ".join(pieces) + ". Borrower promised to pay the EMI."
        s.vecs.put({"id": f"vec-derived-summary-{s.case}", "text": summary, "conversation_id": None, "embedding": [round(float(x), 4) for x in embed(summary)], "consent": new_tag("service", borrower_id=s.B)})
        s.logs.put({"id": f"log-debug-retry-{s.case}", "conversation_id": None, "consent": new_tag("service"), "text": "DEBUG retry payload " + " ".join(pieces)})
        with open(os.path.join(s.root, "debug_dump", f"app_debug_{s.case}.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": f"dbg-{s.case}", "text": "crash dump: customer " + " ".join(pieces)}) + "\n")
        s.reg.save(s.reg_path())
        s.planted = True
        s.planted_items = [f"vector_store:vec-derived-summary-{s.case}", f"logs:log-debug-retry-{s.case}", f"unmapped:debug_dump/app_debug_{s.case}.jsonl:dbg-{s.case}"]
        return {"planted": [{"store": "vector_store", "id": f"vec-derived-summary-{s.case}", "what": "derived summary chunk (not in the registry)"},
                            {"store": "logs", "id": f"log-debug-retry-{s.case}", "what": "application debug log holding RAW values"},
                            {"store": "unmapped:debug_dump/app_debug.jsonl", "id": "dbg-0", "what": "crash dump in a folder nobody registered"}],
                "stores": store_snapshot(s)}


# ---------------------------------------------------------------- API: layer 6 (erasure)
def req_summary(r):
    res = r.get("result")
    return {"request_id": r["request_id"], "case": r["case"], "sid": r["sid"], "type": r["type"], "state": r["state"], "reason": r["reason"], "created": r["created"], "updated": r["updated"],
            "initiator": r["initiator"], "history": r["history"], "status": res["status"] if res else None, "clean": res["clean"] if res else None, "secs": res["secs"] if res else None,
            "found": sum(res["stores_before"].values()) if res else None, "audit_seq": res["audit_seq"] if res else None, "ts": r["created"], "borrower_hash": r["borrower_hash"]}


@app.get("/api/erasure")
def erasure_view():
    s = sess()
    return {"requests": [req_summary(r) for r in s.ws.privacy], "borrower": {"id": s.B, "case": s.case, "sid": s.sid, "hash": s.audit.hash_borrower(s.B)[:16], "conversation": s.CID, "messages": s.n, "state": "ERASED" if s.erased else "ACTIVE"},
            "stores": store_snapshot(s), "vault_entries": len(s.vault.entries_for_borrower(s.B)), "key_present": s.vault.has_key(s.B), "planted": s.planted_items}


REASONS = ("borrower_request", "consent_withdrawn", "retention_expired")


class NewReq(BaseModel):
    sid: str
    reason: str = "borrower_request"
    note: str = ""


class Decision(BaseModel):
    note: str = ""


def _actor():
    uid, u = cur_user()
    return {"id": uid, "name": u["name"], "role": u["role"]}


def _step(r, s, step, event, note=""):
    ev = s.audit.append({"event": event, "request_id": r["request_id"], "borrower_hash": s.audit.hash_borrower(s.B), "resource": {"type": "privacy_request", "id": r["request_id"]},
                         "transition": f"{r['state']}", "reason": r["reason"], "has_note": bool(note)})
    r["history"].append({"step": step, "actor": _actor(), "ts": time.time(), "audit_seq": ev["seq"], "note": note})
    r["updated"] = time.time()


def _get_req(rid):
    r = next((x for x in WS["ws"].privacy if x["request_id"] == rid), None)
    if not r:
        raise HTTPException(404, "no such request")
    return r


@app.get("/api/privacy/requests")
def privacy_list():
    return [req_summary(r) for r in WS["ws"].privacy]


@app.get("/api/privacy/requests/{rid}")
def privacy_get(rid: str):
    r = _get_req(rid)
    return {**req_summary(r), "result": r.get("result"), "note": r.get("note")}


@app.post("/api/privacy/requests")
def privacy_create(b: NewReq):
    require("agent", "privacy_officer")
    ws = WS["ws"]
    s = ws.borrowers.get(b.sid)
    if not s:
        raise HTTPException(404, "no such case")
    if s.erased:
        raise HTTPException(409, "this borrower has already been erased")
    if b.reason not in REASONS:
        raise HTTPException(400, "unknown reason")
    if any(x["sid"] == b.sid and x["state"] in ("INITIATED", "RECOMMENDED", "APPROVED") for x in ws.privacy):
        raise HTTPException(409, "an open request already exists for this borrower")
    with LOCK:
        r = {"request_id": f"PR-{s.case}-{uuid.uuid4().hex[:4]}", "case": s.case, "sid": s.sid, "type": "erasure", "state": "INITIATED", "reason": b.reason, "note": b.note, "created": time.time(), "updated": time.time(),
             "initiator": _actor(), "history": [], "result": None, "borrower_hash": s.audit.hash_borrower(s.B)}
        ws.privacy.append(r)
        _step(r, s, "initiated", "PRIVACY_REQUEST_INITIATED", b.note)
    return req_summary(r)


@app.post("/api/privacy/requests/{rid}/recommend")
def privacy_recommend(rid: str, b: Decision):
    require("privacy_officer")
    r = _get_req(rid)
    s = WS["ws"].borrowers[r["sid"]]
    if r["state"] != "INITIATED":
        raise HTTPException(409, f"request is {r['state']}, not INITIATED")
    uid = cur_user()[0]
    if uid == r["initiator"]["id"]:
        s.audit.append({"event": "MAKER_CHECKER_VIOLATION", "request_id": rid, "borrower_hash": s.audit.hash_borrower(s.B), "resource": {"type": "privacy_request", "id": rid}, "attempted": "recommend"})
        raise HTTPException(409, "maker-checker: the person who initiated a request cannot recommend it")
    with LOCK:
        _step(r, s, "recommended", "PRIVACY_REQUEST_RECOMMENDED", b.note)
        r["state"] = "RECOMMENDED"
    return req_summary(r)


@app.post("/api/privacy/requests/{rid}/reject")
def privacy_reject(rid: str, b: Decision):
    require("privacy_officer", "dpo")
    r = _get_req(rid)
    s = WS["ws"].borrowers[r["sid"]]
    if r["state"] not in ("INITIATED", "RECOMMENDED"):
        raise HTTPException(409, f"request is {r['state']}")
    if not b.note.strip():
        raise HTTPException(400, "a rejection needs a reason")
    with LOCK:
        _step(r, s, "rejected", "PRIVACY_REQUEST_REJECTED", b.note)
        r["state"] = "REJECTED"
    return req_summary(r)


@app.post("/api/privacy/requests/{rid}/approve")
def privacy_approve(rid: str, b: Decision):
    """Final approval by the data protection officer. Only then does the erasure agent run."""
    require("dpo")
    r = _get_req(rid)
    s = WS["ws"].borrowers[r["sid"]]
    if r["state"] != "RECOMMENDED":
        raise HTTPException(409, f"request is {r['state']}, it must be RECOMMENDED first")
    uid = cur_user()[0]
    prior = {h["actor"]["id"] for h in r["history"]}
    if uid in prior:
        s.audit.append({"event": "MAKER_CHECKER_VIOLATION", "request_id": rid, "borrower_hash": s.audit.hash_borrower(s.B), "resource": {"type": "privacy_request", "id": rid}, "attempted": "approve"})
        raise HTTPException(409, "maker-checker: the approver must differ from the initiator and the recommender")
    with LOCK:
        _step(r, s, "approved", "PRIVACY_REQUEST_APPROVED", b.note)
        r["state"] = "APPROVED"
        r["result"] = _execute_erasure(s, rid)
        r["state"] = "EXECUTED"
        r["updated"] = time.time()
    return req_summary(r)


def _execute_erasure(s, request_id):
    with LOCK:
        if s.erased:
            raise HTTPException(409, "already erased")
        if not s.n:
            raise HTTPException(400, "nothing to erase yet")
        needles = [v for v in s.values if len(v) >= 4]
        s.reg.save(s.reg_path())
        before_stores = {x["store"]: x["count"] for x in store_snapshot(s)}
        raw_before = raw_scan(s, needles)
        vault_before = len(s.vault.entries_for_borrower(s.B))
        t0 = time.perf_counter()
        app_ = build_agent(s.root, s.vault, s.reg, s.audit, registry_path=s.reg_path())
        tok = REQ.set({**(REQ.get() or {}), "user": "erasure-agent"})
        try:
            out = erase(app_, {"request_id": request_id, "borrower_id": s.B})
        finally:
            REQ.reset(tok)
        secs = time.perf_counter() - t0
        ok, bad, why = s.audit.verify_chain()
        s.ws.reload_stores()
        after_snap = {x["store"]: x["count"] for x in store_snapshot(s)}
        after_stores = {k: after_snap.get(k, 0) for k in before_stores}
        raw_after = raw_scan(s, needles)
        toks = sorted(set(TOKEN.findall(" ".join(t.get("masked", "") for t in s.turns)))) if s.turns else []
        try:
            probe = s.vault.rehydrate_many(s.CID, toks[:1]) if toks else {}
            probe_result = "value returned" if probe and any(probe.values()) else "no value: the key is gone"
        except Exception as e:                                                      # noqa: BLE001
            probe_result = f"decrypt failed ({type(e).__name__})"
        s.gw.forget(s.CID)
        s.erased = True
        s.values.clear()
        s.turns.clear()
        s.history = [{"turn": h["turn"], "message_id": h["message_id"], "ts": h["ts"], "speaker": h["speaker"], "status": "erased"} for h in s.history]     # raw text must not outlive the erasure, even in this demo server
        rec = out["record"]
        s.last_erasure = rec
        clean = raw_after == 0 and not s.vault.entries_for_borrower(s.B)
        return {"request_id": rec["request_id"], "audit_seq": rec["seq"], "ts": rec["ts"], "full_hash": rec["hash"], "full_borrower_hash": rec["borrower_hash"], "status": out["status"], "trace": out["trace"], "secs": round(secs, 2), "stores_before": before_stores, "stores_after": after_stores,
                "raw_before": raw_before, "raw_after": raw_after, "vault_before": vault_before, "vault_after": len(s.vault.entries_for_borrower(s.B)),
                "key_present": s.vault.has_key(s.B), "probe": probe_result, "clean": clean, "record": {k: rec[k] for k in ("seq", "request_id", "status", "passes", "stores", "vault", "methods", "methods_run",
                                                                                                         "probabilistic", "unresolved_count", "unregistered_stores_with_hits", "escalate")},
                "hash": rec["hash"][:16], "borrower_hash": rec["borrower_hash"][:16], "proof": render_proof(rec, ok, why, head=s.audit.head()),
                "chain": {"ok": ok, "bad_seq": bad, "reason": why}}


# ---------------------------------------------------------------- API: layer 7 (audit)
@app.get("/api/audit/full")
def audit_full():
    require(*AUDIT_READ)
    """Every record with ALL its fields, plus a per-record check recomputed here: hash matches its body, prev_hash matches the record before, seq is in order."""
    import hashlib
    from layer7_audit.chain import GENESIS, _canon
    s = sess()
    prev, out = GENESIS, []
    for i, r in enumerate(_records(s)):
        body = {k: v for k, v in r.items() if k != "hash"}
        h = hashlib.sha256((prev + _canon(body)).encode()).hexdigest()
        out.append({**r, "kind_name": r.get("event") or r.get("kind") or r.get("type") or "record",
                    "_verify": {"hash_ok": h == r.get("hash"), "link_ok": r.get("prev_hash") == prev, "seq_ok": r.get("seq") == i}})
        prev = r.get("hash", prev)
    ok, bad, why = s.audit.verify_chain()
    return {"records": out, "length": len(out), "head": s.audit.head(), "genesis": GENESIS, "chain": {"ok": ok, "bad_seq": bad, "reason": why},
            "algorithm": "SHA-256(prev_hash + canonical_json(record))", "borrower_hash": s.audit.hash_borrower(s.B), "session": s.sid}


@app.get("/api/audit/export")
def audit_export():
    require(*AUDIT_READ)
    from fastapi.responses import Response
    ws = WS["ws"]
    data = open(ws.audit.path, "rb").read()
    digest = hashlib.sha256(data).hexdigest()
    n = data.count(b"\n")
    ok, _, _ = ws.audit.verify_chain()
    ws.audit.append({"event": "AUDIT_EXPORT", "request_id": (REQ.get() or {}).get("trace_id", "")[:16], "resource": {"type": "audit_log", "id": f"{n} records"}, "sha256": digest, "records": n, "chain_ok": ok})
    return Response(content=data, media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="audit_{ws.id}.jsonl"', "X-Audit-SHA256": digest, "X-Audit-Records": str(n), "X-Audit-Chain-Ok": str(ok).lower(),
                                                                              "Access-Control-Expose-Headers": "X-Audit-SHA256, X-Audit-Records, X-Audit-Chain-Ok, Content-Disposition"})


class AuditAccess(BaseModel):
    action: str
    ref: str = ""
    result: str = ""


@app.post("/api/audit/access")
def audit_access(b: AuditAccess):
    """Reading the audit log is itself audited."""
    require(*AUDIT_READ)
    ev = {"AUDIT_VIEW": "AUDIT_VIEW", "AUDIT_VERIFY": "AUDIT_VERIFY"}.get(b.action)
    if not ev:
        raise HTTPException(400, "unknown action")
    r = WS["ws"].audit.append({"event": ev, "request_id": (REQ.get() or {}).get("trace_id", "")[:16], "resource": {"type": "audit_record" if ev == "AUDIT_VIEW" else "audit_log", "id": b.ref[:64]}, "result": b.result[:40]})
    return {"seq": r["seq"]}


class DetectReq(BaseModel):
    text: str


@app.post("/api/detect")
def detect_only(b: DetectReq):
    """Layer 1 on its own: nothing is tokenised, stored or sent anywhere."""
    with LOCK:
        t = time.perf_counter()
        res = DET["det"].inner.detect_calls([[{"speaker": "Customer", "text": b.text}]])[0][-1]
        ms = (time.perf_counter() - t) * 1000
    spans = [sp for sp in merge_spans(res) if 0 <= sp["start"] < sp["end"] <= len(b.text)]
    return {"ms": round(ms, 1), "chars": len(b.text), "spans": [{"start": sp["start"], "end": sp["end"], "label": sp["label"], "sources": sp.get("sources", [sp.get("source")]), "conf": sp.get("conf"),
                                                                   "uncertain": bool(sp.get("uncertain")), "text": b.text[sp["start"]:sp["end"]], "entity": sp.get("entity"), "part": sp.get("part")} for sp in spans]}


@app.get("/api/vault")
def vault_view():
    s = sess()
    entries, keys = vault_rows(s)
    try:
        access = list(s.vault.access_log())[-200:]
    except Exception:                                                   # noqa: BLE001
        access = []
    return {"entries": entries, "keys": keys, "access_log": access, "borrower": s.B, "borrower_hash": s.audit.hash_borrower(s.B)[:16], "conversation": s.CID, "backend": s.vault_kind,
            "cipher": "AES-256-GCM", "kdf": "HKDF-SHA256 (master -> KEK); per-borrower DEK wrapped by the KEK", "blind_index": "HMAC-SHA256"}


@app.get("/api/audit")
def audit_view(limit: int = 80):
    require(*AUDIT_READ)
    s = sess()
    recs = _records(s)
    ok, bad, why = s.audit.verify_chain()
    return {"length": len(recs), "records": [_slim_event(r) for r in recs[-limit:]], "head": s.audit.head()[:16], "chain": {"ok": ok, "bad_seq": bad, "reason": why}}


@app.post("/api/audit/tamper")
def audit_tamper():
    require(*AUDIT_READ)
    """Edit one number inside a COPY of the log and verify the copy. The real log is never touched."""
    s = sess()
    with LOCK:
        lines = [l for l in open(s.audit.path, encoding="utf-8").read().splitlines() if l.strip()]
        if len(lines) < 3:
            raise HTTPException(400, "not enough audit records yet: send a message first")
        idx = len(lines) // 2
        r = json.loads(lines[idx])
        field = next((k for k in ("latency_ms", "passes", "values", "spans", "found", "retries") if isinstance(r.get(k), (int, float))), "ts")
        old = r[field]
        r[field] = old + 5
        lines[idx] = json.dumps(r, ensure_ascii=False)
        tmp = os.path.join(s.root, "audit_tampered_copy.jsonl")
        open(tmp, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        ok, bad, why = AuditLog(tmp, key=s.audit.key).verify_chain()
        os.remove(tmp)
        real_ok, _, _ = s.audit.verify_chain()
        return {"edited_seq": idx, "field": field, "old": old, "new": old + 5, "copy_verifies": ok, "detected_at": bad, "reason": why, "real_log_intact": real_ok}


# ---------------------------------------------------------------- API: layer 0 sample, tests
_CORPUS_INDEX = {}


def _corpus_rows(lang):
    path = os.path.join(ROOT, "data", "corpus_dev.jsonl")           # DEV only: test/final stay untouched
    if not _CORPUS_INDEX:
        with open(path, "rb") as f:
            off = 0
            for line in f:
                m = re.search(rb'"lang": "([a-z_]+)"', line[:60])
                _CORPUS_INDEX.setdefault(m.group(1).decode() if m else "?", []).append(off)
                off += len(line)
    return path, _CORPUS_INDEX.get(lang) or [o for v in _CORPUS_INDEX.values() for o in v]


@app.get("/api/data/list")
def data_list(lang: str = "", page: int = 0, size: int = 40):
    path, offs = _corpus_rows(lang)
    out = []
    with open(path, "rb") as f:
        for off in offs[page * size:(page + 1) * size]:
            f.seek(off)
            r = json.loads(f.readline())
            out.append({"id": r["id"], "lang": r["lang"], "scenario": r["scenario"], "channel": r["channel"], "form": r["form"], "primary_pii": r.get("primary_pii"),
                        "spans": len(r["spans"]), "turns": len(r["turns"]), "hard": r.get("hard"), "offset": off})
    return {"rows": out, "total": len(offs), "page": page, "size": size, "languages": sorted(k for k in _CORPUS_INDEX if k != "?"), "per_language": {k: len(v) for k, v in _CORPUS_INDEX.items() if k != "?"}}


@app.get("/api/data/row")
def data_row(offset: int):
    path, _ = _corpus_rows("")
    with open(path, "rb") as f:
        f.seek(offset)
        r = json.loads(f.readline())
    return {"id": r["id"], "lang": r["lang"], "scenario": r["scenario"], "channel": r["channel"], "form": r["form"], "text": r["text"], "turns": r["turns"],
            "spans": [{"start": x["start"], "end": x["end"], "type": x["type"], "turn": x.get("turn"), "rendering": x.get("rendering"), "value": x.get("value"), "group": x.get("group_id")} for x in r["spans"]]}


@app.get("/api/data/sample")
def data_sample(lang: str = "", seed: int = 0):
    path, offs = _corpus_rows(lang)
    off = random.Random(seed or time.time_ns()).choice(offs)
    with open(path, "rb") as f:
        f.seek(off)
        row = json.loads(f.readline())
    return {"id": row["id"], "lang": row["lang"], "scenario": row["scenario"], "channel": row["channel"], "form": row["form"], "text": row["text"],
            "spans": [{"start": x["start"], "end": x["end"], "type": x["type"], "rendering": x.get("rendering")} for x in row["spans"]],
            "languages": sorted(k for k in _CORPUS_INDEX if k != "?")}


@app.post("/api/tests/{layer}")
def run_tests(layer: str):
    mods = ui_content.SUITES.get(layer)
    if not mods:
        raise HTTPException(404, "no suite for that layer")
    res = []
    for mod in mods:
        t = time.perf_counter()
        try:
            p = subprocess.run([sys.executable, "-m", mod], cwd=ROOT, capture_output=True, text=True, timeout=900, env=dict(os.environ, VAULT_TEST_DB_URL=""))
            txt = (p.stdout or "") + (p.stderr or "")
            skipped = 0
            m = re.search(r"(\d+) tests passed, (\d+) failed(?:, (\d+) skipped)?", txt)         # suite-level total (layer 2 prints per-file lines and a total)
            if m:
                passed, failed, skipped = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            else:
                ms = re.findall(r"(\d+) passed(?:, (\d+) failed)?", txt)
                if ms:
                    passed, failed = int(ms[-1][0]), int(ms[-1][1] or 0)
                else:
                    passed, failed = len(re.findall(r"^PASS ", txt, re.M)), len(re.findall(r"^FAIL ", txt, re.M))
            res.append({"module": mod, "passed": passed, "failed": failed, "skipped": skipped, "ok": p.returncode == 0 and failed == 0 and passed > 0,
                        "secs": round(time.perf_counter() - t, 1), "tail": [l for l in txt.strip().splitlines()[-3:]]})
        except subprocess.TimeoutExpired:
            res.append({"module": mod, "passed": 0, "failed": 0, "ok": False, "secs": 900, "tail": ["timed out"]})
    return {"layer": layer, "results": res, "ok": all(r["ok"] for r in res), "passed": sum(r["passed"] for r in res), "failed": sum(r["failed"] for r in res),
            "skipped": sum(r.get("skipped", 0) for r in res)}


@app.get("/api/architecture.jpg")
def architecture_image():
    return FileResponse(os.path.join(ROOT, "docs", "te03_architecture.jpg"), media_type="image/jpeg")


# ---------------------------------------------------------------- console (dpdp-privacy-console.html): read-only summaries over the same real layers
CONSOLE_HTML = os.path.join(ROOT, "dpdp-privacy-console.html")
FEED_KINDS = ("write_allowed", "write_rejected", "write_failed", "CONSENT_DENIED", "L3_BLOCKED", "LEAK_PREVENTED")


def _kind(r):
    return r.get("event") or r.get("kind") or r.get("type") or ""


@app.get("/console")
def console_page():
    return FileResponse(CONSOLE_HTML, headers={"Cache-Control": "no-store"})


@app.get("/api/console/overview")
def console_overview():
    """Health + gauges for the top bar and the gateway view. Every number is measured here, now, from the running layers (no text, no raw values)."""
    ws = WS["ws"]
    gw = ws.gw
    recs = ws.audit.records()
    ok, bad, why = ws.audit.verify_chain()
    lat = sorted(METRICS["lat"])
    n = len(lat)
    now_wall = time.time()
    per_min = sum(1 for t in METRICS["t"] if now_wall - t <= 60)
    now = gw.clock()
    idle = {c: now - t for c, t in list(gw.last_seen.items())}
    ttl = gw.history_ttl
    stale = sum(1 for v in idle.values() if v > ttl + 65)                      # the sweeper runs every 60 s: more than that late = the TTL is not working
    tag = {"purpose": "service"}
    t = time.perf_counter()
    for _ in range(2000):
        policy.decide("logs", tag)
    decide_us = (time.perf_counter() - t) / 2000 * 1e6
    entities = sum(int(r.get("values", 0) or 0) for r in recs if r.get("event") == "L2_TOKENISE_COMPLETE")
    k = lambda x: sum(1 for r in recs if _kind(r) == x)
    live = [x for x in ws.borrowers.values() if not x.erased]
    done = [r for r in ws.privacy if r.get("result")]
    layers = [
        {"id": "L0", "name": "Test data", "ok": os.path.exists(os.path.join(ROOT, "data", "corpus_dev.jsonl")), "detail": "labelled synthetic corpus present"},
        {"id": "L1", "name": "Detection", "ok": DET["det"] is not None, "detail": f"last call {DET['det'].last_ms:.0f} ms" if DET["det"] else "not loaded"},
        {"id": "L2", "name": "Vault", "ok": ws.vault is not None, "detail": f"backend: {ws.vault_kind}"},
        {"id": "L3", "name": "Gateway", "ok": stale == 0, "detail": f"{len(idle)} conversations in memory, idle TTL {int(ttl)} s"},
        {"id": "L4", "name": "Consent", "ok": bool(policy.POLICY), "detail": f"{len(policy.POLICY)} stores, default deny"},
        {"id": "L5", "name": "Lineage", "ok": ws.reg is not None, "detail": f"{len(ws.reg.borrower_refs)} borrowers indexed"},
        {"id": "L6", "name": "Erasure", "ok": True, "detail": f"{len(done)} executed"},
        {"id": "L7", "name": "Audit", "ok": ok, "detail": f"{len(recs)} records, chain {'intact' if ok else 'BROKEN at #' + str(bad)}"},
    ]
    return {"layers": layers, "healthy": all(x["ok"] for x in layers),
            "latency": {"n": n, "last": round(lat and METRICS["lat"][-1] or 0, 1), "p50": round(lat[n // 2], 1) if n else None, "p95": round(lat[min(n - 1, int(n * .95))], 1) if n else None,
                        "max": round(lat[-1], 1) if n else None, "budget_ms": 50, "over_budget": sum(1 for v in lat if v > 50)},
            "throughput": {"per_min": per_min, "messages_total": METRICS["messages"]},
            "memory": {"ttl_s": int(ttl), "conversations": len(idle), "stale": stale, "freshest_remaining_s": int(max(0, ttl - min(idle.values()))) if idle else int(ttl),
                       "oldest_idle_s": int(max(idle.values())) if idle else 0},
            "entities_tokenised": entities, "vault": ws.vault_kind,
            "consent": {"stores": len(policy.POLICY), "decide_us": round(decide_us, 1), "allowed": k("write_allowed"), "refused": k("write_rejected") + k("CONSENT_DENIED"),
                        "unverified": sum(1 for r in recs if r.get("kind") == "write_allowed" and r.get("consent_verified") is False)},
            "chain": {"ok": ok, "length": len(recs), "head": ws.audit.head(), "bad_seq": bad, "erasures_sealed": sum(1 for r in recs if r.get("type") == "erasure" and r.get("status") == "ERASED_VERIFIED")},
            "cases": {"total": len(ws.borrowers), "active": len(live)}, "llm_live": LLM["live"], "server_ts": now_wall}


@app.get("/api/console/feed")
def console_feed(after: int = -1, limit: int = 60):
    """The consent firewall's decisions as the audit chain recorded them (ids and reasons only)."""
    require(*AUDIT_READ)
    recs = _records(sess())
    out = []
    for r in recs:
        kind = _kind(r)
        if r.get("seq", -1) > after and kind in FEED_KINDS:
            out.append({"seq": r.get("seq"), "ts": r.get("ts_utc") or "", "kind": kind, "store": r.get("store"), "purpose": r.get("purpose"), "reason": r.get("reason"),
                        "record": r.get("record_id"), "borrower": (r.get("borrower_hash") or "")[:8], "consent_id": r.get("consent_id"), "verified": r.get("consent_verified"),
                        "actor": (r.get("actor") or {}).get("name")})
    return {"events": out[-limit:], "head_seq": recs[-1]["seq"] if recs else -1}


@app.get("/api/console/ablation")
def console_ablation():
    """The erasure ablation table, parsed from layer6_erasure/LAYER6_CONTEXT.md section 5 (a recorded run on the public set; not re-run here)."""
    path = os.path.join(ROOT, "layer6_erasure", "LAYER6_CONTEXT.md")
    rows = []
    try:
        txt = open(path, encoding="utf-8").read().split("## 5.", 1)[1].split("## 6.", 1)[0]
        for a, b, c in re.findall(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*([^|]*?)\s*\|", txt, re.M):
            if a.strip().lower().startswith(("method", "---")):
                continue
            rows.append({"config": a.strip(), "silent": int(b), "missed": c.strip()})
    except Exception:                                                           # noqa: BLE001
        pass
    return {"rows": rows, "source": "layer6_erasure/LAYER6_CONTEXT.md section 5", "set": "public set: 10 requests, 187 seeded copies, mock stores", "recorded": "19 Nov (not re-run live)"}


@app.get("/api/console/detection")
def console_detection():
    """Layer 1 scores parsed from LAYER1_REPORT.md: the fresh held-out split scored ONCE for the improved detector (old vs new), if the report has it."""
    path = os.path.join(ROOT, "layer1_detection", "LAYER1_REPORT.md")
    try:
        txt = open(path, encoding="utf-8").read()
        sec = txt.split("### Fresh held-out split", 1)[1].split("\n### ", 1)[0]
        rows = {k.split()[0].lower(): [float(a), float(b), float(c), int(d), int(e), float(f)] for k, a, b, c, d, e, f in re.findall(
            r"^\| (old detector|NEW detector) \| \**([\d.]+)%\** \| \**([\d.]+)%\** \| ([\d.]+)% \| (\d+) of (\d+) \(([\d.]+)%\)", sec, re.M)}
        calls, spans = re.search(r"([\d,]+) calls, ([\d,]+) gold spans", sec).groups()
        tline, lline = re.search(r"By type, fully masked old -> new: (.*?)\. By language variant, new: (.*?) \(old weakest", sec, re.S).groups()
        types = []
        for tok in re.split(r",\s*", tline.replace("UPI / DOB / EMAIL 100", "UPI 100, DOB 100, EMAIL 100")):
            m = re.match(r"([A-Z]+) ([\d.]+) -> ([\d.]+)", tok.strip())
            if m:
                types.append({"name": m.group(1), "old": float(m.group(2)), "new": float(m.group(3))})
                continue
            m = re.match(r"([A-Z]+) ([\d.]+)", tok.strip())
            if m:
                types.append({"name": m.group(1), "old": float(m.group(2)), "new": float(m.group(2))})
        langs = [{"name": a, "new": float(b)} for a, b in re.findall(r"(\w+) ([\d.]+)", lline)]
        amt = re.search(r"amounts (\d+) -> (\d+) of ([\d,]+)", sec)
        commit = re.search(r"frozen at commit (\w+)", sec)
        if "new" not in rows or "old" not in rows or not types or not langs:
            return {"fresh": None}
        keys = ("full", "masked50", "label", "fa_n", "fa_of", "fa_pct")
        return {"fresh": {"calls": calls, "spans": spans, "commit": commit.group(1) if commit else None, "old": dict(zip(keys, rows["old"])), "new": dict(zip(keys, rows["new"])), "types": types, "langs": langs,
                          "amounts": {"old": int(amt.group(1)), "new": int(amt.group(2)), "of": amt.group(3)} if amt else None, "source": "layer1_detection/LAYER1_REPORT.md"}}
    except Exception:                                                           # noqa: BLE001
        return {"fresh": None}


@app.get("/api/console/sample")
def console_sample(lang: str = "", seed: int = 0):
    """One synthetic call (dev split only) as turns, so the console can play it through the real gateway."""
    path, offs = _corpus_rows(lang)
    off = random.Random(seed or time.time_ns()).choice(offs)
    with open(path, "rb") as f:
        f.seek(off)
        row = json.loads(f.readline())
    return {"id": row["id"], "lang": row["lang"], "channel": row["channel"], "scenario": row["scenario"], "turns": [{"speaker": t["speaker"], "text": t["text"]} for t in row["turns"]][:14],
            "languages": sorted(k for k in _CORPUS_INDEX if k != "?")}


# ---------------------------------------------------------------- static front end
@app.get("/")
def index():
    return FileResponse(CONSOLE_HTML, headers={"Cache-Control": "no-store"})          # the DPDP Privacy Console is the front door


@app.get("/app")
def workspace_app():
    """The earlier multi-role workspace (conversations, privacy requests, audit, story mode). Same layers, same server."""
    return FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=WEB), name="static")


def main():
    try:
        import uvicorn
    except ImportError:
        sys.exit("uvicorn is not installed for THIS Python (" + sys.executable + ").\nUse the project environment:  .venv/bin/python layer8_demo/server.py   (or: pip install uvicorn)")
    import socket
    with socket.socket() as sk:
        if sk.connect_ex(("127.0.0.1", PORT)) == 0:
            sys.exit(f"Port {PORT} is already in use (is the demo already running?). Open http://127.0.0.1:{PORT} or set DEMO_PORT=9000.")
    print(f"Starting the demo server (loading the detector, about 8 s)...", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
