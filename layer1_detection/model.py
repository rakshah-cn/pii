"""Chitragupta wrapper (names, addresses, other types). Reused, not retrained.
Results are cached on disk (synthetic data only!) so reruns are fast."""
import hashlib
import json
import os
import re
import sys

CACHE = "data/cache_chitragupta.json"
_ORG = re.compile(r"\b(capital|finance|finserv|financial|bank|ltd|limited|pvt|housing|credit)\b", re.I)


class OrtShim:
    """Looks like the HF model to Chitragupta.detect_batch but runs an ONNX Runtime session (int8: ~10 ms/msg)."""

    def __init__(self, onnx_path, config, threads=8):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(onnx_path, so, providers=["CPUExecutionProvider"])
        self.config = config

    def __call__(self, input_ids, attention_mask):
        import types
        import torch
        lg = self.sess.run(["logits"], {"input_ids": input_ids.numpy().astype("int64"),
                                        "attention_mask": attention_mask.numpy().astype("int64")})[0]
        return types.SimpleNamespace(logits=torch.from_numpy(lg))


class ModelDetector:
    def __init__(self, path="models/chitragupta", cache=True, backend="torch"):
        # backend "onnx_int8": needs models/chitragupta_onnx/model_int8.onnx (see compare/latency_bench.py export).
        # The disk cache is only used for the default torch backend (so int8 results never mix into it).
        self.path, self.m, self.backend = path, None, backend
        self.cache = {}
        self.use_cache = cache and backend == "torch"
        if self.use_cache and os.path.exists(CACHE):
            self.cache = json.load(open(CACHE, encoding="utf-8"))

    def _load(self):
        if self.m is None:
            sys.path.insert(0, self.path)
            from chitragupta import Chitragupta
            self.m = Chitragupta(self.path, device="cpu")
            if self.backend == "onnx_int8":
                self.m.model = OrtShim(os.path.join(os.path.dirname(self.path.rstrip("/")), "chitragupta_onnx",
                                                    "model_int8.onnx"), self.m.model.config)

    @staticmethod
    def _key(t):
        return hashlib.md5(t.encode()).hexdigest()

    def detect_texts(self, texts, bs=16, contexts=None):
        """Spans per text, offsets relative to that text.
        contexts[i] (optional): the text of the turn BEFORE texts[i]. The model then reads  context + '\\n' + text  and only the part of each span that lies inside the text is returned
        (a span inside the context, or the context half of a span that crosses the boundary, is dropped). A short reply such as 'Pooja.' has no context on its own; the question before it
        tells the model it is a name. The caller passes the RAW previous turn (it is only ever read by the local model, never stored or logged here)."""
        if contexts is None:
            contexts = [""] * len(texts)
        elif len(contexts) != len(texts):
            raise ValueError("contexts must have one entry per text")
        inputs = [(c + "\n" + t, len(c) + 1) if c else (t, 0) for t, c in zip(texts, contexts)]
        keys = [self._key(x) for x, _ in inputs]
        have = {}                                                        # a LOCAL view of the cache: the gateway may trim the shared dict from another thread between the check and the read
        for k in keys:
            v = self.cache.get(k)
            if v is not None:
                have[k] = v
        todo = list(dict.fromkeys((x, k) for (x, _), k in zip(inputs, keys) if k not in have))
        if todo:
            self._load()
            for i in range(0, len(todo), bs):
                chunk = todo[i:i + bs]
                for (t, k), spans in zip(chunk, self.m.detect_batch([t for t, _ in chunk])):
                    have[k] = self.cache[k] = [dict(label=s["label"], start=s["start"], end=s["end"]) for s in spans]
            if self.use_cache:
                json.dump(self.cache, open(CACHE, "w", encoding="utf-8"))
        return [have[k] if not shift else _inside_text(have[k], shift) for (_, shift), k in zip(inputs, keys)]


CONTEXT_CHARS = 120      # how much of the previous turn the model reads (its tail: the question is at the end). Model time grows with the input: measured p50 12 ms with no context, 25 ms with 100 chars, 35 ms with 300; the name gain is already there at 60-150 chars


def context_tail(prev):
    """The last CONTEXT_CHARS characters of the previous turn, not starting in the middle of a word ('' if there is no previous turn). The question is at the END of a turn, so the tail is the part worth keeping."""
    if not prev or not prev.strip():
        return ""
    if len(prev) <= CONTEXT_CHARS:
        return prev
    cut = prev[-CONTEXT_CHARS:]
    if not prev[-CONTEXT_CHARS - 1].isspace() and not cut[0].isspace():     # the cut fell inside a word: drop that partial word
        m = re.search(r"\s", cut)
        cut = cut[m.end():] if m else ""
    return cut


