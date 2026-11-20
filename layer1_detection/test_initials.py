"""Tests for initials in names ('S. Nadar', 'Usha M. Tiwari', 'K.S. Rao').  python -m layer1_detection.test_initials

Measured on dev (3,000 calls): 28 of the 52 names that were not fully masked had an initial in them; the model finds the surname and leaves 'S.' visible, or splits 'A' / 'Khan',
or stops after 'Vinayak E' and misses '. Mane'. After a name span, the expansion absorbs the initials next to it (no surname is taken after an initial). It never touches
non-name spans, never takes an honorific ('Mr.'), and never takes a lower-case word."""
import sys

from layer1_detection.expand import expand_spans
from layer1_detection.name_memory import NameMemory


def P(text, sub, source="model"):
    i = text.index(sub)
    return {"label": "PERSON", "start": i, "end": i + len(sub), "source": source}


def covered(text, spans):
    """The union of the PERSON spans as text pieces (adjacent / overlapping spans merged)."""
    out, cur = [], None
    for s in sorted((s for s in spans if s["label"] == "PERSON"), key=lambda s: s["start"]):
        if cur and s["start"] <= cur[1] + 1 and text[cur[1]:s["start"]].strip(" ") == "":
            cur[1] = max(cur[1], s["end"])
        else:
            cur = [s["start"], s["end"]]
            out.append(cur)
    return [text[a:b] for a, b in out]


def ex(text, *subs):
    return covered(text, expand_spans(text, [P(text, x) for x in subs]))


# ---- leading initials
def test_leading_initial_is_absorbed():
    assert ex("S. Thorat bolat ahe.", "Thorat") == ["S. Thorat"]


def test_two_leading_initials_with_dots_and_spaces():
    assert ex("my name is K. S. Rao", "Rao") == ["K. S. Rao"]


def test_glued_initials():
    assert ex("this is K.S. Rao speaking", "Rao") == ["K.S. Rao"]
    assert ex("M.G. Ramachandran here", "Ramachandran") == ["M.G. Ramachandran"]


def test_honorific_is_not_an_initial():
    assert ex("Hello sir, am I speaking with Mr. V. Kamath?", "Kamath") == ["V. Kamath"]
    assert ex("Mr. Kamath here", "Kamath") == ["Kamath"]


# ---- the model split the initial off, or stopped after it
def test_initial_and_surname_as_two_model_spans():
    assert ex("A. Khan this side", "A", "Khan") == ["A. Khan"]


def test_name_stopped_after_the_initial_letter():
    assert ex("Is the borrower Renuka S. Hiremath ok", "Renuka S", "Hiremath") == ["Renuka S. Hiremath"]
    assert ex("Vinayak E. Mane.", "Vinayak E") == ["Vinayak E."]            # the surname is not guessed: it is the model's job


def test_middle_initial_is_absorbed_but_no_surname_is_guessed():
    assert ex("Account la irukkura peyar Kumaresan B. Pillai sariya?", "Kumaresan") == ["Kumaresan B."]
    assert ex("Haudu, Bhagya D. Kamath matadtiddini.", "Bhagya") == ["Bhagya D."]


def test_next_sentence_word_is_never_eaten():
    for t, n, want in (("Ravi K. Monday I will pay the EMI", "Ravi", "Ravi K."), ("I am Suresh P. Mujhe loan ke baare mein poochna hai", "Suresh", "Suresh P."),
                       ("This is Ravi K. HDFC Bank is the lender", "Ravi", "Ravi K."), ("My name is Ravi K. Pay 5000 rupees on 15 March", "Ravi", "Ravi K.")):
        assert ex(t, n) == [want], (t, ex(t, n))


def test_label_before_a_capital_letter_is_not_an_initial():
    assert ex("Plan B. Ramesh Kumar will call you", "Ramesh Kumar") == ["Ramesh Kumar"]
    assert ex("Section A. Suresh Patil is the guarantor", "Suresh Patil") == ["Suresh Patil"]
    assert ex("Vitamin C. Kumar said hello", "Kumar") == ["Kumar"]


