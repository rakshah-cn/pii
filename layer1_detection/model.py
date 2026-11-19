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

    def detect_texts(self, texts, bs=16):
        todo = [t for t in dict.fromkeys(texts) if self._key(t) not in self.cache]
        if todo:
            self._load()
            for i in range(0, len(todo), bs):
                chunk = todo[i:i + bs]
                for t, spans in zip(chunk, self.m.detect_batch(chunk)):
                    self.cache[self._key(t)] = [dict(label=s["label"], start=s["start"], end=s["end"]) for s in spans]
            if self.use_cache:
                json.dump(self.cache, open(CACHE, "w", encoding="utf-8"))
        return [self.cache[self._key(t)] for t in texts]


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
