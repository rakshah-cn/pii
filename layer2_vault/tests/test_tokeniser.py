"""Scope behaviour of tokenisation: reuse inside the exact scope, separation everywhere else; masked output and storage hygiene."""
import os
import tempfile

from layer2_vault.core.errors import ValidationFailed, VaultDenied
from layer2_vault.core.tokeniser import PLACEHOLDER_RE
from layer2_vault.schemas.vault_schemas import DetectedEntity, TokenizeRequest
from layer2_vault.tests._util import GW, ER, expect, make, run, tok_req, who, reh_req

TEXT = "My name is Rahul Sharma. My phone number is 98450 12345."


def test_masks_all_spans_and_returns_safe_mappings():
    s, v = make(); r = v.tokenizer.tokenize(tok_req(TEXT, [("NAME", "Rahul Sharma"), ("PHONE", "98450 12345")]), who(s, GW))
    assert r.masked_text == "My name is <NAME_1>. My phone number is <PHONE_1>."
    assert [m.pii_type for m in r.mappings] == ["NAME", "PHONE"] and all(m.vault_id.startswith("v_") for m in r.mappings)
    assert "98450" not in r.model_dump_json() and "Rahul" not in r.masked_text
def test_same_pii_same_scope_reuses_the_token_even_in_a_different_format():
    s, v = make(); g = who(s, GW)
    a = v.tokenizer.tokenize(tok_req("call 98450 12345", [("PHONE", "98450 12345")], rid="a"), g)
    b = v.tokenizer.tokenize(tok_req("or +91 9845012345 please", [("PHONE", "+91 9845012345")], rid="b"), g)
    assert a.mappings[0].token == b.mappings[0].token == "<PHONE_1>" and a.mappings[0].vault_id == b.mappings[0].vault_id
    assert len(v.tokenizer.list_for_borrower("B1", who(s, ER))) == 1
def test_repeated_value_inside_one_message_gets_one_token():
    s, v = make(); t = "9845012345 and again 9845012345"
    r = v.tokenizer.tokenize(tok_req(t, [("PHONE", "9845012345"), ("PHONE", "9845012345")]), who(s, GW))
    assert r.masked_text == "<PHONE_1> and again <PHONE_1>"
def test_same_pii_other_conversation_gets_a_different_vault_record_and_its_own_numbering():
    s, v = make(); g = who(s, GW)
    a = v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], conv="C1"), g).mappings[0]
    b = v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], conv="C2"), g).mappings[0]
    assert a.vault_id != b.vault_id and a.token == b.token == "<PHONE_1>"          # placeholder is per conversation; the record is not shared
def test_same_pii_other_purpose_is_not_linked():
    s, v = make(); g = who(s, GW)
    a = v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], purpose="service"), g).mappings[0]
    b = v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], purpose="collections"), g).mappings[0]
    assert a.vault_id != b.vault_id
def test_different_borrowers_never_share_a_mapping_or_key():
    s, v = make(); g = who(s, GW)
    a = v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], borrower="B1", conv="C1"), g).mappings[0]
    b = v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], borrower="B2", conv="C1"), g).mappings[0]
    assert a.vault_id != b.vault_id and b.token == "<PHONE_2>"                       # same conversation id, different borrower: separate record, no linking
    keys = {r["key_id"] for b_ in ("B1", "B2") for r in v.tokenizer.list_for_borrower(b_, who(s, ER))}; assert len(keys) == 2
def test_numbering_is_per_type_and_in_order():
    s, v = make(); t = "a 9845012345 b 9811111111 c Ramesh"
    r = v.tokenizer.tokenize(tok_req(t, [("PHONE", "9845012345"), ("PHONE", "9811111111"), ("NAME", "Ramesh")]), who(s, GW))
    assert r.masked_text == "a <PHONE_1> b <PHONE_2> c <NAME_1>"
