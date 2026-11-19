# Layer 2 vault: measured performance

Machine: x86_64, 16 CPUs, Python 3.12.3. Synthetic fake values. Each number is from this run on this machine; production hardware and a networked database will differ. No target is asserted: read the numbers.

## Primitives (microseconds, in-process, no database)

| operation | latency |
|---|---|
| AES-256-GCM encrypt (11-char value) | p50 1.110 | p95 1.218 | p99 1.302 | max 5.343 us (n=5000) |
| AES-256-GCM decrypt | p50 1.100 | p95 1.223 | p99 1.316 | max 10.458 us (n=5000) |
| DEK wrap (AES-GCM under KEK, incl. HKDF) | p50 4.001 | p95 4.417 | p99 5.065 | max 16.439 us (n=5000) |
| DEK unwrap (incl. HKDF) | p50 3.789 | p95 4.041 | p99 5.199 | max 11.192 us (n=5000) |
| HKDF derive blind-index key | p50 1.829 | p95 1.948 | p99 2.049 | max 6.059 us (n=5000) |
| blind-index HMAC (key already derived) | p50 3.190 | p95 3.388 | p99 3.852 | max 48.630 us (n=5000) |

## File SQLite (WAL, single connection, writes serialised)

- single scoped blind-index lookup (miss, one query): p50 0.002 | p95 0.002 | p99 0.002 | max 0.042 ms (n=3000)
- token -> record lookup (one query): p50 0.004 | p95 0.005 | p99 0.006 | max 0.039 ms (n=3000)

| operation | latency (ms) |
|---|---|
| tokenise, 1 NEW value | p50 0.642 | p95 3.993 | p99 7.425 | max 8.226 ms (n=400) |
| tokenise, 2 NEW values | p50 1.056 | p95 8.699 | p99 18.816 | max 22.354 ms (n=400) |
| tokenise, 10 NEW values | p50 3.569 | p95 13.268 | p99 32.792 | max 36.722 ms (n=300) |
| tokenise, 50 NEW values | p50 5.221 | p95 14.276 | p99 17.003 | max 18.037 ms (n=150) |
| tokenise, 100 NEW values | p50 14.247 | p95 64.255 | p99 181.759 | max 181.759 ms (n=80) |
| tokenise, 200 NEW values | p50 20.835 | p95 29.820 | p99 32.028 | max 32.028 ms (n=40) |
| tokenise, 2 values already known (reuse) | p50 0.505 | p95 0.984 | p99 1.371 | max 4.384 ms (n=300) |
| tokenise, 10 values already known (reuse) | p50 0.581 | p95 1.182 | p99 1.903 | max 4.939 ms (n=300) |
| tokenise, 50 values already known (reuse) | p50 1.208 | p95 1.501 | p99 3.111 | max 4.456 ms (n=300) |
| rehydrate 1 token | p50 0.238 | p95 0.389 | p99 0.501 | max 2.778 ms (n=400) |
| rehydrate 10 tokens | p50 0.362 | p95 0.487 | p99 0.748 | max 1.751 ms (n=300) |
| HTTP tokenise, 2 values reused (auth + validation + service) | p50 2.244 | p95 3.161 | p99 4.940 | max 12.014 ms (n=300) |

| concurrent clients | workload | req/s | p50 ms | p95 ms | p99 ms | errors |
|---|---|---|---|---|---|---|
| 1 | independent borrowers (2 new values) | 981 | 0.78 | 3.03 | 5.00 | 0 |
| 1 | ONE hot conversation (2 new values) | 1077 | 0.75 | 1.08 | 5.65 | 0 |
| 4 | independent borrowers (2 new values) | 1105 | 3.04 | 7.12 | 7.80 | 0 |
| 4 | ONE hot conversation (2 new values) | 879 | 3.68 | 9.63 | 10.06 | 0 |
| 8 | independent borrowers (2 new values) | 1098 | 6.12 | 10.62 | 11.53 | 0 |
| 8 | ONE hot conversation (2 new values) | 1014 | 6.23 | 12.20 | 31.58 | 0 |
| 16 | independent borrowers (2 new values) | 715 | 17.89 | 43.89 | 84.33 | 0 |
| 16 | ONE hot conversation (2 new values) | 448 | 9.55 | 107.58 | 214.82 | 0 |

