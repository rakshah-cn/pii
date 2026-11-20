"""The voice bot's brain: what it is told, how its words are made safe to speak, and the LLM function the gateway calls.

The gateway (layer 3) hands its LLM ONE masked message per turn and nothing else, so a bot that must hold a conversation needs its own memory. CallLLM keeps it, and it only ever
holds MASKED text: each customer message exactly as the gateway sent it (already tokenised and audited) and the model's own replies. Raw speech never enters it."""
import contextlib
import os
import re

from layer3_gateway.graph import TOKEN
from layer9_voice import config, disposition
from layer9_voice.sarvam import SarvamClient, lang_code

# Everything below that the bot SAYS or is TOLD comes from layer9_voice/config.py (defaults + config.local.json, editable in the console's Settings tab):
# the greeting / chat greeting / "say again" / goodbye lines per language, the placeholder-neutral word, the bot's job, extra rules, the models and the limits.
# NOT checked by a native speaker (hi / mr): read those lines before a live demo.
_BYE = re.compile(r"\b(good\s?bye|bye|have a (good|nice|great|wonderful) day|take care)\b|अलविदा|निरोप|दिन शुभ|दिन अच्छा|दिवस चांगला|दिवस शुभ", re.I)   # the prompt asks for ONE closing word; the rest are fall-backs for how a model may close


def is_goodbye(text):
    """True when the bot's line ends the call (so a phone line hangs up / a screen generates the disposition instead of waiting for an answer)."""
    t = text or ""
    return bool(_BYE.search(t)) or any(L["goodbye"] in t for L in config.get()["languages"].values())


def fixed_lines():
    return config.fixed_lines()                 # the lines whose speech is cached (they contain nothing about the customer)


def greeting(language, channel="voice"):
    return config.line(language, "chat_greeting" if channel == "chat" else "greeting")


def repeat_line(language):
    return config.line(language, "repeat")


def goodbye_line(language):
    return config.line(language, "goodbye")


def closing_word(language):
    """The last sentence of a language's goodbye line ('Goodbye.'): the single word the bot is asked to end with."""
    parts = [x.strip() for x in re.split(r"(?<=[.!?\u0964])\s+", goodbye_line(language).strip()) if x.strip()]
    return parts[-1] if parts else goodbye_line(language)


# The privacy and conduct rules are NOT configurable: Settings shows them read-only, and they are always in the prompt whatever the config says.
FIXED_RULES = [
    "Words like <PHONE_1> or <PERSON_1> stand for private values that were hidden on purpose. Never repeat or write one, never ask for a phone number, card number, Aadhaar, PAN, "
    "email or address, and never use the customer's name: say 'you'. If the customer says such a value anyway, do not mention it, comment on it or refuse it: just carry on with the payment question.",
    "Never threaten, never mention legal action or family members, never be rude.",
    "When the customer commits to an amount or a date, repeat it once to confirm and thank them.",
]


def style_rule(channel):
    return ("This is a text chat: answer in one or two short sentences, plain words, no lists, no emojis." if channel == "chat" else
            "You are being spoken aloud: answer in ONE short sentence of at most 15 words (two at most), plain words, no lists or symbols. Speech takes longer the longer you talk.")


def bot_system(profile, channel="voice"):
    """The bot's instructions. Built from the case profile and the config; it never contains the customer's name, number or loan reference."""
    p = profile or {}
    cfg = config.get()
    names = list(cfg["languages"])
    facts = []
    if p.get("product"):
        facts.append(f"the loan is a {p['product'].lower()}")
    if p.get("outstanding"):
        facts.append(f"about Rs {int(p['outstanding']):,} is overdue")
    if p.get("dpd"):
        facts.append("it is already late")                              # no bare day-count: the gateway's reply filter blanks a bare number the model repeats ('[redacted] days late', measured)
    words = "; ".join(f"'{closing_word(n)}' in {n}" for n in names)
    lang_rule = (f"Reply in the language the customer writes or speaks in ({', '.join(names[:-1]) + ' or ' + names[-1] if len(names) > 1 else names[0]})."
                 + (f" The customer's preferred language is {p['language']}: use it if unsure, and write the whole reply in one language." if p.get("language") else ""))
    rules = [style_rule(channel), lang_rule] + FIXED_RULES + [
        "When the conversation is complete (the customer committed to pay, refused, disputed, asked for a call back, or said goodbye or asked you to stop), END your last message with exactly ONE closing word "
        f"in the language you are writing: {words}. Never write more than one of them and never say it before the conversation is complete."]
    extra = cfg["bot"].get("extra_rules", "").strip()
    return (("You are a polite collections assistant chatting by text with a customer" if channel == "chat" else "You are a polite phone collections assistant calling a customer")
            + " about an overdue loan instalment" + (f" ({', '.join(facts)})" if facts else "") + ".\n"
            + cfg["bot"]["job"].strip() + "\n"
            "Stay on that one topic. If the customer asks about anything else, say politely that you can only help with this payment and ask when they can pay.\n"
            "Rules:\n" + "\n".join("- " + r for r in rules) + ("\n" + "Extra rules from the operator:\n" + extra if extra else ""))


