# Gateway scale-out with non-sticky routing

Machine: x86_64, 16 CPUs. 160 conversations (2099 messages), synthetic. P gateway PROCESSES (own detector, own vault pool), one shared PostgreSQL vault, each TURN sent to whichever process is free (so a conversation's turns spread over processes). 2P client threads. LLM stubbed. 'pre-LLM' = entry -> LLM boundary (50 ms budget).

| processes | model threads each | msgs/s | pre-LLM p50 | p95 | p99 | over 50 ms | wall p50 | wall p95 | conversations that moved between processes | blocked convs | output fingerprint |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 16 | 19 | 37.0 | 62.2 | 91.4 | 396 of 2099 | 48.6 | 73.1 | 0 of 160 | 0 | 664499e30a6101db |
| 2 | 8 | 37 | 34.0 | 60.7 | 80.3 | 262 of 2099 | 47.0 | 75.4 | 132 of 160 | 0 | 664499e30a6101db |
| 4 | 4 | 67 | 29.9 | 51.8 | 65.5 | 133 of 2099 | 37.7 | 61.5 | 160 of 160 | 0 | 664499e30a6101db |
| 8 | 2 | 75 | 42.1 | 78.2 | 117.0 | 610 of 2099 | 49.9 | 87.2 | 160 of 160 | 0 | 664499e30a6101db |

**Correctness:** 1 distinct output fingerprint(s) across 1, 2, 4 and 8 processes (1 = every message masked byte-for-byte the same however it was routed).
