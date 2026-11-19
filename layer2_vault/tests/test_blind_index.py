import os

from layer2_vault.core.blind_index import blind_index as BI, normalise
from layer2_vault.tests._util import run

K = os.urandom(32)


def test_phone_formats_normalise_together():
    assert len({normalise("PHONE", v) for v in ("98450 12345", "+91 98450-12345", "09845012345", "9845012345")}) == 1
def test_indic_digits_normalise():
    assert normalise("PHONE", "೯೮೪೫೦ ೧೨೩೪೫") == "9845012345"
def test_codes_are_case_and_separator_insensitive():
    assert normalise("PAN", "abcpk 1234-l") == "ABCPK1234L" == normalise("PAN", "ABCPK1234L")
def test_name_normalisation_ignores_case_spacing_honorific():
    assert normalise("NAME", "Mr.  Rahul   SHARMA") == normalise("NAME", "rahul sharma")
def test_same_scope_same_digest_different_format():
    assert BI(K, "B1", "C1", "p", "PHONE", "98450 12345") == BI(K, "B1", "C1", "P", "PHONE", "+919845012345")
def test_each_scope_field_changes_the_digest():
    base = BI(K, "B1", "C1", "service", "PHONE", "9845012345")
    for args in (("B2", "C1", "service", "PHONE"), ("B1", "C2", "service", "PHONE"), ("B1", "C1", "collections", "PHONE"), ("B1", "C1", "service", "ACCOUNT")):
        assert BI(K, *args, "9845012345") != base
def test_different_key_different_digest_so_it_is_keyed():
    assert BI(K, "B1", "C1", "p", "PHONE", "9845012345") != BI(os.urandom(32), "B1", "C1", "p", "PHONE", "9845012345")
def test_digest_is_not_a_plain_hash_of_the_value():
    import hashlib
    d = BI(K, "B1", "C1", "p", "PHONE", "9845012345")
    assert d != hashlib.sha256(b"9845012345").hexdigest() and "9845012345" not in d
def test_field_boundary_ambiguity_cannot_collide():
    assert BI(K, "B1", "C1x", "p", "PHONE", "9845012345") != BI(K, "B1x", "C1", "p", "PHONE", "9845012345")

def test_spoken_and_mixed_values_with_no_digit_form_never_collide():
    a, b = "nine eight four five", "zero one two three four five"
    assert normalise("PHONE", a) != normalise("PHONE", b) and BI(K, "B", "C", "p", "PHONE", a) != BI(K, "B", "C", "p", "PHONE", b)
    assert BI(K, "B", "C", "p", "PHONE", "98450 twelve") != BI(K, "B", "C", "p", "PHONE", "98450 thirteen")
    assert BI(K, "B", "C", "p", "AADHAAR", "ek do teen") != BI(K, "B", "C", "p", "AADHAAR", "char paanch")
def test_spoken_codes_with_letters_do_not_collide_with_each_other():
    assert BI(K, "B", "C", "p", "PAN", "A B C P K one two") != BI(K, "B", "C", "p", "PAN", "A B C P K three four")
def test_a_value_is_always_distinguishable_from_a_different_value_of_the_same_type():
    import itertools
    vals = ["9845012345", "9845012346", "nine eight four five", "ek do teen char", "98450 12345 6", "+91 9845012345"]
    digs = {v: BI(K, "B", "C", "p", "PHONE", v) for v in vals}
    for a, b in itertools.combinations(vals, 2):
        same_phone = {normalise("PHONE", a), normalise("PHONE", b)}
        if len({normalise("PHONE", a), normalise("PHONE", b)}) == 2: assert digs[a] != digs[b]
    assert digs["9845012345"] == digs["+91 9845012345"]


if __name__ == "__main__":
    run(globals())
