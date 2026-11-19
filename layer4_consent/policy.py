"""Which purposes may feed which stores. DEFAULT DENY: an unknown store or purpose is rejected.

ASSUMPTION: this table is our reading of purpose limitation, not legal advice. Edit it with legal.
Each entry: store -> purpose -> rule, where rule is 'allow' or 'explicit_consent' (needs a consent_id and explicit_consent=True).
"""
POLICY = {
    "logs": {"service": "allow", "collections": "allow", "compliance": "allow", "dispute": "allow"},
    "vector_store": {"service": "allow", "collections": "allow", "model_improvement": "explicit_consent"},
    "eval_sets": {"model_improvement": "explicit_consent"},      # 'service' data may NOT feed eval sets
}


from shared.constants import PURPOSES, PURPOSE_ALIASES as ALIASES      # the brief says 'collection'; the canonical name is 'collections' (shared/constants.py)


def check_consistency():
    """Every purpose a rule names must be a project purpose (shared/constants.py): a rule for an unknown purpose would be a silent drift between layers."""
    bad = sorted({p for rules in POLICY.values() for p in rules} - set(PURPOSES))
    if bad:
        raise ValueError(f"policy.py names purposes that are not in shared/constants.py: {bad}")


check_consistency()


def decide(store, tag):
    """Return (ok, reason)."""
    rules = POLICY.get(store)
    if rules is None:
        return False, f"unknown_store:{store}"
    if tag is None:
        return False, "missing_consent_tag"
    purpose = tag.get("purpose") if isinstance(tag, dict) else tag.purpose
    purpose = ALIASES.get(purpose, purpose)
    rule = rules.get(purpose)
    if rule is None:
        return False, f"purpose_not_allowed:{purpose}->{store}"
    if rule == "explicit_consent":
        cid = tag.get("consent_id") if isinstance(tag, dict) else tag.consent_id
        ex = tag.get("explicit_consent") if isinstance(tag, dict) else tag.explicit_consent
        if not (cid and ex):
            return False, f"explicit_consent_required:{purpose}->{store}"
    return True, "ok"