def test_span_that_starts_at_the_dot_of_the_initial():
    t = "En peyar S. Murthy."
    assert covered(t, expand_spans(t, [{"label": "PERSON", "start": t.index(". Murthy"), "end": t.index("Murthy") + 6, "source": "model"}])) == ["S. Murthy"]


def test_name_never_grows_into_a_number_or_id_span():
    t = "naanu B. Shetty matadtiddini"
    sp = {"label": "SPOKEN_NUMBER", "start": 0, "end": t.index("B") + 1, "source": "spoken"}          # 'naanu B' was read as digits by the spoken engine
    out = expand_spans(t, [sp, P(t, "Shetty")])
    assert [t[s["start"]:s["end"]] for s in out if s["label"] == "PERSON"] == ["Shetty"], out


def test_cost_stays_linear_on_a_long_run_of_initials():
    import time
    t = "A. " * 30000 + "x Ravi"
    t0 = time.perf_counter(); out = ex(t, "Ravi"); dt = time.perf_counter() - t0
    assert out == ["Ravi"] and dt < 0.5, (out, dt)


def test_name_with_a_trailing_model_word_is_kept_as_the_model_gave_it():
    assert ex("M. Gounder pesuren", "M", "Gounder pesuren") == ["M. Gounder pesuren"]
    assert ex("Mani Z. Thevar pesuren", "Mani Z", "Thevar pesuren") == ["Mani Z. Thevar pesuren"]


# ---- what must NOT happen
def test_lower_case_word_after_an_initial_is_not_taken():
    assert ex("Ravi K. is my brother", "Ravi") == ["Ravi K."]


def test_honorific_or_chatter_after_an_initial_is_not_taken():
    assert ex("Ravi K. Sir please", "Ravi") == ["Ravi K."]
    assert ex("Ravi K. Thanks a lot", "Ravi") == ["Ravi K."]


def test_two_letter_abbreviation_is_not_an_initial():
    assert ex("Okay. Kumar called", "Kumar") == ["Kumar"]
    assert ex("PAN. Ravi said yes", "Ravi") == ["Ravi"]


def test_initial_glued_to_a_word_is_not_an_initial():
    assert ex("ABC. Ramesh called", "Ramesh") == ["Ramesh"]


def test_lower_case_initial_is_left_alone():
    assert ex("a. ramesh called", "ramesh") == ["ramesh"]


def test_name_alone_unchanged():
    assert ex("Ramesh Kumar here", "Ramesh Kumar") == ["Ramesh Kumar"]
    assert ex("Pooja.", "Pooja") == ["Pooja"]


def test_sentence_final_initial_does_not_run_on_into_the_next_sentence():
    assert ex("I am Ravi K. Then I called", "Ravi") == ["Ravi K."]            # 'Then' is capitalised but is a common sentence starter (in the stop-word list), so it is not taken for a surname
    assert ex("my name is Ravi K.", "Ravi") == ["Ravi K."]


def test_non_name_spans_are_untouched():
    t = "S. 9876543210"
    s = {"label": "PHONE", "start": 3, "end": 13, "source": "rules"}
    assert expand_spans(t, [s]) == [s]


def test_expansion_is_idempotent_and_keeps_every_other_field():
    t = "S. Thorat bolat ahe."
    once = expand_spans(t, [P(t, "Thorat")])
    assert expand_spans(t, once) == once and once[0]["source"] == "model" and once[0]["label"] == "PERSON"


def test_offsets_stay_inside_the_text_at_both_ends():
    t = "S. Thorat"
    s = expand_spans(t, [P(t, "Thorat")])[0]
    assert (s["start"], s["end"]) == (0, len(t))
    t2 = "name: K."
    for sp in expand_spans(t2, [{"label": "PERSON", "start": 6, "end": 7, "source": "model"}]):
        assert 0 <= sp["start"] < sp["end"] <= len(t2)


# ---- later mentions (name memory) get the same treatment
def test_memory_mention_with_an_initial_is_absorbed():
    nm = NameMemory()
    t1 = "I am Meena Thorat"
    nm.update(t1, [P(t1, "Meena Thorat")])
    t2 = "Is that S. Thorat again?"
    assert covered(t2, nm.find(t2)) == ["S. Thorat"]


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
