"""The phone leg: someone dials a number WE own and talks to the bot. INBOUND ONLY - nothing in this file can place a call (rule: no call to the team's real numbers).

  caller's phone --> Twilio number --webhook--> /voice/phone/incoming  (greeting)
     each answer:  Twilio <Record> --> /voice/phone/turn --> fetch the recording (then DELETE it at Twilio) --> speech-to-text --> mask -> audit -> LLM --> text-to-speech --> <Play>
     hang-up / goodbye / turn cap --> the disposition is generated (status callback or immediately)

Honest scope:
  * Twilio request shapes and the signature rule come from Twilio's public docs (read 20 Nov 2026). NOT exercised against a real Twilio account: that needs an account, a number and a public https URL.
  * Twilio and the speech vendor hear the RAW audio, and Twilio keeps call metadata (the numbers involved, times). We delete each recording right after fetching it and never read, log or store
    the caller's number (From/To are ignored). This is outside our masking boundary and the slide should say so.
  * Every webhook must carry a valid X-Twilio-Signature (HMAC-SHA1 of the public URL + sorted form fields with the auth token), or it is refused (403): the tunnel URL is public.
  * Which borrower an incoming call belongs to is chosen in the console ("arm"), not by looking up the caller's number, so no phone number is stored anywhere."""
import base64
import contextvars
import hashlib
import hmac
import re
import secrets
import threading
import time
from urllib.parse import parse_qsl, urlparse
from xml.sax.saxutils import escape, quoteattr

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from layer9_voice import bot, config
from layer9_voice.calls import CallError

TWILIO_HOST = "api.twilio.com"
CALL_SID = re.compile(r"CA[0-9a-f]{32}")
REC_SID = re.compile(r"RE[0-9a-f]{32}")
TERMINAL = ("completed", "busy", "failed", "no-answer", "canceled")
SAY_LANG = {"en-IN": "en-IN", "hi-IN": "hi-IN", "mr-IN": "hi-IN"}      # Twilio's own voices are only a fallback when Sarvam speech fails (no Marathi voice assumed)
UNAVAILABLE = "Sorry, this line is not available right now. Goodbye."
TROUBLE = "Sorry, something went wrong. Goodbye."


class PhoneError(Exception):
    def __init__(self, reason, status=400):
        super().__init__(reason)
        self.reason, self.status = reason, status


# ---------------------------------------------------------------- TwiML
def _doc(*verbs):
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(verbs) + "</Response>"


def _say(text, lang="en-IN"):
    return f"<Say language={quoteattr(lang)}>{escape(text)}</Say>"


def _play(url):
    return f"<Play>{escape(url)}</Play>"


def _record(action):
    return f'<Record action={quoteattr(action)} method="POST" timeout="2" maxLength="20" playBeep="false" trim="trim-silence"/>'


HANGUP = "<Hangup/>"


