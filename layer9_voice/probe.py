"""Live check of every guess in sarvam.py.   .venv/bin/python -m layer9_voice.probe [--detect]      (needs SARVAM_API_KEY in .env)

Sends ONLY made-up sentences (a fake phone number, a fake PAN): nothing real leaves the machine. Prints what worked, what failed and how long it took. Never prints the key.
  1. chat: the raw response shape, whether reasoning text appears, how to switch reasoning off, how long a bot-sized answer takes
  2. placeholders: does the model keep <PHONE_1> intact, and answer in one or two sentences
  3. speech round trip, per language (en / hi / mr): text -> speech -> text. Shows how the speech-to-text WRITES numbers (digits? words?)
  4. with --detect: that transcript through our real detector: was the fake number and PAN masked?
  5. a sample disposition through the real prompt and the strict parser"""
import os
import sys
import time

import common.llm  # noqa: F401,E402  (loads .env)
from layer9_voice import bot, disposition
from layer9_voice.sarvam import SarvamClient, SarvamError

SAY = {"en-IN": "My phone number is nine eight seven six five zero one two three four and my PAN is A B C P K one two three four L. I will pay by the seventh.",
       "hi-IN": "मेरा फोन नंबर नौ आठ सात छह पाँच शून्य एक दो तीन चार है और मैं सात तारीख तक भुगतान कर दूँगा।",
       "mr-IN": "माझा फोन नंबर नऊ आठ सात सहा पाच शून्य एक दोन तीन चार आहे आणि मी सात तारखेपर्यंत पैसे भरीन."}
SAMPLE = ("Agent: Hello, this is a reminder call about your overdue loan instalment. Is this a good time to talk?\n"
          "Customer: Yes. My phone number is <PHONE_1>.\nAgent: Thank you. When can you pay?\nCustomer: I will pay Rs 4,200 by the 7th.\nAgent: Thank you, goodbye.")


def step(name):
    print(f"\n--- {name}", flush=True)


def timed(fn, *a, **k):
    t = time.perf_counter()
    try:
        return fn(*a, **k), None, round((time.perf_counter() - t) * 1000)
    except SarvamError as e:
        return None, str(e), round((time.perf_counter() - t) * 1000)


def main():
    detect = "--detect" in sys.argv
    c = SarvamClient(timeout=40.0)
    if not c.configured:
        print("SARVAM_API_KEY is not set (put it in .env). Nothing was sent.")
        return False
    ok = True

    step("1. chat: raw response shape")
    for model in (os.environ.get("SARVAM_BOT_MODEL", "sarvam-105b-conversations"), "sarvam-105b"):
        body = {"model": model, "messages": [{"role": "user", "content": "Say hello in five words."}], "max_tokens": 400}
        j, err, ms = timed(c._post, "/v1/chat/completions", json=body)
        if err:
            print(f"  {model}: FAIL {err} ({ms} ms)")
            ok = False
            continue
        ch = j["choices"][0]
        print(f"  {model}: {ms} ms  finish={ch.get('finish_reason')}  message keys={sorted(ch['message'])}  usage={j.get('usage')}  think-tag-in-content={'<think>' in (ch['message'].get('content') or '')}")

    step("1b. how to switch reasoning off on sarvam-105b (latency + whether reasoning text comes back)")
    for label, extra in (("omitted", {}), ('"low"', {"reasoning_effort": "low"}), ("null", {"reasoning_effort": None}), ('"None"', {"reasoning_effort": "None"})):
        body = {"model": "sarvam-105b", "messages": [{"role": "user", "content": "In one short sentence, why do people pay loan instalments on time?"}], "max_tokens": 500, **extra}
        j, err, ms = timed(c._post, "/v1/chat/completions", json=body)
        if err:
            print(f"  reasoning_effort {label:8}: REJECTED {err} ({ms} ms)")
            continue
        m = j["choices"][0]["message"]
        print(f"  reasoning_effort {label:8}: {ms} ms  completion_tokens={(j.get('usage') or {}).get('completion_tokens')}  reasoning_text={'yes' if m.get('reasoning_content') else 'no'}  answer_chars={len(m.get('content') or '')}")

    step("2. placeholders and bot style (the bot's own system prompt)")
    system = bot.bot_system({"product": "Personal loan", "outstanding": 4200, "dpd": 12})
    msg = "Yes this is me. My number is <PHONE_1> and my PAN is <PAN_1>. I can pay Rs 4,200 by the 7th."
    reply, err, ms = timed(c.chat, [{"role": "system", "content": system}, {"role": "user", "content": msg}], model=os.environ.get("SARVAM_BOT_MODEL", "sarvam-105b-conversations"),
                           max_tokens=500, temperature=0.4)
    if err:
        print("  FAIL", err)
        ok = False
    else:
        import re
        toks = re.findall(r"<[^<>\s]{1,30}>", reply)
        print(f"  {ms} ms  words={len(reply.split())}  tokens_in_reply={toks}  speakable={bot.speakable(reply)!r}")
        print(f"  reply: {reply}")

    step("3. speech round trip (text -> speech -> text), per language")
    det = None
    if detect:
        from layer3_gateway.graph import make_detector
        det = make_detector(model=True)
    for code, sentence in SAY.items():
        audio, err, ms_tts = timed(c.synthesize, sentence, code)
        if err:
            print(f"  {code}: TTS FAIL {err} ({ms_tts} ms)  (check SARVAM_TTS_MODEL / SARVAM_TTS_SPEAKER; docs did not name the Bulbul v3 strings)")
            ok = False
            continue
        heard, err, ms_stt = timed(c.transcribe, audio, language=code)
        if err:
            print(f"  {code}: STT FAIL {err} ({ms_stt} ms)")
            ok = False
            continue
        print(f"  {code}: TTS {ms_tts} ms, {len(audio)} bytes | STT {ms_stt} ms -> {heard['transcript']!r}")
        if det is not None:
            spans = det.detect_calls([[{"speaker": "Customer", "text": heard["transcript"]}]])[0][-1]
            t = heard["transcript"]
            for sp in sorted(spans, key=lambda s: -s["start"]):
                t = t[:sp["start"]] + f"[{sp['label']}]" + t[sp["end"]:]
            print(f"      detector: {sorted({s['label'] for s in spans})}  ->  {t!r}")

    step("5. disposition on a sample transcript (sarvam-105b + the strict parser)")
    reply, err, ms = timed(c.chat, [{"role": "system", "content": disposition.SYSTEM}, {"role": "user", "content": SAMPLE}], model=os.environ.get("SARVAM_DISPOSITION_MODEL", "sarvam-105b"), max_tokens=1200, temperature=0.1, reasoning_effort="off")
    if err:
        print("  FAIL", err)
        ok = False
    else:
        try:
            print(f"  {ms} ms  parsed: {disposition.parse(reply)}")
        except disposition.DispositionError as e:
            print(f"  {ms} ms  PARSE FAILED: {e}\n  raw: {reply[:400]}")
            ok = False
    print("\nDONE." if ok else "\nDONE with failures above.")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
