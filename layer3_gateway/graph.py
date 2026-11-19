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
import contextlib
import hashlib
import json
import os
import re
import threading
import time
import uuid
import weakref
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from layer1_detection.detector import Detector
from layer1_detection.name_memory import NameMemory, _parts as _name_parts
from layer1_detection.normalise import normalise
from layer2_vault.core.errors import VaultDenied, VaultError
from layer2_vault.services.context_service import MAX_BYTES as CONTEXT_MAX_BYTES
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
HISTORY_TTL = 900.0        # seconds (15 min): everything the gateway remembers about a conversation is dropped after this much idle time (DPDP: no raw text kept longer than needed)
MAX_MESSAGE_CHARS = int(os.environ.get("GATEWAY_MAX_MESSAGE_CHARS", "4000"))   # a real turn is far shorter (the longest of 210,000 synthetic turns: 392 characters). One huge message must not be able to freeze the process.
LOCK_STRIPES = 64          # same-conversation requests take one of these locks around memory + tokenise (striped: bounded memory, no clean-up, no lock ordering problem: a request holds one at a time)
NOT_REMEMBERED = ("empty_or_invalid_input", "invalid_request", "message_too_long")   # reasons whose raw text must never be kept in memory or in the shared context
_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,128}")


def _is_id(v):
    """Identifiers go into events, stores and the audit chain: only the safe alphabet is accepted (free text in an id would bypass every 'no free text' rule downstream)."""
    return isinstance(v, str) and _ID.fullmatch(v) is not None


LOG_MAX = 10000            # the in-memory (masked) log keeps the newest entries only
REPLAY_CACHE_MAX = 1000    # this process's local copy of sanitised, TOKENISED replies (bounded). The SOURCE OF TRUTH is the encrypted shared context in the vault (below)
CONTEXT_REPLIES = 8        # replies per conversation kept in the vault context: enough for a client retrying its last few requests, small enough to stay far below the size cap
CONTEXT_REPLY_MAX_CHARS = 4000
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
    issued: list            # tokens the vault issued for THIS message (all attempts): the only token-shaped strings the audit may blank
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
    erased: bool            # the vault says this borrower is erased / being erased: nothing about this message may be remembered
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
    if r == "message_too_long":
        return 413
    if r in ("empty_or_invalid_input", "invalid_request"):
        return 422
    if r in ("over_masking", "residual_pii_after_retries"):
        return 422
    return 503          # a dependency (detector, vault, consent service, LLM, audit sink) is unavailable: nothing was sent