class PhoneLine:
    def __init__(self, mgr, account_sid, auth_token, public_base, http=None, sleep=time.sleep, clock=time.time, max_turns=None, max_silent=None):
        if not (account_sid and auth_token and str(public_base).startswith("https://")):
            raise ValueError("phone_line_needs_account_sid_auth_token_and_an_https_PUBLIC_BASE_URL")
        self.mgr, self.sid, self._token = mgr, account_sid, auth_token
        self.base = public_base.rstrip("/")
        self._http, self._sleep, self._clock = http or httpx.Client(timeout=15.0), sleep, clock
        self.max_turns = max_turns if max_turns is not None else int(config.limits()["max_phone_turns"])
        self.max_silent = max_silent if max_silent is not None else int(config.limits()["max_silent_answers"])
        self.armed = None                  # {"borrower_id", "until"}
        self.sessions = {}                 # CallSid -> call_id (the CallSid is Twilio's id for the call: not a phone number)
        self.silent = {}                   # call_id -> consecutive empty answers
        self.audio = {}                    # token -> (bytes, expires): the bot's own placeholder-free lines, served to Twilio's <Play>
        self._finished, self._threads = set(), []
        self._lock = threading.Lock()

    def __repr__(self):
        return f"PhoneLine(base={self.base!r}, sessions={len(self.sessions)})"        # never the token

    # ---- request signature -----------------------------------------------------------------------------------------
    def sign(self, url, pairs):
        s = url + "".join(k + v for k, v in sorted(pairs))
        return base64.b64encode(hmac.new(self._token.encode(), s.encode("utf-8"), hashlib.sha1).digest()).decode()

    def valid(self, url, pairs, signature):
        return bool(signature) and hmac.compare_digest(self.sign(url, pairs), signature)

    # ---- which borrower the next incoming call is -----------------------------------------------------------------
    def arm(self, borrower_id, ttl=600):
        if not any(b["id"] == borrower_id for b in self.mgr.borrowers()):
            raise CallError("unknown_borrower", 404)
        self.armed = {"borrower_id": borrower_id, "until": self._clock() + ttl}

    def disarm(self):
        self.armed = None

    def state(self):
        a = self.armed if self.armed and self.armed["until"] > self._clock() else None
        return {"armed": a is not None, "borrower_id": a["borrower_id"] if a else None, "seconds_left": int(a["until"] - self._clock()) if a else 0,
                "active_calls": [c for c in self.sessions.values() if self.mgr.calls[c].status == "active"], "number_setup": self.base + "/voice/phone/incoming"}

    # ---- the bot's lines as audio ----------------------------------------------------------------------------------
    def _store(self, audio_b64):
        now = self._clock()
        for k in [k for k, (_, exp) in self.audio.items() if exp < now]:
            del self.audio[k]
        tok = secrets.token_urlsafe(18)
        self.audio[tok] = (base64.b64decode(audio_b64), now + 180)
        return f"{self.base}/voice/phone/audio/{tok}.wav"

    def audio_bytes(self, tok):
        hit = self.audio.get(tok)
        return hit[0] if hit and hit[1] >= self._clock() else None

    def _reply(self, call, text, audio_b64, hangup=False):
        say = _play(self._store(audio_b64)) if audio_b64 else _say(text, SAY_LANG.get(bot.lang_code(call.language), "en-IN"))
        return _doc(say, HANGUP if hangup else _record(self.base + "/voice/phone/turn"))

    # ---- the recording -----------------------------------------------------------------------------------------------
    def _fetch(self, url):
        """The audio of one answer. Twilio may call us before the file is ready (its docs say so): retry a few times. Credentials go only to api.twilio.com."""
        u = urlparse(url)
        if u.scheme != "https" or u.hostname != TWILIO_HOST:
            raise PhoneError("recording_url_not_twilio")
        for attempt in range(6):
            try:
                r = self._http.get(url, auth=(self.sid, self._token))
            except httpx.HTTPError:
                raise PhoneError("recording_fetch_failed") from None
            if r.status_code == 200 and r.content:
                return r.content
            if r.status_code not in (404, 200):
                raise PhoneError(f"recording_http_{r.status_code}")
            self._sleep(0.5)
        raise PhoneError("recording_not_ready")

    def _delete(self, rec_sid):
        """The raw audio must not stay at Twilio: delete it right after we have it (best effort; a failure is not fatal and never carries a body)."""
        if not REC_SID.fullmatch(rec_sid or ""):
            return False
        try:
            r = self._http.delete(f"https://{TWILIO_HOST}/2010-04-01/Accounts/{self.sid}/Recordings/{rec_sid}.json", auth=(self.sid, self._token))
            return r.status_code in (200, 204)
        except httpx.HTTPError:
            return False

    # ---- webhooks (each returns TwiML) -------------------------------------------------------------------------------
    def incoming(self, p):
        sid = p.get("CallSid", "")
        if not CALL_SID.fullmatch(sid):
            raise PhoneError("bad_call_sid")
        with self._lock:
            if sid in self.sessions:                                  # Twilio re-sent the webhook: same call, same greeting
                call_id = self.sessions[sid]
            else:
                if any(self.mgr.calls[c].status == "active" for c in self.sessions.values()):
                    return _doc(_say("Sorry, the line is busy. Please try again later. Goodbye."), HANGUP)
                a = self.armed
                if not a or a["until"] < self._clock():
                    return _doc(_say(UNAVAILABLE), HANGUP)             # nobody armed a borrower: nothing is recorded, nothing is sent anywhere
                call_id = self.mgr.start(a["borrower_id"])["call_id"]
                self.sessions[sid] = call_id
                self.armed = None                                      # one call per arming
        call = self.mgr.get(call_id)
        greeting = bot.greeting(call.language)
        audio, _err, _ms = self.mgr.speak(greeting, call, sample_rate=8000)
        return self._reply(call, greeting, audio)

    def turn(self, p):
        call_id = self.sessions.get(p.get("CallSid", ""))
        if call_id is None:
            return _doc(_say(TROUBLE), HANGUP)
        call = self.mgr.get(call_id)
        if p.get("Digits") == "hangup":                                # the caller hung up while we were recording
            self._finish_async(call_id)
            return _doc()
        if call.status != "active":
            return _doc(_say(TROUBLE), HANGUP)
        try:
            dur = float(p.get("RecordingDuration") or 0)
        except ValueError:
            dur = 0.0
        out = None
        if p.get("RecordingUrl") and dur >= 0.4:
            audio = None
            try:
                audio = self._fetch(p["RecordingUrl"])
            except PhoneError:
                audio = None
            finally:
                self._delete(p.get("RecordingSid", ""))                # whether or not we could use it
            if audio:
                try:
                    out = self.mgr.turn_from_audio(call_id, audio, "audio.wav", "audio/wav", sample_rate=8000)
                except CallError:
                    out = None                                         # speech-to-text failed: treated like not understood
        if out is None or out.get("status") == "no_speech" or out.get("status") == "blocked":
            n = self.silent.get(call_id, 0) + 1
            self.silent[call_id] = n
            if n >= self.max_silent:
                self._finish_async(call_id)
                return _doc(_say(bot.goodbye_line(call.language), SAY_LANG.get(bot.lang_code(call.language), "en-IN")), HANGUP)
            line = (out or {}).get("bot_say") or bot.repeat_line(call.language)
            audio_b64, _e, _m = self.mgr.speak(line, call, sample_rate=8000)
            return self._reply(call, line, audio_b64)
        self.silent[call_id] = 0
        end = bot.is_goodbye(out["bot_say"]) or call.n >= self.max_turns          # the bot said goodbye, or the call hit its turn cap: play its last line, then hang up
        if end:
            self._finish_async(call_id)
        return self._reply(call, out["bot_say"], out.get("bot_audio_b64"), hangup=end)

    def status(self, p):
        call_id = self.sessions.get(p.get("CallSid", ""))
        if call_id and p.get("CallStatus") in TERMINAL:
            self._finish_async(call_id)

    # ---- the end of a call ---------------------------------------------------------------------------------------------
    def _finish(self, call_id):
        with self._lock:
            if call_id in self._finished:
                return
            self._finished.add(call_id)
        try:
            self.mgr.end(call_id)                                      # the disposition (the raw transcript is wiped inside)
        except CallError:
            pass

    def _finish_async(self, call_id):
        ctx = contextvars.copy_context()                               # keeps the audit actor (the phone line), which a bare thread would lose
        t = threading.Thread(target=ctx.run, args=(self._finish, call_id), daemon=True)
        self._threads.append(t)
        t.start()

    def join(self, timeout=60):
        for t in list(self._threads):
            t.join(timeout)


