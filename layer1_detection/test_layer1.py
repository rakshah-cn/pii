"""Layer 1 tests (no model needed).   Run:  .venv/bin/python -m layer1_detection.test_layer1"""
import sys

from layer0_data import pii_gen
from layer1_detection.detector import Detector

DET = Detector(use_rules=True, use_model=False)


def run(*turns):
    call = [{"speaker": sp, "text": tx} for sp, tx in turns]
    return call, DET.detect_calls([call])[0]


def masked_text(text, spans):
    out = list(text)
    for s in spans:
        for i in range(s["start"], s["end"]):
            out[i] = "#"
    return "".join(out)


def covered_ratio(text, spans, sub):
    i = text.index(sub)
    hit = sum(any(s["start"] <= k < s["end"] for s in spans) for k in range(i, i + len(sub)))
    return hit / len(sub)


# ---------------- loan / agreement number formats (30 invented) ----------------
LOAN_FORMATS = [
    "LAN-2023-00123456", "AB12345678", "PL/BLR/2021/004567", "HL0012345678", "1234567890123", "LN00123456789",
    "HOME-2022-98765", "BL_2023_001234", "XYZ/PL/21/009871", "2023LOAN004455", "ABFL/BLR/PL/00012345", "PL2021BLR00045",
    "ML-000456789", "MSME/2022/1234567", "CD00123456", "KA-BLR-HL-2019-00321", "P0123456789", "4521-7788-90",
    "TWL/2020/55661", "GL12345678901", "AAAAA00001", "a1b2c3d4e5f6", "LNPL-23-004455", "0000123456789012",
    "FIN/CHN/2024/00012", "BL.2023.4455.12", "UBL998877665", "PLN-0044-5566-77", "LA/2024/BLR/778899", "HF2019000123",
]


def test_loan_formats_with_cue():
    bad = []
    for f in LOAN_FORMATS:
        text = f"Customer: My loan number is {f}, please check."
        _, sp = run(("Customer", f"My loan number is {f}, please check."))
        if covered_ratio(f"My loan number is {f}, please check.", sp[0], f) < 0.95:
            bad.append(f)
    assert not bad, f"loan formats not fully masked (with cue): {bad}"


def test_loan_formats_without_cue():
    """No cue word: alphanumeric ids (>=7 chars, >=3 digits) and long digit runs must still be caught."""
    bad = []
    for f in LOAN_FORMATS:
        t = f"Please check {f} today."
        _, sp = run(("Agent", t))
        if covered_ratio(t, sp[0], f) < 0.95:
            bad.append(f)
    # tolerated without any cue: short/odd shapes (< 7 alnum chars or < 3 digits) - none of ours should be
    assert not bad, f"loan formats not fully masked (no cue): {bad}"


def test_loan_labelled_with_cue():
    _, sp = run(("Agent", "Please tell me your loan account number."), ("Customer", "It is HL0012345678."))
    assert sp[1] and sp[1][0]["label"] == "LOAN", sp[1]


# ---------------- new identifier rules ----------------
def _labels(text, speaker="Customer"):
    _, sp = run((speaker, text))
    return {s["label"] for s in sp[0]}


def test_upi_email_voter_gstin_dl_passport():
    assert "UPI_ID" in _labels("pay to rohit.k@okhdfcbank")
    assert "EMAIL" in _labels("mail me at priya.k@example.com")
    assert "VOTER_ID" in _labels("my voter id ABC1234567")
    assert "GSTIN" in _labels("gst 29ABCDE1234F1Z5")
    assert "DRIVING_LICENCE" in _labels("licence KA0120110012345")
    assert "PASSPORT" in _labels("my passport number is J8369854")
    assert "IFSC" in _labels("ifsc HDFC0001234")


def test_dob_forms():
    assert "DOB" in _labels("I was born on 5th March 1990")
    assert "DOB" in _labels("date of birth 12/05/1991")
    assert "DOB" in _labels("March 5, 1990 is my birthday")
    assert "DOB" not in _labels("your EMI is due on 12/05/2026")          # due dates are not DOB


def test_masked_values():
    for t in ("card XXXX XXXX 1234", "ending ****5678", "account xxxxxx9012"):
        assert "MASKED_ID" in _labels(t), t


def test_card_luhn():
    _, sp = run(("Customer", "my card is 4111 1111 1111 1111"))
    assert sp[0][0]["label"] == "CARD_NUMBER" and sp[0][0].get("checksum_valid") is True, sp[0]


