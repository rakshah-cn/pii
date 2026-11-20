"""Calls: one phone call = one borrower case in the demo console, driven turn by turn through the REAL pipeline (layers 1-7).

   speech --(speech-to-text vendor: sees RAW speech)--> text --> server._message --> detect -> mask -> audit -> LLM (masked text only) --> bot line --> speech
   end of call: masked transcript --> gateway (same masking + audit) --> LLM --> disposition (strictly parsed)

`srv` is the running demo server module (layer8_demo.server), passed in so this file never imports it and tests can hand in a fake. Nothing here edits the server."""
import base64
import concurrent.futures
import io
import json
import os
import re
import threading
import time
import uuid
import wave

from layer9_voice import borrowers as borrowerlib, bot, config, disposition
from layer9_voice.sarvam import SarvamError, lang_code

HERE = os.path.dirname(os.path.abspath(__file__))
PURPOSE = "collections"


class CallError(Exception):
    def __init__(self, reason, status=400):
        super().__init__(reason)
        self.reason, self.status = reason, status


def load_borrowers(path=None):
    """The list of people (see borrowers.py: borrowers.local.json wins over borrowers.example.json)."""
    if path is None:
        return borrowerlib.load()
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise CallError("duplicate_borrower_id", 500)
    return rows


def split_sentences(text, min_len=18, max_parts=4):
    """A reply cut into speakable sentences (so they can be turned into speech in parallel). Short fragments (an 'Rs.' abbreviation, 'Thank you.') join the next one."""
    raw = [p.strip() for p in re.split(r"(?<=[.!?\u0964])\s+", text.strip()) if p.strip()]
    parts = []
    for p in raw:
        if parts and (len(parts[-1]) < min_len or re.search(r"\bRs\.$", parts[-1], re.I)):       # a short fragment or an 'Rs.' abbreviation joins what follows
            parts[-1] += " " + p
        else:
            parts.append(p)
    if len(parts) > 1 and len(parts[-1]) < min_len:
        last = parts.pop()                                       # pop FIRST: 'parts[-2] += parts.pop()' shrinks the list before it stores (crashed on exactly two parts)
        parts[-1] += " " + last
    while len(parts) > max_parts:
        last = parts.pop()
        parts[-1] += " " + last
    return parts


def hedged(fn, delay=2.5):
    """Run fn(); if it has not finished after `delay` seconds (or it failed) start one identical second attempt and use whichever succeeds first. Cuts a random slow tail
    (Sarvam text-to-speech was seen at 1.1 s, 6.7 s and 7.6 s for similar lines). The second attempt is only paid for when the first is slow."""
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    futs = [ex.submit(fn)]
    try:
        done, _ = concurrent.futures.wait(futs, timeout=delay)
        if not done or futs[0].exception() is not None:
            futs.append(ex.submit(fn))
        for f in concurrent.futures.as_completed(futs):
            if f.exception() is None:
                return f.result()
        raise futs[-1].exception()
    finally:
        ex.shutdown(wait=False)


def join_wavs(wavs):
    """Several WAV files with the same format -> one WAV (raises ValueError if the formats differ)."""
    params, frames = None, []
    for w in wavs:
        with wave.open(io.BytesIO(w), "rb") as r:
            p = (r.getnchannels(), r.getsampwidth(), r.getframerate())
            if params not in (None, p):
                raise ValueError("wav_formats_differ")
            params = p
            frames.append(r.readframes(r.getnframes()))
    out = io.BytesIO()
    with wave.open(out, "wb") as o:
        o.setnchannels(params[0])
        o.setsampwidth(params[1])
        o.setframerate(params[2])
        o.writeframes(b"".join(frames))
    return out.getvalue()


def mask_phone(p):
    d = "".join(ch for ch in str(p or "") if ch.isdigit())
    return ("•" * max(0, len(d) - 4)) + d[-4:] if d else ""


class Call:
    def __init__(self, borrower, sid, language, system, channel="voice"):
        self.id = "call-" + uuid.uuid4().hex[:8]
        self.borrower_id, self.sid, self.language, self.system, self.channel = borrower["id"], sid, language, system, channel
        self.llm_history = []                                    # MASKED messages only (see bot.CallLLM)
        self.turns = []                                          # [{"speaker", "masked"}]: what is shown and kept after the call
        self.raw_turns = []                                      # [{"speaker", "text"}]: RAW, held only until the call ends so the gateway can mask the whole transcript in one pass; then wiped
        self.n = 0
        self.status = "active"
        self.disposition = None
        self.disposition_error = None
        self.last_llm_error = None                               # the safe reason the last model call failed (status/code), for the screen
        self.started = time.time()
        self.ended = None