class Gateway:
    def __init__(self, detector=None, audit_detector=None, vault=None, llm=None, detect_timeout=DETECT_TIMEOUT, overmask_action="block",
                 consent=None, events=None, llm_timeout=llmmod.DEFAULT_TIMEOUT, shared_context=True,
                 history_ttl=HISTORY_TTL, sweep_interval=60.0, clock=None, persistence=None):
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
        self.persistence = persistence               # optional layer3_gateway.persist.LogPersistence: tokenised log record via Layer 4's guarded write + Layer 5 lineage
        self.persist_stats = collections.Counter()
        self.clock = clock or time.monotonic
        self.history_ttl = history_ttl
        self.last_seen = {}            # conversation_id -> clock() of its last processed message (drives the idle TTL)
        self.owner_of = {}             # conversation_id -> borrower id (or "conv:<id>"): lets an erasure flush every conversation of a borrower
        self._last_sweep = self.clock()
        self.name_tokens = {}          # conversation_id -> {name part (casefold): token of the full name}
        self.names = {}                # conversation_id -> NameMemory: parts of detected names, whole conversation (memory only, never logged)
        self._trim_lock = threading.Lock()
        self._stripes = [threading.Lock() for _ in range(LOCK_STRIPES)]
        self.graph = self._build()
        if sweep_interval:
            self._start_sweeper(sweep_interval)

    @staticmethod
    def _as_client(vault):
        """The gateway needs batch tokenise + rehydrate_many. A plain ModularVault is upgraded; the old single-file prototype vault is not supported any more."""
        if hasattr(vault, "tokenize_batch") and hasattr(vault, "rehydrate_many"):
            return vault
        if hasattr(vault, "svc"):
            return VaultClient(services=vault.svc)
        raise TypeError("Gateway needs a layer2_vault VaultClient (the modular vault); the legacy single-file Vault is no longer supported")

    def forget(self, conversation_id):
        """Drop every in-memory trace of a conversation (raw history, joined-number state, name parts, retry-cache replies). Erasure hook; also used by the idle TTL."""
        self.history.pop(conversation_id, None)
        self.turns.pop(conversation_id, None)
        self.ctx_version.pop(conversation_id, None)
        self.last_seen.pop(conversation_id, None)
        self.owner_of.pop(conversation_id, None)
        for k in [k for k in list(self.replies) if k[1] == conversation_id]:           # list(): a C-level copy; iterating the live dict while another thread adds to it raises
            self.replies.pop(k, None)
        self.names.pop(conversation_id, None)
        self.name_tokens.pop(conversation_id, None)
        for k in [k for k in list(self.entity_tokens) if k[0] == conversation_id]:
            self.entity_tokens.pop(k, None)

    # ---- the LLM call (stub: replace with llm.make_bedrock_llm(...); it only ever receives masked text)
    @staticmethod
    def _stub_llm(masked_text):
        toks = list(dict.fromkeys(TOKEN.findall(masked_text)))
        return "Thank you. We have noted " + ", ".join(toks) + "." if toks else "Thank you. Noted."

    @contextlib.contextmanager
    def _stripe(self, cid, wait=30.0):
        """The lock for one conversation (shared by the conversations that hash to it): taken around the memory + tokenise steps, so two requests of ONE conversation can never swap each
        other's pending token references (found by review: a name was rehydrated as a phone number). A bounded wait: if it cannot be had the request fails closed."""
        lock = self._stripes[hash(cid) % LOCK_STRIPES]
        if not lock.acquire(timeout=wait):
            raise TimeoutError("conversation_busy")
        try:
            yield
        finally:
            lock.release()

    # ---- nodes -------------------------------------------------------------------------------
    def forget_borrower(self, borrower_id):
        """Flush EVERY conversation this process remembers for a borrower (erasure hook). Returns how many were dropped."""
        cids = [c for c, o in list(self.owner_of.items()) if o == borrower_id]
        for c in cids:
            self.forget(c)
        return len(cids)

    def purge_idle(self):
        """Drop every conversation idle for longer than history_ttl. Returns how many."""
        now, n = self.clock(), 0
        for cid, t in list(self.last_seen.items()):
            if now - t > self.history_ttl:
                self.forget(cid); n += 1
        self._last_sweep = now
        self.context_stats["expired"] += n
        return n

    def _start_sweeper(self, interval):
        """A daemon thread enforces the idle TTL even when the process gets no traffic. It holds only a weak reference, so it never keeps a gateway alive."""
        ref = weakref.ref(self)

        def loop():
            while True:
                time.sleep(interval)
                g = ref()
                if g is None:
                    return
                try:
                    g.purge_idle()
                except Exception:
                    pass
                del g
        threading.Thread(target=loop, daemon=True, name="gateway-ttl-sweeper").start()

    # ---- shared conversation context: several gateway processes can serve one conversation ------------------------------------
    def _snapshot(self, cid):
        nm = self.names.get(cid)
        snap = {"v": 1, "n": self.turns.get(cid, 0), "history": list(self.history.get(cid, []))[-HISTORY_TURNS:], "names": dict(nm.parts) if nm else {},
                "name_tokens": {k: v for k, v in list(self.name_tokens.get(cid, {}).items()) if isinstance(v, str)},
                "entities": [[k[1], dict(rec)] for k, rec in list(self.entity_tokens.items()) if k[0] == cid and isinstance(rec["token"], str)],
                # sanitised, tokenised replies of the latest requests: lets ANY process answer a client retry without calling the LLM again (stored encrypted, expires, erased with the borrower)
                "replies": [[k[0], k[2], v] for k, v in list(self.replies.items()) if k[1] == cid and len(v) <= CONTEXT_REPLY_MAX_CHARS][-CONTEXT_REPLIES:]}
        return self._fit(snap)

    def _fit(self, snap):
        """The vault refuses a context over its size limit, and the refusal was silent (other processes then got no shared context). Drop the OLDEST replies, then the oldest turns, until it fits."""
        limit, trimmed = CONTEXT_MAX_BYTES - 1024, False
        while len(json.dumps(snap, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > limit:
            if snap["replies"]:
                snap["replies"].pop(0)
            elif len(snap["history"]) > 1:
                snap["history"].pop(0)
            else:
                break
            trimmed = True
        if trimmed:
            self.context_stats["trimmed"] += 1
        return snap

    def _apply(self, cid, state):
        if not isinstance(state, dict) or state.get("v") != 1:
            return
        self.history[cid] = [dict(t) for t in state.get("history", [])][-HISTORY_TURNS:]
        self.turns[cid] = int(state.get("n", len(self.history[cid])))
        nm = NameMemory(); nm.parts = dict(state.get("names", {})); self.names[cid] = nm
        self.name_tokens[cid] = dict(state.get("name_tokens", {}))
        for k in [k for k in list(self.entity_tokens) if k[0] == cid]:
            self.entity_tokens.pop(k, None)
        for ent, rec in state.get("entities", []):
            self.entity_tokens[(cid, ent)] = rec
        for req, h, reply in state.get("replies", []):
            self.replies[(req, cid, h)] = reply                        # a retry that lands on THIS process finds the answer another process already gave
        while len(self.replies) > REPLAY_CACHE_MAX:
            self.replies.popitem(last=False)

    def _sync_in(self, cid, borrower_id):
        """If ANOTHER process advanced this conversation since we last looked, adopt its memory (one cheap version check; the context is only decrypted when it changed)."""
        """Returns True when the vault says this borrower is ERASED / being erased (everything we remember about them has then been flushed)."""
        if not self.shared_context:
            return False
        known = self.ctx_version.get(cid)
        try:
            res = self.vault.load_context(cid, borrower_id, newer_than=known, strict=True)
        except VaultDenied as e:
            if e.code == "borrower_not_active":                   # the vault confirms an erasure: flush this borrower's conversations from memory
                self.forget(cid)
                self.forget_borrower(borrower_id if borrower_id is not None else f"conv:{cid}")
                self.context_stats["erasure_flushed"] += 1
                return True
            self.context_stats["load_failed"] += 1
            return False
        except Exception:
            self.context_stats["load_failed"] += 1
            return False                                      # carry on with our own memory; the vault being down is handled (blocked) at tokenise
        if res is None and known is not None:
            self.forget(cid)                                  # we had a shared context for this conversation and it is gone (conversation erased, expired or re-keyed): forget our copy too
            self.context_stats["vanished_flushed"] += 1
            return False
        if res and res[1] is not None:
            self._apply(cid, res[1]); self.ctx_version[cid] = res[0]; self.context_stats["adopted"] += 1
        elif res:
            self.ctx_version[cid] = res[0]
        return False

    def _sync_out(self, cid, borrower_id):
        if not self.shared_context:
            return
        try:
            self.ctx_version[cid] = self.vault.save_context(cid, borrower_id, self._snapshot(cid)); self.context_stats["saved"] += 1
        except Exception:
            self.context_stats["save_failed"] += 1        # never blocks the message: the context is a convenience for OTHER processes, our own memory is intact

    def _initialize(self, st):
        """Start of the request: events, input validation, and the Layer 4 consent / purpose check. A 'no' (or a Layer 4 that cannot answer) blocks before ANY detection."""
        ids_ok = _is_id(st.get("request_id")) and _is_id(st.get("conversation_id")) and all(st.get(k) is None or _is_id(st.get(k)) for k in ("borrower_id", "consent_id"))
        ev = self.events.begin(st.get("request_id") if _is_id(st.get("request_id")) else None, st.get("borrower_id") if _is_id(st.get("borrower_id")) else None, st.get("purpose"))
        base = {"_ev": ev, "_t0": time.perf_counter(), "trace": ["initialize"]}
        text = st.get("text", "")
        if not ids_ok:                                                     # an identifier with free text in it must never reach an event, a store or the audit chain
            ev.add("L3_STARTED")
            return {**base, "status": "blocked", "reason": "invalid_request"}
        if not isinstance(text, str) or not text.strip():
            ev.add("L3_STARTED")
            return {**base, "status": "blocked", "reason": "empty_or_invalid_input"}
        if len(text) > MAX_MESSAGE_CHARS:
            ev.add("L3_STARTED")
            return {**base, "status": "blocked", "reason": "message_too_long"}
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:                                         # a lone surrogate: it could not be hashed, stored or sent to a model
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
        erased = self._sync_in(st["conversation_id"], st.get("borrower_id"))
        return {**base, "work_text": text, "retries": 0, "status": "ok", "warnings": [], "purpose": d.purpose, "erased": erased,
                "turn_no": self.turns.get(st["conversation_id"], 0)}

    def _detect(self, st):
        t = time.perf_counter()
        cid = st["conversation_id"]
        if st.get("erased"):                                               # the vault says this borrower is erased: nothing about this message may be processed or remembered
            return {"status": "blocked", "reason": "vault_error:borrower_not_active", "trace": st["trace"] + ["detect"]}
        self.last_seen.setdefault(cid, self.clock())                       # a TTL clock and an owner exist from the moment any memory is made, even if this message ends up blocked
        self.owner_of.setdefault(cid, st.get("borrower_id") if st.get("borrower_id") is not None else f"conv:{cid}")
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
        with self._stripe(cid):
            nm = self.names.setdefault(cid, NameMemory())
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
        for k in [k for k in list(self.entity_tokens) if k[0] == cid]:
            rec = self.entity_tokens.get(k)
            if rec is None:
                continue
            rec["token"] = fix(rec["token"])
            if isinstance(rec["token"], _Pending):
                self.entity_tokens.pop(k, None)

    @staticmethod
    def _idem_key(borrower_id, conversation_id, request_id, attempt):
        """Idempotency key for the vault: a hash of borrower + conversation + request id, so a key always fits what the vault accepts and the ids themselves are not stored there. The borrower and
        conversation are part of it because another borrower reusing the same request id must not collide with (and be refused by) this one. The attempt number is part of it too: an audit
        retry sends a DIFFERENT batch and must not collide with the first."""
        h = hashlib.sha256("\x00".join((str(borrower_id), str(conversation_id), str(request_id))).encode()).hexdigest()[:32]
        return f"gw:{h}:t{attempt}"

    def _tokenise(self, st):
        t = time.perf_counter()
        cid, text, rid = st["conversation_id"], st["work_text"], st["request_id"]
        spans = sorted(st.get("spans", []), key=lambda x: x["start"])
        items, deferred, refs = [], [], {}
        with self._stripe(cid):                                       # plan -> vault batch -> settle is ONE step per conversation (no other request may see the pending references in between)
            try:
                known = set(self.vault.tokens(cid, borrower_id=st.get("borrower_id"))) if any(s.get("token") for s in spans) else set()      # THIS borrower's tokens only
                for s in spans:
                    refs[id(s)] = self._plan_span(st, s, text[s["start"]:s["end"]], known, items, deferred)
                answers = [tok for tok, _ in self.vault.tokenize_batch(cid, st.get("borrower_id"), st["purpose"], items,
                                                                       idempotency_key=self._idem_key(st.get("borrower_id"), cid, rid, st.get("retries", 0)), request_id=rid)] if items else []
                self._settle_refs(cid, answers)
                resolve = lambda r: answers[r.k] if isinstance(r, _Pending) else r
                merged = {}
                for ref, piece, joined in deferred:
                    cur = resolve(ref)
                    got = self.vault.extend(cid, cur, piece=piece, joined=joined)
                    if isinstance(got, str) and got != cur:                  # the number, put together, is one already held under another token: THAT token stands for it from now on
                        merged[cur] = got
                        for key, rec in list(self.entity_tokens.items()):
                            if key[0] == cid and rec.get("token") == cur:
                                rec["token"] = got
                resolved = {k: merged.get(resolve(v), resolve(v)) for k, v in refs.items()}
                masked = substitute(text, spans, resolved)
            except VaultError as e:                                   # vault cannot encrypt/store: never send raw
                self._settle_refs(cid, None)
                out = {"status": "blocked", "reason": f"vault_error:{e.code}", "trace": st["trace"] + ["tokenise"]}
                if e.code == "borrower_not_active":                    # the vault confirms an erasure: flush everything remembered about this borrower (also when shared_context is off)
                    self.forget(cid)
                    self.forget_borrower(st["borrower_id"] if st.get("borrower_id") is not None else f"conv:{cid}")
                    self.context_stats["erasure_flushed"] += 1
                    out["erased"] = True
                return out
            except Exception as e:
                self._settle_refs(cid, None)
                return {"status": "blocked", "reason": f"vault_error:{type(e).__name__}", "trace": st["trace"] + ["tokenise"]}
        upd = {"masked_text": masked, "trace": st["trace"] + ["tokenise"],
               "issued": sorted(set(st.get("issued", [])) | {v for v in resolved.values() if isinstance(v, str)})}
        if st.get("retries", 0) == 0:                    # first pass: remember what was masked, for the audit survival check
            upd["raw_values"] = [text[s["start"]:s["end"]] for s in spans]
        st["_ev"].add("L2_TOKENISE_COMPLETE", values=len(items), spans=len(spans), ms=round((time.perf_counter() - t) * 1000, 2), attempt=st.get("retries", 0))
        return upd

    def _audit_route(self, st):
        return {"clean": "clean", "retry": "retry"}.get(st.get("audit"), "fail")

    def _final_audit(self, st):
        """Any failure of the audit itself (a broken scanner, an unexpected exception) blocks the message: the audit is a gate, never a best effort."""
        try:
            return self._final_audit_checks(st)
        except Exception as e:
            st["_ev"].add("L3_AUDIT_FAILED", reason="audit_error")
            return {"audit": "fail", "status": "blocked", "reason": f"audit_error:{type(e).__name__}", "trace": st["trace"] + ["final_audit"]}

    def _final_audit_checks(self, st):
        """Independent re-scan of the MASKED text. Anything still found is masked again (max 2 retries) or the message is blocked.
        Checks: 1 digit tripwire, 2 spoken-number tripwire, 3 survival (values this turn + values the vault holds), 4 utility (over-masking); extras: second pattern family, Layer-1 rules re-scan."""
        cid, masked, ev = st["conversation_id"], st["masked_text"], st["_ev"]
        neutral = router._blank(masked, st.get("issued", ()))   # blank ONLY the tokens the vault issued for this message (same length): a token-shaped string the user typed stays visible to the checks
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
            self._log({"conversation_id": st["conversation_id"], "text": st["masked_text"], "degraded": "llm_timeout", "warnings": st.get("warnings", [])})
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
            clean, stats = router.sanitise(st["llm_reply"], self.vault.tokens(cid, borrower_id=st.get("borrower_id")), self.audit_detector)       # THIS borrower's tokens only
        except Exception as e:
            reason = f"vault_error:{e.code}" if isinstance(e, VaultError) else f"sanitise_error:{type(e).__name__}"
            return {"status": "blocked", "reason": reason, "trace": st["trace"] + ["sanitise"]}
        # the log gets the masked incoming text + the TOKENISED reply, never rehydrated text
        if not st.get("replayed"):
            self._log({"conversation_id": cid, "text": st["masked_text"], "reply_masked": clean, "warnings": st.get("warnings", [])})
            self.replies[self._replay_key(st)] = clean                          # only after a successful, sanitised answer (failures are never cached)
            while len(self.replies) > REPLAY_CACHE_MAX:
                self.replies.popitem(last=False)
        return {"reply_masked": clean, "trace": st["trace"] + ["sanitise"]}

    def _rehydrate_route(self, st):
        cid = st["conversation_id"]
        try:
            reply, log_entry = router.route(st["reply_masked"], st.get("audience", "borrower"),
                                            lambda toks: self.vault.rehydrate_many(cid, toks, borrower_id=st.get("borrower_id"), purpose=st.get("purpose")))     # only THIS borrower's tokens, made for THIS purpose
        except Exception as e:
            return {"status": "blocked", "reply": "", "reason": f"rehydration_failed:{e.code if isinstance(e, VaultError) else type(e).__name__}", "trace": st["trace"] + ["rehydrate_route"]}
        return {"reply": reply, "log_entry": log_entry, "status": "ok", "trace": st["trace"] + ["rehydrate_route"]}

    def _log(self, entry):
        self.log.append(entry)
        if len(self.log) > LOG_MAX:
            del self.log[: LOG_MAX // 10]                 # keep the newest; the masked log must not grow without limit

    def _block(self, st):
        self._log({"conversation_id": st.get("conversation_id") if _is_id(st.get("conversation_id")) else None, "blocked": st.get("reason", "unknown")})
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
        rid = request_id or "gw-" + uuid.uuid4().hex[:12]
        try:
            out = self.graph.invoke({"request_id": rid, "conversation_id": conversation_id, "speaker": speaker, "text": text,
                                     "trace": [], "audience": audience, "borrower_id": borrower_id, "purpose": purpose, "consent_id": consent_id})
        except Exception as e:                                                       # fail closed: whatever escaped a node, the message is BLOCKED and the audit trail says so (it used to raise)
            out = self._crashed(rid, conversation_id, borrower_id, purpose, e)
        if self.clock() - self._last_sweep > min(30.0, self.history_ttl / 4):          # lazy sweep: the idle TTL holds even without the background thread
            self.purge_idle()
        if (isinstance(text, str) and len(text) <= MAX_MESSAGE_CHARS and not out.get("replayed") and not out.get("erased") and out.get("reason") not in NOT_REMEMBERED
                and not str(out.get("reason", "")).startswith("consent") and self._encodable(text)):
            h = self.history.setdefault(conversation_id, []); h.append({"speaker": speaker, "text": text}); del h[:-HISTORY_TURNS]     # bounded: only the window is ever used
            self.turns[conversation_id] = self.turns.get(conversation_id, 0) + 1
            self.last_seen[conversation_id] = self.clock()
            self.owner_of[conversation_id] = borrower_id if borrower_id is not None else f"conv:{conversation_id}"
            self._sync_out(conversation_id, borrower_id)
        if self.persistence is not None and borrower_id is not None and out.get("status") in ("ok", "degraded") and not out.get("replayed") and not out.get("erased"):
            try:
                if self.persistence.record(out.get("request_id"), conversation_id, borrower_id, out.get("purpose") or purpose, consent_id, out.get("masked_text"), out.get("reply_masked")):
                    self.persist_stats["recorded"] += 1
                else:
                    self.persist_stats["not_recorded"] += 1
            except Exception:
                self.persist_stats["failed"] += 1                    # persistence must never block or break a conversation
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

    @staticmethod
    def _encodable(text):
        try:
            text.encode("utf-8")
            return True
        except UnicodeEncodeError:
            return False

    def _crashed(self, rid, cid, borrower_id, purpose, exc):
        """A blocked result for an exception that escaped a node (nothing was sent anywhere unaudited: every node that talks to the model sits behind the audit, which is itself guarded)."""
        reason = f"gateway_error:{type(exc).__name__}"
        safe_rid, safe_cid = (rid if _is_id(rid) else None), (cid if _is_id(cid) else None)
        out = {"status": "blocked", "reason": reason, "reply": "", "request_id": safe_rid, "conversation_id": safe_cid, "trace": ["error"]}
        try:
            ev = self.events.begin(safe_rid, borrower_id if _is_id(borrower_id) else None, purpose)
            ev.add("L3_STARTED")
            ev.add("L3_BLOCKED", reason="gateway_error")
            out["_ev"] = ev
        except Exception:
            pass
        if safe_cid is not None:
            try:
                with self._stripe(safe_cid, wait=5.0):
                    self._settle_refs(safe_cid, None)                                # no pending token reference may outlive a crashed request
            except Exception:
                pass
        self._log({"conversation_id": safe_cid, "blocked": reason})
        return out

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
            return {"status": "blocked", "http_status": http_status(reason),
                    "reason": reason.split(":")[0] if reason.startswith(("consent", "vault_error", "detector_error", "llm_error", "rehydration_failed", "gateway_error", "audit_error", "sanitise_error")) else reason,
                    "request_id": request["request_id"] if _is_id(request["request_id"]) else None, "borrower_reply": "", "log_entry": ""}
        return {"status": "success" if r["status"] == "ok" else r["status"], "request_id": request["request_id"], "borrower_reply": r.get("reply", ""), "log_entry": r.get("log_entry", ""),
                "pre_llm_ms": r.get("pre_llm_ms")}

    def diagram(self) -> str:
        return self.graph.get_graph().draw_mermaid()
