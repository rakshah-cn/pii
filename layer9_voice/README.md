# Layer 9 - voice bot + call disposition (demo)

A call is a normal case in the demo console. Every customer utterance goes through the REAL pipeline (layers 1-7); the model only ever sees masked text.

```
speech -> [speech-to-text vendor: hears RAW speech] -> text -> detect -> mask -> audit -> LLM (masked) -> bot line -> [text-to-speech vendor: hears only the placeholder-free line]
end of call: raw transcript -> the gateway masks it in ONE pass -> LLM -> disposition (strictly parsed; no default)
```

## Run
1. `.env`: add `SARVAM_API_KEY=...` (never paste it in chat or commit it).
2. `.venv/bin/python -m layer9_voice.probe --detect` - checks every guess about Sarvam against the real API (needs the key; sends only made-up sentences).
3. `.venv/bin/python -m layer9_voice.run` - the console plus `/voice/*`; the BROWSER CALL (mic, no phone) is at `/voice/page` and the TEXT CHAT at `/voice/chat` (same bot, pipeline and disposition; `channel: "chat"` on `/voice/call/start`) (`VOICE_LLM=sarvam|bedrock|stub`; default sarvam when the key is set, else stub).
4. The call list is `borrowers.example.json` (fake placeholder numbers). The `phone` field is display-only (shown masked): NOTHING in this package dials a number.

**Rule (user, 20 Nov): no call may ever be placed to the team's real phone numbers.** The phone leg is INBOUND ONLY: people dial a number the team owns. A test scans the package for any way to create a call.
Tests: `.venv/bin/python -m layer9_voice.test_voice` (34, offline; the pipeline part uses the real detector/vault/audit chain and a recording fake model).

## Endpoints (agent role)
`GET /voice/status`, `GET /voice/borrowers`, `POST /voice/call/start`, `POST /voice/call/{id}/say` (text), `POST /voice/call/{id}/audio` (raw audio body), `POST /voice/call/{id}/end` (disposition), `GET /voice/call/{id}`.

## What the tests prove (and what they do not)
Proven offline: no raw value (number, PAN, name, loan ref, phone from the call list) reaches the model - in a turn, in its memory or in the disposition request; a placeholder the model writes is never spoken or filled in; a blocked turn adds nothing; the raw transcript is wiped when the call ends; a malformed or self-contradicting disposition is an error, never a default; errors never carry the key or a response body.
NOT proven: anything against the real Sarvam service (no key yet), real phone audio, real speech-to-text output through Layer 1, latency of a spoken turn.

## Findings that shaped the design (measured 20 Nov)
- The gateway hands the LLM ONE masked message per turn, no history. The bot keeps its own memory of MASKED text (`bot.CallLLM`).
- Re-sending an already-masked transcript through the gateway garbles it (`Agent` became `<ADDRESS_1>`). The disposition request therefore sends the RAW transcript once and lets the gateway mask it (kept raw only until the call ends).
- The gateway's reply sanitiser blanks a bare number in the model's answer (`"promised_amount": 4200` -> `[redacted]`) but leaves a rupee-marked amount (`"Rs 4,200"`, up to 8 digits). The model is told to write it that way.
- Customers saying a bare amount ("4200") may have it masked on the way in: the disposition can then lack the amount.

## Limits
One call at a time (shared server lock). Demo identity only. The greeting / repeat lines in Hindi and Marathi are not native-checked. Speech vendors sit outside the masking boundary; retention on Sarvam's side must be set to "no retention" by the workspace owner. Data location and training use are not stated in the docs read.
Not built yet: a console view of the live call and the disposition, an "Arm" button (today: the API call below), a browser microphone page, the chat/WhatsApp channel.


## The phone line (inbound only) - `telephony.py`
```
caller's phone -> Twilio number -> /voice/phone/incoming (greeting) -> <Record> -> /voice/phone/turn -> fetch the recording, DELETE it at Twilio -> speech-to-text
   -> mask -> audit -> LLM -> text-to-speech -> <Play> -> ... -> hang-up / goodbye / 12-turn cap -> disposition
```
Set up (needs a Twilio account; NOT done or tested against real Twilio):
1. A voice-capable Twilio number. Trial accounts: voice only to/from verified numbers and the sign-up country (Twilio docs, read 20 Nov); whether a number you can use accepts calls from your phone is something only the account can tell you.
2. A public https address for this machine's port 8808 (a tunnel). Put it in `.env` as `PUBLIC_BASE_URL=https://...` with `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `SARVAM_API_KEY`.
3. On the number: "A call comes in" -> Webhook, POST, `<PUBLIC_BASE_URL>/voice/phone/incoming`; "Call status changes" -> POST `<PUBLIC_BASE_URL>/voice/phone/status`.
4. `.venv/bin/python -m layer9_voice.run`, then choose who the next call is: `curl -X POST http://127.0.0.1:8808/voice/phone/arm -H 'content-type: application/json' -d '{"borrower_id":"demo-1"}'` (valid 10 minutes, one call). Then dial the number.

