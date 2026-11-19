# PII Vault: the application (Layer 8 front end)

Run:  `.venv/bin/python layer8_demo/server.py`  (or `-m layer8_demo.server`), wait for READY, open http://127.0.0.1:8808.

A collections workspace over the real layers. One shared workspace (one vault with a key per borrower, one audit chain, one lineage registry, one set of mock stores, one gateway) with several borrower cases (4 sample cases are loaded by running real messages through the pipeline).

Workspace: Conversations (case inbox, thread with Protected/Original view, per-message protection trail, Borrower and Protection panels) · Borrowers (profile, where the data lives, lineage, activity) · Privacy requests (DPDP erasure workflow ending in a certificate) · Audit trail (activity feed over the L7 chain, raw records, verify, tamper test, export).
Governance: Consent policy (L4) · Data inventory (L5) · Architecture (live diagram of L0-L8 with test status).
Engineering: Detection lab (L1) · Vault inspector (L2) · Test data (L0) · Scenario (L8) · Test suites.

Every id is a link; `/` focuses search; "Layer details" gives evidence, tests and limits per layer.

Notes: stores are MOCK files under data/stores/ui/<workspace>/. The LLM is a stub unless the sidebar switch is on (tokenised text only goes to Bedrock Nova Pro; a live model can invent facts).
Erasing one borrower leaves the others intact; the demo server also drops that borrower's raw text from its own memory. "Detector off (test)" disables Layer 1 so the audit net is exercised; it cannot catch names.
/api/message returns the raw typed text only so the page can offer the "Original text" view.
