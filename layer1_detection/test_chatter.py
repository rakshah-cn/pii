"""python -m layer1_detection.test_chatter"""
from layer1_detection.model import suppress_chatter

def P(text, sub): i = text.index(sub); return {"label": "PERSON", "start": i, "end": i + len(sub), "source": "model"}
def kept(text, spans): return [text[s["start"]:s["end"]] for s in suppress_chatter(text, spans)]

def test_greeting_in_native_script_dropped():
    t = "हलो, ಶುಭೋದಯ ಮೇಡಂ."; assert kept(t, [P(t, "ಶುಭೋದಯ ಮೇಡಂ")]) == []
def test_midword_fragment_of_greeting_dropped():
    t = "Dhanyavad sir, tumcha vel"; assert kept(t, [P(t, "Dha")]) == []
def test_midword_fragment_of_real_name_grown_to_whole_word():
    t = "this is Rameshwar speaking"; assert kept(t, [P(t, "Ramesh")]) == ["Rameshwar"]
def test_real_name_kept():
    t = "am I speaking with Ramesh Kumar?"; assert kept(t, [P(t, "Ramesh Kumar")]) == ["Ramesh Kumar"]
def test_name_next_to_honorific_kept():
    t = "Mr. Naidu here"; assert kept(t, [P(t, "Mr. Naidu")]) == ["Mr. Naidu"]
def test_non_person_spans_untouched():
    t = "call 9876543210"; s = {"label": "PHONE", "start": 5, "end": 15, "source": "rules"}; assert suppress_chatter(t, [s]) == [s]
def test_native_real_name_kept():
    t = "क्या मेरी बात रमेश शर्मा जी से"; assert kept(t, [P(t, "रमेश शर्मा")]) == ["रमेश शर्मा"]

if __name__ == "__main__":
    n = ok = 0
    for k, fn in sorted(globals().items()):
        if k.startswith("test_"):
            n += 1
            try: fn(); ok += 1; print("PASS", k)
            except AssertionError as e: print("FAIL", k, e)
    print(f"{ok}/{n} passed")