## PostgreSQL 16, pooled (min 4 / max 16 connections, local unix socket)

- single scoped blind-index lookup (miss, one query): p50 0.033 | p95 0.035 | p99 0.041 | max 1.241 ms (n=3000)
- token -> record lookup (one query): p50 0.034 | p95 0.036 | p99 0.083 | max 0.876 ms (n=3000)

| operation | latency (ms) |
|---|---|
| tokenise, 1 NEW value | p50 0.847 | p95 2.046 | p99 5.060 | max 14.690 ms (n=400) |
| tokenise, 2 NEW values | p50 0.980 | p95 2.193 | p99 5.367 | max 7.507 ms (n=400) |
| tokenise, 10 NEW values | p50 1.647 | p95 3.929 | p99 5.861 | max 7.390 ms (n=300) |
| tokenise, 50 NEW values | p50 5.236 | p95 7.865 | p99 11.157 | max 14.259 ms (n=150) |
| tokenise, 100 NEW values | p50 9.828 | p95 14.356 | p99 39.544 | max 39.544 ms (n=80) |
| tokenise, 200 NEW values | p50 18.369 | p95 21.136 | p99 37.677 | max 37.677 ms (n=40) |
| tokenise, 2 values already known (reuse) | p50 0.507 | p95 0.998 | p99 2.557 | max 5.638 ms (n=300) |
| tokenise, 10 values already known (reuse) | p50 0.583 | p95 0.880 | p99 2.810 | max 5.161 ms (n=300) |
| tokenise, 50 values already known (reuse) | p50 1.612 | p95 2.334 | p99 4.238 | max 7.498 ms (n=300) |
| rehydrate 1 token | p50 0.374 | p95 0.557 | p99 1.147 | max 1.872 ms (n=400) |
| rehydrate 10 tokens | p50 0.795 | p95 1.249 | p99 2.007 | max 3.121 ms (n=300) |
| HTTP tokenise, 2 values reused (auth + validation + service) | p50 2.599 | p95 4.592 | p99 6.634 | max 18.487 ms (n=300) |

| concurrent clients | workload | req/s | p50 ms | p95 ms | p99 ms | errors |
|---|---|---|---|---|---|---|
| 1 | independent borrowers (2 new values) | 809 | 1.12 | 2.04 | 2.09 | 0 |
| 1 | ONE hot conversation (2 new values) | 871 | 1.05 | 1.53 | 2.14 | 0 |
| 4 | independent borrowers (2 new values) | 1118 | 3.07 | 6.58 | 8.93 | 0 |
| 4 | ONE hot conversation (2 new values) | 639 | 5.42 | 10.61 | 11.05 | 0 |
| 8 | independent borrowers (2 new values) | 711 | 10.24 | 19.99 | 22.62 | 0 |
| 8 | ONE hot conversation (2 new values) | 466 | 17.39 | 24.12 | 27.07 | 0 |
| 16 | independent borrowers (2 new values) | 670 | 22.91 | 31.20 | 43.21 | 0 |
| 16 | ONE hot conversation (2 new values) | 466 | 34.81 | 47.72 | 50.00 | 0 |

## How to read this

- The PostgreSQL server here is on the SAME machine over a unix socket: a networked / managed database adds a round trip per statement (batching in Phase 5 keeps the statement count small and independent of the number of values).
- Between the write-heavy and read-heavy sections the database is given ANALYZE / a checkpoint and a 2 s pause (see settle()); without it background work from the bulk inserts inflated the first read section.
- SQLite tails (p95/p99 of the 100- and 200-value requests, and 16 clients) are WAL checkpoints and one connection shared by 16 threads: SQLite is the test / development backend, PostgreSQL is the deployment target.
- 'ONE hot conversation' is the worst case: every client writes into the same conversation, so writes queue on the borrower / conversation locks. Errors there would show up as retries exhausted.
- This is the vault alone. Detection (Layer 1) is measured separately (about 10 ms p50 on this machine); the project budget of 50 ms is for detection plus tokenisation together.
