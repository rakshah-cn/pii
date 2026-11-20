# Layer 1 (detection): final report

> **UPDATE, Fri 20 Nov, after the one-shot final run:** Layer 1 was changed AFTER `corpus_final` was scored, at the owner's request (section 'Changes after the final run' at the end). The final-split numbers below belong to the detector AS IT WAS at 01:46-01:49 IST. The changed detector has NOT been scored on `corpus_final`.

Detector: rules + spoken-number engine (cross-turn memory) + Chitragupta (int8) + resolver (types, joined numbers) + span expansion + name memory + chatter suppression. Synthetic data, gold labels; **optimistic for real data**.

## How to read the final split (added by hand after the run)

`corpus_final` was scored ONCE, on 20 Nov 01:46-01:49 IST, after Layers 0-2 and the gateway were frozen; nothing in Layer 0/1 was changed before it. (Layer 1 WAS changed afterwards, see the update note at the top: this final number belongs to the OLD detector.)

- **Same score as test, as expected from the same generator:** fully masked 96.9% (test 97.0%), masked 98.4% (test 98.4%), right label 90.6% (test 89.0%). This says we did not overfit to the dev/test ROWS; it does NOT say how real transcripts will score (all three splits come from one generator).
- **The one number that moved: false alarms on PII-free calls, 6.7% (50/745) on final vs 2.9% (20/694) on test.** The chatter stoplist was written after looking at test failures, so the test figure was optimistic; the final figure is the honest one for unseen text from this generator, and real chatter will differ.
- **Below the 99% bar** (by the 'masked, at least 50% of the span' measure): PAN 97.0% and NAME 94.3%. By the stricter 'fully masked, at least 95%' measure: Aadhaar 98.7%, phone 98.5%, PAN 96.8%, name 92.0%, address 90.1% (account 99.0%, loan 99.7%, UPI / DOB / email 100%). Which types count as 'high-risk' is the brief's definition, not ours.
- Romanised Hindi / Marathi / Tamil are the weakest language variants (93.6% / 93.9% / 94.9% fully masked); 48 of the 50 cells are thin (< 300 spans).

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

_Not re-scored in the final run: copied from the earlier run of this same script on the same detector (no change to Layer 0/1 or the model since)._

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

## final: 3750 rows, 5765 gold PII spans

- masked (>=50% of span): **98.4%**   fully masked (>=95%): **96.9%**   right type label too: **90.6%**
- PII-free calls with a false alarm: **50/745 (6.7%)**
- must-NOT-mask things touched: AMOUNT 3/2521, DATE 4/2823, ORG 0/2567

| type | spans | masked | fully masked | right label |
|---|---|---|---|---|
| NAME | 1272 | 94.3% | 92.0% | 92.2% |
| ACCOUNT | 793 | 100.0% | 99.0% | 87.4% |
| PHONE | 754 | 99.9% | 98.5% | 80.4% |
| AADHAAR | 679 | 100.0% | 98.7% | 97.5% |
| PAN | 601 | 97.0% | 96.8% | 90.2% |
| LOAN | 579 | 99.8% | 99.7% | 91.5% |
| UPI | 281 | 100.0% | 100.0% | 100.0% |
| DOB | 278 | 100.0% | 100.0% | 96.4% |
| ADDRESS | 274 | 99.6% | 90.1% | 78.1% |
| EMAIL | 254 | 100.0% | 100.0% | 100.0% |

| language variant | spans | fully masked |
|---|---|---|
| en | 636 | 97.5% |
| hi | 558 | 98.7% |
| hi_rom | 609 | 93.6% |
| kn | 747 | 99.2% |
| kn_rom | 646 | 96.7% |
| mr | 636 | 97.5% |
| mr_rom | 524 | 93.9% |
| ta | 680 | 99.4% |
| ta_rom | 729 | 94.9% |

| channel | rows | spans | fully masked |
|---|---|---|---|
| call | 2569 | 4049 | 97.2% |
| message | 772 | 1109 | 95.9% |
| prompt | 409 | 607 | 96.7% |

- THIN cells (< 300 gold spans, read their % with care): 48 of 50
- biggest miss groups (type, form): [(('NAME', 'written'), 26), (('NAME', 'split_turns'), 21), (('NAME', 'spoken'), 18), (('PAN', 'split_turns'), 18), (('NAME', 'spoken_fillers'), 6), (('LOAN', 'split_turns'), 1)]

## Latency (one message at a time, gateway configuration, n=160)

p50 **8.8 ms**, p95 11.9 ms, p99 13.7 ms (budget 50 ms). Measured on short synthetic turns on this machine; real, longer messages will be slower.

