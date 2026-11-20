"""Phone-line tests.   .venv/bin/python -m layer9_voice.test_phone

Twilio is SIMULATED (signed requests, recordings served by a mock); the pipeline behind it is the real one (real detector, vault, audit chain) with a recording fake model, as in test_voice.
Nothing here touches Twilio or Sarvam, and nothing dials anything: the line is inbound-only."""
import base64

from layer9_voice import _testenv  # noqa: F401  (must come before any other layer9_voice import)
import json
import re
import sys
import traceback
from urllib.parse import urlencode

import httpx

from layer9_voice import bot, router, telephony
from layer9_voice.calls import CallError
from layer9_voice.test_voice import GOOD, manager, scripted, wav_bytes

ACC = "AC" + "a" * 32
TOKEN = "twilio-token-for-tests-only"
BASE = "https://demo.example.test"
CALL = "CA" + "1" * 32
CALL2 = "CA" + "3" * 32
REC = "RE" + "2" * 32
REC_URL = f"https://api.twilio.com/2010-04-01/Accounts/{ACC}/Recordings/{REC}"
CALLER = "+919876512345"                 # the person dialling in: must never be read, stored or logged
OUR_NUMBER = "+14155550100"


class FakeStt:
    configured = True

    def __init__(self, transcript):
        self.transcript, self.heard = transcript, []

    def transcribe(self, audio, language=None, filename=None, content_type=None):
        self.heard.append(audio)
        return {"transcript": self.transcript, "language_code": "en-IN", "language_probability": 0.9}


class FakeTts:
    configured = True

    def __init__(self):
        self.said, self.rates = [], []

    def synthesize(self, text, language, sample_rate=None):
        self.said.append(text)
        self.rates.append(sample_rate)
        return wav_bytes(0.1)


class FakeTwilio:
    """Serves recordings (get_plan = status codes, the last one repeats) and records every request."""
    def __init__(self, get_plan=(200,)):
        self.requests, self.plan, self.audio = [], list(get_plan), wav_bytes(1)

    def handler(self, req):
        self.requests.append((req.method, str(req.url), req.headers.get("authorization")))
        if req.method == "GET":
            code = self.plan.pop(0) if len(self.plan) > 1 else self.plan[0]
            return httpx.Response(code, content=self.audio if code == 200 else b"")
        return httpx.Response(204)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def calls(self, method):
        return [r for r in self.requests if r[0] == method]


def phone(transcript="Yes this is me, my PAN is ABCPK1234L", script=scripted, get_plan=(200,), **kw):
    stt, tts, tw = FakeStt(transcript), FakeTts(), FakeTwilio(get_plan)
    srv, mgr, chat = manager(script, stt=stt, tts=tts)
    line = telephony.PhoneLine(mgr, ACC, TOKEN, BASE, http=tw.client(), sleep=lambda s: None, **kw)
    mgr.phone = line
    srv.USERS.setdefault("voice-line", {"name": "Voice line", "role": "agent", "title": "Phone line (voice bot)", "type": "service"})
    return srv, mgr, chat, line, stt, tts, tw


def answer(rec_url=REC_URL, dur="3", sid=CALL, **extra):
    return {"CallSid": sid, "RecordingUrl": rec_url, "RecordingSid": REC, "RecordingDuration": dur, "From": CALLER, "To": OUR_NUMBER, **extra}


def incoming(sid=CALL):
    return {"CallSid": sid, "From": CALLER, "To": OUR_NUMBER, "CallStatus": "ringing"}


def has(twiml, *things):
    return all(t in twiml for t in things)


_MOUNTED = []


def mount(srv, mgr, line):
    """Put this test's routers on the real app. The routers are removed again before the next test mounts its own (this FastAPI adds an included router as ONE lazy object,
    so removing by path does not work: an earlier test's line would keep answering)."""
    routes = srv.app.router.routes
    routes[:] = [r for r in routes if not any(r is m for m in _MOUNTED)]
    before = list(routes)
    srv.app.include_router(router.build(srv, mgr))
    srv.app.include_router(telephony.build(srv, mgr, line))
    _MOUNTED[:] = [r for r in routes if not any(r is b for b in before)]


