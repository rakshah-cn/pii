"""One test that runs the whole stack: gateway -> vault -> consent-guarded stores -> erasure -> audit.
   .venv/bin/python -m layer8_demo.test_end_to_end"""
import io
import contextlib

from layer8_demo import end_to_end as e2e


def test_whole_stack_ends_clean_and_shares_name_token():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        clean = e2e.main()
    out = buf.getvalue()
    assert clean, out[-600:]
    assert "<PHONE_1>" in out and out.count("<PHONE_2>") == 0, "a number said in two pieces must share ONE token"
    assert "This is <PERSON_1> here" in out, "a later name part must reuse the full name's token"
    assert "written=False  (purpose_not_allowed:service->eval_sets)" in out, "consent policy must refuse the eval write"
    assert "ERASED_VERIFIED" in out and "key destroyed: True" in out and "chain: INTACT" in out


if __name__ == "__main__":
    test_whole_stack_ends_clean_and_shares_name_token()
    print("PASS test_whole_stack_ends_clean_and_shares_name_token")
