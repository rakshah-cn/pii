"""Tests for the previous-turn CONTEXT given to the name/address model.  python -m layer1_detection.test_context

Why it exists: a short reply like 'Pooja.' or 'KAB' has no context on its own, so the model missed it (measured on dev: names fully masked 95.1% -> 97.8% with the previous turn
as context, same false-alarm rate). The model now reads  <previous turn> + newline + <this turn>  and only spans INSIDE this turn are kept (offsets stay relative to this turn).
These tests use a fake model: they check the plumbing (what the model is shown, offsets, clipping, cache, cap, flag), not the model's accuracy."""
import sys

from layer1_detection.detector import Detector
from layer1_detection.model import CONTEXT_CHARS, ModelDetector, context_tail


class FakeM:
    """Stands in for Chitragupta.  `rule(text)` -> [(label, start, end)]; remembers every input it was shown."""

    def __init__(self, rule):
        self.rule, self.seen = rule, []

    def detect_batch(self, texts):
        self.seen += list(texts)
        return [[{"label": l, "start": a, "end": b, "text": t[a:b]} for l, a, b in self.rule(t)] for t in texts]


def _md(rule):
    md = ModelDetector(backend="onnx_int8", cache=False)
    md.m = FakeM(rule)                      # _load() is skipped: the model is already 'loaded'
    return md


def name_after_question(t):
    """Finds the name only when the text shows it was ASKED for - like the real model, which needs the question to recognise a bare name."""
    if "name" in t.lower() and "\n" in t:
        i = t.rindex("\n") + 1
        return [("PERSON", i, i + len(t[i:].rstrip(".")))]
    return []


# ------------------------------------------------------------------ the model wrapper
def test_model_is_shown_previous_turn_then_this_turn():
    md = _md(lambda t: [])
    md.detect_texts(["Pooja."], contexts=["May I know your name?"])
    assert md.m.seen == ["May I know your name?\nPooja."], md.m.seen


def test_offsets_are_relative_to_this_turn():
    md = _md(name_after_question)
    out = md.detect_texts(["Pooja."], contexts=["May I know your name?"])
    assert out == [[{"label": "PERSON", "start": 0, "end": 5}]], out


def test_span_only_in_the_context_is_dropped():
    md = _md(lambda t: [("PERSON", 0, 4)] if t.startswith("Raju") else [])
    assert md.detect_texts(["ok thanks"], contexts=["Raju said hello"]) == [[]]


def test_span_across_the_boundary_is_clipped_to_this_turn():
    # the model glues the last word of the context to the first word of this turn: only the part inside this turn may come back
    md = _md(lambda t: [("PERSON", t.index("name") , len(t))])
    out = md.detect_texts(["Pooja."], contexts=["your name"])
    assert out == [[{"label": "PERSON", "start": 0, "end": 6}]], out


def test_no_context_means_the_text_alone_as_before():
    md = _md(lambda t: [])
    md.detect_texts(["hello", "bye"])
    md.detect_texts(["again"], contexts=[""])
    assert md.m.seen == ["hello", "bye", "again"], md.m.seen


def test_mixed_batch_keeps_every_text_aligned_with_its_own_context():
    md = _md(lambda t: [("PERSON", t.rindex("\n") + 1, len(t))] if "\n" in t else [])
    out = md.detect_texts(["Asha", "Meera", "Ravi"], contexts=["q1", "", "q3"])
    assert out[0] == [{"label": "PERSON", "start": 0, "end": 4}]
    assert out[1] == []                                       # no context -> no newline -> the fake finds nothing
    assert out[2] == [{"label": "PERSON", "start": 0, "end": 4}]


def test_same_text_with_different_context_is_not_mixed_up_in_the_cache():
    md = _md(name_after_question)
    a = md.detect_texts(["Pooja."], contexts=["May I know your name?"])
    b = md.detect_texts(["Pooja."], contexts=["The due date is the 5th."])
    c = md.detect_texts(["Pooja."])
    assert a != [[]] and b == [[]] and c == [[]], (a, b, c)


def test_same_input_twice_is_one_model_call():
    md = _md(name_after_question)
    md.detect_texts(["Pooja."], contexts=["your name?"]); md.detect_texts(["Pooja."], contexts=["your name?"])
    assert len(md.m.seen) == 1


