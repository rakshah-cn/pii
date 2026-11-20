"""Sarvam AI client: speech-to-text (Saaras), the chat model (sarvam-105b) and text-to-speech (Bulbul).

Request shapes come from Sarvam's public docs (read 20 Nov 2026). NOT yet exercised against the real service: no SARVAM_API_KEY was available when this was written.
Run `python -m layer9_voice.probe` once the key is in .env; it checks every guess marked UNVERIFIED below.

Privacy rules this file enforces:
  * the key is read from the environment, is never printed, never put in an error and never returned
  * an error carries a status code and Sarvam's short error code, never the response body (a body can echo the request text)
  * the chat call is only ever handed text that already went through the gateway (layer 3). Speech-to-text hears RAW speech: that vendor sits OUTSIDE our masking boundary.
"""
import base64
import io
import os
import re
import wave

import httpx

from layer9_voice import config

BASE_URL = "https://api.sarvam.ai"
MAX_STT_SECONDS = 30.0           # the synchronous speech-to-text endpoint refuses longer audio (docs)
MAX_TTS_CHARS = 2500             # bulbul v3 limit per request (docs)

def lang_code(name_or_code):
    """'Hindi' / 'hindi' / 'hi-IN' -> 'hi-IN' using the configured languages (Settings); a code that looks like a code is kept as it is; anything unknown -> English."""
    s = str(name_or_code or "").strip()
    if re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}", s):
        return s
    return config.language(s)[1]["code"]


class SarvamError(Exception):
    """str(e) is safe to log: a short reason, an HTTP status and Sarvam's own error code only."""


