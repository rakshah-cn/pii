"""Tests for the pincode-anchored ADDRESS rule: the house-number words are part of the address, and a full stop after an abbreviation ('opp.', 'T.') does not end the address.
python -m layer1_detection.test_address

Measured on dev (3,000 calls): addresses fully masked 90.9%; of the 22 that were not, 14 left the word before the house number visible ('H.No.', 'Flat', 'வீட்டு எண்', 'घर क्रमांक'),
1 had a house number with a slash ('772/50'), 2 were cut by an abbreviation full stop. The words BEFORE the marker (a lead-in such as 'I live at') must stay unmasked."""
import sys

from layer1_detection import rules
from layer1_detection.detector import Detector


def address(text):
    """The ADDRESS spans the written-identifier rules find in `text`, as text."""
    return [text[s["start"]:s["end"]] for s in rules.find(text, rules.excluded_ranges(text)) if s["label"] == "ADDRESS"]


# ---- the marker words are part of the address
def test_tamil_house_number_marker():
    assert address("No, I have moved. It is வீட்டு எண் 172, மெயின் ரோடு, திருச்சி - 620396.") == ["வீட்டு எண் 172, மெயின் ரோடு, திருச்சி - 620396"]


def test_marathi_flat_with_devanagari_digits():
    assert address("at फ्लॅट १३२, हडपसर, नाशिक - ४२२४३० now") == ["फ्लॅट १३२, हडपसर, नाशिक - ४२२४३०"]


def test_h_no_with_dots():
    assert address("Address: H.No. 156, Main Road, Coimbatore 641697") == ["H.No. 156, Main Road, Coimbatore 641697"]


def test_no_dot():
    assert address("It is No. 538, Park Street, Indiranagar, Bengaluru 560197.") == ["No. 538, Park Street, Indiranagar, Bengaluru 560197"]


def test_flat_english():
    assert address("my address is Flat 744, Karol Bagh, near Bharat Temple, Lucknow - 226455") == ["Flat 744, Karol Bagh, near Bharat Temple, Lucknow - 226455"]


def test_hindi_makan_number():
    assert address("मकान नंबर 806, एम जी रोड, दिल्ली - 110431") == ["मकान नंबर 806, एम जी रोड, दिल्ली - 110431"]


def test_marathi_ghar_kramank():
    assert address("घर क्रमांक 822, गणेश निवास, मुंबई - 400407") == ["घर क्रमांक 822, गणेश निवास, मुंबई - 400407"]


def test_kannada_mane_sankhye():
    assert address("ಮನೆ ಸಂಖ್ಯೆ 658, ಎಂ ಜಿ ರಸ್ತೆ, ಬೆಂಗಳೂರು - 560156") == ["ಮನೆ ಸಂಖ್ಯೆ 658, ಎಂ ಜಿ ರಸ್ತೆ, ಬೆಂಗಳೂರು - 560156"]


def test_hindi_flat_word():
    assert address("फ्लैट 130, साईं अपार्टमेंट, एम जी रोड - 208371") == ["फ्लैट 130, साईं अपार्टमेंट, एम जी रोड - 208371"]


def test_other_markers():
    for t in ("D.No. 12-3-45, Gandhi Road, Hyderabad 500001", "Plot 14, Sector 5, Noida 201301", "Door No. 7, Temple Street, Madurai 625001",
              "House No. 9, Lake View, Pune 411001", "Plot No. 14, Sector 5, Noida 201301", "Apt. 93, Park Street, Delhi 110001"):
        assert address("it is " + t) == [t], t


def test_romanised_markers():
    assert address("ghar number 12, Gandhi Road, Pune 411001") == ["ghar number 12, Gandhi Road, Pune 411001"]
    assert address("veetu ennu 12, Gandhi Road, Chennai 600001") == ["veetu ennu 12, Gandhi Road, Chennai 600001"]


# ---- the lead-in before the marker stays out
def test_lead_in_stays_out_with_a_marker():
    assert address("I live at Flat 744, Karol Bagh, Lucknow - 226455.") == ["Flat 744, Karol Bagh, Lucknow - 226455"]
    assert address("No. I live at 93, Sai Krupa Apartments, Delhi - 110833.") == ["93, Sai Krupa Apartments, Delhi - 110833"]