def _inside_text(spans, shift):
    """Spans of  context + '\\n' + text  -> spans of `text` alone (shift = len(context) + 1)."""
    return [dict(s, start=max(s["start"], shift) - shift, end=s["end"] - shift) for s in spans if s["end"] > shift]


def suppress_orgs(text, spans):
    """Drop PERSON spans that are really a company name ('Sunrise Capital')."""
    return [s for s in spans if not (s["label"] == "PERSON" and _ORG.search(text[s["start"]:s["end"] + 12]))]


# ---- chatter suppression: the name model tags greetings / politeness words in Indian scripts as PERSON ----
import unicodedata

CHATTER = {w.casefold() for w in """
sir madam mam maam hello hi yes no okay ok thanks thank please good morning afternoon evening night bye minute one
नमस्ते नमस्कार धन्यवाद शुक्रिया जी हाँ हां नहीं ठीक अच्छा सर मैडम मेम शुभ प्रभात संध्या रात्रि सुप्रभात बोलिए बताइए कृपया हैलो एक मिनट
namaste namaskar namaskara dhanyavad dhanyavada shukriya ji haan han nahi theek thik achha accha boliye bataiye kripya ek
हो नाही बरं बोला सांगा आभारी हॅलो अजून काही मदत
ho nahi barr bola sanga ajun kahi madat hapta
ನಮಸ್ಕಾರ ಧನ್ಯವಾದ ಶುಭೋದಯ ಶುಭ ಹೌದು ಇಲ್ಲ ಸರಿ ಹೇಳಿ ಸರ್ ಮೇಡಂ ಹಲೋ ಮತ್ತೆ ನಿಮ್ಮ
haudu illa sari heli nimma matte subhodaya dhanyavada
வணக்கம் நன்றி ஆமா இல்லை சரி சொல்லுங்க சார் மேடம் மன்னிக்கவும் ஹலோ கொஞ்சம்
vanakkam nandri aama illa sari sollunga konjam kandippa sir madam
""".split()}


def _wordchar(c):
    return unicodedata.category(c)[0] in "LMN"


def suppress_chatter(text, spans):
    """For PERSON spans: (1) grow a span that starts/ends in the middle of a word to the whole word; (2) drop it if every
    word in it is a greeting / honorific / acknowledgement. Returns the spans to keep (non-PERSON spans untouched)."""
    out = []
    for s in spans:
        if s["label"] != "PERSON":
            out.append(s); continue
        a, b = s["start"], s["end"]
        while a > 0 and _wordchar(text[a - 1]) and _wordchar(text[a]):
            a -= 1
        while b < len(text) and _wordchar(text[b]) and _wordchar(text[b - 1]):
            b += 1
        words = [w.strip(".,;:!?\"'()").casefold() for w in text[a:b].split()]
        words = [w for w in words if len(w) > 1]
        if words and all(w in CHATTER for w in words):
            continue
        out.append(dict(s, start=a, end=b))
    return out


def suppress_digits(text, spans):
    """A name has no digits. For PERSON spans: drop the words that contain a digit at either end ('Nimage 15th' -> 'Nimage', '19th' -> nothing; the model sometimes tags an ordinal as a name,
    which also masks a DATE the downstream task needs). Spans of other labels are untouched."""
    out = []
    for s in spans:
        if s["label"] != "PERSON":
            out.append(s)
            continue
        a, b = s["start"], s["end"]
        toks = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", text[a:b])]
        while toks and any(c.isdigit() for c in toks[0][2]):
            toks.pop(0)
        while toks and any(c.isdigit() for c in toks[-1][2]):
            toks.pop()
        if toks:
            out.append(dict(s, start=a + toks[0][0], end=a + toks[-1][1]))
    return out


def clean_model_spans(text, spans):
    """All the filters applied to the model's spans, in this order: grow a span to whole words and drop greetings (suppress_chatter), THEN drop company names (suppress_orgs), then take digit
    words off names (suppress_digits). The company check must come after the growing: the model can tag just 'serv' of 'Finserv', and only the whole word is a company suffix."""
    return suppress_digits(text, suppress_orgs(text, suppress_chatter(text, spans)))
