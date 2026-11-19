"""Layer 3: the gateway as a LangGraph state graph - the single fail-closed checkpoint between people and the LLM.

    START -> initialize(+consent, Layer 4) -> detect(Layer 1) -> tokenise(Layer 2, ONE batch) -> final_audit --(clean)--> llm_dispatch -> sanitise -> rehydrate_route -> END
                    \\--(denied/invalid)->block      \\--(error)->block   \\--(vault error)->block   |  \\--(retry, max 2)--> tokenise     \\--(fail)--> block
Everything that can go wrong ends in `block` (fail closed). Only masked text is ever logged, sent to the LLM or emitted to Layer 7.
Uncertain detections (number fragments, id-shaped guesses) are MASKED by default: there is no review step (Layer 1's policy: if unsure, mask).
The audit has four independent checks on the tokenised text (digit tripwire, spoken-number tripwire, survival of known values, utility/over-masking) plus two extras
(a second pattern family and a re-scan with the Layer-1 rules - extra safety, not required by the spec). Replies are rehydrated only for audience="borrower",
after unknown tokens and invented PII were removed.

Use:  gw = Gateway();  out = gw.handle("call-1", "Customer", "my number is 98450 12345")        or   gw.process({"request_id":..., "borrower_id":..., "conversation_id":..., "purpose":..., "text":...})
"""
import collections
import concurrent.futures
import hashlib
import os
import re
import threading
import time
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from layer1_detection.detector import Detector
from layer1_detection.name_memory import NameMemory, _parts as _name_parts
from layer1_detection.normalise import normalise
from layer2_vault.core.errors import VaultError
from layer3_gateway import audit as au
from layer3_gateway import llm as llmmod
from layer3_gateway import router
from layer3_gateway.consent import ConsentGate
from layer3_gateway.events import Events
from layer3_gateway.vault_client import VaultClient

TOKEN = re.compile(r"<[A-Z_]+_\d+>")
UNCERTAIN = {"NUMBER_FRAGMENT", "ID_ALNUM", "ID_FRAGMENT"}     # masked like everything else; kept only to document which labels Layer 1 is unsure about
MAX_RETRIES = 2
HISTORY_TURNS = 6          # earlier raw turns kept IN MEMORY ONLY, so a number said in pieces can be joined
OVERMASK_WARN = 0.6        # a message longer than 20 characters whose tokenised form is more than this share placeholders gets a warning ...
OVERMASK_BLOCK = au.UTILITY_MAX_SHARE   # ... and is BLOCKED above 85% (audit check 4)
DETECT_TIMEOUT = 10.0      # seconds; None is NOT allowed to mean 'wait forever'
MAX_STUCK_DETECTIONS = 3   # abandoned (timed-out) detector calls tolerated before failing closed outright
REPLAY_CACHE_MAX = 1000    # sanitised, TOKENISED replies kept so a retried request does not call the LLM again (bounded; lost on restart)
MODEL_CACHE_MAX = 2000     # Layer 1's model keeps an in-memory result cache that is ALWAYS on (use_cache=False only stops the disk copy): bounded here so a long-running gateway cannot grow without limit
SAFE_KEYS = ("request_id", "conversation_id", "status", "reason", "masked_text", "reply", "reply_masked", "log_entry", "trace", "retries",
             "warnings", "masked_share", "turn_no", "audit_results", "pre_llm_ms")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GatewayState(TypedDict, total=False):
    request_id: str
    conversation_id: str
    speaker: str
    text: str               # incoming raw text of this turn (memory only)
    work_text: str          # text currently being masked
    spans: list             # spans on work_text
    masked_text: str
    retries: int
    status: str             # ok | blocked | degraded
    reason: str
    reply: str              # rehydrated reply for the borrower only (never stored)
    raw_values: list        # raw values masked on the first pass (memory only; used by the audit survival check)
    audit: str              # clean | retry | fail
    audit_results: dict     # COUNTS per check, never values
    trace: list
    audience: str           # "borrower" (rehydrate) or anything else (never rehydrate)
    borrower_id: Any
    purpose: str            # canonical purpose after the consent check
    consent_id: Any
    turn_no: int
    masked_share: float
    warnings: list
    llm_reply: str
    replayed: bool          # True when the reply came from the retry cache (the LLM was not called again)
    reply_masked: str       # the tokenised reply: this is what gets logged
    log_entry: str
    pre_llm_ms: float       # entry -> LLM dispatch boundary (the 50 ms budget covers this)
    _ev: Any
    _t0: float


