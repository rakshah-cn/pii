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

## v5: roles, maker-checker, audit envelope (from the CreditNirvana KB's audit gaps)
- Sign in as one of four demo users from the sidebar (agent, privacy officer, DPO, auditor). Roles are enforced server-side (X-User header); a refusal is itself audited.
- Erasure is a workflow: initiate (agent / privacy officer) -> recommend (privacy officer) -> approve (DPO) -> the erasure agent runs. Approver must differ from initiator and recommender.
- Audit records (audit.v2) carry actor + role, source IP, session, W3C trace id, explicit UTC time, action, resource, outcome. Reading, verifying and exporting the log are audited. Export returns a SHA-256.
- Only the agent role sees conversations; other roles get the protected form (raw text is withheld by the server).
- Architecture picture: docs/te03_architecture.jpg

## Story mode
Sidebar button "Follow the story": a narrator walks one borrower (Ramesh) through the real app in 18 short steps / 6 chapters (the call, when something slips, consent, being forgotten, the proof, the whole system).
Each step performs real actions (it creates a new case, sends messages, switches user Asha -> Rohit -> Meera -> Vikram, runs the approval chain and the erasure), spotlights the real element, and tags the layer(s) shown. Back/Next or arrow keys; Esc/x exits and restores your user.
Code: web/story.js (steps are plain data at the top).

## v6: production shell
Top bar (breadcrumbs, search, Guided tour, help, notifications for the signed-in role, user menu). Sidebar is navigation only. Demo scaffolding (simulation bar, borrower's screen toggle, engineering screens) is OFF by default: user menu > "Demo and engineering tools". Cases carry synthetic loan-account context (product, loan ref, days past due, outstanding, language, channel, assignee). Each message shows one quiet "Protected" line; click it for the per-layer view.

## v7: the layer console (`dpdp-privacy-console.html`, served at `/console`)
A second front end, one screen per layer, in the visual design from `dpdp-privacy-console.html`. Open **http://127.0.0.1:8808/console** (the file is served by `server.py`; opened as a plain file it shows an "offline" banner because the browser blocks the calls).
Every number comes from the running layers; nothing is simulated in the page. Views: L3·L1 gateway (send a message, or "Start live stream" = synthetic dev-corpus calls through the real gateway, max 3 calls per press), L2 vault (ciphertext table, who-can-read-a-token calls), L4 firewall (policy matrix, "Try a write", write_guard feed from the audit chain), L5 lineage (graph of one case, unregistered copies in amber), L6 erasure (real maker-checker chain then the agent; independent file scan; ablation table), L7 ledger (per-record recomputed hashes, verify, tamper drill on a COPY), L0·L1 detection quality (scoreboard parsed from `LAYER1_REPORT.md`, detector lab), L9 voice (only when started with `layer9_voice.run`; otherwise the view says so).
The page acts as different demo users per call (agent for messages, privacy officer / DPO for the privacy chain and "Try a write", auditor for audit reads), exactly like the roles in v5.
New read-only endpoints in `server.py`: `/api/console/overview` (health, latency p50/p95, idle-memory TTL, counts), `/api/console/feed` (consent decisions from the chain), `/api/console/ablation` (parsed from `layer6_erasure/LAYER6_CONTEXT.md` section 5, a recorded run), `/api/console/sample` (one dev-corpus call as turns); `/api/lineage` now also returns `lookup_ms`.
What the old mock page showed that is NOT in the console because it does not exist in the code: a consent registry with millions of grants, a "locked / quarantine" purpose state, gateway pods and CPU, 1,200-conversation raw-vs-masked LLM accuracy (never measured), an hourly-anchored ledger head. The Firewall's third counter is "allowed on an unverified consent claim" (no registry is connected to `guarded_write` here).
