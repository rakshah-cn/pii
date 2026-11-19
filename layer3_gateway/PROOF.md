# Layer 3 proof experiments

300 random conversations (3878 messages) from data/corpus_test.jsonl (synthetic, fake values; final split untouched), seed 7. Every row is the REAL gateway: Layer 1 (rules + spoken engine + Chitragupta int8), Layer 4 consent, Layer 2 vault, 4-part audit. A value 'leaks' if it (intact) or >= 50% of it reaches the text sent to the LLM.

## A-C. Does gateway memory matter? (full detector)

| configuration | gold values | intact leaks | >=50% leaks | blocked msgs | msgs needing retry |
|---|---|---|---|---|---|
| A  one gateway, whole conversation | 559 | 7 (1.3%) | 11 (2.0%) | 0 | 1 |
| B  a NEW gateway process every 2 turns (memory lost) | 559 | 7 (1.3%) | 11 (2.0%) | 0 | 1 |
| C  two gateways alternating turns (no sticky routing) | 559 | 7 (1.3%) | 11 (2.0%) | 0 | 1 |

Leaks by rendering, A vs B vs C (>= 50% leaks / values):

| rendering | A | B | C |
|---|---|---|---|
| digits | 1/98 | 1/98 | 1/98 |
| n/a | 8/215 | 8/215 | 8/215 |
| spelled | 2/63 | 2/63 | 2/63 |
| spoken | 0/183 | 0/183 | 0/183 |

Extras on an INTACT detector (false-alarm cost): second pattern family fired on 0 of 3878 messages, the Layer-1 rules re-scan on 0; messages needing an audit retry: 1.

## D. Detector misses 50% of its spans

| configuration | gold values | intact leaks | >=50% leaks | blocked msgs | msgs needing retry |
|---|---|---|---|---|---|
| audit OFF (tripwire, family2, rescan, vault scan all off) | 559 | 293 (52.4%) | 293 (52.4%) | 0 | 0 |
| audit FULL | 559 | 85 (15.2%) | 88 (15.7%) | 0 | 214 |
| full minus digit/spoken tripwire | 559 | 87 (15.6%) | 90 (16.1%) | 0 | 212 |
| full minus second pattern family | 559 | 85 (15.2%) | 88 (15.7%) | 0 | 214 |
| full minus Layer-1 rules re-scan | 559 | 161 (28.8%) | 176 (31.5%) | 0 | 132 |
| full minus vault scan | 559 | 85 (15.2%) | 88 (15.7%) | 0 | 214 |
| ONLY tripwire | 559 | 182 (32.6%) | 197 (35.2%) | 0 | 111 |
| ONLY Layer-1 rules re-scan | 559 | 87 (15.6%) | 90 (16.1%) | 0 | 212 |

## E. Detector finds NOTHING (the audit alone)

| configuration | gold values | intact leaks | >=50% leaks | blocked msgs | msgs needing retry |
|---|---|---|---|---|---|
| audit OFF (tripwire, family2, rescan, vault scan all off) | 559 | 559 (100.0%) | 559 (100.0%) | 0 | 0 |
| audit FULL | 559 | 146 (26.1%) | 155 (27.7%) | 0 | 418 |
| full minus digit/spoken tripwire | 559 | 150 (26.8%) | 160 (28.6%) | 0 | 414 |
| full minus second pattern family | 559 | 146 (26.1%) | 155 (27.7%) | 0 | 418 |
| full minus Layer-1 rules re-scan | 559 | 288 (51.5%) | 311 (55.6%) | 0 | 271 |
| full minus vault scan | 559 | 146 (26.1%) | 155 (27.7%) | 0 | 418 |
| ONLY tripwire | 559 | 331 (59.2%) | 354 (63.3%) | 0 | 228 |
| ONLY Layer-1 rules re-scan | 559 | 151 (27.0%) | 161 (28.8%) | 0 | 413 |

## F. Spoken-number tripwire alone, per language (gold values written as words)

| language | values | seen by the tripwire (>= 50% covered) |
|---|---|---|
| en | 182 | 142 (78.0%) |
| hi | 189 | 149 (78.8%) |
| hi_rom | 151 | 114 (75.5%) |
| kn | 223 | 188 (84.3%) |
| kn_rom | 218 | 181 (83.0%) |
| mr | 198 | 160 (80.8%) |
| mr_rom | 174 | 128 (73.6%) |
| ta | 213 | 165 (77.5%) |
| ta_rom | 220 | 166 (75.5%) |

Caveat: the word lists the tripwire uses and the ones that generated this data are both ours; this measures coverage of OUR forms, not of real speakers' variants. Native-speaker review is still undone.