Safety built in: every webhook needs a valid `X-Twilio-Signature` (checked against Twilio's published test vector) or gets 403; an unarmed line turns callers away; one call at a time; 12-turn and 20-second-answer caps; the caller's number (`From`/`To`) is never read, stored or logged (tested); each recording is deleted at Twilio right after it is fetched; the recording URL must be on api.twilio.com before credentials are sent; Twilio never hears a stack trace.
Limits: Twilio and Sarvam hear the raw audio and Twilio keeps call metadata (numbers, times) - outside our masking boundary. Recording-ready timing is handled by retrying (Twilio's docs say the file may not be ready yet), not verified live. A Twilio-voiced fallback (`<Say>`) is used when Sarvam speech fails and has no Marathi voice assumed.
Tests: `.venv/bin/python -m layer9_voice.test_phone` (20, Twilio simulated).


## Inside the main console (preferred)
`dpdp-privacy-console.html` (the console at `/console`) has an **L9 Voice bot** tab that uses these routes. It has a Voice call / Chat switch, a microphone button, spoken replies, masked-token highlighting, and the disposition card. It only works when the console is started with `.venv/bin/python -m layer9_voice.run` (the plain `layer8_demo/server.py` does not mount `/voice/*`). The standalone pages `/voice/page` and `/voice/chat` remain as fall-backs.
Verified 20 Nov in a real browser against real Sarvam: spoken turn and typed Hindi chat both masked the number, replied, and produced PROMISE_TO_PAY Rs 4,200; every other console view loaded with no JavaScript error. The Voice view was edited in the teammate's file at the user's request (backup: scratchpad `console_before_voice_edit.html`).
Observed: the same Hindi number got `<PHONE_1>` in the turn but `<LOAN_1>` in the end-of-call transcript pass (two tokens for one value: Layer 1 labelling is not consistent; no leak).

## One job, then the disposition (20 Nov)
The bot's only job is the customer's intent to pay (amount and date), or a refusal / dispute / call-back / wrong person. Off-topic questions get "I can only help with your overdue payment ... when can you pay?" (measured). When the conversation is complete the bot ends its last message with ONE closing word (Goodbye. / अलविदा। / निरोप.); every turn result carries `goodbye: true` and the console tab, `/voice/page` and `/voice/chat` then generate the disposition by themselves, with no click on End (measured in a real browser: 0.8-1.6 s after the closing line; refusal -> REFUSES_TO_PAY, promise -> PROMISE_TO_PAY with amount and date). The detector also accepts "have a good day", "take care", etc. as closings. Tests: 35 (voice) + 20 (phone).
Found while testing: Layer 1 turned the plain English word "I" into a token in "I am not going to pay" (`<LOAN_1> am not going to pay`) and swallowed "and I" after a phone number: a false positive on the most common English word (Layer 1 is frozen / not ours).

## Console tabs (20 Nov)
The console has two L9 tabs built from one component (`makeBot` in `dpdp-privacy-console.html`): **Voice bot** (key 8: microphone + typed fallback, spoken replies) and **Chat bot** (key 9: typed only, no microphone). Each starts its own case with its own channel, shows the masked transcript, and generates the disposition by itself when the bot says goodbye. The borrower's preferred language is now in the bot's prompt. Verified in a real browser against real Sarvam on the running server: both tabs end with PROMISE_TO_PAY; no JavaScript error across all 9 views. Backup of the console file before this edit: scratchpad `console_before_chat_tab.html`.
Known: a Marathi borrower typing English got an English reply plus the Marathi closing word (mixed); the Hindi borrower got Hindi.

## Chat bot tab, reference design (20 Nov)
The console's **Chat bot** tab (key 9) is now a dedicated module in the look of the user's reference (`~/Downloads/dpdp-privacy-console_2.html`, which simulates its vault in the page): chat window, Customer view / LLM X-Ray switch, live "LLM will receive" preview (`/api/detect`, detection only), token vault panel (`/api/vault`, real encrypted entries), live telemetry (real audit events from `/api/case`), disposition card inside the conversation, quick chips. Not included: the reference's "Right to forget" button (not wired to the real erasure workflow). The **Voice bot** tab is unchanged (makeBot, voice only). Verified in a real browser (screenshots) against real Sarvam: promise -> PROMISE_TO_PAY Rs 4,200 by the 7th; X-Ray hides the typed number; no JS error across 9 views. Backup of the console file before the redesign: scratchpad `console_before_chat_redesign.html`.

## Configuration (20 Nov): everything in one place
**Run it:** `./run_demo.sh` - checks everything (`layer9_voice/doctor.py`), picks the first free port from 8808 (it never stops another program), starts the console with Voice bot, Chat bot and Settings, opens the browser. `--no-browser` to skip. `.venv/bin/python -m layer9_voice.doctor [--live]` is the check on its own (`--live` makes two tiny real Sarvam calls); it never prints a secret.
**Change it:** the console's **Settings** tab (or edit `layer9_voice/config.local.json`; git-ignored; defaults are in `config.py`). Editable: models + reasoning, speech model/mode/voice, languages (code + opening/chat/"say again"/goodbye lines + neutral word; add Tamil etc.), the bot's job and extra rules, limits. Applied to the NEXT message, no restart. NOT editable: the privacy/conduct rules in the prompt (always applied, shown read-only), the disposition categories + strict parser, the masking pipeline, every API key (they stay in `.env`). Every value is validated; a broken file is reported and the defaults are used. Changes are written to the audit chain as `VOICE_CONFIG_CHANGED` with the changed key NAMES only; only the DPO or privacy officer role may change settings (the Settings tab acts as the DPO demo identity), any console role may read.
**Your borrowers:** Settings -> Borrowers: add one, remove, or import a CSV (columns `name, language` required; `product, outstanding, dpd, loan_last4, phone, id` optional; comma/semicolon/tab). All-or-nothing with line-numbered errors; language must be a configured language. Stored in `layer9_voice/borrowers.local.json` (git-ignored, mode 600). The phone is display-only and masked; nothing dials it. The model sees none of it (its prompt gets the product, the overdue amount and "already late").
API: `GET/PUT/DELETE /voice/settings`, `POST /voice/settings/borrowers/import`, `POST/DELETE /voice/settings/borrowers[/id]`.
Tests: `test_config` 18, `test_voice` 35, `test_phone` 20 (all isolated from the real config via `_testenv.py`).
