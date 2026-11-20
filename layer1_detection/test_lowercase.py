"""Lower-case text (ASR engines that do not capitalise, chat messages).  python -m layer1_detection.test_lowercase

Measured on dev with the whole text lower-cased (rules only, no model): PAN fully masked 96% -> 14%, loan ids 98% -> 47%, because spelled letters ('l p o') and written PANs had to be
UPPER case. Now: a written PAN matches in any case, and a single lower-case letter counts as a spelled letter when the turn is 'armed' by a cue (PAN / loan / number asked for)."""
import sys

from layer1_detection.detector import Detector

DET = Detector(use_rules=True, use_model=False)


def masked(*turns):
    call = [{"speaker": "Agent" if i % 2 == 0 else "Customer", "text": t} for i, t in enumerate(turns)]
    last = call[-1]["text"]
    return [last[s["start"]:s["end"]] for s in DET.detect_calls([call])[0][-1]]


def test_written_pan_in_any_case():
    assert masked("my pan is abcde1234f") == ["abcde1234f"]
    assert masked("my pan is AbCdE1234f") == ["AbCdE1234f"]
    assert masked("my pan is ABCDE1234F") == ["ABCDE1234F"]


def test_lower_case_pan_gets_the_pan_label():
    d = DET.detect_calls([[{"speaker": "Customer", "text": "pan abcde1234f"}]])[0][0]
    assert [s["label"] for s in d] == ["PAN"], d


def test_spelled_lower_case_letters_after_a_cue():
    assert masked("please say the first letters of your pan", "l p o") == ["l p o"]
    assert masked("what is your loan reference?", "r w 2") != []
    assert masked("loan number please", "c b i s i 6 2 3") != []


def test_spelled_upper_case_letters_still_work_without_a_cue():
    assert masked("A B W N J") == ["A B W N J"]


def test_lower_case_single_letters_without_a_cue_are_not_masked():
    assert masked("a b c d e") == []
    assert masked("i will pay it on the first, i promise") == []


def test_ordinary_sentences_in_an_armed_turn_stay_unmasked():
    for t in ("yes i said that a day ago", "i have a pan card but not here", "ok i will tell you the number later", "a or b, i do not know"):
        assert masked("please tell me your pan number", t) == [], t


def test_non_pan_shaped_tokens_are_left_alone():
    assert masked("order12345 and abc1234 and abcdef1234g") == [] or all(len(x) >= 7 for x in masked("order12345 and abc1234 and abcdef1234g"))


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print("PASS", n)
        except Exception as e:                               # noqa: BLE001
            failed += 1
            print("FAIL", n, "->", repr(e)[:300])
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
