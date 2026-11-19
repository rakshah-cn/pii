# Gateway under concurrent load

Machine: x86_64, 16 CPUs. 240 conversations (3169 messages) from data/corpus_test.jsonl (synthetic), one gateway process, N client threads. Real detector with the model cache defeated (every message runs the model). LLM stubbed. 'pre-LLM' = entry -> LLM boundary (the 50 ms budget); 'wall' = time the caller waits per message incl. queueing.

## File SQLite (WAL)

| clients | msgs/s | pre-LLM p50 | p95 | p99 | wall p50 | p95 | p99 | max | pre-LLM > 50 ms | failed / blocked | exceptions | leaks (>=50%) | output fingerprint |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 66 | 12.4 | 18.7 | 23.5 | 14.4 | 22.0 | 26.5 | 42.7 | 0 of 3169 | 0 | 0 | 14/459 | a450bad04af820f1 |
| 2 | 96 | 15.9 | 24.3 | 29.2 | 20.0 | 29.4 | 36.0 | 180.0 | 1 of 3169 | 0 | 0 | 14/459 | a450bad04af820f1 |
| 4 | 123 | 23.6 | 36.6 | 44.1 | 31.5 | 46.3 | 55.0 | 69.8 | 7 of 3169 | 0 | 0 | 14/459 | a450bad04af820f1 |
| 8 | 122 | 39.9 | 67.4 | 78.0 | 62.0 | 91.8 | 104.9 | 147.9 | 606 of 3169 | 0 | 0 | 14/459 | a450bad04af820f1 |
| 16 | 120 | 71.6 | 129.7 | 146.9 | 124.0 | 186.3 | 208.8 | 354.2 | 3056 of 3169 | 0 | 0 | 14/459 | a450bad04af820f1 |

## PostgreSQL 16, pooled (min 4 / max 16)

| clients | msgs/s | pre-LLM p50 | p95 | p99 | wall p50 | p95 | p99 | max | pre-LLM > 50 ms | failed / blocked | exceptions | leaks (>=50%) | output fingerprint |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 59 | 14.0 | 21.7 | 27.1 | 16.2 | 24.2 | 31.6 | 74.4 | 1 of 3169 | 0 | 0 | 14/459 | a450bad04af820f1 |
| 2 | 92 | 17.2 | 27.5 | 34.3 | 20.6 | 32.1 | 38.6 | 50.7 | 0 of 3169 | 0 | 0 | 14/459 | a450bad04af820f1 |
| 4 | 115 | 24.9 | 48.4 | 61.7 | 31.9 | 55.4 | 71.5 | 219.7 | 138 of 3169 | 0 | 0 | 14/459 | a450bad04af820f1 |
| 8 | 114 | 43.1 | 119.6 | 145.7 | 60.6 | 137.8 | 161.6 | 226.6 | 1060 of 3169 | 0 | 0 | 14/459 | a450bad04af820f1 |
| 16 | 113 | 78.3 | 227.0 | 272.0 | 122.0 | 269.2 | 313.5 | 436.9 | 2923 of 3169 | 0 | 0 | 14/459 | a450bad04af820f1 |

**Correctness:** 1 distinct output fingerprint(s) across every concurrency level and backend (1 = identical masked output everywhere, i.e. no cross-conversation interference).
