"""Tests for the phone-number LABEL rules in the resolver.  python -m layer1_detection.test_phone_label

Masking was already right; the TYPE label was wrong for ~30% of phone numbers (measured on dev: 68% right), mostly because of a loan cue that was not about this number:
  1. 'the mobile number linked to your loan': two cues in one question; the number being asked for is the one right before the word 'number' (the mobile), not the last cue word.
  2. a valid 10-digit mobile shape said 2+ turns after the last loan/account cue (nothing in between) is a phone number; the cue is stale.
A loan / account cue in the SAME turn, or in the turn right before the number, still wins (a 10-digit account number is possible)."""
import sys

from layer1_detection.detector import Detector

DET = Detector(use_rules=True, use_model=False)


def labels(*turns):
    """Turn texts (alternating Agent / Customer) -> the label of every span in the LAST turn."""
    call = [{"speaker": "Agent" if i % 2 == 0 else "Customer", "text": t} for i, t in enumerate(turns)]
    return [s["label"] for s in DET.detect_calls([call])[0][-1]]


# ---- 1. which cue is the question about
def test_mobile_number_linked_to_your_loan_is_a_phone_question():
    assert labels("Can you read out the mobile number linked to your loan?", "9876543210") == ["PHONE"]


def test_registered_mobile_number_for_the_loan_account():
    assert labels("Please confirm the registered mobile number for the loan account.", "9876543210") == ["PHONE"]


def test_loan_account_mobile_number_phrase():
    assert labels("What is the loan account mobile number?", "9876543210") == ["PHONE"]


def test_loan_number_question_is_still_a_loan_question():
    assert labels("What is your loan number?", "9876543210") == ["LOAN"]


def test_account_number_question_is_still_an_account_question():
    assert labels("Please tell me your account number.", "9876543210") == ["ACCOUNT"]


def test_loan_account_number_phrase_is_loan():
    assert labels("Read out the loan account number linked to your mobile.", "9876543210") == ["LOAN"]


def test_single_cue_in_the_question_unchanged():
    assert labels("Which loan is this about?", "9876543210") == ["LOAN"]
    assert labels("Which mobile do you use?", "9876543210") == ["PHONE"]


# ---- 2. a stale loan / account cue must not out-vote the shape of a mobile number
def test_stale_loan_cue_two_turns_back_loses_to_the_phone_shape():
    assert labels("Your loan payment is overdue.", "okay", "Please go ahead.", "9876543210") == ["PHONE"]


def test_stale_account_cue_loses_too():
    assert labels("Your bank account shows a pending EMI.", "yes", "Go ahead please.", "hmm", "9876543210") == ["PHONE"]


def test_cue_in_the_turn_right_before_still_wins():
    assert labels("What is your account number?", "9876543210") == ["ACCOUNT"]


def test_cue_in_the_same_turn_always_wins():
    assert labels("my loan number is 9876543210") == ["LOAN"]
    assert labels("account number 9876543210") == ["ACCOUNT"]


def test_only_valid_mobile_shapes_are_retyped():
    # 10 digits starting with 1-5 is not a mobile number: the stale cue keeps its say
    assert labels("Your loan payment is overdue.", "okay", "Please go ahead.", "1234567890") == ["LOAN"]
    # 12 digits: not a phone either
    assert "PHONE" not in labels("Your loan payment is overdue.", "okay", "Please go ahead.", "123456789012")


def test_aadhaar_valid_number_is_never_a_phone():
    assert labels("Your loan payment is overdue.", "okay", "Please go ahead.", "234123412346") == ["AADHAAR"]


def test_number_said_in_pieces_across_turns_gets_the_phone_label_when_joined():
    out = DET.detect_calls([[{"speaker": "Agent", "text": "Can you read out the mobile number linked to your loan?"},
                             {"speaker": "Customer", "text": "98765"}, {"speaker": "Agent", "text": "Go on."},
                             {"speaker": "Customer", "text": "43210"}]])[0]
    assert [s["label"] for t in out for s in t] == ["PHONE", "PHONE"], out


# ---- found by the independent review
def test_two_cues_in_the_numbers_own_turn_last_one_wins():
    assert labels("my loan number is 123456 and my mobile is 9876543210") == ["LOAN", "PHONE"]
    assert labels("my account number is 123456789 and my phone is 9876543210")[-1] == "PHONE"


def test_head_cue_must_be_in_the_same_clause_as_the_last_cue():
    assert labels("Your loan number is on file. Please confirm your mobile.", "9876543210") == ["PHONE"]
    assert labels("Your loan number is on file, please confirm your mobile", "9876543210") == ["PHONE"]


def test_a_cue_after_the_number_beats_a_stale_one():
    assert labels("What is your loan number?", "ok", "hold on", "9876543210 is my loan number") == ["LOAN"]
    assert labels("What is your account number?", "ok", "hold on", "9876543210 is my account number") == ["ACCOUNT"]


def test_joined_pieces_use_the_freshest_cue():
    out = DET.detect_calls([[{"speaker": "Agent", "text": "What is your account number?"}, {"speaker": "Customer", "text": "ok"}, {"speaker": "Agent", "text": "hold on"},
                             {"speaker": "Customer", "text": "98765"}, {"speaker": "Agent", "text": "Next five digits of the account number please."},
                             {"speaker": "Customer", "text": "43210"}]])[0]
    assert [s["label"] for t in out for s in t] == ["ACCOUNT", "ACCOUNT"], out


def test_blank_run_after_a_cue_word_is_cheap():
    import time
    t0 = time.perf_counter(); labels("Please tell me your loan" + " " * 16000 + "x", "9876543210"); dt = time.perf_counter() - t0
    assert dt < 0.5, dt


def test_marathi_and_hindi_number_words():
    assert labels("तुमचा मोबाईल क्रमांक लोन खात्याशी जोडलेला", "9876543210") == ["PHONE"]


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