def merge_spans(spans):
    """Union of overlapping spans from different layers (rules, spoken engine, model)."""
    out = []
    for s in sorted(spans, key=lambda x: (x["start"], -x["end"])):
        if out and s["start"] < out[-1]["end"]:
            if s["end"] > out[-1]["end"]:
                out[-1]["end"] = s["end"]
            out[-1]["source"] += "+" + s["source"] if s["source"] not in out[-1]["source"] else ""
            if (s["start"], s["end"]) != (out[-1]["start"], out[-1]["end"]):
                out[-1].pop("token", None)                 # boundaries changed: mint a fresh token for the merged text
        else:
            out.append(dict(s))
    return out


def substitute(text, spans, tokens_by_id):
    """Replace spans (right to left) with their tokens."""
    for s in sorted(spans, key=lambda x: -x["start"]):
        text = text[:s["start"]] + tokens_by_id[id(s)] + text[s["end"]:]
    return text


def _n(v):
    return re.sub(r"[\s-]", "", str(v)).casefold()


class _Pending:
    """Stands for 'the k-th NEW value of this batch' until the vault has answered."""
    __slots__ = ("k",)

    def __init__(self, k):
        self.k = k


def make_detector(model=True, threads=None):
    """Layer-1 detector for the gateway. Model results are NOT cached to disk here (real traffic).
    threads: CPU threads for the model (ONNX intra-op + torch). Layer 1 hard-codes 8 per process, which oversubscribes the machine as soon as you run several gateway processes
    (measured: throughput FELL from 46 to 30 msg/s going from 1 to 8 processes). Give each process roughly cores / processes. Default (None): the GATEWAY_MODEL_THREADS environment variable, else Layer 1's own default."""
    # Fast path: model on the NEWEST turn only (earlier window turns are history for cues/joining, which the
    # rules cover) + int8 ONNX when exported. Measured p50 ~10 ms / p99 ~17 ms per message (synthetic, loaded CPU);
    # the old all-turns setting was p50 33 ms / p99 61 ms. Falls back to the torch model if ONNX is absent.
    onnx = os.path.isdir(os.path.join(ROOT, "models", "chitragupta_onnx"))
    det = Detector(use_rules=True, use_model=model, model_path=os.path.join(ROOT, "models", "chitragupta"),
                   model_last_only=True, model_backend="onnx_int8" if onnx else "torch")
    if det.model:
        det.model.use_cache = False
        threads = threads or int(os.environ.get("GATEWAY_MODEL_THREADS", "0")) or None
        if threads and onnx:
            from layer1_detection.model import OrtShim
            det.model._load()
            det.model.m.model = OrtShim(os.path.join(ROOT, "models", "chitragupta_onnx", "model_int8.onnx"), det.model.m.model.config, threads=threads)
            import torch
            torch.set_num_threads(threads)
    return det


# reason code -> HTTP-style status for process()
def http_status(reason):
    r = reason or ""
    if r.startswith("consent_denied") or r == "consent_denied":
        return 403
    if r in ("empty_or_invalid_input", "invalid_request"):
        return 422
    if r in ("over_masking", "residual_pii_after_retries"):
        return 422
    return 503          # a dependency (detector, vault, consent service, LLM, audit sink) is unavailable: nothing was sent


