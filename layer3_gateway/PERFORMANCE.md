# Layer 3 gateway: latency to the LLM boundary

Machine: x86_64, 16 CPUs. Detector = the gateway's own configuration (rules + spoken engine + Chitragupta int8, model on the newest turn). Synthetic dev conversations, fake values. LLM stubbed and not timed. Budget: p95 < 50 ms.

## File SQLite (WAL)

1711 messages from 120 conversations; 1708 reached the LLM boundary (clean audit).

| step | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|
| **entry -> LLM boundary (the 50 ms budget)** | 15.19 | 21.08 | 23.61 | 27.30 |
| detect (Layer 1) | 13.27 | 18.76 | 21.32 | 23.82 |
| tokenise (Layer 2 batch) | 0.01 | 2.13 | 2.76 | 5.54 |
| audit + consent + overhead (remainder) | 1.35 | 3.25 | 3.89 | 4.62 |
| whole request incl. stub LLM + rehydrate | 18.52 | 25.58 | 29.57 | 34.62 |

- messages over 50 ms before the LLM: 0 of 1708 (0.0%)
- outcomes: {'ok': 1708, 'blocked': 3}
- block reasons: {'over_masking': 3}
- blocked by the 85% utility rule: 3 of 1711 messages (0.2%); lengths of those messages: [17, 35, 35]
