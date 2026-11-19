"""One list of purposes for the whole project: Layer 2 and Layer 4 must agree (they drifted once: Layer 4 allowed 'dispute', the vault did not)."""
import os

from layer2_vault.compat import ModularVault
from layer2_vault.config.settings import Settings
from layer2_vault.tests._util import run
from shared.constants import PURPOSE_ALIASES, PURPOSES


def test_the_vault_accepts_every_shared_purpose_and_alias():
    allowed = Settings.for_testing().allowed_purposes
    assert set(PURPOSES) <= set(allowed) and set(PURPOSE_ALIASES) <= set(allowed)


def test_every_purpose_layer_4_allows_works_through_the_vault_end_to_end():
    from layer4_consent.policy import POLICY
    v = ModularVault(ephemeral=True)
    for store, rules in POLICY.items():
        for purpose in rules:
            tok = v.put(f"C-{store}-{purpose}", "PHONE", "9845012345", borrower_id=f"b-{store}-{purpose}", purpose=purpose)       # tokenise accepted ...
            assert v.get(f"C-{store}-{purpose}", tok) == "9845012345", (store, purpose)                                              # ... and rehydration policy covers it


def test_layer_4_cannot_name_a_purpose_the_project_does_not_know():
    import layer4_consent.policy as pol
    saved = pol.POLICY["logs"].copy()
    try:
        pol.POLICY["logs"]["marketing"] = "allow"
        try: pol.check_consistency(); raise AssertionError("drift must be detected")
        except ValueError as e: assert "marketing" in str(e)
    finally:
        pol.POLICY["logs"].clear(); pol.POLICY["logs"].update(saved)
    pol.check_consistency()


def test_layer_4_and_the_gateway_use_the_shared_lists():
    import layer4_consent.schema as sch
    from layer3_gateway import consent
    assert sch.PURPOSES is PURPOSES and set(PURPOSE_ALIASES) <= set(consent.ALIASES)
    for k, v in PURPOSE_ALIASES.items(): assert consent.ALIASES[k] == v


if __name__ == "__main__":
    run(globals())
