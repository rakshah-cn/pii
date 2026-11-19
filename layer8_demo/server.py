"""Layer 8 demo server: a thin web layer over the REAL layers 1-7.   .venv/bin/python -m layer8_demo.server   ->  http://127.0.0.1:8808

Nothing here re-implements a layer. Each endpoint calls the layer's own code and returns what it did, so the front end can show
the same message as every layer saw it. Honest scope (the UI says the same):
  * all values typed or generated are FAKE; the stores (logs / vectors / eval sets) are MOCK JSONL files under data/stores/ui/
  * the LLM is a stub unless the user switches "live LLM" on (then only TOKENISED text goes to Bedrock Nova Pro)
  * /api/message returns the raw text the user just typed (to draw the highlights). The production gateway never returns it.
"""
import collections
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
class Session:
    def __init__(self, name="Ramesh Kumar"):
        self.sid = "S" + uuid.uuid4().hex[:7]
        self.root = os.path.join(UI_ROOT, self.sid)
        os.makedirs(os.path.join(self.root, "debug_dump"), exist_ok=True)
        self.name = name
        self.B, self.CID = f"B-{self.sid}", f"conv-{self.sid}"
        self.vault, self.vault_kind = get_vault()
        self.audit = AuditLog(os.path.join(self.root, "audit_log.jsonl"))
        self.events = Events(sink=Layer7Sink(self.audit))
        self.gw = Gateway(detector=DET["det"], vault=self.vault, events=self.events, llm=_llm)
        self.reg = LineageRegistry()
        self.reg.add_borrower(self.B, name=name)
        self.reg.add_conversation(self.B, self.CID)
        self.logs = JsonlStore(os.path.join(self.root, "logs.jsonl"), "logs", "logs")
        self.vecs = JsonlStore(os.path.join(self.root, "vectors.jsonl"), "vector_store", "vector_store")
        self.evals = JsonlStore(os.path.join(self.root, "eval_sets.jsonl"), "eval_sets", "eval_sets")
        self.adapters = {"logs": self.logs, "vector_store": self.vecs, "eval_sets": self.evals}
        self.turns, self.values, self.n = [], collections.OrderedDict(), 0     # values: raw -> label (server memory only; never returned)
        self.planted, self.erased, self.last_erasure = False, False, None
        self.chunks = []                                                       # (record id) of chunks for lineage / eval writes
        self.rejections = 0

    def reg_path(self):
        return os.path.join(self.root, "registry.json")


S = {"s": None}


def sess():
    if S["s"] is None:
        S["s"] = Session()
    return S["s"]


def new_session(name="Ramesh Kumar"):
    S["s"] = Session(name)
    return S["s"]


@asynccontextmanager
async def lifespan(app):
    t = time.perf_counter()
    DET["det"] = RecordingDetector(make_detector(model=True))
    sess()
    DET["det"].inner.detect_calls([[{"speaker": "Customer", "text": "warm up my number is nine eight four five"}]])     # first call is slow (model load)
    url = f"http://127.0.0.1:{PORT}"
    print(f"\n{'=' * 64}\n  READY in {time.perf_counter() - t:.1f}s (vault: {S['s'].vault_kind})\n  Open  {url}   in your browser (Ctrl+C here to stop)\n{'=' * 64}\n", flush=True)
    if not os.environ.get("DEMO_NO_BROWSER"):
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:                                               # noqa: BLE001  (no browser available: the URL above is enough)
            pass
    yield


app = FastAPI(title="TE-03 demo console", lifespan=lifespan)


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
    known = S_.reg.known_for(S_.B)
    known_ids = {i for ids in known.values() for i in ids}
    out = []
    for a in StoreCatalog(S_.root).all():
        ids = [i for i, _, _ in a.records()]
        out.append({"store": a.name, "registered_store": bool(a.registered), "count": a.count(),
                    "records": [{"id": i, "in_registry": i in known_ids} for i in ids[:40]],
                    "unregistered_records": sum(1 for i in ids if i not in known_ids)})
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
        nodes.append({"id": r, "kind": kind, "label": ident, "purpose": md.get("purpose"), "consent_id": md.get("consent_id")})
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
            "borrower_name": s.name}


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
    name: str = "Ramesh Kumar"


