# Red-team probes (written 20 Nov by a review session; all values are fake)

Each script sends deliberately hostile inputs through the REAL gateway (int8 detector, modular vault, real audit) and reports what reaches the LLM / log.
Run from the project root with the project env:  `.venv/bin/python redteam/<script>.py out.json`  (the third one prints instead of writing a file).

| Script | Covers |
|---|---|
| `probe_written_and_spoken_formats.py` | phone / Aadhaar / PAN / account / UPI / e-mail / DOB / name / address in many formats, spoken, split across turns |
| `probe_languages_length_asr_ids.py` | a language we did not build for (Telugu, Malayalam, Bengali, Gujarati, Punjabi), 3,000-character messages, ASR-style noise, rare names, other id types, abuse inputs |
| `probe_authorization_and_abuse.py` | cross-borrower token forgery, conversation reuse, replayed request ids, bad purposes |

Known results on 20 Nov: see the review notes. These are probes, not yet a pass/fail suite: the checker is a substring test, so read the printed masked text too.
