"""ONE place for everything about the voice / chat bot that someone may want to change without touching code.

  defaults  (this file)  <  overrides  layer9_voice/config.local.json  (git-ignored; written by the console's Settings tab or by hand)

What is configurable: models and speech settings, the languages (their codes and the fixed lines the bot says), the bot's job and extra rules, limits.
What is deliberately NOT configurable (shown read-only in Settings): the privacy rules in the bot's prompt, the disposition categories and their strict parser, the masking pipeline,
and every secret (API keys stay in .env and are never read into, written to or returned from this config).
Every value is validated before it is saved; a bad file never takes the bot down: it is reported and the defaults are used."""
import copy
import json
import os
import re
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_PATH = os.environ.get("VOICE_CONFIG_PATH") or os.path.join(HERE, "config.local.json")

SARVAM_CODES = ("en-IN", "hi-IN", "bn-IN", "ta-IN", "te-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "pa-IN", "od-IN")      # the languages Sarvam's speech models list (docs)
REASONING = (None, "off", "low", "medium", "high")

DEFAULTS = {
    "models": {
        "bot": "sarvam-105b-conversations",          # the conversational model (v1 endpoint)
        "bot_reasoning": None,                        # None = the model's own default; "off" switches reasoning off
        "disposition": "sarvam-105b",                 # the model that reads the finished conversation
        "disposition_reasoning": "off",               # measured: "off" (JSON null) works on the v1 endpoint, v2 ignores it
        "stt_model": "saaras:v4",
        "stt_mode": "transcribe",                     # transcribe keeps the words as spoken (the right input for masking); translate is NOT recommended
        "tts_model": "bulbul:v3",
        "tts_speaker": "shubh",
    },
    "languages": {
        "English": {"code": "en-IN", "neutral": "that",
                    "greeting": "Hello, this is a reminder call about your overdue loan instalment. Is this a good time to talk?",
                    "chat_greeting": "Hello, this is a reminder message about your overdue loan instalment. Is now a good time to chat?",
                    "repeat": "Sorry, I did not catch that. Could you say it again?",
                    "goodbye": "Thank you for your time. Goodbye."},
        "Hindi": {"code": "hi-IN", "neutral": "वह",
                  "greeting": "नमस्ते, मैं आपके लोन की बकाया किस्त के बारे में फ़ोन कर रही हूँ। क्या अभी बात करने का सही समय है?",
                  "chat_greeting": "नमस्ते, यह आपके लोन की बकाया किस्त के बारे में एक रिमाइंडर संदेश है। क्या अभी बात कर सकते हैं?",
                  "repeat": "माफ़ कीजिए, मैं समझ नहीं पाई। क्या आप दोबारा बता सकते हैं?",
                  "goodbye": "आपके समय के लिए धन्यवाद। अलविदा।"},
        "Marathi": {"code": "mr-IN", "neutral": "ते",
                    "greeting": "नमस्कार, मी तुमच्या कर्जाच्या थकीत हप्त्याबद्दल फोन करत आहे. आत्ता बोलायला योग्य वेळ आहे का?",
                    "chat_greeting": "नमस्कार, हा तुमच्या कर्जाच्या थकीत हप्त्याबद्दलचा स्मरणसंदेश आहे. आत्ता बोलू शकता का?",
                    "repeat": "माफ करा, मला समजले नाही. तुम्ही पुन्हा सांगाल का?",
                    "goodbye": "तुमच्या वेळेबद्दल धन्यवाद. निरोप."},
    },
    "bot": {
        "job": "Your ONLY job: find out whether the customer intends to pay, how much and by when. If they dispute the amount, say you have the wrong person, or ask for a call back, note it and agree.",
        "extra_rules": "",
    },
    "limits": {
        "history_messages": 12,          # how many earlier (masked) messages the model gets back each turn
        "tts_hedge_seconds": 2.5,        # a slow speech call is raced by a second identical one after this long
        "max_phone_turns": 12,           # a phone call hangs up after this many answers
        "max_silent_answers": 3,         # ...or after this many empty answers in a row
        "max_audio_seconds": 25,         # the browser microphone stops by itself after this long (Sarvam accepts 30)
    },
}
LINE_KEYS = ("greeting", "chat_greeting", "repeat", "goodbye", "neutral")
_LOCK = threading.RLock()
_CACHE = {"mtime": None, "path": None, "cfg": None, "errors": [], "source": "defaults"}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k == "languages" and isinstance(v, dict):
            out["languages"] = copy.deepcopy(v)                      # the languages table is replaced as a whole: removing one is a real edit
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def validate(cfg):
    """-> list of human-readable problems (empty = valid)."""
    e = []
    if not isinstance(cfg, dict):
        return ["config must be an object"]
    m = cfg.get("models", {})
    for k in ("bot", "disposition", "stt_model", "tts_model", "tts_speaker"):
        if not isinstance(m.get(k), str) or not re.fullmatch(r"[A-Za-z0-9._:\-]{1,64}", m.get(k) or ""):
            e.append(f"models.{k}: letters, digits and . _ : - only (1-64 characters)")
    for k in ("bot_reasoning", "disposition_reasoning"):
        if m.get(k) not in REASONING:
            e.append(f"models.{k}: one of null, off, low, medium, high")
    if m.get("stt_mode") not in ("transcribe", "verbatim", "codemix", "translit", "translate"):
        e.append("models.stt_mode: transcribe, verbatim, codemix, translit or translate")
    langs = cfg.get("languages")
    if not isinstance(langs, dict) or not langs:
        e.append("languages: at least one language is required")
    else:
        if "English" not in langs:
            e.append("languages: English must stay (it is the fallback)")
        codes = []
        for name, L in langs.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z ]{0,29}", str(name)):
                e.append(f"languages.{name}: the name must be 1-30 letters")
            if not isinstance(L, dict):
                e.append(f"languages.{name}: must be an object")
                continue
            if L.get("code") not in SARVAM_CODES:
                e.append(f"languages.{name}.code: must be one of {', '.join(SARVAM_CODES)}")
            codes.append(L.get("code"))
            for k in LINE_KEYS:
                v = L.get(k)
                if not isinstance(v, str) or not v.strip() or len(v) > (300 if k != "neutral" else 20):
                    e.append(f"languages.{name}.{k}: required text, at most {300 if k != 'neutral' else 20} characters")
            for k in ("greeting", "chat_greeting", "repeat", "goodbye"):
                if "<" in str(L.get(k, "")) and re.search(r"<[A-Z_]+_\d+>", str(L.get(k, ""))):
                    e.append(f"languages.{name}.{k}: must not contain a <TOKEN> placeholder")
        if len(set(codes)) != len(codes):
            e.append("languages: two languages share the same code")
    b = cfg.get("bot", {})
    if not isinstance(b.get("job"), str) or not 20 <= len(b["job"].strip()) <= 1500:
        e.append("bot.job: 20-1500 characters")
    if not isinstance(b.get("extra_rules", ""), str) or len(b.get("extra_rules", "")) > 1500:
        e.append("bot.extra_rules: at most 1500 characters")
    if re.search(r"<[A-Z_]+_\d+>", str(b.get("job", "")) + str(b.get("extra_rules", ""))):
        e.append("bot: the prompt must not contain a <TOKEN> placeholder")
    li = cfg.get("limits", {})
    for k, (lo, hi, kind) in {"history_messages": (2, 40, int), "tts_hedge_seconds": (0.5, 10, float), "max_phone_turns": (1, 30, int), "max_silent_answers": (1, 10, int), "max_audio_seconds": (3, 30, int)}.items():
        v = li.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi or (kind is int and int(v) != v):
            e.append(f"limits.{k}: a {'whole ' if kind is int else ''}number from {lo} to {hi}")
    return e