def test_lead_in_stays_out_without_a_marker():
    assert address("I live at 93, Sai Krupa Apartments, Delhi - 110833.") == ["93, Sai Krupa Apartments, Delhi - 110833"]


def test_marker_inside_another_word_is_not_a_marker():
    assert address("the piano 5, Park Street, Delhi 110001") == ["5, Park Street, Delhi 110001"]


def test_no_comma_is_not_a_marker_for_the_house_number():
    assert address("No, 93, Park Street, Delhi 110001") == ["93, Park Street, Delhi 110001"]


# ---- house numbers
def test_house_number_with_a_slash():
    assert address("I stay at 772/50, Kothrud, Nashik, Maharashtra - 422095") == ["772/50, Kothrud, Nashik, Maharashtra - 422095"]
    assert address("607/55, Sunrise Towers, MG Road, Mumbai - 400001") == ["607/55, Sunrise Towers, MG Road, Mumbai - 400001"]


def test_a_phone_number_is_not_taken_for_a_house_number():
    assert address("call 9876543210, Delhi 110001") == ["Delhi 110001"]


# ---- abbreviation full stops do not end the address
def test_opp_abbreviation():
    assert address("I live at 93, Sai Krupa Apartments, Delhi, opp. Govt School - 110833.") == ["93, Sai Krupa Apartments, Delhi, opp. Govt School - 110833"]


def test_initial_with_full_stop_inside_the_address():
    assert address("607/55, Sunrise Towers, MG Road, T. Nagar - 641251") == ["607/55, Sunrise Towers, MG Road, T. Nagar - 641251"]
    assert address("12, M.G. Road, Bengaluru 560001") == ["12, M.G. Road, Bengaluru 560001"]


def test_a_real_sentence_end_still_ends_the_lead_in():
    assert address("I paid on the 5th. Send it to 93, Park Street, Delhi - 110001") == ["93, Park Street, Delhi - 110001"]
    assert address("I will pay Rs. 500 tomorrow. Address is 93, Park Street, Delhi - 110001") == ["93, Park Street, Delhi - 110001"]


def test_question_and_exclamation_still_end_it():
    assert address("Is that right? 93, Park Street, Delhi - 110001") == ["93, Park Street, Delhi - 110001"]


# ---- end to end: the whole address is masked, and nothing else is
def test_detector_masks_the_marker_too():
    t = "It is வீட்டு எண் 172, மெயின் ரோடு, திருச்சி - 620396."
    sp = Detector(use_rules=True, use_model=False).detect_calls([[{"speaker": "Customer", "text": t}]])[0][0]
    assert [t[s["start"]:s["end"]] for s in sp if s["label"] == "ADDRESS"] == ["வீட்டு எண் 172, மெயின் ரோடு, திருச்சி - 620396"], sp


def test_pincode_in_native_digits_and_never_a_leading_zero():
    assert address("at फ्लॅट १३२, हडपसर, नाशिक - ४२२४३० now") == ["फ्लॅट १३२, हडपसर, नाशिक - ४२२४३०"]
    assert address("at 93, Park Street, Delhi 010001") == []            # a pincode never starts with 0


# ---- an address must not run left into the other fields of a record (found by the A/B run: an amount 'Rs. 7,200' and a due date were swallowed)
def test_address_in_a_record_starts_after_the_field_label():
    t = 'overdue Rs. 7,200, registered residence: #448, Krishna Kunj, Indira Nagar, opp. Bharat School, Lucknow, Uttar Pradesh - 226154, please draft a notice'
    assert address(t) == ["#448, Krishna Kunj, Indira Nagar, opp. Bharat School, Lucknow, Uttar Pradesh - 226154"]


def test_amount_and_due_date_before_the_address_are_not_swallowed():
    t = "EMI Rs. 4,652 due 1st\nregistered residence: #732, Green Valley Residency, Nehru Road, Thane West, opp. Shiva School, Maharashtra 411011, please draft a polite visit notice"
    assert address(t) == ["#732, Green Valley Residency, Nehru Road, Thane West, opp. Shiva School, Maharashtra 411011"]


def test_json_style_value():
    assert address('{"name": "x", "address": "12, Main Road, Pune 411001"}') == ["12, Main Road, Pune 411001"]