class Gateway:
    def __init__(self, detector=None, audit_detector=None, vault=None, llm=None, detect_timeout=DETECT_TIMEOUT, overmask_action="block",
                 consent=None, events=None, llm_timeout=llmmod.DEFAULT_TIMEOUT, shared_context=True):
        self.detector = detector or make_detector(model=True)
        self.audit_detector = audit_detector or Detector(use_rules=True, use_model=False)  # fast, deterministic
        self.vault = self._as_client(vault) if vault is not None else VaultClient()   # the modular, database-backed Layer 2 vault: needs VAULT_MASTER_KEY + VAULT_DB_URL / VAULT_DB_PATH (or an explicit dev opt-out)
        self.consent = consent or ConsentGate()                                            # Layer 4
        self.events = events or Events()                                                   # Layer 7 (in-memory sink by default)
        self.llm = llm or self._stub_llm
        self.llm_timeout = llm_timeout
        self.detect_timeout = DETECT_TIMEOUT if detect_timeout is None else detect_timeout
        self._stuck = []               # futures of detector calls that timed out and are still running
        self.overmask_action = overmask_action          # "block" (default) or "flag"
        self.entity_tokens = {}        # (conversation_id, entity id) -> {"token","turn","joined"}: one token per number said in pieces
        self.replies = collections.OrderedDict()   # (request_id, conversation, hash of text) -> sanitised tokenised reply: makes a client retry idempotent at the LLM too
        self.log = []                  # masked text only (never rehydrated text)
        self.history = {}              # conversation_id -> the last HISTORY_TURNS raw turns (memory; also kept ENCRYPTED in the vault when shared_context is on)
        self.turns = {}                # conversation_id -> number of turns seen (history is trimmed, the count is not)
        self.shared_context = shared_context and hasattr(self.vault, "load_context")
        self.ctx_version = {}          # conversation_id -> version of the shared context this process last saw / wrote
        self.context_stats = collections.Counter()
        self.name_tokens = {}          # conversation_id -> {name part (casefold): token of the full name}
        self.names = {}                # conversation_id -> NameMemory: parts of detected names, whole conversation (memory only, never logged)
        self._trim_lock = threading.Lock()
        self.graph = self._build()

    @staticmethod
    def _as_client(vault):
        """The gateway needs batch tokenise + rehydrate_many. A plain ModularVault is upgraded; the old single-file prototype vault is not supported any more."""
        if hasattr(vault, "tokenize_batch") and hasattr(vault, "rehydrate_many"):
            return vault
        if hasattr(vault, "svc"):
            return VaultClient(services=vault.svc)
        raise TypeError("Gateway needs a layer2_vault VaultClient (the modular vault); the legacy single-file Vault is no longer supported")

    def forget(self, conversation_id):
        """Drop every in-memory trace of a conversation (history, joined-number state, name parts). Erasure hook."""
        self.history.pop(conversation_id, None)
        self.turns.pop(conversation_id, None)
        self.ctx_version.pop(conversation_id, None)
        self.names.pop(conversation_id, None)
        self.name_tokens.pop(conversation_id, None)
        for k in [k for k in self.entity_tokens if k[0] == conversation_id]:
            self.entity_tokens.pop(k, None)

    # ---- the LLM call (stub: replace with llm.make_bedrock_llm(...); it only ever receives masked text)
    @staticmethod
    def _stub_llm(masked_text):
        toks = list(dict.fromkeys(TOKEN.findall(masked_text)))
        return "Thank you. We have noted " + ", ".join(toks) + "." if toks else "Thank you. Noted."

    # ---- nodes -------------------------------------------------------------------------------
    # ---- shared conversation context: several gateway processes can serve one conversation ------------------------------------
    def _snapshot(self, cid):
        nm = self.names.get(cid)
        return {"v": 1, "n": self.turns.get(cid, 0), "history": self.history.get(cid, [])[-HISTORY_TURNS:], "names": dict(nm.parts) if nm else {},
                "name_tokens": {k: v for k, v in self.name_tokens.get(cid, {}).items() if isinstance(v, str)},
                "entities": [[k[1], dict(rec)] for k, rec in self.entity_tokens.items() if k[0] == cid and isinstance(rec["token"], str)]}

    def _apply(self, cid, state):
        if not isinstance(state, dict) or state.get("v") != 1:
            return
        self.history[cid] = [dict(t) for t in state.get("history", [])][-HISTORY_TURNS:]
        self.turns[cid] = int(state.get("n", len(self.history[cid])))
        nm = NameMemory(); nm.parts = dict(state.get("names", {})); self.names[cid] = nm
        self.name_tokens[cid] = dict(state.get("name_tokens", {}))
        for k in [k for k in self.entity_tokens if k[0] == cid]:
            self.entity_tokens.pop(k)
        for ent, rec in state.get("entities", []):
            self.entity_tokens[(cid, ent)] = rec

    def _sync_in(self, cid, borrower_id):
        """If ANOTHER process advanced this conversation since we last looked, adopt its memory (one cheap version check; the context is only decrypted when it changed)."""
        if not self.shared_context:
            return
        try:
            res = self.vault.load_context(cid, borrower_id, newer_than=self.ctx_version.get(cid))
        except Exception:
            self.context_stats["load_failed"] += 1
            return                                        # carry on with our own memory; the vault being down is handled (blocked) at tokenise
        if res and res[1] is not None:
            self._apply(cid, res[1]); self.ctx_version[cid] = res[0]; self.context_stats["adopted"] += 1
        elif res:
            self.ctx_version[cid] = res[0]

    def _sync_out(self, cid, borrower_id):
        if not self.shared_context:
            return
        try:
            self.ctx_version[cid] = self.vault.save_context(cid, borrower_id, self._snapshot(cid)); self.context_stats["saved"] += 1
        except Exception:
            self.context_stats["save_failed"] += 1        # never blocks the message: the context is a convenience for OTHER processes, our own memory is intact

    def _initialize(self, st):
        """Start of the request: events, input validation, and the Layer 4 consent / purpose check. A 'no' (or a Layer 4 that cannot answer) blocks before ANY detection."""
        ev = self.events.begin(st.get("request_id"), st.get("borrower_id"), st.get("purpose"))
        base = {"_ev": ev, "_t0": time.perf_counter(), "trace": ["initialize"]}
        text = st.get("text", "")
        if not isinstance(text, str) or not text.strip():
            ev.add("L3_STARTED")
            return {**base, "status": "blocked", "reason": "empty_or_invalid_input"}
        d = self.consent.check(st.get("borrower_id"), st.get("purpose", "service"), st.get("consent_id"))
        if d.allowed:
            ev.base["purpose"] = d.purpose                   # every event of a request carries the canonical purpose (what Layer 4 and the vault use)
        ev.add("L3_STARTED")
        if not d.allowed:
            ev.add("CONSENT_DENIED", reason=d.reason)
            return {**base, "status": "blocked", "reason": "consent_unavailable" if d.unavailable else f"consent_denied:{d.reason}"}
        assert len(normalise(text)) == len(text)        # offsets stay valid on the original text
        self._sync_in(st["conversation_id"], st.get("borrower_id"))
        return {**base, "work_text": text, "retries": 0, "status": "ok", "warnings": [], "purpose": d.purpose,
                "turn_no": self.turns.get(st["conversation_id"], 0)}

    def _detect(self, st):
        t = time.perf_counter()
        prior = self.history.get(st["conversation_id"], [])[-HISTORY_TURNS:]
        turns = prior + [{"speaker": st["speaker"], "text": st["text"]}]
        self._stuck = [f for f in self._stuck if not f.done()]
        if len(self._stuck) >= MAX_STUCK_DETECTIONS:                 # detector looks hung: do not pile up more threads
            return {"status": "blocked", "reason": "detector_unhealthy", "trace": st["trace"] + ["detect"]}
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)  # fresh worker per call: a hung call cannot starve the next
        fut = pool.submit(self.detector.detect_calls, [turns])
        pool.shutdown(wait=False)
        try:
            spans = fut.result(timeout=self.detect_timeout)[0][-1]
        except concurrent.futures.TimeoutError:
            self._stuck.append(fut)
            return {"status": "blocked", "reason": "detector_timeout", "trace": st["trace"] + ["detect"]}
        except Exception as e:                           # any detector failure -> fail closed
            return {"status": "blocked", "reason": f"detector_error:{type(e).__name__}", "trace": st["trace"] + ["detect"]}
        self._trim_model_cache()
        nm = self.names.setdefault(st["conversation_id"], NameMemory())
        spans = spans + nm.find(st["text"], spans)       # a name said earlier than the window is still masked (source='memory')
        nm.update(st["text"], spans)
        spans = merge_spans(spans)
        st["_ev"].add("L1_DETECT_COMPLETE", spans=len(spans), ms=round((time.perf_counter() - t) * 1000, 2))
        return {"spans": spans, "trace": st["trace"] + ["detect"]}

    def _trim_model_cache(self):
        """Keep Layer 1's always-on model cache bounded. Drops the OLDEST entries only: a concurrent detection reads the entry it has just written (always the newest), so trimming
        old ones can never raise KeyError in another thread - clearing everything could (proved with a widened-window test)."""
        mc = getattr(getattr(self.detector, "model", None), "cache", None)
        if not isinstance(mc, dict) or len(mc) <= MODEL_CACHE_MAX:
            return
        with self._trim_lock:
            try:
                for k in list(mc)[: max(0, len(mc) - MODEL_CACHE_MAX // 2)]:
                    mc.pop(k, None)
            except RuntimeError:                      # another thread changed the dict while we listed it: skip, the next message trims
                pass

    @staticmethod
    def _ok(st):
        return "block" if st.get("status") == "blocked" else "ok"

    def _plan_span(self, st, s, raw, known, items, deferred):
        """Decide what one span becomes WITHOUT calling the vault: an existing token, or 'the k-th new value of the batch'. One token per span - except pieces of ONE
        number (same `entity`, from Layer 1) share a token, and a name part reuses the token of the full name."""
        cid, turn = st["conversation_id"], st.get("turn_no", 0)

        def new(label, value):
            items.append((label, value))
            return _Pending(len(items) - 1)
        if s.get("token") and s["token"] in known:
            return s["token"]                                          # audit found a surviving copy of a known value
        if s["label"] in ("PERSON", "NAME"):
            parts = self.name_tokens.setdefault(cid, {})
            if raw.casefold() in parts:
                return parts[raw.casefold()]                           # 'Kumar' later (model- or memory-detected) -> same token as 'Ramesh Kumar'
            ref = new(s["label"], raw)
            for part in _name_parts(raw):
                parts.setdefault(part.casefold(), ref)
            return ref
        ent, joined = s.get("entity"), s.get("joined_value")
        if ent is None:
            return new(s["label"], raw)
        key = (cid, ent)
        rec = self.entity_tokens.get(key)
        if rec and turn - rec["turn"] > HISTORY_TURNS:
            rec = None                                                 # entity ids are only meaningful inside one window
        if rec and joined and rec.get("joined") and not (_n(joined).startswith(_n(rec["joined"])) or _n(rec["joined"]).startswith(_n(joined))):
            rec = None                                                 # same id, different number: do not merge
        if rec is None:
            ref = new(s["label"], joined or raw)
            self.entity_tokens[key] = {"token": ref, "turn": turn, "joined": joined, "pieces": [_n(raw)]}
            return ref
        if _n(raw) not in rec["pieces"] or joined:                    # a further piece of the same number
            deferred.append((rec["token"], None if joined else raw, joined))
            rec["pieces"].append(_n(raw))
        rec["turn"], rec["joined"] = turn, joined or rec.get("joined")
        return rec["token"]

    def _settle_refs(self, cid, answers):
        """Replace _Pending references held in the conversation memories by real tokens (answers = list of tokens), or - when the vault failed (answers=None) - forget them."""
        def fix(v):
            return answers[v.k] if isinstance(v, _Pending) and answers is not None else v
        parts = self.name_tokens.get(cid, {})
        for k in list(parts):
            parts[k] = fix(parts[k])
            if isinstance(parts[k], _Pending):
                del parts[k]
        for k in [k for k in self.entity_tokens if k[0] == cid]:
            rec = self.entity_tokens[k]
            rec["token"] = fix(rec["token"])
            if isinstance(rec["token"], _Pending):
                del self.entity_tokens[k]

    @staticmethod
    def _idem_key(request_id, attempt):
        """Idempotency key for the vault, derived from the gateway request_id: a hash, so any request id (short, odd characters) gives a key the vault accepts and the id itself is not
        stored there. The attempt number is part of it: an audit retry sends a DIFFERENT batch and must not collide with the first."""
        import hashlib
        return f"gw:{hashlib.sha256(str(request_id).encode()).hexdigest()[:32]}:t{attempt}"

    def _tokenise(self, st):
        t = time.perf_counter()
        cid, text, rid = st["conversation_id"], st["work_text"], st["request_id"]
        spans = sorted(st.get("spans", []), key=lambda x: x["start"])
        items, deferred, refs = [], [], {}
        try:
            known = set(self.vault.tokens(cid)) if any(s.get("token") for s in spans) else set()
            for s in spans:
                refs[id(s)] = self._plan_span(st, s, text[s["start"]:s["end"]], known, items, deferred)
            answers = [tok for tok, _ in self.vault.tokenize_batch(cid, st.get("borrower_id"), st["purpose"], items, idempotency_key=self._idem_key(rid, st.get("retries", 0)), request_id=rid)] if items else []
            self._settle_refs(cid, answers)
            resolve = lambda r: answers[r.k] if isinstance(r, _Pending) else r
            for ref, piece, joined in deferred:
                self.vault.extend(cid, resolve(ref), piece=piece, joined=joined)
            masked = substitute(text, spans, {k: resolve(v) for k, v in refs.items()})
        except VaultError as e:                                       # vault cannot encrypt/store: never send raw
            self._settle_refs(cid, None)
            return {"status": "blocked", "reason": f"vault_error:{e.code}", "trace": st["trace"] + ["tokenise"]}
        except Exception as e:
            self._settle_refs(cid, None)
            return {"status": "blocked", "reason": f"vault_error:{type(e).__name__}", "trace": st["trace"] + ["tokenise"]}
        upd = {"masked_text": masked, "trace": st["trace"] + ["tokenise"]}
        if st.get("retries", 0) == 0:                    # first pass: remember what was masked, for the audit survival check
            upd["raw_values"] = [text[s["start"]:s["end"]] for s in spans]
        st["_ev"].add("L2_TOKENISE_COMPLETE", values=len(items), spans=len(spans), ms=round((time.perf_counter() - t) * 1000, 2), attempt=st.get("retries", 0))
        return upd

    def _audit_route(self, st):
        return {"clean": "clean", "retry": "retry"}.get(st.get("audit"), "fail")

    def _final_audit(self, st):
        """Independent re-scan of the MASKED text. Anything still found is masked again (max 2 retries) or the message is blocked.
        Checks: 1 digit tripwire, 2 spoken-number tripwire, 3 survival (values this turn + values the vault holds), 4 utility (over-masking); extras: second pattern family, Layer-1 rules re-scan."""
        cid, masked, ev = st["conversation_id"], st["masked_text"], st["_ev"]
        neutral = router._blank(masked)                  # placeholders contain words like NUMBER and digits: blank them (same length) so they cannot trigger the checks
        t = st["trace"] + ["final_audit"]
        trip = au.tripwire(neutral)
        digits, spoken = [f for f in trip if f["label"] == "DIGITS"], [f for f in trip if f["label"] == "DIGIT_WORDS"]
        fam2 = au.family2(neutral)
        rescan = list(self.audit_detector.detect_calls([[{"speaker": "x", "text": neutral}]])[0][0])      # extra: Layer-1 rules re-scan
        survival = []
        try:
            for tok, a, b in self.vault.scan_text(cid, neutral):                                       # any value the vault holds still visible?
                survival.append({"label": "RESIDUAL", "start": a, "end": b, "source": "audit-vault", "token": tok})
        except VaultError as e:
            ev.add("L3_AUDIT_FAILED", reason=f"vault_error:{e.code}")
            return {"audit": "fail", "status": "blocked", "reason": f"vault_error:{e.code}", "trace": t}
        for rawv in {r for r in st.get("raw_values", []) if len(r) >= 4}:                              # raw values from this turn must not survive anywhere
            i = masked.find(rawv)
            while i != -1:
                survival.append({"label": "RESIDUAL", "start": i, "end": i + len(rawv), "source": "audit"})
                i = masked.find(rawv, i + len(rawv))
        util = au.utility(masked)
        results = {"digit_tripwire": len(digits), "spoken_tripwire": len(spoken), "survival": len(survival), "utility_share": util["share"], "utility_ok": util["ok"],
                   "family2": len(fam2), "rules_rescan": len(rescan)}
        warnings = list(st.get("warnings", []))
        if util["length"] > au.UTILITY_MIN_LEN and util["share"] > OVERMASK_WARN and not any(w.startswith("over_masking") for w in warnings):
            warnings.append(f"over_masking:{util['share']:.2f}")
        base = {"audit_results": results, "masked_share": util["share"], "warnings": warnings, "trace": t}
        if not util["ok"] and self.overmask_action == "block":                                          # check 4: nothing re-masking can fix
            ev.add("L3_AUDIT_FAILED", reason="over_masking", **results)
            return {**base, "audit": "fail", "status": "blocked", "reason": "over_masking"}
        found = merge_spans(digits + spoken + fam2 + rescan + survival)
        if not found:
            ev.add("L3_AUDIT_PASSED", retries=st.get("retries", 0), **results)
            return {**base, "audit": "clean", "pre_llm_ms": round((time.perf_counter() - st["_t0"]) * 1000, 2)}
        if st.get("retries", 0) < MAX_RETRIES:
            return {**base, "audit": "retry", "retries": st.get("retries", 0) + 1, "work_text": masked, "spans": found}
        ev.add("L3_AUDIT_FAILED", reason="residual_pii_after_retries", **results)
        ev.add("LEAK_PREVENTED", checks=sum(1 for k in ("digit_tripwire", "spoken_tripwire", "survival", "family2", "rules_rescan") if results[k]))
        return {**base, "audit": "fail", "status": "blocked", "reason": "residual_pii_after_retries"}

    def _replay_key(self, st):
        return (st["request_id"], st["conversation_id"], hashlib.sha256(st["text"].encode()).hexdigest())

    def _llm_dispatch(self, st):
        t = time.perf_counter()
        cached = self.replies.get(self._replay_key(st))
        if cached is not None:                                                  # a retry of a request that already got its answer: no second LLM call
            st["_ev"].add("LLM_RESPONDED", outcome="replayed", ms=0)
            return {"llm_reply": cached, "replayed": True, "trace": st["trace"] + ["llm_dispatch(replayed)"]}
        try:
            reply = llmmod.dispatch(self.llm, st["masked_text"], self.llm_timeout)    # the LLM sees audited, tokenised text only
        except llmmod.LLMTimeout:
            st["_ev"].add("LLM_RESPONDED", outcome="timeout", ms=round((time.perf_counter() - t) * 1000, 2))
            self.log.append({"conversation_id": st["conversation_id"], "text": st["masked_text"], "degraded": "llm_timeout", "warnings": st.get("warnings", [])})
            return {"status": "degraded", "reason": "llm_timeout", "reply": llmmod.DEGRADED_REPLY, "reply_masked": llmmod.DEGRADED_REPLY, "log_entry": llmmod.DEGRADED_REPLY,
                    "trace": st["trace"] + ["llm_dispatch"]}
        except Exception as e:                                                  # LLM failure: nothing is sent
            st["_ev"].add("LLM_RESPONDED", outcome="error", ms=round((time.perf_counter() - t) * 1000, 2))
            return {"status": "blocked", "reason": f"llm_error:{type(e).__name__}", "trace": st["trace"] + ["llm_dispatch"]}
        st["_ev"].add("LLM_RESPONDED", outcome="ok", ms=round((time.perf_counter() - t) * 1000, 2))
        return {"llm_reply": reply, "trace": st["trace"] + ["llm_dispatch"]}

    def _after_llm(self, st):
        return {"blocked": "block", "degraded": "end"}.get(st.get("status"), "ok")

    def _sanitise(self, st):
        cid = st["conversation_id"]
        try:
            clean, stats = router.sanitise(st["llm_reply"], self.vault.tokens(cid), self.audit_detector)
        except VaultError as e:
            return {"status": "blocked", "reason": f"vault_error:{e.code}", "trace": st["trace"] + ["sanitise"]}
        # the log gets the masked incoming text + the TOKENISED reply, never rehydrated text
        if not st.get("replayed"):
            self.log.append({"conversation_id": cid, "text": st["masked_text"], "reply_masked": clean, "warnings": st.get("warnings", [])})
            self.replies[self._replay_key(st)] = clean                          # only after a successful, sanitised answer (failures are never cached)
            while len(self.replies) > REPLAY_CACHE_MAX:
                self.replies.popitem(last=False)
        return {"reply_masked": clean, "trace": st["trace"] + ["sanitise"]}

    def _rehydrate_route(self, st):
        cid = st["conversation_id"]
        try:
            reply, log_entry = router.route(st["reply_masked"], st.get("audience", "borrower"), lambda toks: self.vault.rehydrate_many(cid, toks))
        except VaultError as e:
            return {"status": "blocked", "reply": "", "reason": f"rehydration_failed:{e.code}", "trace": st["trace"] + ["rehydrate_route"]}
        return {"reply": reply, "log_entry": log_entry, "status": "ok", "trace": st["trace"] + ["rehydrate_route"]}

    def _block(self, st):
        self.log.append({"conversation_id": st.get("conversation_id"), "blocked": st.get("reason", "unknown")})
        ev = st.get("_ev")
        if ev:
            ev.add("L3_BLOCKED", reason=(st.get("reason", "unknown").split(":")[0]))
        return {"status": "blocked", "reply": "", "trace": st.get("trace", []) + ["block"]}

    # ---- wiring --------------------------------------------------------------------------------
    def _build(self):
        g = StateGraph(GatewayState)
        for name, fn in (("initialize", self._initialize), ("detect", self._detect), ("tokenise", self._tokenise), ("final_audit", self._final_audit),
                         ("llm_dispatch", self._llm_dispatch), ("sanitise", self._sanitise), ("rehydrate_route", self._rehydrate_route), ("block", self._block)):
            g.add_node(name, fn)
        g.add_edge(START, "initialize")
        g.add_conditional_edges("initialize", self._ok, {"ok": "detect", "block": "block"})
        g.add_conditional_edges("detect", self._ok, {"ok": "tokenise", "block": "block"})
        g.add_conditional_edges("tokenise", self._ok, {"ok": "final_audit", "block": "block"})
        g.add_conditional_edges("final_audit", self._audit_route, {"clean": "llm_dispatch", "retry": "tokenise", "fail": "block"})
        g.add_conditional_edges("llm_dispatch", self._after_llm, {"ok": "sanitise", "block": "block", "end": END})
        g.add_conditional_edges("sanitise", self._ok, {"ok": "rehydrate_route", "block": "block"})
        g.add_conditional_edges("rehydrate_route", self._ok, {"ok": END, "block": "block"})
        g.add_edge("block", END)
        return g.compile()

    def handle(self, conversation_id: str, speaker: str, text: str, audience: str = "borrower", borrower_id=None, purpose: str = "service",
               consent_id=None, internal: bool = False, request_id: str = None) -> dict[str, Any]:
        """Returns a SANITISED result (no raw input, no raw spans/values, and no residual text for blocked messages).
        internal=True returns the whole graph state (raw data included): for tests/diagnostics inside the trust boundary."""
        t0 = time.perf_counter()
        out = self.graph.invoke({"request_id": request_id or "gw-" + uuid.uuid4().hex[:12], "conversation_id": conversation_id, "speaker": speaker, "text": text,
                                 "trace": [], "audience": audience, "borrower_id": borrower_id, "purpose": purpose, "consent_id": consent_id})
        if isinstance(text, str) and not out.get("replayed") and out.get("reason") not in ("empty_or_invalid_input",) and not str(out.get("reason", "")).startswith("consent"):
            h = self.history.setdefault(conversation_id, []); h.append({"speaker": speaker, "text": text}); del h[:-HISTORY_TURNS]     # bounded: only the window is ever used
            self.turns[conversation_id] = self.turns.get(conversation_id, 0) + 1
            self._sync_out(conversation_id, borrower_id)
        ev = out.pop("_ev", None)
        out.pop("_t0", None)
        if ev is not None and not ev.flush() and self.events.strict:           # the audit trail could not be written: withhold the answer
            out.update(status="blocked", reason="audit_sink_failed", reply="", log_entry="")
        ms = (time.perf_counter() - t0) * 1000
        if internal:
            out["ms"] = ms
            return out
        safe = {k: out[k] for k in SAFE_KEYS if k in out}
        if safe.get("status") == "blocked":                         # a blocked message may still contain residual PII
            for k in ("masked_text", "reply_masked", "log_entry"):
                safe.pop(k, None)
            safe["reply"] = ""
        safe["ms"] = ms
        return safe

    def process(self, request: dict) -> dict[str, Any]:
        """The gateway API: {request_id, borrower_id, conversation_id, purpose, text[, speaker, audience, consent_id]} ->
        {status: success|degraded|blocked, borrower_reply, log_entry, request_id[, http_status, reason]}. Blocked answers carry a CODE only, never text."""
        need = ("request_id", "borrower_id", "conversation_id", "purpose", "text")
        if not isinstance(request, dict) or any(not isinstance(request.get(k), str) or not request.get(k).strip() for k in need):
            return {"status": "blocked", "http_status": 422, "reason": "invalid_request", "borrower_reply": "", "log_entry": ""}
        r = self.handle(request["conversation_id"], request.get("speaker", "Customer"), request["text"], audience=request.get("audience", "borrower"),
                        borrower_id=request["borrower_id"], purpose=request["purpose"], consent_id=request.get("consent_id"), request_id=request["request_id"])
        if r["status"] == "blocked":
            reason = r.get("reason", "blocked")
            return {"status": "blocked", "http_status": http_status(reason), "reason": reason.split(":")[0] if reason.startswith(("consent", "vault_error", "detector_error", "llm_error", "rehydration_failed")) else reason,
                    "request_id": request["request_id"], "borrower_reply": "", "log_entry": ""}
        return {"status": "success" if r["status"] == "ok" else r["status"], "request_id": request["request_id"], "borrower_reply": r.get("reply", ""), "log_entry": r.get("log_entry", ""),
                "pre_llm_ms": r.get("pre_llm_ms")}

    def diagram(self) -> str:
        return self.graph.get_graph().draw_mermaid()
