"""Voice bot tests.   .venv/bin/python -m layer9_voice.test_voice

Part 1 (fast, no network): the Sarvam client against a mocked server, the strict disposition parser, the speech-safe filter, the model's masked-only memory.
Part 2 (the real pipeline: real detector, vault and audit chain; a RECORDING fake model): a whole call, and the one claim that matters -
no raw value is ever handed to the model, in a turn, in its memory, or in the disposition request.
Nothing here calls Sarvam or Bedrock: that needs the key, see `python -m layer9_voice.probe`."""
import base64

from layer9_voice import _testenv  # noqa: F401  (must come before any other layer9_voice import)
import io
import json
import os
import re
import sys
import traceback
import wave

import httpx

from layer9_voice import bot, calls, config, disposition
from layer9_voice.sarvam import SarvamClient, SarvamError, clean_reply, lang_code, wav_seconds

KEY = "SECRET-KEY-do-not-leak-123"


def wav_bytes(seconds, rate=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def client_with(handler):
    return SarvamClient(api_key=KEY, http=httpx.Client(transport=httpx.MockTransport(handler)))


def jresp(obj, status=200):
    return httpx.Response(status, json=obj)


# ------------------------------------------------------------------ part 1: client
def test_chat_sends_the_documented_request_and_returns_content_only():
    seen = {}

    def h(req):
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers.get("api-subscription-key"), json.loads(req.content)
        return jresp({"choices": [{"message": {"role": "assistant", "content": "Hello there", "reasoning_content": "secret thoughts"}, "finish_reason": "stop"}]})
    out = client_with(h).chat([{"role": "user", "content": "hi"}], model="sarvam-105b")
    assert out == "Hello there"
    assert seen["url"] == "https://api.sarvam.ai/v1/chat/completions" and seen["key"] == KEY                  # sarvam-105b*: v1 (on v2 reasoning cannot be switched off, measured)
    assert seen["body"]["model"] == "sarvam-105b" and "reasoning_effort" not in seen["body"]            # left out unless asked for
    client_with(h).chat([{"role": "user", "content": "hi"}], model="deepseekv4.1-flash-1017")
    assert seen["url"] == "https://api.sarvam.ai/v2/chat/completions"                                         # other models: v2 (Sarvam's own example)


def test_chat_passes_reasoning_effort_when_given():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return jresp({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    client_with(h).chat([{"role": "user", "content": "hi"}], reasoning_effort="low")
    assert seen["body"]["reasoning_effort"] == "low"
    client_with(h).chat([{"role": "user", "content": "hi"}], reasoning_effort="off")
    assert "reasoning_effort" in seen["body"] and seen["body"]["reasoning_effort"] is None             # JSON null = reasoning off (measured)


def test_think_block_in_content_is_stripped_and_unterminated_one_is_refused():
    assert clean_reply("<think>plan</think>Pay by Friday.") == "Pay by Friday."
    for bad in ("<think>never closed", "<think>x</think>", "", None):
        try:
            clean_reply(bad)
        except SarvamError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_empty_content_with_length_stop_is_a_clear_error():
    def h(req):
        return jresp({"choices": [{"message": {"content": None, "reasoning_content": "..."}, "finish_reason": "length"}]})
    try:
        client_with(h).chat([{"role": "user", "content": "hi"}])
    except SarvamError as e:
        assert str(e) == "ran_out_of_tokens_while_reasoning"
        return
    raise AssertionError("no error")


def test_errors_never_carry_the_key_or_the_response_body():
    def h(req):
        return jresp({"error": {"message": "echo of YOUR PRIVATE TEXT 98450 12345", "code": "invalid_api_key_error", "request_id": "r1"}}, 403)
    c = client_with(h)
    try:
        c.chat([{"role": "user", "content": "x"}])
    except SarvamError as e:
        s = str(e)
        assert s == "http_403:invalid_api_key_error", s
        assert KEY not in s and "98450" not in s and "PRIVATE" not in s
    else:
        raise AssertionError("no error")
    assert KEY not in repr(c) and "set" in repr(c)


def test_missing_key_fails_before_any_network_call():
    called = []
    c = SarvamClient(api_key="", http=httpx.Client(transport=httpx.MockTransport(lambda r: called.append(1) or jresp({}))))
    os.environ.pop("SARVAM_API_KEY", None)
    c._key = ""
    try:
        c.chat([{"role": "user", "content": "x"}])
    except SarvamError as e:
        assert str(e) == "sarvam_api_key_missing" and not called
        return
    raise AssertionError("no error")


def test_timeouts_and_network_errors_become_short_reasons():
    def boom(req):
        raise httpx.ReadTimeout("slow")
    try:
        client_with(boom).chat([{"role": "user", "content": "x"}])
    except SarvamError as e:
        assert str(e) == "timeout"
    else:
        raise AssertionError("no error")


def test_transcribe_posts_multipart_with_model_mode_and_language():
    seen = {}

    def h(req):
        seen["ctype"], seen["body"], seen["key"], seen["path"] = req.headers["content-type"], req.content, req.headers.get("api-subscription-key"), req.url.path
        return jresp({"request_id": "r", "transcript": " मैं सात तारीख तक भर दूँगा ", "language_code": "hi-IN", "language_probability": 0.97})
    out = client_with(h).transcribe(wav_bytes(1), language="Hindi")
    assert out == {"transcript": "मैं सात तारीख तक भर दूँगा", "language_code": "hi-IN", "language_probability": 0.97}
    assert seen["path"] == "/speech-to-text" and seen["ctype"].startswith("multipart/form-data") and seen["key"] == KEY
    body = seen["body"]
    for needle in (b'name="file"', b'name="model"', b"saaras:v4", b'name="mode"', b"transcribe", b'name="language_code"', b"hi-IN"):
        assert needle in body, needle


def test_transcribe_refuses_empty_and_over_30_seconds_without_a_call():
    called = []
    c = client_with(lambda r: called.append(1) or jresp({}))
    for audio, why in ((b"", "empty_audio"), (wav_bytes(31), "audio_longer_than_30s")):
        try:
            c.transcribe(audio)
        except SarvamError as e:
            assert str(e) == why
        else:
            raise AssertionError(why)
    assert not called and wav_seconds(wav_bytes(2)) == 2.0 and wav_seconds(b"not a wav") is None


def test_synthesize_decodes_audio_and_sends_language_and_speaker():
    seen = {}
    pcm = wav_bytes(0.1)

    def h(req):
        seen["body"] = json.loads(req.content)
        return jresp({"request_id": "r", "audios": [base64.b64encode(pcm).decode()]})
    out = client_with(h).synthesize("Namaste", language="Marathi", speaker="shubh")
    assert out == pcm and seen["body"]["language_code"] == "mr-IN" and seen["body"]["speaker"] == "shubh" and seen["body"]["text"] == "Namaste"
    for bad_text in ("", "   ", "x" * 2501):
        try:
            client_with(h).synthesize(bad_text)
        except SarvamError:
            continue
        raise AssertionError("accepted bad text")


def test_language_names_map_to_codes():
    assert [lang_code(x) for x in ("English", "hindi", "Marathi", "ta-IN", "Klingon", None)] == ["en-IN", "hi-IN", "mr-IN", "ta-IN", "en-IN", "en-IN"]


# ------------------------------------------------------------------ part 1b: disposition parser
GOOD_REPLY = {"disposition": "PROMISE_TO_PAY", "will_pay": "yes", "promised_amount": "Rs 4,200", "promised_date": "the 7th", "reason": "Customer committed to pay on the 7th.", "evidence": ["I will pay by the 7th"]}
GOOD = {**GOOD_REPLY, "promised_amount": 4200}          # what the parser returns: a number


def expect_error(reply, why):
    try:
        disposition.parse(reply)
    except disposition.DispositionError as e:
        assert why in str(e), (why, str(e))
        return
    raise AssertionError(f"accepted: {reply!r}")


def test_parse_accepts_a_good_answer_even_with_chatter_and_extras():
    out = disposition.parse("Sure! Here you go:\n```json\n" + json.dumps({**GOOD_REPLY, "extra": "dropped"}) + "\n```")
    assert out == GOOD and "extra" not in out
    assert disposition.parse(json.dumps({**GOOD_REPLY, "promised_amount": "\u20b94200.50"}))["promised_amount"] == 4200.5
    assert disposition.parse(json.dumps({**GOOD_REPLY, "promised_amount": None}))["promised_amount"] is None
    assert disposition.parse(json.dumps({**GOOD_REPLY, "promised_amount": 4200}))["promised_amount"] == 4200


def test_parse_rejects_everything_off_spec():
    expect_error("I think they will pay.", "no_json_object")
    expect_error("{not json", "json_unreadable")
    expect_error("[1,2]", "no_json_object")
    expect_error(json.dumps({**GOOD, "disposition": "MAYBE"}), "unknown_disposition")
    expect_error(json.dumps({**GOOD, "will_pay": "probably"}), "unknown_will_pay")
    expect_error(json.dumps({**GOOD, "will_pay": "no"}), "contradicts")                                       # promise to pay but will_pay no
    expect_error(json.dumps({**GOOD, "disposition": "REFUSES_TO_PAY", "will_pay": "yes"}), "contradicts")
    expect_error(json.dumps({**GOOD, "promised_amount": -5}), "amount")
    expect_error(json.dumps({**GOOD, "promised_amount": True}), "amount")
    expect_error(json.dumps({**GOOD, "promised_amount": "4200"}), "amount")                                   # a string without the rupee marker
    expect_error(json.dumps({**GOOD, "promised_amount": "[redacted]"}), "amount")                              # what the gateway's sanitiser leaves of a bare number
    expect_error(json.dumps({**GOOD, "reason": ""}), "reason")
    expect_error(json.dumps({**GOOD, "evidence": "just a string"}), "evidence")
    expect_error(json.dumps({**GOOD, "evidence": ["a", "b", "c", "d"]}), "evidence")


def test_generate_retries_once_then_fails_loudly_never_defaults():
    replies = iter(["garbage", json.dumps(GOOD_REPLY)])
    assert disposition.generate("t", lambda t: next(replies)) == GOOD
    n = []
    try:
        disposition.generate("t", lambda t: n.append(1) or "still garbage")
    except disposition.DispositionError as e:
        assert "after 3 attempts" in str(e) and len(n) == 3
        return
    raise AssertionError("returned a default")


def test_build_transcript_drops_oldest_turns_first():
    turns = [{"speaker": "Agent" if i % 2 == 0 else "Customer", "masked": f"line {i} " + "x" * 400} for i in range(20)]
    text, trimmed = disposition.build_transcript(turns, max_chars=1500)
    assert trimmed and len(text) <= 1500 and "line 19" in text and "line 0 " not in text
    text, trimmed = disposition.build_transcript(turns[:2], max_chars=5000)
    assert not trimmed and text.startswith("Agent: line 0")


# ------------------------------------------------------------------ part 1c: speech-safe text, the model's memory
def test_speakable_never_reads_a_placeholder_or_symbol():
    s = bot.speakable("Noted <PHONE_1> and <PERSON_2>.\n**Thank you**", "English")
    assert "<" not in s and "PHONE" not in s and "*" not in s and s.startswith("Noted that and that.")
    assert bot.speakable("<PHONE_1>", "Marathi") == "ते"                            # only a token: still no placeholder
    assert bot.speakable("", "Hindi") == bot.repeat_line("Hindi") and bot.speakable("   ", "English") == bot.repeat_line("English")


def test_bot_system_never_contains_name_number_or_loan_reference():
    p = {"product": "Personal loan", "outstanding": 4200, "dpd": 12, "loan_ref": "••4821", "language": "English"}
    s = bot.bot_system(p)
    assert "Rs 4,200" in s and "already late" in s and "12" not in s and "4821" not in s


class FakeChat:
    """Records exactly what the model would be sent. script(messages, kind) -> reply."""
    provider = "fake"

    def __init__(self, script):
        self.script, self.seen = script, []

    def __call__(self, messages, kind):
        self.seen.append((kind, [dict(m) for m in messages]))
        return self.script(messages, kind)

    def everything(self):
        return "\n".join(m["content"] for _, msgs in self.seen for m in msgs)


def test_callllm_memory_is_bounded_and_outside_a_call_it_defers():
    chat = FakeChat(lambda m, k: "ok")
    llm = bot.CallLLM(chat, lambda t: "fallback:" + t)
    assert llm("hello") == "fallback:hello" and not chat.seen

    class C:
        system, llm_history = "SYS", []
    c = C()
    with bot.active(c, "bot"):
        for i in range(20):
            llm(f"u{i}")
    assert len(c.llm_history) == config.limits()['history_messages'] and c.llm_history[-2]["content"] == "u19"
    assert bot.ACTIVE["call"] is None                                             # the context was released


# ------------------------------------------------------------------ part 1d: speech speed-ups (sentence-parallel text-to-speech, cached fixed lines)
class _Call:
    language = "English"


class _Tts:
    configured = True

    def __init__(self, fail_on=None):
        self.said, self.fail_on = [], fail_on

    def synthesize(self, text, language, sample_rate=None):
        self.said.append(text)
        if self.fail_on and self.fail_on in text:
            raise SarvamError("http_503")
        return wav_bytes(0.1 * len(text.split()), rate=8000)


def test_split_sentences_keeps_every_word_and_joins_short_fragments():
    t = "I am calling about your personal loan. An instalment of Rs. 4,200 is overdue. When can you pay?"
    parts = calls.split_sentences(t)
    assert 1 < len(parts) <= 4 and " ".join(parts).split() == t.split(), parts
    assert not any(p.rstrip().endswith("Rs.") for p in parts), parts                                         # an abbreviation is never cut off from its number
    assert calls.split_sentences("Thank you.") == ["Thank you."] and calls.split_sentences("") == []
    assert len(calls.split_sentences("Hello there my friend. Thanks a lot.")) in (1, 2)                    # exactly two parts must not crash
    assert len(calls.split_sentences(". ".join(f"Sentence number {i} is here" for i in range(9)) + ".")) <= 4


def test_join_wavs_adds_durations_and_refuses_mixed_formats():
    out = calls.join_wavs([wav_bytes(1, 8000), wav_bytes(2, 8000)])
    assert abs(wav_seconds(out) - 3.0) < 0.01
    try:
        calls.join_wavs([wav_bytes(1, 8000), wav_bytes(1, 16000)])
    except ValueError:
        return
    raise AssertionError("joined different formats")


def test_a_long_reply_is_spoken_in_parallel_sentences_and_comes_back_as_one_wav():
    tts = _Tts()
    mgr = calls.CallManager(None, tts=tts, borrowers=[])
    text = "I am calling about your personal loan. An instalment of 4200 rupees is overdue. When can you pay this amount?"
    b64, err, ms = mgr.speak(text, _Call())
    assert err is None and len(tts.said) == 3 and sorted(tts.said) == sorted(calls.split_sentences(text)), tts.said          # parallel: the order they were requested in is not fixed
    audio = base64.b64decode(b64)
    assert wav_seconds(audio) is not None and abs(wav_seconds(audio) - 0.1 * len(text.split())) < 0.05          # one valid WAV, as long as the parts together


def test_if_one_sentence_fails_the_whole_line_is_sent_in_one_call():
    tts = _Tts(fail_on="instalment")
    mgr = calls.CallManager(None, tts=tts, borrowers=[])
    text = "I am calling about your personal loan. An instalment of 4200 rupees is overdue. When can you pay this amount?"
    # the failing part makes the parallel attempt fail; the fallback sends the whole text, which contains the same word and fails too: the error is reported, not raised
    b64, err, ms = mgr.speak(text, _Call())
    assert b64 is None and err == "http_503" and tts.said[-1] == text


def test_a_slow_speech_call_is_raced_by_a_second_one_and_the_faster_wins():
    import time as _t
    n = []

    def fn():
        n.append(1)
        if len(n) == 1:
            _t.sleep(1.0)                                                      # the first attempt is slow
            return "slow"
        return "fast"
    t = _t.time()
    assert calls.hedged(fn, delay=0.1) == "fast" and _t.time() - t < 0.8 and len(n) == 2
    m = []
    assert calls.hedged(lambda: m.append(1) or "ok", delay=0.5) == "ok" and len(m) == 1                          # a quick call is never doubled


def test_a_failed_speech_call_gets_one_retry_and_a_double_failure_is_raised():
    n = []

    def flaky():
        n.append(1)
        if len(n) == 1:
            raise SarvamError("http_503")
        return "ok"
    assert calls.hedged(flaky, delay=0.5) == "ok" and len(n) == 2
    try:
        calls.hedged(lambda: (_ for _ in ()).throw(SarvamError("http_500")), delay=0.5)
    except SarvamError as e:
        assert str(e) == "http_500"
        return
    raise AssertionError("swallowed")


def test_fixed_lines_are_spoken_once_and_cached_but_customer_lines_never_are():
    tts = _Tts()
    mgr = calls.CallManager(None, tts=tts, borrowers=[])
    g = bot.greeting("English")
    mgr.speak(g, _Call())
    n = len(tts.said)
    mgr.speak(g, _Call())
    assert len(tts.said) == n and len(mgr._fixed_audio) == 1                                                  # the second time cost no speech call at all
    mgr.speak("You will pay 4200 rupees by the 7th.", _Call())
    mgr.speak("You will pay 4200 rupees by the 7th.", _Call())
    assert len(mgr._fixed_audio) == 1                                                                       # an answer built from the customer's words is never kept
    mgr2 = calls.CallManager(None, tts=_Tts(), borrowers=[{"id": "x", "language": "Hindi"}, {"id": "y", "language": "English"}])
    assert mgr2.warm() == 12 and len(mgr2._fixed_audio) == 12                                                 # 2 languages x 3 lines x 2 sample rates


# ------------------------------------------------------------------ part 2: the real pipeline
RAW = ("98450", "12345", "ABCPK1234L")      # values the customer says; none may ever reach the model

_ENV = {}


def pipeline():
    """The real console workspace (real detector incl. the model, real vault, real audit chain), without starting a web server. Built once."""
    if "srv" not in _ENV:
        os.environ.setdefault("DEMO_NO_BROWSER", "1")
        import layer8_demo.server as srv
        srv.DET["det"] = srv.RecordingDetector(srv.make_detector(model=True))
        srv.WS["ws"] = srv.Workspace()
        srv.DET["det"].inner.detect_calls([[{"speaker": "Customer", "text": "warm up my number is nine eight four five"}]])
        _ENV["srv"] = srv
    return _ENV["srv"]


def manager(script, stt=None, tts=None):
    srv = pipeline()
    chat = FakeChat(script)
    srv.WS["ws"].gw.llm = bot.CallLLM(chat, srv.Gateway._stub_llm)             # what run.py does at start-up, done on the live gateway
    mgr = calls.CallManager(srv, chat=chat, stt=stt, tts=tts, borrowers=[
        {"id": "t-1", "name": "Voice Test Person", "phone": "+919845012345", "language": "English", "product": "Personal loan", "outstanding": 4200, "dpd": 12, "loan_last4": "4821"}])
    return srv, mgr, chat


def scripted(messages, kind):
    if kind == "disposition":
        return json.dumps(GOOD_REPLY)
    last = messages[-1]["content"]
    if "pay" in last.lower():
        return "Thank you, I have noted that you will pay. Goodbye."
    return "Thanks. I have noted <PHONE_1> and your PAN, thank you."            # the model tries to repeat a private value


def test_a_whole_call_the_model_only_ever_sees_masked_text():
    srv, mgr, chat = manager(scripted)
    start = mgr.start("t-1")
    assert start["language"] == "en-IN" and start["greeting"].startswith("Hello")
    t1 = mgr.turn(start["call_id"], "Yes this is me. My phone number is 98450 12345 and my PAN is ABCPK1234L.")
    assert t1["status"] == "ok", t1
    assert {f["label"] for f in t1["found"]} >= {"PAN"}, t1["found"]
    assert not any(r in (t1["customer_masked"] or "") for r in RAW), t1["customer_masked"]
    assert re.search(r"<[A-Z_]+_\d+>", t1["customer_masked"])
    assert "<" not in t1["bot_say"] and "98450" not in t1["bot_say"], t1["bot_say"]        # a placeholder the model wrote is never spoken or filled in
    t2 = mgr.turn(start["call_id"], "I will pay Rs 4,200 by the 7th")
    assert t2["status"] == "ok"
    # the model's memory: turn 2 was sent turn 1's MASKED message and its own reply
    kinds, msgs = chat.seen[1]
    assert kinds == "bot" and msgs[0]["role"] == "system" and msgs[1]["role"] == "user" and msgs[2]["role"] == "assistant" and msgs[-1]["role"] == "user"
    assert msgs[1]["content"] == t1["customer_masked"]
    end = mgr.end(start["call_id"])
    assert end["disposition"] == GOOD and end["error"] is None, end
    dkind, dmsgs = chat.seen[-1]
    assert dkind == "disposition" and dmsgs[0]["content"] == disposition.SYSTEM and dmsgs[1]["content"].startswith("Agent: Hello")
    assert "Customer: " in dmsgs[1]["content"] and "Agent: Thank you" in dmsgs[1]["content"]
    assert re.search(r"<PHONE_\d+>", dmsgs[1]["content"]) and re.search(r"<PAN_\d+>", dmsgs[1]["content"]) and "Rs 4,200" in dmsgs[1]["content"], dmsgs[1]["content"]   # masked once, amount kept
    assert "ADDRESS" not in dmsgs[1]["content"], dmsgs[1]["content"]                                          # the old re-masking garble must not come back
    assert mgr.get(start["call_id"]).raw_turns == []                                                          # the raw transcript did not outlive the call
    blob = chat.everything()
    for r in RAW + ("Voice Test Person", "+919845012345", "4821"):
        assert r not in blob, f"a raw value reached the model: {r!r}"
    # the call is a normal case in the console, and the audit chain recorded it and is still intact
    ws = srv.WS["ws"]
    s = ws.borrowers[start["case_sid"]]
    assert s.name == "Voice Test Person" and s.profile["channel"] == "Voice call"
    events = [r.get("event") for r in ws.audit.records()]
    assert "VOICE_CALL_STARTED" in events and "VOICE_CALL_ENDED" in events
    assert ws.audit.verify_chain()[0]
    assert mgr.state(start["call_id"])["status"] == "ended" and all("98450" not in json.dumps(mgr.state(start["call_id"])) for _ in [0])


def test_a_blocked_or_failed_turn_makes_the_bot_ask_again_and_adds_nothing():
    def flaky(messages, kind):
        raise RuntimeError("model down")
    srv, mgr, chat = manager(flaky)
    cid = mgr.start("t-1")["call_id"]
    t = mgr.turn(cid, "My number is 98450 12345")
    assert t["status"] == "blocked" and t["bot_say"] == bot.repeat_line("English"), t
    assert mgr.state(cid)["transcript_masked"] == [{"speaker": "Agent", "masked": bot.greeting("English")}]       # only the greeting: nothing from the failed turn
    assert mgr.get(cid).raw_turns == [{"speaker": "Agent", "text": bot.greeting("English")}]                      # and the failed utterance was not kept raw either
    assert not any(r in chat.everything() for r in ("98450", "12345"))


def test_disposition_failure_is_reported_never_defaulted():
    srv, mgr, chat = manager(lambda m, k: "this is not json" if k == "disposition" else "Thank you.")
    cid = mgr.start("t-1")["call_id"]
    mgr.turn(cid, "hello")
    end = mgr.end(cid)
    assert end["disposition"] is None and "after 3 attempts" in end["error"], end
    assert mgr.get(cid).raw_turns == []                                                                       # wiped even when the disposition failed


def test_call_lifecycle_errors():
    srv, mgr, chat = manager(scripted)
    for fn, args, status in ((mgr.start, ("nobody",), 404), (mgr.turn, ("call-nope", "hi"), 404)):
        try:
            fn(*args)
        except calls.CallError as e:
            assert e.status == status
        else:
            raise AssertionError("no error")
    cid = mgr.start("t-1")["call_id"]
    try:
        mgr.turn(cid, "   ")
    except calls.CallError as e:
        assert e.reason == "empty_utterance"
    mgr.end(cid)
    for fn, args in ((mgr.turn, (cid, "hi again")), (mgr.end, (cid,))):
        try:
            fn(*args)
        except calls.CallError as e:
            assert e.status == 409
        else:
            raise AssertionError("allowed after the call ended")


def test_audio_path_speech_in_text_out_with_a_tts_failure_not_stopping_the_call():
    class Stt:
        configured = True

        def transcribe(self, audio, language=None, filename=None, content_type=None):
            return {"transcript": "I will pay by the 7th, my PAN is ABCPK1234L", "language_code": "en-IN", "language_probability": 0.9}

    class Tts:
        configured = True

        def __init__(self):
            self.said = []

        def synthesize(self, text, language):
            self.said.append(text)
            raise SarvamError("http_503")
    tts = Tts()
    srv, mgr, chat = manager(scripted, stt=Stt(), tts=tts)
    cid = mgr.start("t-1")["call_id"]
    out = mgr.turn_from_audio(cid, wav_bytes(1))
    assert out["status"] == "ok" and out["heard"].startswith("I will pay") and out["bot_audio_b64"] is None and out["tts_error"] == "http_503"
    assert out["bot_say"] and "ABCPK1234L" not in chat.everything() and "ABCPK1234L" not in "".join(tts.said)       # the speech vendor is only handed the bot's masked line


def test_audio_path_empty_transcript_asks_again_without_calling_the_pipeline():
    class Stt:
        configured = True

        def transcribe(self, *a, **k):
            return {"transcript": "", "language_code": None}
    srv, mgr, chat = manager(scripted, stt=Stt())
    cid = mgr.start("t-1")["call_id"]
    out = mgr.turn_from_audio(cid, wav_bytes(1))
    assert out["status"] == "no_speech" and not chat.seen and mgr.get(cid).n == 0


def test_http_routes_through_the_real_app():
    from fastapi.testclient import TestClient
    from layer9_voice import router
    srv, mgr, chat = manager(scripted)
    if not any(getattr(r, "path", "") == "/voice/status" for r in srv.app.routes):
        srv.app.include_router(router.build(srv, mgr))
    else:                                                                       # a previous test already mounted a manager: point the routes at this one
        router.build(srv, mgr)
        srv.app.router.routes[:] = [r for r in srv.app.router.routes if not getattr(r, "path", "").startswith("/voice")]
        srv.app.include_router(router.build(srv, mgr))
    c = TestClient(srv.app)
    st = c.get("/voice/status").json()
    assert st["llm"] == "fake" and st["borrowers"] == 1
    b = c.get("/voice/borrowers").json()
    assert b[0]["phone"].endswith("2345") and "98450" not in b[0]["phone"] and b[0]["phone"].startswith("•")
    start = c.post("/voice/call/start", json={"borrower_id": "t-1"}).json()
    say = c.post(f"/voice/call/{start['call_id']}/say", json={"text": "I will pay by the 7th"})
    assert say.status_code == 200 and say.json()["status"] == "ok"
    assert c.post(f"/voice/call/{start['call_id']}/say", json={"text": ""}).status_code == 400
    assert c.post("/voice/call/call-zzz/say", json={"text": "x"}).status_code == 404
    assert c.post(f"/voice/call/{start['call_id']}/audio", content=b"", headers={"content-type": "audio/wav"}).status_code == 400
    assert c.post(f"/voice/call/{start['call_id']}/audio", content=b"RIFF", headers={"content-type": "audio/wav"}).status_code == 503       # no speech-to-text configured
    assert c.get(f"/voice/call/{start['call_id']}").json()["status"] == "active"
    end = c.post(f"/voice/call/{start['call_id']}/end")
    assert end.status_code == 200 and end.json()["disposition"]["will_pay"] == "yes"
    assert c.post(f"/voice/call/{start['call_id']}/end").status_code == 409
    # a non-agent role is refused (and the refusal is audited by the console itself)
    assert c.get("/voice/borrowers", headers={"x-user": "vikram"}).status_code == 403


# ------------------------------------------------------------------ part 2b: the chat channel
def test_a_chat_is_a_voice_call_without_speech_and_with_a_texting_prompt():
    srv, mgr, chat, = manager(scripted)[:3]
    out = mgr.start("t-1", "chat")
    assert out["channel"] == "chat" and out["greeting"] == bot.greeting("English", "chat") and "message" in out["greeting"] and out["greeting"] != bot.greeting("English")
    call = mgr.get(out["call_id"])
    assert "text chat" in call.system and "spoken aloud" not in call.system and "chatting by text" in call.system
    assert "spoken aloud" in bot.bot_system({}, "voice") and "text chat" not in bot.bot_system({}, "voice")
    case = srv.WS["ws"].borrowers[out["case_sid"]]
    assert case.profile["channel"] == "Chat" and case.profile["assignee"] == "Chat bot"
    t = mgr.turn(out["call_id"], "My number is 98450 12345 and I will pay Rs 4,200 by the 7th")
    assert t["status"] == "ok" and "98450" not in (t["customer_masked"] or "") and "<PHONE_" in t["customer_masked"]
    assert "98450" not in chat.everything()
    end = mgr.end(out["call_id"])
    assert end["disposition"] == GOOD
    start = [r for r in srv.WS["ws"].audit.records() if r.get("call_id") == out["call_id"] and r.get("event") == "VOICE_CALL_STARTED"]
    assert start and start[0]["channel"] == "chat"
    try:
        mgr.start("t-1", "carrier-pigeon")
    except calls.CallError as e:
        assert e.status == 400
    else:
        raise AssertionError("accepted an unknown channel")


def test_chat_page_and_channel_over_http():
    from fastapi.testclient import TestClient
    from layer9_voice import router
    srv, mgr, chat, = manager(scripted)[:3]
    srv.app.include_router(router.build(srv, mgr))
    c = TestClient(srv.app)
    page = c.get("/voice/chat")
    assert page.status_code == 200 and "Start chat" in page.text and "http://" not in page.text.replace("http://www.w3.org", "") and "https://" not in page.text
    r = c.post("/voice/call/start", json={"borrower_id": "t-1", "channel": "chat"})
    assert r.status_code == 200 and r.json()["channel"] == "chat"
    assert c.post("/voice/call/start", json={"borrower_id": "t-1", "channel": "fax"}).status_code == 400


def test_the_bot_has_one_job_and_the_turn_says_when_it_closed_the_conversation():
    sysmsg = bot.bot_system({"product": "Personal loan", "outstanding": 4200})
    assert "ONLY job" in sysmsg and "Stay on that one topic" in sysmsg and "intends to pay" in sysmsg and "END your last message" in sysmsg
    for closing in ("Thank you. Goodbye.", "Thanks, have a good day.", "Take care!", "धन्यवाद, अलविदा।", "आपका दिन शुभ हो।", "निरोप.", "तुमचा दिवस चांगला जावो"):
        assert bot.is_goodbye(closing), closing
    for open_ in ("When can you pay?", "Good morning, is this a good time?", "You will pay Rs 4,200 by the 7th, is that right?"):
        assert not bot.is_goodbye(open_), open_
    srv, mgr, chat = manager(scripted)[:3]
    cid = mgr.start("t-1", "chat")["call_id"]
    t1 = mgr.turn(cid, "Yes this is me")                                      # scripted reply: no goodbye
    t2 = mgr.turn(cid, "I will pay Rs 4,200 by the 7th")                      # scripted reply ends with Goodbye.
    assert t1["goodbye"] is False and t2["goodbye"] is True, (t1["bot_say"], t2["bot_say"])
    srv2, mgr2, chat2 = manager(lambda m, k: (_ for _ in ()).throw(RuntimeError("down")))[:3]
    cid2 = mgr2.start("t-1")["call_id"]
    assert mgr2.turn(cid2, "hello")["goodbye"] is False                       # a blocked turn never closes the call


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print("PASS", n, flush=True)
        except Exception:                                                      # noqa: BLE001
            failed += 1
            print("FAIL", n, flush=True)
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed", flush=True)
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