def test_thousands_separator_comma_is_not_a_part_separator():
    assert address("I owe Rs. 4,652 and I live at 93, Park Street, Delhi 110001") == ["93, Park Street, Delhi 110001"]
    assert address("I owe 4,652 and I live at 93, Park Street, Delhi 110001") == ["93, Park Street, Delhi 110001"]


def test_kannada_flat_spelling_used_in_the_corpus():
    assert address("ಫ್ಲ್ಯಾಟ್ 674, ಶ್ರೀ ಕಾಂಪ್ಲೆಕ್ಸ್, ಸ್ಟೇಷನ್ ರಸ್ತೆ, ಬೆಂಗಳೂರು - 560834") == ["ಫ್ಲ್ಯಾಟ್ 674, ಶ್ರೀ ಕಾಂಪ್ಲೆಕ್ಸ್, ಸ್ಟೇಷನ್ ರಸ್ತೆ, ಬೆಂಗಳೂರು - 560834"]


# ---- found by the independent review
def test_pincode_after_a_label_colon_keeps_its_address():
    for t in ("My address is 12, Main Road, Koramangala, Bengaluru, Pincode: 560034", "Flat 5, Sai Apartments, MG Road, Pune, PIN: 411001.",
              "Address: 12, Main Road, Pune\nPincode: 411001", "पता: 12, मेन रोड, पुणे, पिन कोड: 411001"):
        assert address(t), t
    assert address("My address is 12, Main Road, Koramangala, Bengaluru, Pincode: 560034") == ["12, Main Road, Koramangala, Bengaluru, Pincode: 560034"]


def test_compact_address_without_spaces_after_commas():
    assert address("No.14,12th Main,Koramangala,Bengaluru,560034") == ["No.14,12th Main,Koramangala,Bengaluru,560034"]
    assert address("93,4th Cross, Delhi 110833") == ["93,4th Cross, Delhi 110833"]
    assert address("Plot 3,2nd Street, Anna Nagar, Chennai 600040") == ["Plot 3,2nd Street, Anna Nagar, Chennai 600040"]


def test_lakh_and_thousands_commas_still_do_not_split():
    assert address("I owe 1,00,000 and I live at 93, Park Street, Delhi 110001") == ["93, Park Street, Delhi 110001"]


def test_a_pan_upi_or_loan_id_before_the_address_is_not_taken_for_a_house_number():
    for lead in ("My PAN is ABCDE1234F, my address is", "Loan HL-123456, I live at", "UPI ram123@sbi, I live at", "card XXXX1234, I live at"):
        a = address(lead + " 12, Main Road, Pune 411001")
        assert a and a[0].endswith("12, Main Road, Pune 411001") and not any(x in a[0] for x in ("ABCDE", "HL-1", "ram123", "XXXX")), (lead, a)


def test_sentence_ending_in_rs_or_a_word_like_no_is_a_sentence_end():
    assert address("Okay, I will pay 5000 Rs. My address is 12, Main Road, Pune 411001") == ["12, Main Road, Pune 411001"]
    assert address("Sure sir, one sec. It's 12, Main Road, Pune 411001") == ["12, Main Road, Pune 411001"]


def test_previous_line_amount_or_date_is_not_swallowed_in_a_multi_line_record():
    assert address("Ramesh Kumar\nRs. 7200\n12, Main Road, Pune 411001") == ["12, Main Road, Pune 411001"]
    assert address("Name Ramesh\nEMI due 5th\n12, Main Road, Pune 411001") == ["12, Main Road, Pune 411001"]
    assert address("Ramesh\tRs. 7200\t12, Main Road, Pune 411001") == ["12, Main Road, Pune 411001"]


def test_danda_ends_a_sentence():
    assert address("जी, मैं 5000 रुपये भुगतान करूँगा। मेरा पता 12, मेन रोड, पुणे - 411001") == ["12, मेन रोड, पुणे - 411001"]


def test_many_pincodes_in_a_long_text_stay_fast():
    import time
    t = "flat 5, Pune 411001. " * 2000
    t0 = time.perf_counter(); address(t); dt = time.perf_counter() - t0
    assert dt < 1.0, dt


def test_no_address_without_a_pincode_and_commas():
    assert address("my pincode is 400001") == [] and address("No. 5 is my house number") == []


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
