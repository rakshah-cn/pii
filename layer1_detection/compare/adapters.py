"""Adapters that make other PII detectors look like our Detector, so the scoreboard can score them.

  sakshi        rotalabs/sakshi-pii-in (Apache-2.0; PERSON / ADDRESS / DOB only; threshold 0.25 per its card)
  gliner        urchade/gliner_multi_pii-v1 (Apache-2.0; zero-shot label list below)
  full+sakshi   our full Layer 1 + Sakshi spans added to the union (source 'sakshi')
  full+gliner   our full Layer 1 + GLiNER spans added to the union (source 'gliner')

All models run locally. Results are cached in memory by text (many turns repeat: 'Hello.').
"""
import os
import time

from layer1_detection.detector import Detector
from layer1_detection.normalise import normalise
from layer1_detection.resolve import resolve_call

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GLINER_LABELS = {      # zero-shot label text -> our label
    "person": "PERSON", "phone number": "PHONE", "aadhaar number": "AADHAAR", "pan card number": "PAN",
    "bank account number": "ACCOUNT", "loan account number": "LOAN", "address": "ADDRESS",
    "email": "EMAIL", "date of birth": "DOB",
}


class _Base:
    name = "base"

    def __init__(self):
        self.cache, self.model_ms, self.n_calls = {}, 0.0, 0

    def predict_texts(self, texts):                       # -> list of span-lists [{label,start,end,source}]
        todo = [t for t in dict.fromkeys(texts) if t not in self.cache]
        if todo:
            t0 = time.perf_counter()
            for t, sp in zip(todo, self._predict(todo)):
                self.cache[t] = sp
            self.model_ms += (time.perf_counter() - t0) * 1000
            self.n_calls += len(todo)
        return [self.cache[t] for t in texts]

    def detect_calls(self, calls):
        flat = [t["text"] for c in calls for t in c]
        res, k, out = self.predict_texts(flat), 0, []
        for c in calls:
            out.append(res[k:k + len(c)]); k += len(c)
        return out


class SakshiAdapter(_Base):
    name = "sakshi"

    def __init__(self, path=os.path.join(ROOT, "models", "sakshi"), threshold=0.25):
        super().__init__()
        from transformers import pipeline
        self.ner = pipeline("token-classification", model=path, aggregation_strategy="first", device=-1)
        self.threshold = threshold

    def _predict(self, texts):
        res = self.ner(texts, batch_size=16)
        if texts and res and isinstance(res[0], dict):     # single-text return shape
            res = [res]
        return [[dict(label=s["entity_group"], start=int(s["start"]), end=int(s["end"]), source="sakshi")
                 for s in r if s["score"] >= self.threshold] for r in res]


class GlinerAdapter(_Base):
    name = "gliner"

    def __init__(self, path=os.path.join(ROOT, "models", "gliner_multi_pii"), threshold=0.4):
        super().__init__()
        from gliner import GLiNER
        self.model = GLiNER.from_pretrained(path)
        self.labels, self.threshold = list(GLINER_LABELS), threshold

    def _predict(self, texts):
        out = []
        for t in texts:
            ents = self.model.predict_entities(t, self.labels, threshold=self.threshold)
            out.append([dict(label=GLINER_LABELS.get(e["label"], e["label"]), start=e["start"], end=e["end"],
                             source="gliner") for e in ents])
        return out


class UnionWithExtra:
    """Our full Layer 1 (raw spans) + an extra detector's spans, then the same resolve step."""

    def __init__(self, extra):
        self.base = Detector(use_rules=True, use_model=True, resolve=False)
        self.extra = extra

    def detect_calls(self, calls):
        base_raw = self.base.detect_calls(calls)
        extra_raw = self.extra.detect_calls(calls)
        out = []
        for call, b, e in zip(calls, base_raw, extra_raw):
            merged = [bt + et for bt, et in zip(b, e)]
            out.append(resolve_call([normalise(t["text"]) for t in call], [t["speaker"] for t in call], merged))
        return out


def make_adapter(name):
    if name == "sakshi":
        return SakshiAdapter()
    if name == "gliner":
        return GlinerAdapter()
    if name == "full+sakshi":
        return UnionWithExtra(SakshiAdapter())
    if name == "full+gliner":
        return UnionWithExtra(GlinerAdapter())
    raise ValueError(name)
