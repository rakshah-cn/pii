"""python -m layer1_detection.test_chatter"""
from layer1_detection.model import clean_model_spans, suppress_chatter, suppress_digits

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

def digits(text, spans): return [text[s["start"]:s["end"]] for s in suppress_digits(text, spans)]

def test_ordinal_tagged_as_a_name_is_dropped():
    t = "theek ahe, 19th paryant bharto"; assert digits(t, [P(t, "19th")]) == []
def test_ordinal_next_to_a_word_is_trimmed_off_the_name():
    t = "Hello, Nimage 15th ge reminder"; assert digits(t, [P(t, "Nimage 15th")]) == ["Nimage"]
    t = "on 15th Ramesh Kumar called"; assert digits(t, [P(t, "15th Ramesh Kumar")]) == ["Ramesh Kumar"]
def test_name_without_digits_untouched():
    t = "am I speaking with Ramesh Kumar?"; assert digits(t, [P(t, "Ramesh Kumar")]) == ["Ramesh Kumar"]
    t = "क्या मेरी बात रमेश शर्मा जी से"; assert digits(t, [P(t, "रमेश शर्मा")]) == ["रमेश शर्मा"]
def test_digit_trim_ignores_other_labels():
    t = "call 9876543210"; s = {"label": "PHONE", "start": 5, "end": 15, "source": "rules"}; assert suppress_digits(t, [s]) == [s]
def test_native_digits_count_as_digits():
    t = "तारीख १५वी"; assert digits(t, [P(t, "१५वी")]) == []

def cleaned(text, spans): return [text[s["start"]:s["end"]] for s in clean_model_spans(text, spans)]

def test_half_word_of_a_company_name_is_dropped():
    # the model tagged 'Orchid' and just 'serv' of 'Finserv': the company check must see the WHOLE word
    t = "Ee vishaya Orchid Finserv nalli update madtini."; assert cleaned(t, [P(t, "Orchid"), P(t, "serv")]) == []
def test_company_name_as_one_span_is_dropped():
    t = "Hello, Orchid Finserv collections here."; assert cleaned(t, [P(t, "Orchid Finserv")]) == []
def test_real_name_survives_all_the_filters():
    t = "Ee vishaya Ramesh Kumar nalli update madtini."; assert cleaned(t, [P(t, "Ramesh Kumar")]) == ["Ramesh Kumar"]
def test_filters_run_in_a_fixed_order_on_a_mixed_batch():
    t = "Namaste Ramesh, 15th ko Sunrise Capital se call hai"
    assert cleaned(t, [P(t, "Namaste"), P(t, "Ramesh"), P(t, "15th"), P(t, "Sunrise"), P(t, "Capital")]) == ["Ramesh"]

if __name__ == "__main__":
    n = ok = 0
    for k, fn in sorted(globals().items()):
        if k.startswith("test_"):
            n += 1
            try: fn(); ok += 1; print("PASS", k)
            except AssertionError as e: print("FAIL", k, e)
    print(f"{ok}/{n} passed")
