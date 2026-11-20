# CONTEXT: TE-03 PII protection + the voice-bot demo (state at Fri 20 Nov 2026, ~08:00 IST)

Purpose of this file: a complete, honest picture of what is being built, what exists, what was measured, and what is broken or unknown, so someone can optimise it. Nothing here is a secret. Claims are marked MEASURED (we ran it), DOCS (read in a vendor's docs, not run) or UNVERIFIED.

## 1. The problem and the product
Hackathon problem TE-03 (CreditNirvana Agentathon): let a lender use LLMs on customer conversations (calls, chats) WITHOUT leaking personal data, in line with India's DPDP Act. Languages: English, Hindi, Tamil, Telugu (organisers) + Marathi (our chosen 5th).
Core promise: **no raw personal data (phone, Aadhaar, PAN, name, address, email...) ever reaches an LLM, a log or a store; the data stays recoverable only inside an encrypted vault, and can be erased and proven erased.**

Layers (folder per layer, run as `.venv/bin/python -m <pkg.module>`):
| Layer | What it does |
|---|---|
| 0 data | synthetic multilingual conversation corpus (25,000 rows: dev/test/final) + scoreboard |
| 1 detection | finds PII: rules + a spoken-number engine (words like "nine eight four...") + Chitragupta (XLM-R large, int8 ONNX). ~10-17 ms |
| 2 vault | replaces each value with a token (`<PHONE_1>`); values stored AES-encrypted, per-borrower key, key store separate; retention, rotation, KMS (real AWS KMS smoke test passed 8/8) |
| 3 gateway | the only path to an LLM: initialize -> detect -> tokenise -> final audit (independent tripwires, retry) -> LLM (masked text only) -> sanitise reply -> rehydrate for the borrower. Fails closed |
| 4 consent | purpose-based consent policy on every store write |
| 5 lineage | registry of every copy/derivative of a borrower's data (embeddings etc.) |
| 6 erasure | agent that deletes a borrower's data everywhere and verifies by independent scan |
| 7 audit | hash-chained append-only audit log |
| 8 demo | FastAPI console (`layer8_demo/server.py`, web UI): a teammate OWNS this and has uncommitted changes there. Do not edit |
| 9 voice | THIS demo: a voice bot + call disposition (below) |

Layer 1 numbers (MEASURED, synthetic data only, ONE generator, not real-world evidence): final split fully masked 96.9%, masked >=50% 98.4%, right label 90.6%; names weakest (92-94%); false alarms on PII-free text 6.7%. NOTE: Layer 1 files show uncommitted changes made by someone else after that score was taken: the number may no longer describe the code.

## 2. The idea being built now (layer9_voice)
User's demo: a **voice bot on a real phone call** (and later chat): the customer speaks, speech is transcribed live, the pipeline masks it, only masked text goes to the LLM; the bot answers by voice; when the call ends an LLM produces a **disposition** (will the customer pay: promise-to-pay / refuses / dispute / callback ...). The point for judges: a real downstream task done by an LLM that never saw the personal data.

User constraints (RULES):
- **No call may ever be placed to the team's real phone numbers.** Phone leg must be inbound-only (people dial a number the team owns). A test scans the package for any way to create a call.
- Never print or commit secrets. (A Sarvam key and part of a Twilio token appeared in chat: both to be rotated after the demo.)
- Do not edit the teammate's layer8 files, do not touch frozen layers.
- Plain-language explanations; honest about what is measured vs assumed.

## 3. Architecture of the voice demo
```
speech (browser mic, or phone via Twilio)
  -> speech-to-text vendor (Sarvam Saaras v4)           [hears RAW speech: OUTSIDE our masking boundary]
  -> text -> server._message -> L1 detect -> L2 mask -> L3 audit -> L7 log      (the real pipeline, same as the console)
  -> LLM sees MASKED text only (Sarvam sarvam-105b-conversations, v1 API) + its own MASKED history
  -> bot line (tokens removed by bot.speakable, never rehydrated)
  -> text-to-speech vendor (Sarvam Bulbul v3, voice "shubh") [hears only the placeholder-free line]
end of call: the RAW transcript (kept only until now, then wiped) -> gateway masks it in ONE pass
  -> LLM (sarvam-105b, reasoning OFF) -> strict JSON parse -> disposition (no default on failure)
```
A call = a normal case in the console (`ws.add(...)`); audit events `VOICE_CALL_STARTED/ENDED` carry no raw data.

Files (all in `layer9_voice/`, uncommitted, plus one line in `.gitignore`):
- `sarvam.py` client: chat (`/v1` for "-conversations", `/v2` for other models), STT (`/speech-to-text`, multipart, saaras:v4, 30 s max), TTS (`/text-to-speech`, base64 WAV). Errors carry only status+code, never body or key.
- `bot.py` prompts, greeting/repeat/goodbye lines (en/hi/mr; hi/mr NOT native-checked), `speakable`, `CallLLM` (keeps masked history, 12 messages), model selection (`VOICE_LLM=sarvam|bedrock|stub`).
- `disposition.py` JSON schema/system prompt, `build_transcript`, strict `parse`, `generate` (1 retry, then loud failure).
- `calls.py` `CallManager`: start / turn / turn_from_audio / end / state; borrower list; raw turns wiped at end.
- `router.py` `/voice/*` (page, status, borrowers, call start/say/audio/end/state). Role-checked via the console's own `require("agent")`.
- `telephony.py` inbound-only Twilio line: signature-checked webhooks, `<Record>` loop, fetch+DELETE each recording, never reads From/To, one call at a time, arm-a-borrower model (no phone numbers stored).
- `run.py` launcher: imports the console, swaps `srv._llm`, mounts routers (phone routes only if TWILIO_* + PUBLIC_BASE_URL in .env).
- `web/chat.html` TEXT CHAT page (`/voice/chat`): same CallManager with `channel=chat` (texting prompt, 'reminder message' greeting, console case channel 'Chat'); shows each message with what the AI received; disposition card. MEASURED 20 Nov in a real browser, real Sarvam: English/Hindi/Marathi each -> phone masked, reply 0.4-0.8 s, disposition PROMISE TO PAY Rs 4,200. Typed `. I will` also lost the word 'I' into the span (same detector over-mask as in speech).
- `web/call.html` browser call page (mic or typed text; shows raw vs masked side by side, timings, disposition card). URL: `/voice/page`.
- `probe.py` live check of every Sarvam assumption (needs key). `test_voice.py` (34), `test_phone.py` (20), `eval_disposition.py` (13-call labelled smoke check): offline; real detector/vault/audit chain with a recording fake model.
- Run: `.venv/bin/python -m layer9_voice.run`, open `http://127.0.0.1:8808/voice/page`.

## 4. Design decisions and findings (all MEASURED unless marked)
1. The gateway gives the LLM ONE masked message per turn, no history -> the bot keeps its own masked memory.
2. Re-sending an already-masked transcript through the gateway GARBLES it (`Agent` -> `<ADDRESS_1>`) -> the disposition step sends the RAW transcript once and lets the gateway mask it; raw turns wiped at call end.
3. The gateway's reply sanitiser blanks a bare number in the model's JSON (`4200` -> `[redacted]`) but keeps rupee-marked amounts up to 8 digits (`Rs 4,200`) -> the model is told to write the amount that way.
4. Sarvam: `reasoning_effort: null` switches reasoning off (163 ms vs ~700 ms; with reasoning on, `sarvam-105b` burned a 400-token budget thinking and returned empty). The string `"None"` is rejected (400). `sarvam-105b-conversations` answers in ~0.3 s. Models `sarvam-m`, `sarvam-30b` are deprecated (DOCS). Other models the user has: `deepseekv4.1-flash-1017` (v2, reasoning) and decision model `sarvam-prism` (`/v2/systemone`: text state + questions of type choice/score): neither tried yet.
5. Sarvam STT writes spoken numbers as DIGITS ("9876501234") in en/hi/mr -> our Layer 1 masked the phone in all three languages in a controlled round trip. Marathi/Hindi come back in Devanagari with ASCII digits. English PAN spoken as letters came back "A B C P K 1234 L" and was masked.
6. Sarvam defaults that worked: TTS model `bulbul:v3`, speaker `shubh`, languages en-IN/hi-IN/mr-IN. Retention on Sarvam is indefinite until a workspace Owner sets it (DOCS); training use and data location are not stated in the docs read.
7. Do NOT use Sarvam "Voice Agents" (managed loop: would bypass our pipeline).
8. Twilio trial account: buying a number and the Overview pages demanded an upgrade (paid); the user cannot pay now -> the phone leg is blocked, the browser-mic call (`/voice/page`) is the working path. If anyone has a paid Twilio/Exotel account, the phone leg works with 4 env vars (see README).

## 5. Latest live tests (Sarvam real, browser page driven by headless Chrome with a fake microphone playing Sarvam-generated speech) - MEASURED 20 Nov ~09:00
Working end to end: greeting -> speech recorded -> transcribed -> masked -> model -> bot line spoken -> end call -> disposition card (PROMISE TO PAY, Rs 4,200, "the 7th", plain reason): 3 of 3 runs after the fixes.
Timings per turn (server path, no browser, measured with curl): speech-to-text 0.45-0.55 s, mask+audit 20-55 ms, model 0.3-0.8 s, text-to-speech 0.4-1.4 s, whole turn 0.7-1.9 s. In ISOLATION text-to-speech is steady (median 0.95 s, max 1.3 s over 16 calls, idle gaps and fresh connections make no difference).
UNEXPLAINED: in 4 of 6 browser-driven runs one text-to-speech call took 5-8.6 s (turn 6.5-9.5 s). Not reproduced through the server without the browser. The machine had load average ~12 (16 cores) from other processes, so local load is the most likely cause, NOT proven. A hedge (second identical request after 2.5 s) is in place; it did not visibly help in those runs.
Disposition model check (13 synthetic labelled calls, written by the developer, tiny): `sarvam-105b` (v1, reasoning off) 12-13/13 right outcome, 5/5 amounts, 0.3 s; `deepseekv4.1-flash-1017` (v2) 12/13, 5/5, 1.4 s. One case fails to parse in roughly 1 run in 3 (random; `generate` now retries twice). `python -m layer9_voice.eval_disposition`.
Other observed issues: (a) the detector span swallows a neighbouring word after a phone number (", and I" -> `<PHONE_1> will pay`): Layer 1 is frozen/teammate's; (b) speech-to-text sometimes drops a leading "Yes"/garbles the lead-in.

### Fixed in this session
- Disposition failure: on Sarvam `/v2`, `reasoning_effort: null` does NOT switch reasoning off (model spent ~1,000 tokens thinking and returned nothing); on `/v1` it does (0.4 s, no reasoning). `sarvam-105b*` now use `/v1`; other models `/v2`. The real reason a model call failed is now kept (status/code only) and shown in the disposition error.
- The bot said "[redacted] days late": the gateway's reply filter blanks a bare number the model repeats -> the prompt no longer contains a day count.
- Bot prompt: no longer refuses/comments on a private value the customer says; one short sentence (<=15 words).
- Speed: fixed lines (greeting/repeat/goodbye) are cached and pre-spoken at start-up; a long reply is spoken as sentences in parallel and joined into one WAV (with fallback to one call); hedged second request when a speech call is slow; the audio route runs in a worker thread instead of blocking the event loop.
- WRONG_PERSON may carry will_pay "no" or "unclear".
- Bug found by tests: the sentence splitter crashed on exactly two parts.

## 6. What is NOT proven
Anything on a real phone; real (noisy, 8 kHz, accented) audio; multi-turn behaviour with a real model over many turns; Layer 1 on real ASR output beyond the 3 controlled sentences; names in ASR output; Tamil/Telugu speech; the Twilio signature/recording flow against real Twilio; DeepSeek / sarvam-prism options; data residency/retention terms of Sarvam; whether the disposition is accurate (no labelled call set exists); the masked-vs-unmasked utility comparison (no downstream task set yet).

## 7. Things worth optimising (honest candidate list, not decided)
1. Find why text-to-speech is sometimes 5-8 s only under the browser test (load? contention? per-key concurrency limits at Sarvam? the parallel-sentence + hedge requests multiplying load?). Add timing logs per speech request; try on an idle machine.
2. Disposition quality: grow the labelled call set (real-looking noisy transcripts, Tamil/Telugu, code-mixed, partial promises, multiple dates); compare `sarvam-105b`, `deepseekv4.1-flash-1017` and the decision model `sarvam-prism` (`/v2/systemone`, structured choice/score questions: not tried); measure masked vs UNMASKED transcripts (the missing utility evidence; unmasked must be run only on synthetic data).
3. Detector over-mask after a number ("and I" swallowed): a trim at the gateway or a Layer 1 fix, with a test (Layer 1 is frozen/teammate-owned).
4. Speech-to-text: `mode` (transcribe/verbatim/codemix), language hint vs auto-detect, `sample_rate` for 8 kHz phone audio; leading words dropped when recording starts (start the recorder earlier / pre-roll buffer).
5. Bot behaviour: ending the call (goodbye after the first commitment is abrupt), handling a customer who talks over it, Hindi/Marathi lines (not native-checked), multi-turn quality over many turns.
6. UI: a live-call view inside the real console (teammate's files) or keep the standalone `/voice/page`; streaming text-to-speech and playing sentence 1 while sentence 2 is still being made.
7. Phone leg: blocked (Twilio trial cannot get a number without paying; the user cannot pay now). Works with 4 env vars if anyone has a paid Twilio/Exotel account.
8. Robustness: one call at a time (global server lock), demo identity only, mock stores; the raw text of each turn is kept in the console session in memory (demo behaviour).

## 8. Honest caveats for the pitch
- Masking protects what the LLM, logs and stores receive. The speech vendor (and Twilio, if used) hears RAW audio: say so; in production the ASR would run inside the customer's boundary.
- Layer 1 scores are on synthetic data from one generator.
- Real AWS KMS verified once (8/8); least-privilege, throttling, failover unverified. Erasure stores are mock.
