"""Start the demo console WITH the voice bot:    .venv/bin/python -m layer9_voice.run          (then open http://127.0.0.1:8808)

The console (layer8_demo/server.py) is not edited. This launcher imports it, swaps in the bot's LLM function (outside a call it behaves exactly as before), adds the /voice endpoints
and starts it. A call shows up in the console as a normal case.

  VOICE_LLM=sarvam|bedrock|stub   which model answers (default: sarvam when SARVAM_API_KEY is set, else stub)
  SARVAM_API_KEY                  in .env, never printed: the model, speech-to-text and text-to-speech
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, PUBLIC_BASE_URL
                                  in .env: switch on the INBOUND phone line (people dial a number we own). PUBLIC_BASE_URL is the https address Twilio can reach
                                  (a tunnel to this machine's port 8808). Without all three there are no phone routes at all. Nothing ever dials out.
"""
import os

import layer8_demo.server as srv                               # first: it loads .env and sets the working directory
from layer9_voice import bot, calls, router, telephony
from layer9_voice.sarvam import SarvamClient


def wire(app_module=srv):
    chat = bot.make_chat()
    app_module._llm = bot.CallLLM(chat, app_module._llm)       # the workspace is created at start-up and picks this up
    client = SarvamClient(timeout=15.0)
    mgr = calls.CallManager(app_module, chat=chat, stt=client if client.configured else None, tts=client if client.configured else None)
    app_module.app.include_router(router.build(app_module, mgr))
    sid, tok, base = (os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "PUBLIC_BASE_URL"))
    if sid and tok and base:
        # a phone call is not any console user: register it as its own audit actor (a runtime entry, the server file is not edited)
        app_module.USERS.setdefault("voice-line", {"name": "Voice line", "role": "agent", "title": "Phone line (voice bot)", "type": "service"})
        mgr.phone = telephony.PhoneLine(mgr, sid, tok, base)
        app_module.app.include_router(telephony.build(app_module, mgr, mgr.phone))
    if mgr.tts is not None:
        import threading
        threading.Thread(target=mgr.warm, daemon=True).start()  # speak the fixed lines once so the first call does not wait for them
    return mgr


if __name__ == "__main__":
    mgr = wire()
    print(f"voice bot: model={mgr.chat.provider}  speech-to-text/text-to-speech={'sarvam' if mgr.stt else 'NOT configured (no SARVAM_API_KEY)'}  "
          f"phone line={'ON (inbound only): ' + mgr.phone.base + '/voice/phone/incoming' if getattr(mgr, 'phone', None) else 'off'}", flush=True)
    srv.main()
