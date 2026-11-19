# Layer 2 spec: requirements checklist (condensed from the architecture baseline given by the project owner)

## Purpose / trust boundaries
- Accept PII spans already detected by Layer 1; replace each with a random, scoped token; encrypt + persist the original; protected lookup via keyed blind index; rehydrate only for authenticated+authorised callers; support key revocation/deletion/erasure orchestrated by Layer 6; give stable ids/metadata for lineage (L5) and audit (L7).
- NOT responsible for: detecting PII, deciding legal basis, orchestrating deletion across every store, sending prompts to an LLM.
- Layer 3 must never get master keys / KEKs / DEKs. Layer 6 gets deletion-only capability: it must NOT be able to retrieve plaintext. Layer 5 gets stable references, not plaintext. Layer 7 gets safe metadata, never raw PII or secrets.
- Use authenticated service identities + least privilege. NEVER trust a caller-supplied role / is_authorised field as proof of identity.
- Modular component inside the existing FastAPI app, with clear module boundaries.

## Tokens (tokeniser)
- Cryptographically random opaque ids; readable placeholder like <PHONE_1> is a presentation token whose mapping points to a random internal vault_id/token_id.
- Reuse a mapping only within the same borrower + conversation + PII type + purpose scope. Same phone in a different conversation gets a DIFFERENT token by default. Never use cross-borrower lookup to discover shared values.
- No PII, fragments, hashes or predictable ids inside tokens.

## Encryption
- AES-256-GCM. Fresh unique 12-byte nonce per encryption under a key; never reuse a nonce with the same key.
- AAD built from immutable record context (borrower | conversation | vault_id | pii_type); reject decryption if authentication fails or AAD differs.
- Never log plaintext, keys, nonce/ciphertext combos that expose secrets, or decrypted exception payloads. No custom crypto, no custom key wrapping.

## Keys
- master secret (outside source control) -> HKDF-SHA256 -> KEK and blind-index key, independent (distinct info labels, documented salt/version). Never reuse the AES key as the HMAC key.
- Random per-borrower DEK wrapped by the KEK; store only wrapped DEKs + metadata; never persist plaintext DEK/KEK/master in DB rows or logs. Track key ids and versions for rotation. Revoke and invalidate cached key material on destruction. No key caching initially.
- Prototype may use a protected local secret (not in git); production needs KMS/HSM.

## Blind index
- Type-appropriate normalisation; canonical, unambiguous (length-prefixed) encoding of borrower, conversation, purpose, type, normalised value; HMAC-SHA256 with the dedicated blind-index key; query by scoped digest + key version; reuse a mapping only if active and exactly in scope.
- Never a plain hash. Do not return digests to gateway/LLM. Support index key versions (dual-read/dual-write during migration). Avoid cross-borrower/global indexes.

## Rehydration (deny by default)
- Authenticate the calling service; authorise the operation; validate purpose + Layer 4 permission decision; verify borrower/conversation/token scope; confirm mapping and key active and not pending erasure; unwrap DEK; decrypt with right nonce+AAD; return only requested value to authorised destination; never write plaintext to logs/traces/metrics/exceptions/audit.
- If any check fails -> deny; never fall back to stored plaintext or bypass a failed permission service. Prefer tokenised responses.

## Key lifecycle / erasure
- Track ACTIVE/REVOKED/DESTROYED; prevent use of keys of a borrower under erasure; rotation + rewrapping; invalidate caches; coordinate with Layer 6.
- A key is not "destroyed" merely because one DB row was removed: identify wrapped-key copies, backups, replicas, caches and how they are retired.
- Erasure flow: mark erasure-pending; block new writes and rehydration; find records, mappings, blind indexes; delete them; destroy/revoke DEK; invalidate caches; return result to Layer 6. Idempotent jobs + retries. Job states PENDING / IN_PROGRESS / COMPLETED / PARTIAL_FAILURE. NEVER report COMPLETED before vault-side deletion and key actions succeeded; never claim completion if a dependency is unavailable. Destroying a DEK does not delete downstream copies.

## Database
- Tables: borrower_keys, vault_records, blind_indexes, token_mappings, vault_operations. No raw PII as borrower_id. Parameterised queries. Commit record + mapping + index in ONE transaction; never return a token as stored if the transaction failed. Uniqueness constraints + conflict handling for concurrent duplicates. Idempotency for retries. Restrict db roles. Encrypt backups.

## API
- POST /vault/tokenize, POST /vault/rehydrate, GET /vault/records/{vault_id} (safe metadata only), POST /vault/erase, POST /vault/rotate-key, GET /vault/health (no secrets).
- Tokenise response must not include original PII, ciphertext, DEK, KEK, master secret or blind-index key. Caller identity comes from service authentication, not a JSON field.

## Security / logging
- Never log: PII, decrypted values, secrets, wrapped keys, blind-index keys, full request bodies. Safe metadata only (request ids, op types, status, latency, pseudonymous refs, non-sensitive error codes).
- Detector/vault unavailable => gateway must not send raw content to the LLM (fail closed). Permission service unavailable => rehydration denied.

## Tests the spec expects
encrypt/decrypt; same value twice -> different nonce; tamper ciphertext fails; change AAD fails; wrong key fails; same PII same scope reuses token; same PII other conversation different token; no cross-borrower mapping; invalid token denied; wrong purpose denied; permission service unavailable denied; destroy/revoke key makes rehydration impossible; database unavailable -> tokenisation fails; duplicate request no inconsistent mapping; concurrent same-value requests -> one mapping; erasure retry idempotent; log inspection shows no raw PII or key material; erasure with unavailable store -> partial/pending not false completion; key rotation and version migration; backup/replica key retention; expired/revoked mappings; token collision handling; malformed spans; large messages; latency p50/p95/p99.

## Cautions
- Layer 2 does not by itself guarantee DPDP compliance. Do not claim the 50 ms target met until benchmarked. Do not treat deletion of a wrapped-key row as proof every usable key copy is destroyed. Do not store original PII indefinitely when rehydration is no longer needed.
