"""HTTP endpoints for the voice bot, added to the demo console by run.py. Role checks reuse the console's own `require` (the agent role), so a refusal is audited the same way.

  GET  /voice/status                  which model / speech services are configured (booleans and names only, never keys)
  GET  /voice/borrowers               the call list (phone numbers shown masked)
  POST /voice/call/start              {"borrower_id"}                       -> call_id, greeting (+ audio)
  POST /voice/call/{id}/say           {"text", "audio": false}              -> one typed/ASR'd utterance through the pipeline
  POST /voice/call/{id}/audio         raw audio bytes as the body (Content-Type: audio/wav | audio/webm ...) -> speech-to-text -> pipeline -> text-to-speech
  POST /voice/call/{id}/end           -> the disposition
  GET  /voice/call/{id}               -> state + the MASKED transcript
"""
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from layer9_voice import borrowers as borrowerlib, bot, config
from layer9_voice.calls import CallError

MAX_AUDIO_BYTES = 5 * 1024 * 1024


class Start(BaseModel):
    borrower_id: str
    audio: bool = False
    channel: str = "voice"          # "voice" or "chat"


class ConfigBody(BaseModel):
    config: dict


class CsvBody(BaseModel):
    csv: str
    replace: bool = True


class RowBody(BaseModel):
    name: str
    language: str = "English"
    product: str = ""
    outstanding: int | None = None
    dpd: int | None = None
    loan_last4: str = ""
    phone: str = ""


class Say(BaseModel):
    text: str
    audio: bool = False


def build(srv, mgr):
    r = APIRouter(prefix="/voice")

    def run(fn, *a, **kw):
        srv.require("agent")
        try:
            return fn(*a, **kw)
        except CallError as e:
            raise HTTPException(e.status, e.reason) from None

    @r.get("/page")
    def page():
        """The browser call page: the same call, with your laptop's microphone instead of a phone. Static; every API call it makes is role-checked."""
        return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "call.html"), headers={"Cache-Control": "no-store"})

    # ---- settings: the configuration and the borrower list (reads: any console role; changes: DPO or privacy officer only, and audited)
    READ = ("agent", "dpo", "privacy_officer", "auditor")
    WRITE = ("dpo", "privacy_officer")

    def note(event, **fields):
        try:
            ws = srv.WS["ws"]
            ws.audit.append({"event": event, **fields})
        except Exception:                                            # noqa: BLE001  (a failing audit write must not hide the change from the screen)
            pass

    def settings_view():
        cfg = config.get()
        langs = list(cfg["languages"])
        return {"config": cfg, "defaults": config.DEFAULTS, "source": config.source(), "problems": config.errors(), "sarvam_codes": list(config.SARVAM_CODES),
                "fixed_rules": {"privacy_and_conduct": bot.FIXED_RULES, "style_voice": bot.style_rule("voice"), "style_chat": bot.style_rule("chat"),
                                "closing": [f"{n}: {bot.closing_word(n)}" for n in langs]},
                "disposition_categories": list(__import__("layer9_voice.disposition", fromlist=["x"]).DISPOSITIONS),
                "status": {"sarvam_key": bool(getattr(mgr.stt, "configured", False)), "model": getattr(mgr.chat, "provider", None), "phone_line": getattr(mgr, "phone", None) is not None},
                "borrowers": {"source": borrowerlib.source(), "rows": borrowerlib.public()}}

    @r.get("/settings")
    def settings():
        srv.require(*READ)
        return settings_view()

    @r.put("/settings")
    def settings_save(b: ConfigBody):
        srv.require(*WRITE)
        old = config.get()
        ok, problems, new = config.save(b.config)
        if not ok:
            raise HTTPException(422, {"problems": problems})
        note("VOICE_CONFIG_CHANGED", changed=config.changed_keys(old, new)[:40])
        return settings_view()

    @r.delete("/settings")
    def settings_reset():
        srv.require(*WRITE)
        old = config.get()
        new = config.reset()
        note("VOICE_CONFIG_RESET", changed=config.changed_keys(old, new)[:40])
        return settings_view()

    @r.post("/settings/borrowers/import")
    def borrowers_import(b: CsvBody):
        srv.require(*WRITE)
        ok, n, problems = borrowerlib.import_csv(b.csv, b.replace)
        if not ok:
            raise HTTPException(422, {"problems": problems})
        note("VOICE_BORROWERS_IMPORTED", rows=n, replace=bool(b.replace))
        return settings_view()

    @r.post("/settings/borrowers")
    def borrowers_add(b: RowBody):
        srv.require(*WRITE)
        ok, problems = borrowerlib.add(b.model_dump())
        if not ok:
            raise HTTPException(422, {"problems": problems})
        note("VOICE_BORROWER_ADDED")
        return settings_view()

    @r.delete("/settings/borrowers/{bid}")
    def borrowers_remove(bid: str):
        srv.require(*WRITE)
        if not borrowerlib.remove(bid):
            raise HTTPException(404, "no such borrower")
        note("VOICE_BORROWER_REMOVED")
        return settings_view()

    @r.delete("/settings/borrowers")
    def borrowers_reset():
        srv.require(*WRITE)
        borrowerlib.reset()
        note("VOICE_BORROWERS_RESET")
        return settings_view()

    @r.get("/chat")
    def chat_page():
        """The text chat page: the same bot, pipeline and disposition, typed instead of spoken."""
        return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "chat.html"), headers={"Cache-Control": "no-store"})

    @r.get("/status")
    def status():
        return {"llm": getattr(mgr.chat, "provider", None), "speech_to_text": mgr.stt is not None and getattr(mgr.stt, "configured", True),
                "text_to_speech": mgr.tts is not None and getattr(mgr.tts, "configured", True), "phone_line": getattr(mgr, "phone", None) is not None, "borrowers": len(mgr.borrowers()), "active_calls": sum(1 for c in mgr.calls.values() if c.status == "active")}

    @r.get("/borrowers")
    def borrowers():
        return run(mgr.public_borrowers)

    @r.post("/call/start")
    def start(b: Start):
        out = run(mgr.start, b.borrower_id, b.channel)
        if b.audio:
            call = mgr.get(out["call_id"])
            out["audio_b64"], out["tts_error"], _ = mgr.speak(out["greeting"], call)
        return out

    @r.post("/call/{call_id}/say")
    def say(call_id: str, b: Say):
        out = run(mgr.turn, call_id, b.text)
        if b.audio:
            out["bot_audio_b64"], out["tts_error"], out["tts_ms"] = mgr.speak(out["bot_say"], mgr.get(call_id))
        return out

    @r.post("/call/{call_id}/audio")
    async def audio(call_id: str, request: Request):
        body = await request.body()
        if not body:
            raise HTTPException(400, "empty_audio")
        if len(body) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "audio_too_large")
        ctype = (request.headers.get("content-type") or "audio/wav").split(";")[0].strip()
        # a whole turn (speech-to-text, model, text-to-speech) takes seconds: run it in a worker thread so it never blocks the server's event loop (and every other request)
        return await run_in_threadpool(run, mgr.turn_from_audio, call_id, body, "audio." + ctype.split("/")[-1].replace("x-", ""), ctype)

    @r.post("/call/{call_id}/end")
    def end(call_id: str):
        return run(mgr.end, call_id)

    @r.get("/call/{call_id}")
    def state(call_id: str):
        return run(mgr.state, call_id)

    return r