# ------------------------------------------------------------------ signature and set-up
def test_signature_matches_twilios_published_example():
    line = telephony.PhoneLine(object(), ACC, "12345", "https://example.com")
    pairs = [("CallSid", "CA1234567890ABCDE"), ("Caller", "+14158675310"), ("Digits", "1234"), ("From", "+14158675310"), ("To", "+18005551212")]
    sig = line.sign("https://example.com/myapp.php?foo=1&bar=2", pairs)
    assert sig == "L/OH5YylLD5NRKLltdqwSvS0BnU=", sig                     # the vector in Twilio's security docs
    assert line.valid("https://example.com/myapp.php?foo=1&bar=2", list(reversed(pairs)), sig)       # field order on the wire does not matter
    assert not line.valid("https://example.com/myapp.php?foo=1&bar=2", pairs, "") and not line.valid("https://example.com/other", pairs, sig)


def test_phone_line_needs_credentials_and_https_and_never_shows_the_token():
    for args in ((None, TOKEN, BASE), (ACC, "", BASE), (ACC, TOKEN, "http://insecure.example"), (ACC, TOKEN, "")):
        try:
            telephony.PhoneLine(object(), *args)
        except ValueError:
            continue
        raise AssertionError(f"started with {args!r}")
    assert TOKEN not in repr(telephony.PhoneLine(object(), ACC, TOKEN, BASE))


def test_http_refuses_unsigned_wrong_and_tampered_requests():
    from fastapi.testclient import TestClient
    srv, mgr, chat, line, stt, tts, tw = phone()
    mount(srv, mgr, line)
    line.arm("t-1")
    c = TestClient(srv.app)
    path, params = "/voice/phone/incoming", incoming()
    body = urlencode(params)
    hdr = {"content-type": "application/x-www-form-urlencoded"}
    assert c.post(path, content=body, headers=hdr).status_code == 403
    assert c.post(path, content=body, headers={**hdr, "x-twilio-signature": "AAAA"}).status_code == 403
    good = line.sign(BASE + path, list(params.items()))
    assert c.post(path, content=urlencode({**params, "CallSid": CALL2}), headers={**hdr, "x-twilio-signature": good}).status_code == 403        # signed for another body
    assert not mgr.calls and line.armed is not None                          # nothing started by any refused request
    ok = c.post(path, content=body, headers={**hdr, "x-twilio-signature": good})
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("application/xml") and "<Record" in ok.text and len(mgr.calls) == 1


# ------------------------------------------------------------------ the call
def test_unarmed_call_is_turned_away_and_nothing_starts():
    srv, mgr, chat, line, stt, tts, tw = phone()
    n = len(srv.WS["ws"].borrowers)
    out = line.incoming(incoming())
    assert has(out, "not available", "<Hangup/>") and "<Record" not in out
    assert not mgr.calls and len(srv.WS["ws"].borrowers) == n and not stt.heard and not tts.said


def test_armed_call_greets_with_audio_then_records_the_next_answer_and_only_once():
    srv, mgr, chat, line, stt, tts, tw = phone()
    line.arm("t-1")
    assert line.state()["armed"] and line.state()["borrower_id"] == "t-1"
    out = line.incoming(incoming())
    m = re.search(r"<Play>(https://demo\.example\.test/voice/phone/audio/([\w-]+)\.wav)</Play>", out)
    assert m and f'<Record action="{BASE}/voice/phone/turn"' in out, out
    assert line.audio_bytes(m.group(2)) and " ".join(tts.said) == bot.greeting("English") and set(tts.rates) == {8000}       # spoken as parallel sentences, all at phone quality
    assert line.armed is None and len(mgr.calls) == 1
    again = line.incoming(incoming())                                           # Twilio re-sending the webhook for the same CallSid: same call, greeted again
    assert "<Record" in again and len(mgr.calls) == 1 and list(line.sessions) == [CALL]
    busy = line.incoming(incoming(CALL2))
    assert "busy" in busy and "<Hangup/>" in busy and len(mgr.calls) == 1
    assert line.audio_bytes("not-a-token") is None


def test_an_answer_is_fetched_with_credentials_then_deleted_masked_and_answered():
    srv, mgr, chat, line, stt, tts, tw = phone()
    line.arm("t-1")
    line.incoming(incoming())
    out = line.turn(answer())
    gets, dels = tw.calls("GET"), tw.calls("DELETE")
    assert len(gets) == 1 and gets[0][1] == REC_URL and gets[0][2].startswith("Basic ")
    assert base64.b64decode(gets[0][2].split()[1]).decode() == f"{ACC}:{TOKEN}"
    assert len(dels) == 1 and dels[0][1] == f"https://api.twilio.com/2010-04-01/Accounts/{ACC}/Recordings/{REC}.json"        # the raw audio does not stay at Twilio
    assert stt.heard == [tw.audio]
    assert has(out, "<Play>", "<Record") and "<Hangup/>" not in out, out
    blob = chat.everything()
    assert "ABCPK1234L" not in blob and re.search(r"<PAN_\d+>", blob)           # the model saw the masked answer only
    assert "ABCPK1234L" not in "".join(tts.said)