@app.post("/api/session/new")
def session_new(b: NewSession):
    with LOCK:
        new_session(b.name.strip() or "Ramesh Kumar")
    return status()


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
    with LOCK:
        s = sess()
        if s.erased:
            raise HTTPException(409, "session already erased: start a new session")
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
            rid = f"log-{s.n:03d}"
            tag = new_tag(m.purpose, consent_id=m.consent_id, explicit=False, note="loan servicing", borrower_id=s.B)
            written = guarded_write("logs", {"id": rid, "conversation_id": s.CID, "speaker": m.speaker, "text": masked, "consent": tag}, s.logs, s.audit)
            l4["writes"].append({"store": "logs", "id": rid, "allowed": written})
            if written:
                s.reg.register_write(s.B, "logs", rid, masked_text=masked, conversation_id=s.CID, purpose=m.purpose)
                l5["added"].append(ref("log", rid))
            cid_ = f"vec-{s.n:03d}"
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
        entries, keys = vault_rows(s)
        l2 = {"tokens_this_message": sorted(set(toks)), "entries": entries, "keys": keys, "vault": s.vault_kind}
        return {"turn": s.n, "speaker": m.speaker, "text": m.text, "status": out.get("status"), "reason": out.get("reason"),
                "l1": l1, "l2": l2, "l3": l3, "l4": l4, "l5": l5, "l7": {"events": new_events, "chain_len": len(recs), "head": recs[-1]["hash"][:16] if recs else None},
                "demo_note": "the raw text is returned only so the console can draw highlights; the production gateway never returns it"}


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
    return {"graph": lineage_graph(s), "stores": store_snapshot(s), "planted": s.planted}


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
        s.vecs.put({"id": "vec-derived-summary", "text": summary, "conversation_id": None, "embedding": [round(float(x), 4) for x in embed(summary)], "consent": new_tag("service", borrower_id=s.B)})
        s.logs.put({"id": "log-debug-retry", "conversation_id": None, "consent": new_tag("service"), "text": "DEBUG retry payload " + " ".join(pieces)})
        with open(os.path.join(s.root, "debug_dump", "app_debug.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "dbg-0", "text": "crash dump: customer " + " ".join(pieces)}) + "\n")
        s.reg.save(s.reg_path())
        s.planted = True
        return {"planted": [{"store": "vector_store", "id": "vec-derived-summary", "what": "derived summary chunk (not in the registry)"},
                            {"store": "logs", "id": "log-debug-retry", "what": "application debug log holding RAW values"},
                            {"store": "unmapped:debug_dump/app_debug.jsonl", "id": "dbg-0", "what": "crash dump in a folder nobody registered"}],
                "stores": store_snapshot(s)}


# ---------------------------------------------------------------- API: layer 6 (erasure)
@app.post("/api/erase")
def erase_borrower():
    s = sess()
    with LOCK:
        if s.erased:
            raise HTTPException(409, "already erased")
        if not s.n:
            raise HTTPException(400, "nothing to erase yet")
        needles = [v for v in s.values if len(v) >= 4]
        s.reg.save(s.reg_path())
        before_stores = {a.name: a.count() for a in StoreCatalog(s.root).all()}
        raw_before = raw_scan(s, needles)
        vault_before = len(s.vault.entries_for_borrower(s.B))
        t0 = time.perf_counter()
        app_ = build_agent(s.root, s.vault, s.reg, s.audit, registry_path=s.reg_path())
        out = erase(app_, {"request_id": f"ER-{s.sid}", "borrower_id": s.B})
        secs = time.perf_counter() - t0
        ok, bad, why = s.audit.verify_chain()
        after_stores = {a.name: a.count() for a in StoreCatalog(s.root).all()}
        raw_after = raw_scan(s, needles)
        toks = sorted(set(TOKEN.findall(" ".join(t.get("masked", "") for t in s.turns)))) if s.turns else []
        try:
            probe = s.vault.rehydrate_many(s.CID, toks[:1]) if toks else {}
            probe_result = "value returned" if probe and any(probe.values()) else "no value: the key is gone"
        except Exception as e:                                                      # noqa: BLE001
            probe_result = f"decrypt failed ({type(e).__name__})"
        s.gw.forget(s.CID)
        s.erased = True
        rec = out["record"]
        s.last_erasure = rec
        clean = raw_after == 0 and not s.vault.entries_for_borrower(s.B)
        return {"status": out["status"], "trace": out["trace"], "secs": round(secs, 2), "stores_before": before_stores, "stores_after": after_stores,
                "raw_before": raw_before, "raw_after": raw_after, "vault_before": vault_before, "vault_after": len(s.vault.entries_for_borrower(s.B)),
                "key_present": s.vault.has_key(s.B), "probe": probe_result, "clean": clean, "record": {k: rec[k] for k in ("seq", "request_id", "status", "passes", "stores", "vault", "methods", "methods_run",
                                                                                                         "probabilistic", "unresolved_count", "unregistered_stores_with_hits", "escalate")},
                "hash": rec["hash"][:16], "borrower_hash": rec["borrower_hash"][:16], "proof": render_proof(rec, ok, why, head=s.audit.head()),
                "chain": {"ok": ok, "bad_seq": bad, "reason": why}}


# ---------------------------------------------------------------- API: layer 7 (audit)
@app.get("/api/audit/full")
def audit_full():
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
    from fastapi.responses import Response
    s = sess()
    data = open(s.audit.path, "rb").read()
    return Response(content=data, media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="audit_{s.sid}.jsonl"'})


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
    s = sess()
    recs = _records(s)
    ok, bad, why = s.audit.verify_chain()
    return {"length": len(recs), "records": [_slim_event(r) for r in recs[-limit:]], "head": s.audit.head()[:16], "chain": {"ok": ok, "bad_seq": bad, "reason": why}}


@app.post("/api/audit/tamper")
def audit_tamper():
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


# ---------------------------------------------------------------- static front end
@app.get("/")
def index():
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