_The final run's own measurement, taken while the machine was heavily loaded by other jobs (video capture, two other model runs): p50 17.9 ms, p95 26.5 ms, p99 30.6 ms. The quiet-machine figures above are the ones to quote._

## Known limitations

- names (about 93% fully masked) and addresses (about 90%) are the weak types; number types are 97-99%
- every score is on our own synthetic data; real transcripts will score lower by an unknown amount
- spoken tens and double/triple are not covered for Hindi/Marathi/Kannada/Tamil; digit-word lists and ASR spellings are unverified by native speakers
- the greeting stoplist and company-suffix list were tuned on our failures


## Changes after the final run (Fri 20 Nov, about 04:00-07:30 IST)

After the one-shot final score, the owner asked for Layer 1 to be strengthened. A read-only analysis of 3,000 dev calls found where recall was lost; the cheap, measured fixes were then built, each with tests written first. `corpus_final` was NOT re-scored: the 96.9% / 98.4% / 90.6% above are for the OLD detector.

**What changed** (all in `layer1_detection/`, plus one matcher in `layer2_vault/core/sealed_match.py`; 109 new Layer 1 tests, 152 in the layer):
1. **Previous-turn context for the model** (`Detector(model_context=True)`, `ModelDetector.detect_texts(contexts=)`, `CONTEXT_CHARS=120`). The model reads `<tail of the previous turn> + newline + <this turn>`; only spans inside this turn are kept. A bare 'Pooja.' after 'May I know your name?' is now recognised. Live gateway and offline scoreboard behave the same.
2. **Initials in names** (`expand.expand_name`, also used by the name memory): 'S. Nadar', 'K.S. Rao', 'Usha M. Tiwari'. Never an honorific or a label ('Plan B.'), never grows into a number span, no surname is guessed after an initial.
3. **Address rule** (`rules.py`): the house-number word is part of the address ('Flat', 'H.No.', 'வீட்டு எண்', 'घर क्रमांक', 'ಫ್ಲ್ಯಾಟ್'), house numbers like '772/50', a full stop after 'opp.' / 'T.' does not end the address, a ':' label does (not 'Pincode:'), commas inside '7,200' are not separators, '।' ends a sentence.
4. **Number labels** (`resolve.py`, labels only, never what is masked): 'the mobile number linked to your loan' is a phone question; a mobile-shaped 10-digit number said 2+ turns after a loan / account cue is a PHONE (a cue after the number in the same turn wins).
5. Two defects the tests exposed: the company-name check now runs after spans are grown to whole words ('Finserv' was split into 'Orchid' + 'serv'); a name never contains a digit ('19th', 'Nimage 15th' were tagged as names).
6. `sealed_match.py` (erasure matcher): a stored name no longer matches inside a longer word ('R. Rao' inside 'Mr. Rao'), and the words of an initial-bearing name are also searched alone (ambiguous, never auto-deleted).

**Measured** (outputs of the real int8 model, run one input at a time as the gateway does; synthetic data; before = committed code, after = changed code, identical inputs):

| set | calls | fully masked | masked (>=50%) | right label | PII-free calls with a false alarm |
|---|---|---|---|---|---|
| dev, analysed (OPTIMISTIC: the fixes were designed from its errors) | 3,000 | 97.8 -> 99.2 | 99.0 -> 99.6 | 89.0 -> 91.2 | 37 -> 38 of 464 |
| dev, never examined | 3,000 | 98.0 -> 99.1 | 99.1 -> 99.5 | 88.7 -> 90.4 | 43 -> 39 of 474 |
| test (examined in earlier sessions, not for these fixes) | 3,750 | 97.2 -> 98.8 | 98.5 -> 99.6 | 89.1 -> 90.5 | 25 -> 40 of 694 |

Fully masked by type (same three sets): NAME 95.3 -> 98.9 / 95.8 -> 99.0 / 93.1 -> 99.3; ADDRESS 90.9 -> 99.1 / 93.8 -> 99.1 / 93.5 -> 98.5; PAN 97.5 -> 98.7 / 96.8 -> 97.7 / 97.0 -> 97.7; the other types did not change (already 97.6-100%). PHONE right label 68.6 -> 81.9 / 64.2 -> 74.5 / 70.5 -> 74.3; ACCOUNT right label -0.3 / -0.3 / -0.4 and LOAN -0.4 / 0.0 / -0.5 (a few 10-digit loan / account numbers are retyped as phones).
Must NOT be masked: dates touched 21 -> 13, 31 -> 18, 66 -> 7; amounts 4 -> 5, 6 -> 3, 3 -> 1; company names 2 -> 0, 4 -> 0, 1 -> 0.
Older sets from other generators (hard_dev, llm_hard, synth_dev, v2_dev; 1,310 calls): no regression; v2_dev 97.7 -> 99.8% fully masked.
Latency, gateway configuration, real model, 160 messages, quiet machine: p50 9.3 -> 12.1 ms, p95 13.0 -> 16.9, p99 14.6 -> 17.6-21.7 with short previous turns; with a long previous turn (~160 characters, cut to 120) p50 18.5, p95 22, p99 24-27 (one spike of 35 ms and one of 52 ms seen in 2 x 160 messages). Budget 50 ms.