class CallManager:
    def __init__(self, srv, chat=None, stt=None, tts=None, borrowers=None):
        """chat = bot.make_chat(...) result; stt/tts = a SarvamClient (or any object with transcribe / synthesize)."""
        self.srv, self.chat, self.stt, self.tts = srv, chat, stt, tts
        self._borrowers = borrowers
        self.calls = {}
        self._lock = threading.Lock()
        self.tts_hedge = None                                    # None: use limits.tts_hedge_seconds from the config (a number here overrides, for tests)
        self.hedges = 0                                          # how many times that happened (for the numbers)
        self._fixed_audio = {}                                   # (text, language code, sample rate) -> audio, ONLY for the bot's fixed lines (no customer data in them)

    # ---- the list of people ------------------------------------------------------------------------------------
    def borrowers(self):
        return self._borrowers if self._borrowers is not None else load_borrowers()

    def public_borrowers(self):
        return [{"id": b["id"], "name": b["name"], "phone": mask_phone(b.get("phone")), "language": b.get("language", "English"), "product": b.get("product"),
                 "outstanding": b.get("outstanding"), "dpd": b.get("dpd")} for b in self.borrowers()]

    def get(self, call_id):
        c = self.calls.get(call_id)
        if c is None:
            raise CallError("no_such_call", 404)
        return c

    def _session(self, call):
        s = self.srv.WS["ws"].borrowers.get(call.sid)
        if s is None:
            raise CallError("case_not_found", 404)
        return s

    # ---- start ---------------------------------------------------------------------------------------------------
    def start(self, borrower_id, channel="voice"):
        if channel not in ("voice", "chat"):
            raise CallError("unknown_channel", 400)
        b = next((x for x in self.borrowers() if x["id"] == borrower_id), None)
        if b is None:
            raise CallError("unknown_borrower", 404)
        language = b.get("language", "English")
        profile = {"product": b.get("product", "Personal loan"), "loan_ref": "••" + str(b.get("loan_last4", "0000"))[-4:], "dpd": b.get("dpd", 0),
                   "outstanding": b.get("outstanding", 0), "language": language, "channel": "Chat" if channel == "chat" else "Voice call", "assignee": "Chat bot" if channel == "chat" else "Voice bot"}
        with self.srv.LOCK:
            s = self.srv.WS["ws"].add(b["name"], profile)
            call = Call(b, s.sid, language, bot.bot_system(profile, channel), channel)
            call.turns.append({"speaker": "Agent", "masked": bot.greeting(language, channel)})
            call.raw_turns.append({"speaker": "Agent", "text": bot.greeting(language, channel)})
            self.calls[call.id] = call
            s.audit.append({"event": "VOICE_CALL_STARTED", "call_id": call.id, "borrower_hash": s.audit.hash_borrower(s.B), "language": lang_code(language), "channel": channel, "model": getattr(self.chat, "provider", "unknown")})
        return {"call_id": call.id, "case_sid": s.sid, "case": s.case, "language": lang_code(language), "channel": channel, "greeting": bot.greeting(language, channel)}

    # ---- one customer utterance ----------------------------------------------------------------------------------
    def turn(self, call_id, text):
        call = self.get(call_id)
        if call.status != "active":
            raise CallError("call_already_ended", 409)
        text = (text or "").strip()
        if not text:
            raise CallError("empty_utterance", 400)
        s = self._session(call)
        t0 = time.perf_counter()
        with self.srv.LOCK, bot.active(call, "bot"):
            res = self.srv._message(s, self.srv.Msg(text=text, speaker="Customer", purpose=PURPOSE))
        call.n += 1
        ok = res.get("status") in ("ok", "degraded")
        l3 = res.get("l3", {})
        out = {"turn": res["turn"], "status": res.get("status"), "reason": res.get("reason"), "pipeline_ms": round((time.perf_counter() - t0) * 1000, 1),
               "found": [{"label": sp["label"], "token": sp.get("token"), "uncertain": sp.get("uncertain")} for sp in res["l1"]["spans"]],     # labels only, never the values
               "customer_masked": l3.get("llm_saw"), "bot_masked": l3.get("reply_logged"), "gateway_ms": l3.get("ms"), "pre_llm_ms": l3.get("pre_llm_ms")}
        if ok and l3.get("llm_saw"):
            out["bot_say"] = bot.speakable(l3.get("reply_logged"), call.language)
            call.turns.append({"speaker": "Customer", "masked": l3["llm_saw"]})
            call.raw_turns.append({"speaker": "Customer", "text": text})
            if l3.get("reply_logged"):
                call.turns.append({"speaker": "Agent", "masked": l3["reply_logged"]})
                call.raw_turns.append({"speaker": "Agent", "text": out["bot_say"]})        # what the customer actually heard: placeholder-free by construction
        else:
            out["bot_say"] = bot.repeat_line(call.language)                # blocked or failed: the bot asks again, and says nothing about why
        out["goodbye"] = bool(ok and bot.is_goodbye(out["bot_say"]))        # the bot closed the conversation: the screen can generate the disposition by itself
        return out

    # ---- audio path ----------------------------------------------------------------------------------------------
    def _synth(self, text, language, sample_rate):
        """One line -> WAV bytes. Fixed lines are cached. A long reply is spoken as sentences IN PARALLEL (speech time grows with text length, measured 0.4 s for 4 words vs 1.7 s for 25),
        then joined; if that fails in any way the whole text is sent in one call."""
        kw = {"sample_rate": sample_rate} if sample_rate else {}

        def one(x):
            calls_made = []

            def attempt():
                calls_made.append(1)
                return self.tts.synthesize(x, language, **kw)
            out = hedged(attempt, self.tts_hedge if self.tts_hedge is not None else config.limits()["tts_hedge_seconds"])
            if len(calls_made) > 1:
                self.hedges += 1
            return out
        key = (text, lang_code(language), sample_rate)
        if key in self._fixed_audio:
            return self._fixed_audio[key]
        parts = split_sentences(text)
        if len(parts) > 1 and len(text) > 60:
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(parts)) as ex:
                    audio = join_wavs(list(ex.map(one, parts)))
            except (SarvamError, ValueError, wave.Error, EOFError):
                audio = one(text)
        else:
            audio = one(text)
        if text in bot.fixed_lines():
            self._fixed_audio[key] = audio
        return audio

    def warm(self, sample_rates=(None, 8000)):
        """Speak the fixed lines once per language/rate so the first call does not wait for them. Best effort; never raises."""
        if self.tts is None:
            return 0
        n = 0
        for lang in {b.get("language", "English") for b in self.borrowers()}:
            for line in (bot.greeting(lang), bot.repeat_line(lang), bot.goodbye_line(lang)):
                for rate in sample_rates:
                    try:
                        self._synth(line, lang, rate)
                        n += 1
                    except Exception:                                # noqa: BLE001
                        pass
        return n

    def speak(self, text, call, sample_rate=None):
        """-> (audio_b64 | None, error | None, ms). A text-to-speech failure never stops the call: the text is still returned. sample_rate=8000 for a phone line."""
        if self.tts is None:
            return None, "tts_not_configured", 0
        t = time.perf_counter()
        try:
            audio = self._synth(text, call.language, sample_rate)
        except SarvamError as e:
            return None, str(e), round((time.perf_counter() - t) * 1000, 1)
        return base64.b64encode(audio).decode(), None, round((time.perf_counter() - t) * 1000, 1)

    def turn_from_audio(self, call_id, audio, filename="audio.wav", content_type="audio/wav", sample_rate=None):
        call = self.get(call_id)
        if self.stt is None:
            raise CallError("stt_not_configured", 503)
        t = time.perf_counter()
        try:
            heard = self.stt.transcribe(audio, language=call.language, filename=filename, content_type=content_type)
        except SarvamError as e:
            raise CallError(f"speech_to_text_failed:{e}", 502) from None
        stt_ms = round((time.perf_counter() - t) * 1000, 1)
        if not heard["transcript"]:
            return {"heard": "", "status": "no_speech", "bot_say": bot.repeat_line(call.language), "stt_ms": stt_ms}
        out = self.turn(call_id, heard["transcript"])
        out.update(heard=heard["transcript"], stt_ms=stt_ms, detected_language=heard.get("language_code"))
        out["bot_audio_b64"], out["tts_error"], out["tts_ms"] = self.speak(out["bot_say"], call, sample_rate)
        return out

    # ---- end -----------------------------------------------------------------------------------------------------
    def end(self, call_id):
        call = self.get(call_id)
        if call.status != "active":
            raise CallError("call_already_ended", 409)
        s = self._session(call)
        text, trimmed = disposition.build_transcript(call.raw_turns, key="text")       # RAW: the gateway masks it (and a model never sees this text)
        status = {"turns": call.n, "transcript_trimmed": trimmed, "transcript_chars": len(text)}

        def run_llm(t):
            with self.srv.LOCK, bot.active(call, "disposition"):
                out = s.gw.handle(s.CID, "Agent", t, audience="internal", borrower_id=s.B, purpose=PURPOSE, internal=True)       # same masking + audit as every other message
            if out.get("status") != "ok":
                raise disposition.DispositionError(f"gateway_{out.get('status')}:{str(out.get('reason'))[:60]}" + (f" [{call.last_llm_error}]" if call.last_llm_error else ""))
            return out["reply_masked"]                                                # tokenised, never rehydrated

        try:
            call.disposition = disposition.generate(text, run_llm)
            event = {"disposition": call.disposition["disposition"], "will_pay": call.disposition["will_pay"]}
        except disposition.DispositionError as e:
            call.disposition_error = str(e)
            event = {"disposition": None, "error": str(e)[:120]}
        finally:
            call.raw_turns = []                                                       # the raw transcript does not outlive the call (success or not)
            text = ""
        call.status, call.ended = "ended", time.time()
        with self.srv.LOCK:
            s.audit.append({"event": "VOICE_CALL_ENDED", "call_id": call.id, "borrower_hash": s.audit.hash_borrower(s.B), "turns": call.n, **event})
        return {"call_id": call.id, "disposition": call.disposition, "error": call.disposition_error, **status}

    def state(self, call_id):
        c = self.get(call_id)
        return {"call_id": c.id, "case_sid": c.sid, "status": c.status, "turns": c.n, "disposition": c.disposition, "error": c.disposition_error,
                "transcript_masked": [dict(t) for t in c.turns]}
