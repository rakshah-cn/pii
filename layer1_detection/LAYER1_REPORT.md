# Layer 1 (detection): final report

Detector: rules + spoken-number engine (cross-turn memory) + Chitragupta (int8) + resolver (types, joined numbers) + span expansion + name memory + chatter suppression. Synthetic data, gold labels; **optimistic for real data**.

## test: 3750 rows, 5820 gold PII spans

- masked (>=50% of span): **98.4%**   fully masked (>=95%): **97.0%**   right type label too: **89.0%**
- PII-free calls with a false alarm: **20/694 (2.9%)**
- must-NOT-mask things touched: AMOUNT 1/1843, DATE 15/3060, ORG 0/2809

| type | spans | masked | fully masked | right label |
|---|---|---|---|---|
| NAME | 1270 | 94.6% | 92.8% | 92.1% |
| ACCOUNT | 837 | 99.9% | 97.6% | 91.9% |
| PHONE | 764 | 100.0% | 99.2% | 70.5% |
| AADHAAR | 727 | 100.0% | 98.6% | 96.0% |
| LOAN | 582 | 99.5% | 99.1% | 88.1% |
| PAN | 572 | 97.0% | 97.0% | 89.2% |
| UPI | 275 | 100.0% | 100.0% | 100.0% |
| DOB | 267 | 100.0% | 100.0% | 89.1% |
| EMAIL | 263 | 100.0% | 100.0% | 100.0% |
| ADDRESS | 263 | 99.2% | 90.5% | 77.9% |

| language variant | spans | fully masked |
|---|---|---|
| en | 610 | 97.0% |
| hi | 627 | 98.2% |
| hi_rom | 544 | 96.9% |
| kn | 719 | 97.5% |
| kn_rom | 694 | 95.4% |
| mr | 626 | 98.4% |
| mr_rom | 555 | 96.2% |
| ta | 725 | 99.2% |
| ta_rom | 720 | 94.2% |

| channel | rows | spans | fully masked |
|---|---|---|---|
| call | 2608 | 4042 | 97.1% |
| message | 770 | 1161 | 97.4% |
| prompt | 372 | 617 | 95.8% |

- THIN cells (< 300 gold spans, read their % with care): 48 of 50
- biggest miss groups (type, form): [(('NAME', 'split_turns'), 21), (('PAN', 'split_turns'), 17), (('NAME', 'spoken'), 17), (('NAME', 'written'), 17), (('NAME', 'spoken_fillers'), 10), (('NAME', 'partial'), 4)]

## dev: 17500 rows, 28501 gold PII spans

- masked (>=50% of span): **98.7%**   fully masked (>=95%): **97.4%**   right type label too: **88.8%**
- PII-free calls with a false alarm: **165/2690 (6.1%)**
- must-NOT-mask things touched: AMOUNT 39/8051, DATE 81/13519, ORG 1/13388

| type | spans | masked | fully masked | right label |
|---|---|---|---|---|
| NAME | 6404 | 96.9% | 94.8% | 93.5% |
| PHONE | 3809 | 99.8% | 98.9% | 68.0% |
| ACCOUNT | 3791 | 99.8% | 98.3% | 89.8% |
| AADHAAR | 3436 | 99.8% | 98.7% | 94.8% |
| PAN | 2873 | 96.8% | 96.7% | 91.1% |
| LOAN | 2843 | 98.9% | 98.7% | 86.5% |
| UPI | 1367 | 100.0% | 100.0% | 100.0% |
| ADDRESS | 1339 | 98.6% | 90.7% | 80.9% |
| DOB | 1322 | 100.0% | 100.0% | 93.1% |
| EMAIL | 1317 | 100.0% | 100.0% | 100.0% |

| language variant | spans | fully masked |
|---|---|---|
| en | 3133 | 98.1% |
| hi | 2770 | 98.5% |
| hi_rom | 2977 | 97.1% |
| kn | 3359 | 98.0% |
| kn_rom | 3578 | 96.9% |
| mr | 2874 | 98.3% |
| mr_rom | 2899 | 96.6% |
| ta | 3479 | 98.5% |
| ta_rom | 3432 | 95.0% |

| channel | rows | spans | fully masked |
|---|---|---|---|
| call | 12581 | 19872 | 97.8% |
| message | 2886 | 5502 | 95.9% |
| prompt | 2033 | 3127 | 97.2% |

- THIN cells (< 300 gold spans, read their % with care): 9 of 50
- biggest miss groups (type, form): [(('PAN', 'split_turns'), 90), (('NAME', 'split_turns'), 65), (('NAME', 'written'), 64), (('NAME', 'spoken'), 42), (('LOAN', 'split_turns'), 32), (('NAME', 'spoken_fillers'), 14)]

## Latency (one message at a time, gateway configuration, n=160)

p50 **8.8 ms**, p95 11.9 ms, p99 13.7 ms (budget 50 ms). Measured on short synthetic turns on this machine; real, longer messages will be slower.

## Known limitations

- names (about 93% fully masked) and addresses (about 90%) are the weak types; number types are 97-99%
- every score is on our own synthetic data; real transcripts will score lower by an unknown amount
- spoken tens and double/triple are not covered for Hindi/Marathi/Kannada/Tamil; digit-word lists and ASR spellings are unverified by native speakers
- the greeting stoplist and company-suffix list were tuned on our failures