def _read_local(path):
    if not os.path.exists(path):
        return None, []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, ["config.local.json is not an object: ignored"]
        return data, []
    except (OSError, ValueError) as ex:
        return None, [f"config.local.json could not be read ({type(ex).__name__}): defaults are used"]


def load(force=False):
    """The effective config (defaults + overrides), re-read when the file changes. A bad file is reported in errors() and the defaults are used."""
    with _LOCK:
        path = LOCAL_PATH
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if not force and _CACHE["cfg"] is not None and _CACHE["mtime"] == mtime and _CACHE["path"] == path:
            return _CACHE["cfg"]
        over, problems = _read_local(path)
        cfg, source = copy.deepcopy(DEFAULTS), "defaults"
        if over is not None:
            merged = _merge(DEFAULTS, over)
            bad = validate(merged)
            if bad:
                problems += ["config.local.json is invalid, defaults are used: " + "; ".join(bad[:4])]
            else:
                cfg, source = merged, "local"
        _CACHE.update(mtime=mtime, path=path, cfg=cfg, errors=problems, source=source)
        return cfg


def get():
    return load()


def errors():
    load()
    return list(_CACHE["errors"])


def source():
    load()
    return _CACHE["source"]


def save(new_cfg):
    """Validate, then write the whole effective config to config.local.json (atomic). Returns (ok, problems, effective)."""
    merged = _merge(DEFAULTS, new_cfg if isinstance(new_cfg, dict) else {})
    bad = validate(merged)
    if bad:
        return False, bad, load()
    with _LOCK:
        d = os.path.dirname(LOCAL_PATH) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".config.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            os.replace(tmp, LOCAL_PATH)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False, ["the config file could not be written"], load()
        return True, [], load(force=True)


def reset():
    with _LOCK:
        try:
            os.unlink(LOCAL_PATH)
        except FileNotFoundError:
            pass
        return load(force=True)


def changed_keys(old, new, prefix=""):
    """Dotted paths whose value differs (names only: used for the audit record, never the values)."""
    out = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if isinstance(a, dict) and isinstance(b, dict):
            out += changed_keys(a, b, prefix + k + ".")
        elif a != b:
            out.append(prefix + k)
    return out


# ---------------------------------------------------------------- lookups the bot uses
def language(name_or_code):
    """-> (name, entry). Accepts 'Hindi', 'hindi' or 'hi-IN'; anything unknown falls back to English."""
    langs = get()["languages"]
    s = str(name_or_code or "").strip()
    for n, L in langs.items():
        if s.casefold() == n.casefold() or s == L["code"]:
            return n, L
    return "English", langs.get("English") or DEFAULTS["languages"]["English"]


def line(language_name, kind):
    return language(language_name)[1][kind]


def fixed_lines():
    """Every fixed line the bot can say (their speech is cached; they contain nothing about the customer)."""
    return frozenset(L[k] for L in get()["languages"].values() for k in ("greeting", "chat_greeting", "repeat", "goodbye"))


def limits():
    return get()["limits"]