def test_joined_entity_pieces_share_one_token_and_store_the_joined_value():
    s, v = make(); g = who(s, GW); t = "nine eight four five zero and one two three four five"
    ents = [DetectedEntity(type="PHONE", value="nine eight four five zero", start=0, end=25, entity="e1", joined_value="9845012345"),
            DetectedEntity(type="PHONE", value="one two three four five", start=30, end=53, entity="e1", joined_value="9845012345")]
    r = v.tokenizer.tokenize(TokenizeRequest(request_id="r", borrower_id="B1", conversation_id="C1", purpose_id="collections", text=t, detected_entities=ents), g)
    assert r.masked_text == "<PHONE_1> and <PHONE_1>"
    assert v.rehydration.rehydrate(reh_req(["<PHONE_1>"]), g).values == {"<PHONE_1>": "9845012345"}
def test_tokens_contain_nothing_from_the_value_and_have_the_placeholder_shape():
    s, v = make(); r = v.tokenizer.tokenize(tok_req("98450 12345 Rahul", [("PHONE", "98450 12345"), ("NAME", "Rahul")]), who(s, GW))
    for m in r.mappings:
        assert PLACEHOLDER_RE.match(m.token) and "98450" not in m.token and "12345" not in m.token and "Rahul" not in m.vault_id
def test_large_message_with_many_spans():
    s, v = make(); vals = [f"98{i:08d}" for i in range(120)]; t = " | ".join(vals)
    r = v.tokenizer.tokenize(tok_req(t, [("PHONE", x) for x in vals]), who(s, GW))
    assert r.masked_text.count("<PHONE_") == 120 and not any(x in r.masked_text for x in vals) and r.mappings[-1].token == "<PHONE_120>"
def test_no_plaintext_and_no_key_on_disk():
    with tempfile.TemporaryDirectory() as d:
        master = os.urandom(32); s, v = make(master_secrets={1: master}, db_url=f"sqlite:///{d}/vault.db")
        v.tokenizer.tokenize(tok_req("Rahul Sharma 98450 12345 ABCPK1234L", [("NAME", "Rahul Sharma"), ("PHONE", "98450 12345"), ("PAN", "ABCPK1234L")]), who(s, GW))
        v.ctx.db._conn.execute("PRAGMA wal_checkpoint(FULL)")
        blob = b"".join(open(os.path.join(d, f), "rb").read() for f in os.listdir(d))
        for needle in (b"Rahul", b"Sharma", b"98450", b"9845012345", b"ABCPK1234L", master, master.hex().encode()):
            assert needle not in blob, needle
def test_invalid_spans_are_rejected_before_anything_is_stored():
    for bad in ({"start": 0, "end": 5, "value": "98450"}, {"start": 3, "end": 50, "value": "xx"}, {"start": 5, "end": 5, "value": "x"}):
        try:
            TokenizeRequest(request_id="r", borrower_id="B", conversation_id="C", purpose_id="service", text="call 98450 12345", detected_entities=[DetectedEntity(type="PHONE", **bad)]); raise AssertionError("accepted")
        except (ValueError, AssertionError) as e:
            assert "accepted" not in str(e)
def test_overlapping_spans_rejected():
    try:
        TokenizeRequest(request_id="r", borrower_id="B", conversation_id="C", purpose_id="service", text="0123456789",
                        detected_entities=[DetectedEntity(type="PHONE", value="01234", start=0, end=5), DetectedEntity(type="ACCOUNT", value="34567", start=3, end=8)]); raise AssertionError("accepted")
    except ValueError:
        pass
def test_unknown_purpose_is_refused_and_leaves_no_state():
    s, v = make(); expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")], purpose="marketing"), who(s, GW)))
    assert v.tokenizer.list_for_borrower("B1", who(s, ER)) == []
def test_caller_without_the_capability_is_refused():
    s, v = make(); expect(VaultDenied, lambda: v.tokenizer.tokenize(tok_req("9845012345", [("PHONE", "9845012345")]), who(s, ER)), "forbidden")
def test_oversized_request_refused():
    s, v = make(max_text_len=20); expect(ValidationFailed, lambda: v.tokenizer.tokenize(tok_req("x" * 30 + " 9845012345", [("PHONE", "9845012345")]), who(s, GW)))

if __name__ == "__main__":
    run(globals())