# ---------------------------------------------------------------- HTTP
class Arm(BaseModel):
    borrower_id: str


def build(srv, mgr, line):
    r = APIRouter(prefix="/voice/phone")

    def as_line(fn):
        """The audit trail should say a phone call did this, not whichever console user is the default."""
        ctx = dict(srv.REQ.get() or {})
        ctx.update(user="voice-line", ua="phone-line")
        tok = srv.REQ.set(ctx)
        try:
            return fn()
        finally:
            srv.REQ.reset(tok)

    async def verified(request):
        body = (await request.body()).decode("utf-8", "replace")
        pairs = parse_qsl(body, keep_blank_values=True)
        url = line.base + request.url.path + (("?" + request.url.query) if request.url.query else "")
        if not line.valid(url, pairs, request.headers.get("x-twilio-signature")):
            raise HTTPException(403, "bad_signature")
        return dict(pairs)

    def xml(doc):
        return Response(doc, media_type="application/xml")

    async def webhook(request, handler):
        p = await verified(request)
        try:
            return xml(await run_in_threadpool(as_line, lambda: handler(p)))
        except (PhoneError, CallError):
            return xml(_doc(_say(TROUBLE), HANGUP))                    # Twilio must never play a stack trace to the caller
        except Exception:                                              # noqa: BLE001  (the caller hears a polite goodbye; the type is not logged with any text)
            return xml(_doc(_say(TROUBLE), HANGUP))

    @r.post("/incoming")
    async def incoming(request: Request):
        return await webhook(request, line.incoming)

    @r.post("/turn")
    async def turn(request: Request):
        return await webhook(request, line.turn)

    @r.post("/status")
    async def status(request: Request):
        p = await verified(request)
        await run_in_threadpool(as_line, lambda: line.status(p))
        return Response(status_code=204)

    @r.get("/audio/{token}.wav")
    def audio(token: str):
        data = line.audio_bytes(token)
        if data is None:
            raise HTTPException(404, "gone")
        return Response(data, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    # console side (agent role)
    @r.get("/state")
    def state():
        srv.require("agent")
        return line.state()

    @r.post("/arm")
    def arm(b: Arm):
        srv.require("agent")
        try:
            line.arm(b.borrower_id)
        except CallError as e:
            raise HTTPException(e.status, e.reason) from None
        return line.state()

    @r.delete("/arm")
    def disarm():
        srv.require("agent")
        line.disarm()
        return line.state()

    return r
