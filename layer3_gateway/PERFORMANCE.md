# Layer 3 gateway: latency to the LLM boundary

Machine: x86_64, 16 CPUs. Detector = the gateway's own configuration (rules + spoken engine + Chitragupta int8, model on the newest turn). Synthetic dev conversations, fake values. LLM stubbed and not timed. Budget: p95 < 50 ms.

## File SQLite (WAL)

1711 messages from 120 conversations; 1710 reached the LLM boundary (clean audit).

| step | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|
| **entry -> LLM boundary (the 50 ms budget)** | 12.22 | 17.24 | 20.12 | 27.66 |
| detect (Layer 1) | 10.39 | 14.67 | 18.22 | 22.00 |
| tokenise (Layer 2 batch) | 0.01 | 2.06 | 2.99 | 4.88 |
| audit + consent + overhead (remainder) | 1.31 | 3.38 | 3.90 | 8.66 |
| whole request incl. stub LLM + rehydrate | 15.36 | 22.89 | 26.65 | 36.09 |

- messages over 50 ms before the LLM: 0 of 1710 (0.0%)
- outcomes: {'ok': 1710, 'blocked': 1}
- block reasons: {'over_masking': 1}
- blocked by the 85% utility rule: 1 of 1711 messages (0.1%); lengths of those messages: [44]

## PostgreSQL 16, pooled (local socket)

1711 messages from 120 conversations; 1710 reached the LLM boundary (clean audit).

| step | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|
| **entry -> LLM boundary (the 50 ms budget)** | 14.31 | 19.94 | 22.72 | 43.05 |
| detect (Layer 1) | 10.41 | 14.79 | 18.26 | 32.39 |
| tokenise (Layer 2 batch) | 0.01 | 3.64 | 5.41 | 8.32 |
| audit + consent + overhead (remainder) | 3.17 | 5.50 | 6.81 | 10.14 |
| whole request incl. stub LLM + rehydrate | 17.99 | 24.68 | 27.97 | 151.30 |

- messages over 50 ms before the LLM: 0 of 1710 (0.0%)
- outcomes: {'ok': 1710, 'blocked': 1}
- block reasons: {'over_masking': 1}
- blocked by the 85% utility rule: 1 of 1711 messages (0.1%); lengths of those messages: [44]
