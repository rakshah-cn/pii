# Layer 2 (vault + tokenisation): final report

Synthetic fake values only. Run: `python -m layer2_vault.final_report`.

| check | result | detail |
|---|---|---|
| 1. nothing readable on disk (rows, key store, audit log) | PASS | 44 values stored; found in rows/keys/audit files: 0; files: ['v.db', 'v.db.keys', 'v.db.audit.jsonl'] |
| 2. tokens are placeholders, not derived from the value | PASS | tokens look like ['<PHONE_10>', '<PHONE_11>', '<PHONE_12>']; value digits appear in a token: 0 |
| 3. same value in the same conversation gets one token | PASS | same value (spacing differs) -> same token: True; other conversation -> <PHONE_1>; other borrower -> <PHONE_2>; rows for X: 2 |
| 4. round trip works and roles are enforced | PASS | rehydration role restores the value: True; an unknown role is refused: True |
| 5. wrong master key cannot read the data | PASS | opening with a different master key is refused (cannot unwrap key (wrong master key, rotated maste) |
| 6. crypto-shred destroys the borrower's data, even against a backup of the rows | PASS | after destroy_borrower: borrower's values unreadable True, key present False, other borrower intact True, {'entries_deleted': 8, 'key_destroyed': True}; restoring only the old rows file recovers nothing: True. KNOWN LIMIT: restoring BOTH old files with the old master key DOES decrypt (True); rotate_master_key after a shred closes this |
| 7. after shred + master-key rotation even a full old backup is useless | PASS | pre-shred backup of BOTH files is refused after rotation (cannot unwrap key (wrong master key, rot) |
| 8. access log is hash-chained, tamper-evident, and holds no raw values | PASS | 47 audit events; chain intact True; after editing one event verify says False; raw values in the audit log: False |
| 9. the 23-test suite | PASS | existing test suite: 24 passed |

## Speed

put (in memory, 5000 values): p50 0.03 ms, p99 0.05 ms.  get (100 reads): p50 0.02 ms, p99 0.05 ms; 0 reads refused by the per-process rate limit

## Known limits

- the master key lives in an environment file (no KMS/HSM); rate limits are per process
- a pre-shred backup of BOTH files plus the OLD master key can still decrypt: rotate the master key after a shred
- the vault is single-process; data is in memory unless a path is given (encrypted persistence rewrites the file on each change, so persisted writes are much slower than the in-memory figure)
- tokens are scoped per conversation or borrower, so the same number in two unrelated conversations gets different tokens
- a name-part token (for 'Kumar') rehydrates to the full name it was attached to
- masking plus a vault is one technical safeguard, not DPDP compliance by itself
