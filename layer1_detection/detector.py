"""Layer 1 detector: union of (written rules) + (spoken engine with cross-turn memory) + (Chitragupta).

Input : a call = list of {"speaker","text"} turns, in order.
Output: for each turn, a list of spans {label,start,end,source}, offsets relative to that turn's text.
"""
import re

from .model import ModelDetector, suppress_chatter, suppress_orgs
from .resolve import resolve_call
from .normalise import normalise
from . import rules, spoken_engine
from .expand import expand_spans
from .name_memory import NameMemory

# words that make the next few turns likely to contain an identifier ('armed' state)
CUES = ["number", "aadhaar", "aadhar", "pan ", "pan.", "account", "loan", "mobile", "phone", "contact",
        "digits", "nambar", "sankhya", "नंबर", "नम्बर", "संख्या", "ನಂಬರ್", "ಸಂಖ್ಯೆ", "எண்", "நம்பர்",
        "go on", "continue", "the rest", "kyc", "verification"]
ARM_ON_CUE, ARM_ON_FRAGMENT = 6, 3


class Detector:
    def __init__(self, use_rules=True, use_model=True, model_path="models/chitragupta", resolve=True,
                 model_last_only=False, model_backend="torch"):
        # model_last_only=True: run the (slow) model only on the NEWEST turn of each call; earlier turns are history
        # for cues/joining, which the fast rules cover. Default False keeps the scoreboard behaviour.
        self.use_rules, self.use_model, self.resolve = use_rules, use_model, resolve
        self.model_last_only = model_last_only
        self.model = ModelDetector(model_path, backend=model_backend) if use_model else None

    def detect_calls(self, calls):
        model_out = None
        if self.model:
            idx = [(ci, ti) for ci, c in enumerate(calls) for ti in range(len(c))
                   if not self.model_last_only or ti == len(c) - 1]
            res = self.model.detect_texts([calls[ci][ti]["text"] for ci, ti in idx])
            model_out = [[[] for _ in c] for c in calls]
            for (ci, ti), spans in zip(idx, res):
                model_out[ci][ti] = spans
        results = []
        for ci, turns in enumerate(calls):
            armed, per_turn, norm_texts = 0, [], []
            nm = NameMemory()                   # names seen earlier in THIS conversation
            for ti, t in enumerate(turns):
                text = normalise(t["text"])
                norm_texts.append(text)
                spans = []
                if self.use_rules:
                    low = text.casefold()
                    if any(c in low for c in CUES):
                        armed = max(armed, ARM_ON_CUE)
                    excl = rules.excluded_ranges(text)
                    sp = rules.find(text, excl) + spoken_engine.find(text, armed > 0, excl)
                    armed = max(armed, ARM_ON_FRAGMENT) if sp else max(0, armed - 1)
                    spans += sp
                if model_out:
                    ms = suppress_chatter(text, suppress_orgs(text, model_out[ci][ti]))
                    spans += [dict(s, source="model") for s in ms]
                spans = expand_spans(text, spans)          # absorb +91 / straggler digits / spoken edge words
                spans += nm.find(text, spans)              # later mentions of a detected name's parts
                nm.update(text, spans)
                per_turn.append(spans)
            if self.resolve:                    # merge, type, join (see resolve.py); offsets stay valid
                per_turn = resolve_call(norm_texts, [t["speaker"] for t in turns], per_turn)
            results.append(per_turn)
        return results