# ---------------- checksum evidence and typed labels ----------------
def test_aadhaar_checksum_valid_and_invalid():
    good = pii_gen.fake_aadhaar()
    _, sp = run(("Customer", f"my aadhaar is {good[:4]} {good[4:8]} {good[8:]}"))
    assert sp[0][0]["label"] == "AADHAAR" and sp[0][0]["checksum_valid"] is True, sp[0]
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    _, sp = run(("Customer", f"my aadhaar is {bad}"))
    assert sp[0][0]["label"] == "AADHAAR" and sp[0][0].get("checksum_valid") is False, sp[0]   # still masked


def test_typed_labels_spoken_phone_pan():
    _, sp = run(("Agent", "Please confirm your mobile number."), ("Customer", "nine eight double seven six five four three two"))
    assert sp[1][0]["label"] == "PHONE", sp[1]
    _, sp = run(("Agent", "Your PAN card number please."), ("Customer", "A B C P K 1 2 3 4 L"))
    assert sp[1][0]["label"] == "PAN", sp[1]


def test_phone_with_country_code():
    t = "call me on +91 98765 43210"
    _, sp = run(("Customer", t))
    assert covered_ratio(t, sp[0], "98765 43210") == 1.0 and sp[0][0]["label"] == "PHONE", sp[0]


# ---------------- joined split numbers ----------------
def test_pieces_across_turns_share_entity_and_joined_value():
    _, sp = run(("Agent", "Please confirm your registered mobile number."),
                ("Customer", "nine eight double seven six"),
                ("Agent", "Yes, go on."),
                ("Customer", "five four three two one"))
    a, b = sp[1][0], sp[3][0]
    assert a["entity"] == b["entity"], (a, b)
    assert (a["part"], b["part"]) == (1, 2)
    assert a["joined_value"] == b["joined_value"] == "9877654321", a["joined_value"]
    assert a["label"] == b["label"] == "PHONE"


def test_different_numbers_do_not_merge():
    _, sp = run(("Agent", "Your mobile number please."), ("Customer", "9876543210"),
                ("Agent", "And your Aadhaar number?"), ("Customer", "eight one two three four five"))
    assert sp[1][0]["entity"] != sp[3][0]["entity"]


def test_repeat_same_complete_number_not_joined_into_one_long_number():
    _, sp = run(("Agent", "Your mobile number please."), ("Customer", "9876543210"),
                ("Agent", "Say again"), ("Customer", "9876543210"))
    assert len(sp[1][0]["joined_value"]) == 10 and len(sp[3][0]["joined_value"]) == 10


# ---------------- spoken variants ----------------
def test_transliterated_english_digit_words():
    for t in ("नाइन एट डबल सेवन फोर वन टू थ्री", "ನೈನ್ ಎಯ್ಟ್ ಸೆವೆನ್ ಫೋರ್ ವನ್ ಟೂ ತ್ರೀ", "நைன் எய்ட் செவன் ஃபோர் வன் டூ த்ரீ"):
        _, sp = run(("Agent", "Please tell me your mobile number."), ("Customer", t))
        assert covered_ratio(t, sp[1], t) >= 0.95, (t, sp[1])


def test_english_tens_homophones_and_typos():
    t = "ninety eight seven six for to ate"
    _, sp = run(("Agent", "Your mobile number?"), ("Customer", t))
    assert covered_ratio(t, sp[1], t) >= 0.95, sp[1]


def test_zero_width_characters():
    t = "9​8​7​6​5​4​3​2​1​0"
    _, sp = run(("Customer", f"number {t}"))
    assert covered_ratio(f"number {t}", sp[0], t) >= 0.95, sp[0]


def test_marathi_digit_words():
    t = "नऊ आठ सात सहा डबल पाच शून्य एक दोन तीन"
    _, sp = run(("Agent", "Mobile number please."), ("Customer", t))
    assert covered_ratio(t, sp[1], t) >= 0.95, sp[1]


# ---------------- look-alikes must stay unmasked ----------------
def test_lookalikes_not_masked():
    for t in ("Your EMI of ₹4,652 is due on the 5th.", "Pay four thousand six hundred fifty two rupees by the 5th",
              "call between 10:30 and 11:30", "it is a 4G plan, version 2.0", "I will pay on the 27th, I promise",
              "Okay, I will do it. One minute.", "twelve thousand five hundred rupees are pending"):
        _, sp = run(("Agent", t))
        assert not sp[0], (t, sp[0])


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print("PASS", n)
        except AssertionError as e:
            failed += 1
            print("FAIL", n, "->", str(e)[:300])
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
