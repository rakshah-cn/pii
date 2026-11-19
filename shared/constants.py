"""ONE source of truth for the processing purposes the whole project recognises.

Layer 2 (vault: which purposes may tokenise / rehydrate), Layer 3 (gateway consent aliases) and Layer 4 (consent policy tables) all import from here. Before this file each layer
had its own list and they drifted: Layer 4 allowed `dispute`, Layer 2 did not, and the gateway blocked every `dispute` message with `purpose_not_permitted`.
To add a purpose: add it HERE, then give it a rule in layer4_consent/policy.py (tests fail if a policy rule names a purpose that is not listed here)."""

PURPOSES = ("service", "collections", "compliance", "dispute", "analytics", "model_improvement")

# alternative spellings accepted everywhere and mapped to the canonical name (the organisers' brief says 'collection')
PURPOSE_ALIASES = {"collection": "collections"}