def test_a_recording_from_another_host_is_never_fetched_and_no_credentials_leave():
    srv, mgr, chat, line, stt, tts, tw = phone()
    line.arm("t-1")
    line.incoming(incoming())
    out = line.turn(answer(rec_url="https://evil.example/steal.wav"))
    assert not tw.calls("GET") and not stt.heard
    assert all("evil.example" not in r[1] for r in tw.requests)
    assert "<Record" in out                                                    # the caller is simply asked again


def test_a_recording_that_is_not_ready_yet_is_retried():
    srv, mgr, chat, line, stt, tts, tw = phone(get_plan=(404, 404, 200))
    line.arm("t-1")
    line.incoming(incoming())
    out = line.turn(answer())
    assert len(tw.calls("GET")) == 3 and stt.heard and "<Record" in out


def test_a_failed_download_still_deletes_the_recording_and_asks_again():
    srv, mgr, chat, line, stt, tts, tw = phone(get_plan=(500,))
    line.arm("t-1")
    line.incoming(incoming())
    out = line.turn(answer())
    assert len(tw.calls("DELETE")) == 1 and not stt.heard and "<Record" in out and "Traceback" not in out


def test_a_speech_to_text_failure_asks_again_and_does_not_crash():
    srv, mgr, chat, line, stt, tts, tw = phone()
    def boom(*a, **k):
        from layer9_voice.sarvam import SarvamError
        raise SarvamError("http_503")
    stt.transcribe = boom
    line.arm("t-1")
    line.incoming(incoming())
    out = line.turn(answer())
    assert "<Record" in out and not chat.seen and len(tw.calls("DELETE")) == 1


def test_goodbye_hangs_up_and_the_disposition_is_produced():
    srv, mgr, chat, line, stt, tts, tw = phone(transcript="I will pay Rs 4,200 by the 7th")
    line.arm("t-1")
    line.incoming(incoming())
    cid = line.sessions[CALL]
    out = line.turn(answer())
    assert "<Hangup/>" in out and "<Record" not in out and "<Play>" in out
    line.join()
    call = mgr.get(cid)
    assert call.status == "ended" and call.disposition == GOOD and call.raw_turns == [], (call.status, call.disposition_error)


def test_caller_hanging_up_gives_one_disposition_even_if_the_status_callback_also_arrives():
    srv, mgr, chat, line, stt, tts, tw = phone()
    line.arm("t-1")
    line.incoming(incoming())
    cid = line.sessions[CALL]
    line.turn(answer())                                                         # one answer so there is something to judge
    assert line.turn({"CallSid": CALL, "Digits": "hangup", "From": CALLER}) == "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response></Response>"
    line.status({"CallSid": CALL, "CallStatus": "completed", "From": CALLER})
    line.join()
    assert mgr.get(cid).status == "ended"
    ends = [r for r in srv.WS["ws"].audit.records() if r.get("event") == "VOICE_CALL_ENDED" and r.get("call_id") == cid]
    assert len(ends) == 1


def test_a_status_callback_for_a_call_in_progress_ends_it():
    srv, mgr, chat, line, stt, tts, tw = phone()
    line.arm("t-1")
    line.incoming(incoming())
    cid = line.sessions[CALL]
    line.status({"CallSid": CALL, "CallStatus": "ringing"})                      # not terminal: ignored
    line.join()
    assert mgr.get(cid).status == "active"
    line.status({"CallSid": CALL, "CallStatus": "no-answer"})
    line.join()
    assert mgr.get(cid).status == "ended"
    line.status({"CallSid": "CA" + "9" * 32, "CallStatus": "completed"})         # an unknown call is ignored


def test_the_turn_cap_hangs_up():
    srv, mgr, chat, line, stt, tts, tw = phone(max_turns=1)
    line.arm("t-1")
    line.incoming(incoming())
    out = line.turn(answer())
    assert "<Hangup/>" in out
    line.join()