def wav_seconds(audio: bytes):
    """Length of a WAV file in seconds, or None when it is not a WAV we can read (other formats are left to Sarvam to judge)."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError):
        return None


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def clean_reply(text):
    """Defensive: the docs say reasoning arrives in its own field, but if a <think> block ever lands in `content`, it must not be spoken or logged."""
    if text is None:
        raise SarvamError("empty_content")
    out = _THINK.sub("", text).strip()
    if re.search(r"<think>", out, re.I):
        raise SarvamError("unterminated_think_block")
    if not out:
        raise SarvamError("empty_content")
    return out


class SarvamClient:
    def __init__(self, api_key=None, base_url=None, http=None, timeout=25.0):
        self._key = api_key or os.environ.get("SARVAM_API_KEY") or ""
        self.base = (base_url or os.environ.get("SARVAM_BASE_URL") or BASE_URL).rstrip("/")
        self._http = http or httpx.Client(timeout=timeout)

    def __repr__(self):
        return f"SarvamClient(base={self.base!r}, key={'set' if self._key else 'MISSING'})"

    @property
    def configured(self):
        return bool(self._key)

    def _post(self, path, **kw):
        if not self._key:
            raise SarvamError("sarvam_api_key_missing")
        try:
            r = self._http.post(self.base + path, headers={"api-subscription-key": self._key}, **kw)
        except httpx.TimeoutException:
            raise SarvamError("timeout") from None
        except httpx.HTTPError as e:
            raise SarvamError(f"network:{type(e).__name__}") from None
        if r.status_code != 200:
            code = ""
            try:
                err = r.json().get("error")
                code = str(err.get("code", "") if isinstance(err, dict) else err)[:40]
            except Exception:                                              # noqa: BLE001  (an unreadable error body is simply not reported)
                pass
            raise SarvamError(f"http_{r.status_code}" + (f":{re.sub(r'[^A-Za-z0-9_.-]', '', code)}" if code else ""))
        try:
            return r.json()
        except ValueError:
            raise SarvamError("response_not_json") from None

    # ---- chat ------------------------------------------------------------------------------------------------
    def chat(self, messages, model="sarvam-105b", max_tokens=600, temperature=0.4, reasoning_effort=None):
        """messages = [{"role": "system"|"user"|"assistant", "content": str}, ...] -> the answer text (reasoning, if any, is dropped).
        reasoning_effort=None leaves the field out, so the model default applies ('low' on sarvam-105b per the docs).
        reasoning_effort='off' sends null (reasoning off). Left out, sarvam-105b reasons and can spend its whole token budget thinking."""
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if reasoning_effort == "off":
            body["reasoning_effort"] = None                     # MEASURED 20 Nov: JSON null switches reasoning off (163 ms); the string "None" is rejected (400)
        elif reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        # MEASURED 20 Nov: on /v2 reasoning_effort=null does NOT switch reasoning off for a longer prompt (sarvam-105b still spends ~1,000 tokens thinking and can return nothing);
        # on /v1 the same null gives no reasoning (0.4 s). So sarvam-105b* use /v1; other models (deepseekv4.1-flash-1017) are v2-only in Sarvam's examples.
        path = "/v1/chat/completions" if model.startswith("sarvam-105b") or os.environ.get("SARVAM_CHAT_API") == "v1" else "/v2/chat/completions"
        data = self._post(path, json=body)
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise SarvamError("unexpected_chat_response") from None
        if not content and choice.get("finish_reason") == "length":
            raise SarvamError("ran_out_of_tokens_while_reasoning")          # the docs warn about exactly this: raise max_tokens or lower reasoning_effort
        return clean_reply(content)

    # ---- speech to text --------------------------------------------------------------------------------------
    def transcribe(self, audio: bytes, language=None, filename="audio.wav", content_type="audio/wav", mode=None, model=None, sample_rate=None):
        """Raw speech in -> {"transcript", "language_code", "language_probability"}. language=None lets Sarvam detect it. 30 s max per request.
        mode: 'transcribe' (default; what was said, in its own language - the right input for masking), 'verbatim', 'codemix', 'translit', or 'translate' (to English: NOT used
        for masking, a translation can change names and numbers before the detector sees them). Set in Settings (models.stt_mode)."""
        cm = config.get()["models"]
        mode, model = mode or cm["stt_mode"], model or cm["stt_model"]
        if not audio:
            raise SarvamError("empty_audio")
        secs = wav_seconds(audio)
        if secs is not None and secs > MAX_STT_SECONDS:
            raise SarvamError("audio_longer_than_30s")
        data = {"model": model, "mode": mode}
        if language:
            data["language_code"] = lang_code(language)
        if sample_rate:
            data["sample_rate"] = str(sample_rate)             # Sarvam's own example sends it (16000); phone audio is 8000
        j = self._post("/speech-to-text", files={"file": (filename, audio, content_type)}, data=data)
        text = j.get("transcript")
        if not isinstance(text, str):
            raise SarvamError("unexpected_stt_response")
        return {"transcript": text.strip(), "language_code": j.get("language_code"), "language_probability": j.get("language_probability")}

    # ---- text to speech --------------------------------------------------------------------------------------
    def synthesize(self, text, language="English", speaker=None, model=None, pace=None, sample_rate=None):
        """Text -> WAV bytes. The text is what the bot SAYS and is sent to Sarvam as-is: callers must pass the placeholder-free version (bot.speakable).
        UNVERIFIED: the exact model string for Bulbul v3 and which speakers speak Marathi (the docs example uses 'bulbul:v4-flash' / 'shubh_enhi_ads')."""
        text = (text or "").strip()
        if not text:
            raise SarvamError("empty_text")
        if len(text) > MAX_TTS_CHARS:
            raise SarvamError("text_too_long")
        cm = config.get()["models"]
        body = {"text": text, "language_code": lang_code(language), "model": model or cm["tts_model"], "speaker": speaker or cm["tts_speaker"]}
        if pace is not None:
            body["pace"] = pace
        if sample_rate is not None:
            body["speech_sample_rate"] = sample_rate
        j = self._post("/text-to-speech", json=body)
        audios = j.get("audios")
        if not isinstance(audios, list) or not audios or not isinstance(audios[0], str):
            raise SarvamError("unexpected_tts_response")
        try:
            return base64.b64decode(audios[0], validate=True)
        except ValueError:
            raise SarvamError("tts_audio_not_base64") from None
