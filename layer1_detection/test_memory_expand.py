"""Plain-python tests for span expansion and constrained name memory.  python -m layer1_detection.test_memory_expand"""
from layer1_detection.expand import expand_spans
from layer1_detection.name_memory import NameMemory
from layer1_detection.detector import Detector

def sp(text, sub, label="PHONE", source="rules"):
    i = text.index(sub); return {"label": label, "start": i, "end": i + len(sub), "source": source}
def cover(text, spans): return [text[s["start"]:s["end"]] for s in spans]

def test_prefix_plus91():
    t = "my number is +91 98765 43210 ok"; assert cover(t, expand_spans(t, [sp(t, "98765 43210")])) == ["+91 98765 43210"]
def test_prefix_91_dash():
    t = "call 91-9876543210 now"; assert cover(t, expand_spans(t, [sp(t, "9876543210")])) == ["91-9876543210"]
def test_prefix_not_glued_to_other_digits():
    t = "ref 12991 9876543210"; out = cover(t, expand_spans(t, [sp(t, "9876543210")]))
    assert out[0].startswith("9876") or out[0].startswith("91 9876") is False
def test_trailing_digits():
    t = "number 9876543210 thanks"; assert cover(t, expand_spans(t, [sp(t, "98765432")])) == ["9876543210"]
def test_sentence_final_dot_not_swallowed():
    t = "my number is 9876543210."; assert cover(t, expand_spans(t, [sp(t, "9876543210")])) == ["9876543210"]
def test_dash_before_digit_absorbed():
    t = "98765-43210 is it"; assert cover(t, expand_spans(t, [sp(t, "98765")])) == ["98765-43210"]
def test_amount_not_swallowed():
    t = "9876543210 ₹500"; assert cover(t, expand_spans(t, [sp(t, "9876543210")])) == ["9876543210"]
def test_spoken_edge_word_absorbed():
    t = "nine eight seven six five four three two one zero"; core = "eight seven six five four three two one"
    assert cover(t, expand_spans(t, [sp(t, core, "SPOKEN_NUMBER", "spoken")])) == [t]
def test_ambiguous_word_not_absorbed_next_to_written_digits():
    t = "9876543210 one minute"; assert cover(t, expand_spans(t, [sp(t, "9876543210")])) == ["9876543210"]
def test_ambiguous_word_absorbed_next_to_spoken():
    t = "nine eight seven six five one"; core = "nine eight seven six five"
    assert cover(t, expand_spans(t, [sp(t, core, "SPOKEN_NUMBER", "spoken")])) == [t]
def test_non_numeric_spans_untouched():
    t = "Ramesh Kumar 123"; s = sp(t, "Ramesh Kumar", "PERSON", "model"); assert expand_spans(t, [s]) == [s]

def test_memory_masks_later_name_parts():
    nm = NameMemory(); t1 = "speaking with Ramesh Kumar"; nm.update(t1, [sp(t1, "Ramesh Kumar", "PERSON", "model")])
    assert cover("Mr. Kumar said Ramesh will pay", nm.find("Mr. Kumar said Ramesh will pay")) == ["Kumar", "Ramesh"]
def test_memory_ignores_stopwords_and_short_parts():
    nm = NameMemory(); t = "Sir Ravi Bank Jo"; nm.update(t, [sp(t, "Sir Ravi Bank Jo", "PERSON", "model")])
    assert set(nm.parts) == {"ravi"}
def test_memory_whole_token_only():
    nm = NameMemory(); t = "Ramesh"; nm.update(t, [sp(t, "Ramesh", "PERSON", "model")])
    assert nm.find("Rameshwar called Ramesh") and cover("Rameshwar called Ramesh", nm.find("Rameshwar called Ramesh")) == ["Ramesh"]
def test_memory_requires_name_shape_in_roman_script():
    nm = NameMemory(); t = "Kumar"; nm.update(t, [sp(t, "Kumar", "PERSON", "model")])
    assert nm.find("the kumar brand") == []
def test_memory_native_script():
    nm = NameMemory(); t = "रमेश शर्मा"; nm.update(t, [sp(t, "रमेश शर्मा", "PERSON", "model")])
    assert cover("हाँ, शर्मा जी बोल रहे हैं", nm.find("हाँ, शर्मा जी बोल रहे हैं")) == ["शर्मा"]
def test_memory_does_not_duplicate_existing_span():
    nm = NameMemory(); t = "Ramesh"; nm.update(t, [sp(t, "Ramesh", "PERSON", "model")])
    t2 = "Ramesh again"; assert nm.find(t2, [sp(t2, "Ramesh", "PERSON", "model")]) == []

class _StubModel:   # pretends the model found the name only in turn 0
    def detect_texts(self, texts):
        return [[{"label": "PERSON", "start": t.index("Ramesh Kumar"), "end": t.index("Ramesh Kumar") + 12}] if "Ramesh Kumar" in t else [] for t in texts]
def test_detector_integration_name_memory_and_expansion():
    d = Detector(use_rules=True, use_model=False); d.model = _StubModel()
    call = [{"speaker": "Agent", "text": "Am I speaking with Ramesh Kumar?"},
            {"speaker": "Customer", "text": "Yes. Kumar here. My number is +91 98765 43210."}]
    res = d.detect_calls([call])[0]
    t = call[1]["text"]; got = [t[s["start"]:s["end"]] for s in res[1]]
    assert "Kumar" in got, got
    assert any(g.startswith("+91") and g.endswith("43210") for g in got), got

if __name__ == "__main__":
    n = ok = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            n += 1
            try: fn(); ok += 1; print("PASS", name)
            except AssertionError as e: print("FAIL", name, e)
    print(f"\n{ok}/{n} passed")