def test_three_empty_answers_in_a_row_hang_up():
    srv, mgr, chat, line, stt, tts, tw = phone()
    line.arm("t-1")
    line.incoming(incoming())
    a = line.turn(answer(dur="0"))
    b = line.turn(answer(dur="0"))
    c = line.turn(answer(dur="0"))
    assert "<Record" in a and "<Record" in b and "<Hangup/>" in c and not stt.heard
    line.join()


def test_the_callers_number_is_never_read_stored_or_logged():
    srv, mgr, chat, line, stt, tts, tw = phone(transcript="I will pay Rs 4,200 by the 7th")
    line.arm("t-1")
    line.incoming(incoming())
    cid = line.sessions[CALL]
    line.turn(answer())
    line.join()
    everything = json.dumps({"audit": srv.WS["ws"].audit.records(), "state": mgr.state(cid), "line": line.state(), "sessions": line.sessions, "http": tw.requests,
                             "model": chat.everything(), "said": tts.said, "case": srv.WS["ws"].borrowers[mgr.get(cid).sid].history}, default=str, ensure_ascii=False)
    for needle in (CALLER, CALLER.lstrip("+"), "9876512345", OUR_NUMBER, "4155550100"):
        assert needle not in everything, f"the phone number leaked: {needle}"


def test_phone_events_are_audited_as_the_phone_line_not_a_console_user():
    from fastapi.testclient import TestClient
    srv, mgr, chat, line, stt, tts, tw = phone()
    mount(srv, mgr, line)
    line.arm("t-1")
    c = TestClient(srv.app)
    params = incoming()
    r = c.post("/voice/phone/incoming", content=urlencode(params), headers={"content-type": "application/x-www-form-urlencoded", "x-twilio-signature": line.sign(BASE + "/voice/phone/incoming", list(params.items()))})
    assert r.status_code == 200
    cid = line.sessions[CALL]
    recs = [x for x in srv.WS["ws"].audit.records() if x.get("call_id") == cid and x.get("event") == "VOICE_CALL_STARTED"]
    assert recs and recs[0]["actor"]["id"] == "voice-line" and recs[0]["actor"]["role"] == "agent", recs


def test_console_arm_endpoints_need_the_agent_role_and_a_known_borrower():
    from fastapi.testclient import TestClient
    srv, mgr, chat, line, stt, tts, tw = phone()
    mount(srv, mgr, line)
    c = TestClient(srv.app)
    assert c.post("/voice/phone/arm", json={"borrower_id": "t-1"}, headers={"x-user": "vikram"}).status_code == 403
    assert c.post("/voice/phone/arm", json={"borrower_id": "nobody"}).status_code == 404
    s = c.post("/voice/phone/arm", json={"borrower_id": "t-1"}).json()
    assert s["armed"] and s["borrower_id"] == "t-1" and s["number_setup"] == BASE + "/voice/phone/incoming"
    assert c.get("/voice/phone/state").json()["armed"]
    assert not c.delete("/voice/phone/arm").json()["armed"]
    assert c.get("/voice/status").json()["phone_line"] is True
    assert c.get("/voice/phone/audio/nope.wav").status_code == 404


def test_arming_expires():
    clock = [1000.0]
    srv, mgr, chat, line, stt, tts, tw = phone()
    line._clock = lambda: clock[0]
    line.arm("t-1", ttl=60)
    clock[0] += 61
    assert not line.state()["armed"]
    assert "not available" in line.incoming(incoming()) and not mgr.calls


def test_there_is_no_way_to_place_a_call():
    """The user's rule: no call to the team's real numbers. Scan every non-test source file of the package for the ways a call could be created
    (Twilio's REST 'Calls' resource, the helper library's calls.create, TwiML <Dial>, a sip: / tel: target, or an 'outbound' feature)."""
    import glob
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    files = [f for f in glob.glob(os.path.join(here, "*.py")) if not os.path.basename(f).startswith("test_")]
    assert len(files) >= 8
    for f in files:
        src = open(f, encoding="utf-8").read()
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        for pattern in (r"/Calls\b", r"calls\.create", r"<Dial", r"\bsip:", r"\btel:", r"make_call", r"\bplace_call", r"\bdial_out", r"Accounts/[^\"']*/Calls"):
            assert not re.search(pattern, code), f"{os.path.basename(f)} contains {pattern!r}"
    # the only Twilio REST path the package uses is deleting a recording
    src = open(os.path.join(here, "telephony.py"), encoding="utf-8").read()
    assert re.findall(r"/2010-04-01/[^\s\"']*", src) == ["/2010-04-01/Accounts/{self.sid}/Recordings/{rec_sid}.json"]


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