def speakable(reply_masked, language="English"):
    """The bot's reply made safe for the text-to-speech vendor: no placeholder is read out, no markdown. Never rehydrates (the speech vendor must not receive real values)."""
    s = TOKEN.sub(config.line(language, "neutral"), reply_masked or "")
    s = re.sub(r"[*_#`>|~]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or repeat_line(language)


# ---------------------------------------------------------------- which model answers
def sarvam_chat(client=None, bot_model=None, disposition_model=None):
    c = client or SarvamClient(timeout=15.0)

    def models():
        return config.get()["models"]

    def chat(messages, kind):                    # read per call, so a change saved in Settings takes effect on the next message
        m = models()
        if kind == "disposition":
            return c.chat(messages, model=disposition_model or m["disposition"], max_tokens=1200, temperature=0.1, reasoning_effort=m["disposition_reasoning"])
        return c.chat(messages, model=bot_model or m["bot"], max_tokens=500, temperature=0.4, reasoning_effort=m["bot_reasoning"])
    chat.provider = f"sarvam:{bot_model or models()['bot']}"
    return chat


def bedrock_chat(model_id="apac.amazon.nova-pro-v1:0", region=None, client=None):
    """Backup model (Amazon Nova Pro) with the same masked-only rule. Multi-turn use is NOT yet exercised against the real service."""
    def chat(messages, kind):
        c = client
        if c is None:
            import boto3
            from common import llm as _l
            c = boto3.client("bedrock-runtime", region_name=region or _l.REGION)
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages if m["role"] != "system"]
        r = c.converse(modelId=model_id, system=[{"text": system}], messages=convo, inferenceConfig={"maxTokens": 900 if kind == "disposition" else 300, "temperature": 0.2})
        return r["output"]["message"]["content"][0]["text"]
    chat.provider = f"bedrock:{model_id}"
    return chat


def stub_chat():
    """Offline smoke test only: a fixed line for the bot, and NO disposition (a made-up outcome would be dishonest)."""
    def chat(messages, kind):
        if kind == "disposition":
            raise disposition.DispositionError("stub_model_cannot_decide")
        return "Thank you. When can you make the payment?"
    chat.provider = "stub"
    return chat


def make_chat(provider=None):
    p = (provider or os.environ.get("VOICE_LLM") or ("sarvam" if os.environ.get("SARVAM_API_KEY") else "stub")).lower()
    if p == "sarvam":
        return sarvam_chat()
    if p == "bedrock":
        return bedrock_chat()
    if p == "stub":
        return stub_chat()
    raise ValueError(f"unknown VOICE_LLM {p!r} (sarvam | bedrock | stub)")


# ---------------------------------------------------------------- the function the gateway calls
ACTIVE = {"call": None, "mode": None}          # set by calls.py while it holds the server lock, so exactly one call is active at a time


@contextlib.contextmanager
def active(call, mode="bot"):
    prev = (ACTIVE["call"], ACTIVE["mode"])
    ACTIVE["call"], ACTIVE["mode"] = call, mode
    try:
        yield
    finally:
        ACTIVE["call"], ACTIVE["mode"] = prev


class CallLLM:
    """llm(masked_text) -> reply. Outside a call it defers to `fallback`, so the rest of the console keeps working exactly as before."""
    def __init__(self, chat, fallback):
        self.chat, self.fallback = chat, fallback

    def __call__(self, masked_text):
        call, mode = ACTIVE["call"], ACTIVE["mode"]
        if call is None:
            return self.fallback(masked_text)
        try:
            if mode == "disposition":
                return self.chat([{"role": "system", "content": disposition.SYSTEM}, {"role": "user", "content": masked_text}], "disposition")
            msgs = [{"role": "system", "content": call.system}] + call.llm_history[-config.limits()['history_messages']:] + [{"role": "user", "content": masked_text}]
            reply = self.chat(msgs, "bot")
        except Exception as e:                                                  # noqa: BLE001
            # the gateway reports only 'llm_error:<type>': keep the SAFE reason (a SarvamError is a status/code, never a body or key) so the call can show why
            call.last_llm_error = (str(e) if type(e).__name__ == "SarvamError" else type(e).__name__)[:80]
            raise
        call.llm_history += [{"role": "user", "content": masked_text}, {"role": "assistant", "content": reply}]
        del call.llm_history[:-config.limits()['history_messages']]
        return reply