def test_context_is_capped_and_cut_on_a_word_boundary():
    long_prev = "word " * 200                                  # 1000 chars
    tail = context_tail(long_prev)
    assert len(tail) <= CONTEXT_CHARS and tail.startswith("word") and tail.rstrip().endswith("word"), (len(tail), tail[:12], tail[-12:])
    assert not context_tail("a" + "word " * 200).startswith("ord"), "the cut must not leave half a word"       # the cut falls inside a word here
    assert context_tail("short question?") == "short question?"
    assert context_tail("") == "" and context_tail(None) == "" and context_tail("  \n ") == ""
    one_word = "x" * 5000                                      # no space at all: nothing sensible to keep, and nothing huge may reach the model
    assert len(context_tail(one_word)) <= CONTEXT_CHARS


def test_context_tail_never_changes_offsets_of_this_turn():
    md = _md(name_after_question)
    out = md.detect_texts(["Pooja."], contexts=[("filler " * 200) + "your name?"])
    assert out == [[{"label": "PERSON", "start": 0, "end": 5}]], out


# ------------------------------------------------------------------ the detector (gateway window and offline scoreboard behave the same)
def _det(**kw):
    d = Detector(use_rules=True, use_model=False, **kw)
    d.model = _md(name_after_question)
    return d


CALL = [{"speaker": "Agent", "text": "May I know your name?"}, {"speaker": "Customer", "text": "Pooja."}]


def test_detector_offline_masks_a_bare_name_given_the_question():
    spans = _det().detect_calls([CALL])[0][1]
    assert [(s["label"], CALL[1]["text"][s["start"]:s["end"]]) for s in spans] == [("PERSON", "Pooja")], spans


def test_detector_live_window_newest_turn_only_gives_the_same_answer():
    spans = _det(model_last_only=True).detect_calls([CALL])[0][1]
    assert [CALL[1]["text"][s["start"]:s["end"]] for s in spans] == ["Pooja"], spans


def test_detector_flag_off_reproduces_the_old_behaviour():
    assert _det(model_context=False).detect_calls([CALL])[0][1] == []


def test_first_turn_has_no_context():
    d = _det()
    d.detect_calls([[{"speaker": "Customer", "text": "Pooja."}]])
    assert d.model.m.seen == ["Pooja."], d.model.m.seen


def test_live_window_runs_the_model_once_on_the_newest_turn():
    d = _det(model_last_only=True)
    d.detect_calls([CALL + [{"speaker": "Agent", "text": "Thanks."}]])
    assert d.model.m.seen == ["Pooja.\nThanks."], d.model.m.seen


def test_context_is_the_raw_previous_turn_not_a_masked_one():
    d = _det(model_last_only=True)
    d.detect_calls([[{"speaker": "Customer", "text": "my number is 9876543210"}, {"speaker": "Agent", "text": "thanks"}]])
    assert d.model.m.seen == ["my number is 9876543210\nthanks"], d.model.m.seen


def test_a_model_without_the_contexts_argument_still_works():
    class Old:                                               # an older stub / adapter: detect_texts(texts) only
        def __init__(self):
            self.seen = []

        def detect_texts(self, texts):
            self.seen += list(texts)
            return [[] for _ in texts]
    d = Detector(use_rules=True, use_model=False)
    d.model = Old()
    out = d.detect_calls([CALL])[0]                          # two turns: the second would have a context, the old model simply gets the plain text
    assert out == [[], []] and d.model.seen == ["May I know your name?", "Pooja."], (out, d.model.seen)


def test_an_adapter_that_takes_keyword_arguments_gets_the_context():
    class Wrap:
        def __init__(self): self.kw = None
        def detect_texts(self, texts, **kw):
            self.kw = kw
            return [[] for _ in texts]
    d = Detector(use_rules=True, use_model=False); d.model = Wrap()
    d.detect_calls([CALL])
    assert d.model.kw == {"contexts": ["", "May I know your name?"]}, d.model.kw


def test_cache_entry_removed_by_another_thread_does_not_break_a_call():
    md = _md(name_after_question)
    md.detect_texts(["Pooja."], contexts=["your name?"])
    real_get = md.cache.get
    class Trim(dict):                                        # a cache whose entries vanish right after they are looked up (the gateway trims the shared dict)
        def get(self, k, d=None):
            v = real_get(k, d); md.cache.pop(k, None); return v
    md.cache = Trim(md.cache)
    assert md.detect_texts(["Pooja."], contexts=["your name?"]) == [[{"label": "PERSON", "start": 0, "end": 5}]]


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print("PASS", n)
        except Exception as e:                               # noqa: BLE001 - a plain runner: report any failure, keep going
            failed += 1
            print("FAIL", n, "->", repr(e)[:300])
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