**The cost, stated plainly**
- False alarms per PII-free TURN rose slightly (per 1,000 turns: 9.8 -> 10.4, 12.0 -> 12.5, 5.3 -> 8.1). On the test split the call-level count (25 -> 40) is mostly one repeated phrase ('hapta baddal msg aala', 10 calls). The extra false alarms are ordinary Marathi / Kannada / Tamil words tagged as names.
- The context makes the model miss a name it used to catch in about 0.05% of gold spans (6 of 15,555, all romanised Indic text). A 'union with the context-free pass' would remove this and cost one more model pass per turn (+~10 ms) and about +3% false-alarm calls; it was NOT built.
- Through the real gateway (300 test conversations, 559 gold values; measured with the first version of the fixes, before the review round): values that reached the LLM text 11 (2.0%) -> 5 (0.9%), intact copies 7 -> 1; but the gateway's 85% over-masking rule blocked 3 messages (old: 0): two ID pieces said alone ('Modalu SPZ', 'Shuruaat 9302') that are now masked completely, and 'Ondu nimisha' ('one minute', a false alarm).

**Not shown / known limits**
- All numbers come from one generator that we wrote. Real ASR text (no capitals, no punctuation, spelling noise) is untested. The rules for PAN and loan ids are case-sensitive and were already weak on lower-cased text in the old code (PAN 95% -> 12% when the whole text is lower-cased).
- The address marker lists, the initials rule and the stale-cue threshold (2 turns) were tuned by counting errors on this corpus; the marker lists include the spellings our generator uses. Unlisted spellings leave the marker word visible. Initials in native script (Devanagari, Kannada, Tamil letters) are not handled.
- Bare-name recognition needs the agent's question to have gone through the gateway and still be in its history (6 turns, 15-minute idle limit).
- A name with initials is now stored whole ('S. Nadar'); the erasure sweep treats it like any two-word name (found only in that written form, plus its surname alone as ambiguous). Another unregistered person with the same initial and surname could be matched as 'unique'.
- A 10-digit loan / account number said 2+ turns after its cue is typed PHONE; a number said in pieces keeps the token type issued for its first piece.
- Pre-existing and unchanged: any 6-digit number after a comma can be taken for a pincode (amount without a currency marker); bare PAN fragments said alone are still partly missed; 'Noted' is still tagged as a name.
- Independent review: 5 reviewers + 19 verifiers (workflow `wf_348442b4`) found 2 high / 11 medium issues in the first version; all that were confirmed regressions were fixed and have regression tests (the colon before 'Pincode:', 'Rs.' gluing sentences, the head-noun rule overriding a later cue, a PAN or loan id taken for a house number, quadratic regexes, the cache race, the surname guess).
- For a clean number on the changed detector a fresh held-out split scored once is still needed (or a re-score of `corpus_final`, which would no longer be one-shot).

### Second step, same day: lower-case spelled ids (`spoken_engine.py`, `detector.py`, `test_lowercase.py`, 7 tests)
Found by the review's lower-case probe: with the whole text lower-cased (rules only, dev + test, no model) PAN fully masked fell 96% -> 14% and loan ids 98% -> 47%, because a spelled letter ('l p o') had to be UPPER case. Now a lower-case single letter counts as a spelled letter when the turn is armed by a cue (PAN / loan / number asked for); a lone 'a' / 'i' counts only next to another letter or a digit; and a cue word at the very end of a turn ('your PAN') now arms the next turns (it was missed).
Measured (rules only, lower-cased text): PAN 14% -> 68% (dev) / 70% (test) fully masked, loan ids 47% -> 88% / 85%. On the ORIGINAL-case text it also helps (the end-of-turn cue): PAN fully masked 98.7 -> 99.4 (dev analysed), 97.7 -> 98.6 (dev unseen), 97.7 -> 99.1 (test); all other types, false alarms and must-not-mask counts unchanged; whole fully-masked total 99.2 / 99.2 / 99.0%.
Still weak on lower-case text: NAME (the model sees the lower-cased text only through its own tagging: not measured with the model), and any spelled letters with no cue turn before them. Tuned on the same corpus.
