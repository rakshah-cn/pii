"""Skeleton library v2 (authored by Claude in-session; placeholders only, no PII).

Why a grammar: one line like "A: (Hello|Hi) (sir|madam), (this is|I am calling from) {ORG}" expands into
many different sentences, so calls are not repetitive. Alternation groups are written (a|b|c); an empty
choice is allowed (a|).  {SLOTS} are filled later by code with fake values (never by an LLM).

Channels:  call  = speaker-labelled turns (voice transcript style)
           message = short chat/SMS/WhatsApp style, lower-case, abbreviations
           prompt  = a prompt to an LLM that embeds borrower data (single 'User' turn)

!! Hindi / Marathi / Kannada / Tamil lines were written by Claude, NOT checked by native speakers.
!! Run layer0_data/export_review.py and have a native speaker mark each row ok / fix.

Holdout: every line falls in a hash bucket; dev/train use buckets 0-5, test 6-7, final 8-9 (when a pool
is too small to split, all lines are used: see `pick`). This keeps whole template lines from leaking across splits.
"""
import hashlib
import re

_SP = {"A": "Agent", "C": "Customer", "U": "User"}
_ALT = re.compile(r"\(([^()]*)\)")


def T(s):
    """'A: hello ;; C: hi'  ->  [('Agent','hello'),('Customer','hi')]"""
    return [(_SP[p.split(": ", 1)[0].strip()], p.split(": ", 1)[1].strip()) for p in s.split(" ;; ")]


def expand(text, rng):
    while True:
        m = _ALT.search(text)
        if not m:
            return text
        text = text[:m.start()] + rng.choice(m.group(1).split("|")) + text[m.end():]


PARTS = {"dev": range(0, 6), "train": range(0, 6), "test": range(6, 8), "final": range(8, 10)}   # legacy, unused by pick()


def _bucket(x):
    return int(hashlib.md5(repr(x).encode()).hexdigest(), 16) % 10


def _h(x):
    return int(hashlib.md5(repr(x).encode()).hexdigest(), 16)


_ZCACHE = {}


def zones(items):
    """Deterministic holdout allocation for ONE pool (a list of template lines): returns {'dev','test','final'} -> lines.
    Lines are ordered by hash, so the split is stable and independent of list order. Rules:
      n=1  -> dev only            n=2 -> dev 1 + (test or final, by hash parity)
      n>=3 -> test and final get about 25% of the pool each (at least 1), dev gets the rest (>= 1)
    A part with no line in a pool cannot use that pool cleanly (see pick / convo._from)."""
    hit = _ZCACHE.get(id(items))
    if hit is not None and hit[0] is items and hit[1] == len(items):
        return hit[2]
    srt = sorted(items, key=_h)
    n = len(srt)
    if n == 0:
        z = {"dev": [], "test": [], "final": []}
    elif n == 1:
        z = {"dev": srt[:], "test": [], "final": []}
    elif n == 2:
        odd = _h(srt[0]) % 2
        z = {"dev": [srt[0]], "test": [] if odd else [srt[1]], "final": [srt[1]] if odd else []}
    else:
        k = min((n - 1) // 2, max(1, int(0.25 * n + 0.5)))      # ~25% of the pool each for test and final; dev keeps >= 1
        order = srt[::-1]                                  # take held-out lines from the hash-sorted tail, alternating
        test = order[0:2 * k:2]; final = order[1:2 * k:2]
        z = {"dev": [x for x in srt if x not in test and x not in final], "test": test, "final": final}
    _ZCACHE[id(items)] = (items, len(items), z)
    return z


def zone_items(items, part):
    """Lines of this pool that belong to `part` (None = every line)."""
    if not part:
        return items
    return zones(items)["dev" if part == "train" else part]


def pick(items, rng, part=None, cell=None):
    """Pick a line from this part's zone. If the pool has NO line for this part we cannot hold anything out: the row is
    flagged (cell['_fb']) so row['holdout_clean'] is False, and any line is used."""
    sel = zone_items(items, part)
    if sel:
        it = rng.choice(sel)
        if cell is not None and is_ext(it):
            cell["_ext"] = cell.get("_ext", 0) + 1
        return it
    if cell is not None:
        cell.setdefault("_fb", []).append(1)
    return rng.choice(items)


# =========================================================== CALLS: English (also the fallback for every language)
EN = {
 "open": [T(x) for x in [
  "C: Hello. ;; A: Hello. ;; C: Hello?",
  "A: (Hello|Hello sir|Hello ma'am)? ;; C: (Hello|Yes), (who is this|who's speaking|yes tell me)?",
  "C: Hello ;; A: Hello ;; A: Hello (sir|ma'am), (good morning|good afternoon|good evening).",
  "A: (Good morning|Good afternoon|Good evening) (sir|madam). ;; C: (Yes|Haan), (good morning|good afternoon|good evening)(, tell me|).",
  "C: Hello, yes? ;; A: Hello (sir|madam).",
  "A: Hello. ;; A: Hello? ;; C: Yes, (please speak|speak|tell me).",
  "C: Hello hello. ;; A: Hello (sir|madam), can you hear me? ;; C: Yes, I can hear you.",
  "C: (Hello|Yes), who is this? ;; A: Hello (sir|madam), I am calling from {ORG}.",
  "A: Hello? ;; C: (Hello|Hmm) ;; A: Hello? ;; C: Yes?",
  "C: (Haan|Yes) hello. ;; A: (Hello|Namaste) (sir|madam).",
  "A: Hello, (sir|madam)? ;; C: Yes (speaking|tell me).",
  "C: Hello, who? ;; A: Sir, (good morning|good evening), this is {ORG}.",
 ]],
 "intro": [T(x) for x in [
  "A: (Sir|Madam), I am calling from {ORG}. This is a reminder call, it is being recorded.",
  "A: (Sir|Madam), this is {ORG} calling. The call is being recorded.",
  "A: Hello (sir|madam), I am speaking from {ORG}. It's a recorded call.",
  "A: (Sir|Madam), I am calling regarding your (loan|personal loan|business loan) with {ORG}.",
  "A: This is a reminder call from {ORG}. ;; C: (Yes|Okay), tell me.",
  "A: (Sir|Madam), you had taken a (personal|business|two wheeler) loan from {ORG}, right? ;; C: Yes.",
  "A: I'm calling from {ORG} about your loan account. ;; C: (Yes|Haan), (go ahead|tell me).",
  "A: (Sir|Madam), this call is regarding your pending (payment|EMI|dues) with {ORG}. ;; C: Okay.",
  "A: (Hello|Namaste), {ORG} (customer care|collections) (speaking|here). ;; C: (Yes|Haan).",
  "A: Sir, (a quick call|a small reminder) from {ORG}, the call is recorded.",
 ]],
 "due": {
  "emi_reminder": [T(x) for x in [
   "A: Your EMI of {AMOUNT} is due on the {DATE}. ;; C: Okay, I will pay by the {DATE2}.",
   "A: Your due date is the {DATE}, amount {AMOUNT}. ;; C: What? Say again.",
   "A: (Sir|Madam), {AMOUNT} is pending. ;; C: Yes yes, I will pay on the {DATE2}.",
   "A: Your EMI due date is the {DATE}. For that... ;; C: Yes, I know, I will pay.",
   "A: The amount due now is {AMOUNT}. ;; C: How much? ;; A: {AMOUNT}, (sir|madam).",
   "A: Your payment is overdue since the {DATE}. ;; C: Give me time till the {DATE2}.",
   "A: (Sir|Madam), the next EMI of {AMOUNT} is on the {DATE}, please keep (balance|funds) ready. ;; C: Okay okay.",
   "A: Your EMI bounced on the {DATE}, (sir|madam). ;; C: (Oh|Hmm). I will (arrange|pay) by the {DATE2}.",
  ]],
  "dispute": [T(x) for x in [
   "A: Your EMI of {AMOUNT} is due on the {DATE}. ;; C: The money was already deducted, look into it.",
   "A: It shows {AMOUNT} pending, (sir|madam). ;; C: No, I have paid. I received a message that it was cut.",
   "A: (Sir|Madam), the due date is the {DATE}. ;; C: I paid on the {DATE2}. Check again.",
   "A: The system shows {AMOUNT} as unpaid. ;; C: That is wrong. Why are you calling again and again?",
   "A: There is a late fee on {AMOUNT}. ;; C: I will not pay a late fee, the delay was from your side.",
   "A: (Sir|Madam), I see no payment after the {DATE}. ;; C: I have the receipt, I will send it.",
  ]],
  "address_update": [T(x) for x in [
   "A: (Sir|Madam), we need to update your details. Your EMI of {AMOUNT} is due on the {DATE}. ;; C: Okay, tell me what is needed.",
   "A: We have not been able to reach you by post, (sir|madam). ;; C: I moved recently.",
   "A: Your next due date is the {DATE}. ;; C: Yes, but update my details first.",
   "A: The (letter|notice) came back undelivered. ;; C: (Oh|Okay), I changed (house|my place).",
   "A: For the EMI of {AMOUNT} on the {DATE}, we also need the correct details. ;; C: Yes, okay.",
  ]],
  "payment_promise": [T(x) for x in [
   "A: When will you pay the {AMOUNT}, (sir|madam)? ;; C: I will pay on the {DATE2}, I promise.",
   "A: (Sir|Madam), can you pay {AMOUNT} today? ;; C: Not today. By the {DATE2} I will do it.",
   "A: The due date was the {DATE}. When can you pay? ;; C: Wait, I will arrange it by the {DATE2}.",
   "A: (Sir|Madam), will you pay {AMOUNT} by the {DATE}? ;; C: Yes, I will pay, do not worry.",
   "A: Can I note a promise to pay for the {DATE2}? ;; C: (Yes|Okay), note it.",
   "A: If you pay {AMOUNT} today the late fee is waived. ;; C: Let me check, I will tell you by the {DATE2}.",
  ]],
 },
 "close": [T(x) for x in [
  "C: Okay, thank you. ;; A: Thank you (sir|madam).",
  "C: Hmm. Okay. ;; A: Okay, have a good day.",
  "C: Yes yes, thank you, thank you.",
  "C: Okay, I will do it. ;; A: Thank you. Please do it on time.",
  "C: Okay okay. Bye. ;; A: Thank you.",
  "A: Thank you for your time, (sir|madam). ;; C: Yes.",
  "C: Fine, call me later. ;; A: Sure (sir|madam), have a good day.",
  "A: Is there anything else? ;; C: No, that's all.",
  "C: Okay, I understood. ;; A: Thank you, (sir|madam).",
  "A: (Sir|Madam), please do not ignore this. ;; C: Okay okay, I will see.",
  "C: Hmm hmm. ;; A: Hello?",
 ]],
 "noise": ["Hello?", "Hmm.", "Hello hello.", "Yes?", "Okay.", "What?", "Hmm hmm.", "One minute.", "Stay on the line.", "I am here.",
           "Sir, hello?", "Yes, tell me.", "Can you repeat that?", "I can't hear you.", "Please wait.", "Okay okay.", "Haan."],
 "ask": {
  "NAME": {"single": [T(x) for x in [
   "A: Hello (sir|madam), am I speaking with (Mr.|Mrs.|) {NAME}? ;; C: Yes, speaking.",
   "A: Namaste, {NAME} ji? I am calling from {ORG}. ;; C: Yes, tell me.",
   "A: Hello, is this {NAME}? ;; C: Yes, who is this?",
   "A: (Sir|Madam), am I talking to {NAME}? ;; C: Yes, that is me.",
   "A: Is this Mrs. {NAME}'s number? ;; C: No, I am her relative. Tell me.",
   "C: Hello, who are you calling? ;; A: I am calling for {NAME}.",
   "A: May I know your name please? ;; C: {NAME}.",
   "A: Can I speak to {NAME}? ;; C: Speaking.",
  ]]},
  "PHONE": {"single": [T(x) for x in [
   "A: Can you confirm your registered mobile number? ;; C: Yes, it is {PHONE}.",
   "A: Which number should we call you on? ;; C: {PHONE}, call me on this.",
   "A: Please share an alternate contact number. ;; C: Note it down, {PHONE}.",
   "A: (Sir|Madam), your number is not reachable. Give another number. ;; C: Okay, my other number is {PHONE}.",
   "A: What is your (WhatsApp|contact) number? ;; C: {PHONE}.",
   ]], "split": [T(x) for x in [
   "A: Please confirm your registered mobile number. ;; C: {PHONE_P1} ;; A: Yes, go on. ;; C: {PHONE_P2}",
   "A: Tell me your alternate number. ;; C: It is {PHONE_P1} ;; A: Okay? ;; C: {PHONE_P2}, that's it.",
   "A: Your contact number please? ;; C: {PHONE_P1}... ;; A: Hello? Continue. ;; C: ...{PHONE_P2}",
   "A: Mobile number? ;; C: {PHONE_P1} ;; C: Wait. ;; C: {PHONE_P2}",
  ]], "partial": [T(x) for x in [
   "A: Tell me the last four digits of your mobile number. ;; C: {PHONE}",
   "A: Your number ends with? ;; C: It ends with {PHONE}.",
  ]]},
  "AADHAAR": {"single": [T(x) for x in [
   "A: Please tell me your Aadhaar number for verification. ;; C: My Aadhaar is {AADHAAR}.",
   "A: For KYC, I need your Aadhaar. ;; C: Yes, {AADHAAR}.",
   "A: Aadhaar number, (sir|madam)? ;; C: {AADHAAR}.",
  ]], "split": [T(x) for x in [
   "A: Please tell me your Aadhaar number for verification. ;; C: {AADHAAR_P1} ;; A: Okay, and the rest? ;; C: {AADHAAR_P2}",
   "A: Your Aadhaar number, (sir|madam)? ;; C: Wait. {AADHAAR_P1} ;; A: Yes. ;; C: {AADHAAR_P2}",
  ]], "partial": [T(x) for x in [
   "A: Please give the last four digits of your Aadhaar. ;; C: {AADHAAR}",
   "A: Aadhaar ending with? ;; C: Ends with {AADHAAR}.",
  ]]},
  "PAN": {"single": [T(x) for x in [
   "A: Your PAN card number please. ;; C: PAN is {PAN}.",
   "A: Please confirm your PAN. ;; C: It is {PAN}.",
   "A: PAN number, (sir|madam)? ;; C: {PAN}.",
  ]], "split": [T(x) for x in [
   "A: Your PAN card number please. ;; C: {PAN_P1} ;; A: Yes? ;; C: {PAN_P2}",
   "A: PAN number? ;; C: {PAN_P1} ;; A: Please continue. ;; C: {PAN_P2}",
  ]]},
  "ACCOUNT": {"single": [T(x) for x in [
   "A: Which bank account will you pay from? ;; C: My account number is {ACCOUNT}.",
   "A: Please confirm the account for the auto-debit. ;; C: Account number {ACCOUNT}.",
   "A: Your (savings|salary) account number? ;; C: {ACCOUNT}.",
  ]], "split": [T(x) for x in [
   "A: Which bank account will you pay from? ;; C: Account number {ACCOUNT_P1} ;; A: Hello? ;; C: {ACCOUNT_P2}",
   "A: Your bank account number? ;; C: {ACCOUNT_P1} ;; A: Go on, (sir|madam). ;; C: {ACCOUNT_P2}",
  ]], "partial": [T(x) for x in [
   "A: Tell me the last four digits of your account number. ;; C: {ACCOUNT}",
   "A: Account number ending with? ;; C: It ends with {ACCOUNT}.",
  ]]},
  "LOAN": {"single": [T(x) for x in [
   "A: Please tell me your loan account number. ;; C: {LOAN}",
   "A: It will come in the loan account number, (sir|madam). Tell me the number. ;; C: It is {LOAN}.",
   "A: Loan agreement number please. ;; C: {LOAN}, that is it.",
   "A: Which loan is it? Give the (LAN|loan number). ;; C: {LOAN}.",
  ]], "split": [T(x) for x in [
   "A: Your loan account number is? ;; C: {LOAN_P1} ;; A: Okay. ;; C: {LOAN_P2}",
   "A: Please read out your loan number. ;; C: {LOAN_P1} ;; A: Yes? ;; C: {LOAN_P2}",
  ]]},
  "ADDRESS": {"single": [T(x) for x in [
   "A: Is your address still the same? ;; C: No, I have moved. It is {ADDRESS}.",
   "A: Where do you live now, (sir|madam)? ;; C: I live at {ADDRESS}.",
   "A: Please give your current address. ;; C: Write down: {ADDRESS}.",
   "A: Please confirm the address for the notice. ;; C: {ADDRESS}.",
  ]]},
  "UPI": {"single": [T(x) for x in [
   "A: Which UPI id will you pay from? ;; C: It is {UPI}.",
   "A: Share your UPI id for the collect request. ;; C: {UPI}.",
  ]]},
  "EMAIL": {"single": [T(x) for x in [
   "A: Which email id should we send the statement to? ;; C: {EMAIL}.",
   "A: Please confirm your email address. ;; C: It is {EMAIL}.",
  ]]},
  "DOB": {"single": [T(x) for x in [
   "A: For verification, your date of birth? ;; C: {DOB}.",
   "A: Please tell your date of birth. ;; C: It is {DOB}.",
  ]]},
 },
}

# =========================================================== CALLS: other languages (smaller packs, mixed into calls)
def _pack(open_, intro, due, close, noise, ask):
    return {"open": [T(x) for x in open_], "intro": [T(x) for x in intro],
            "due": {k: [T(x) for x in v] for k, v in due.items()}, "close": [T(x) for x in close],
            "noise": noise, "ask": {k: {kk: [T(x) for x in vv] for kk, vv in v.items()} for k, v in ask.items()}}


HI = _pack(
 ["C: हैलो। ;; A: हैलो, नमस्ते (सर|मैडम)।", "A: (नमस्ते|हैलो) (सर|मैडम), (शुभ प्रभात|शुभ संध्या)। ;; C: हाँ, बोलिए।", "C: हैलो, कौन बोल रहा है? ;; A: मैं {ORG} से बोल (रहा|रही) हूँ।"],
 ["A: (सर|मैडम), मैं {ORG} से बोल (रहा|रही) हूँ। यह कॉल रिकॉर्ड हो रही है।", "A: (सर|मैडम), आपके लोन के बारे में कॉल है। ;; C: हाँ जी, बताइए।", "A: नमस्ते, {ORG} से रिमाइंडर कॉल है।"],
 {"emi_reminder": ["A: आपकी (ईएमआई|किस्त) {AMOUNT} की {DATE} को है। ;; C: ठीक है, मैं {DATE2} तक भर दूँ(गा|गी)।", "A: (सर|मैडम), {AMOUNT} बकाया है। ;; C: हाँ हाँ, {DATE2} को दे दूँ(गा|गी)।"],
  "dispute": ["A: {AMOUNT} बाकी दिख रहा है। ;; C: नहीं, पैसे कट चुके हैं, देखिए।", "A: ड्यू डेट {DATE} थी। ;; C: मैंने {DATE2} को भर दिया है।"],
  "address_update": ["A: (सर|मैडम), आपकी डिटेल अपडेट करनी है। ;; C: हाँ, बताइए क्या चाहिए।"],
  "payment_promise": ["A: आप {AMOUNT} कब तक भरेंगे? ;; C: मैं {DATE2} तक भर दूँ(गा|गी), पक्का।", "A: क्या आज भर सकते हैं? ;; C: आज नहीं, {DATE2} तक।"]},
 ["C: ठीक है, धन्यवाद। ;; A: धन्यवाद (सर|मैडम)।", "C: हाँ हाँ, ठीक है। ;; A: आपका दिन शुभ हो।", "C: अच्छा अच्छा, बाद में बात करत(ा|ी) हूँ।"],
 ["हैलो?", "हाँ जी।", "अच्छा।", "हम्म।", "एक मिनट।", "बोलिए।", "फिर से बोलिए।"],
 {"NAME": {"single": ["A: क्या मेरी बात {NAME} जी से हो रही है? ;; C: हाँ, बोल (रहा|रही) हूँ।", "A: (सर|मैडम), आपका नाम? ;; C: {NAME}।"]},
  "PHONE": {"single": ["A: आपका रजिस्टर्ड मोबाइल नंबर बताइए। ;; C: जी, {PHONE}।", "A: दूसरा नंबर दीजिए। ;; C: लिखिए, {PHONE}।"],
            "split": ["A: मोबाइल नंबर बताइए। ;; C: {PHONE_P1} ;; A: हाँ, आगे? ;; C: {PHONE_P2}"], "partial": ["A: मोबाइल नंबर के आखिरी चार अंक बताइए। ;; C: {PHONE}"]},
  "AADHAAR": {"single": ["A: वेरिफिकेशन के लिए आधार नंबर बताइए। ;; C: मेरा आधार {AADHAAR} है।"], "split": ["A: आधार नंबर बताइए। ;; C: {AADHAAR_P1} ;; A: आगे? ;; C: {AADHAAR_P2}"], "partial": ["A: आधार के आखिरी चार अंक? ;; C: {AADHAAR}"]},
  "PAN": {"single": ["A: आपका पैन नंबर बताइए। ;; C: पैन {PAN} है।"], "split": ["A: पैन नंबर? ;; C: {PAN_P1} ;; A: जी? ;; C: {PAN_P2}"]},
  "ACCOUNT": {"single": ["A: किस बैंक खाते से भुगतान करेंगे? ;; C: खाता नंबर {ACCOUNT} है।"], "split": ["A: खाता नंबर बताइए। ;; C: {ACCOUNT_P1} ;; A: आगे? ;; C: {ACCOUNT_P2}"], "partial": ["A: खाता नंबर के आखिरी चार अंक? ;; C: {ACCOUNT}"]},
  "LOAN": {"single": ["A: आपका लोन अकाउंट नंबर बताइए। ;; C: {LOAN}।"], "split": ["A: लोन नंबर बताइए। ;; C: {LOAN_P1} ;; A: जी? ;; C: {LOAN_P2}"]},
  "ADDRESS": {"single": ["A: क्या आपका पता वही है? ;; C: नहीं, अब मैं {ADDRESS} में रहत(ा|ी) हूँ।"]},
  "UPI": {"single": ["A: आपकी यूपीआई आईडी? ;; C: {UPI}।"]}, "EMAIL": {"single": ["A: ईमेल आईडी बताइए। ;; C: {EMAIL}।"]}, "DOB": {"single": ["A: आपकी जन्मतिथि? ;; C: {DOB}।"]}})

HI_ROM = _pack(
 ["C: Hello. ;; A: Hello, namaste (sir|madam).", "A: (Namaste|Hello) (sir|madam), (good morning|good evening). ;; C: Haan ji, boliye.", "C: Hello, kaun bol raha hai? ;; A: Main {ORG} se bol (raha|rahi) hoon."],
 ["A: (Sir|Madam), main {ORG} se bol (raha|rahi) hoon. Ye call record ho rahi hai.", "A: (Sir|Madam), aapke loan ke baare mein call hai. ;; C: Haan ji, bataiye.", "A: Namaste, {ORG} se reminder call hai."],
 {"emi_reminder": ["A: Aapki EMI {AMOUNT} ki {DATE} ko hai. ;; C: Theek hai, main {DATE2} tak bhar dun(ga|gi).", "A: (Sir|Madam), {AMOUNT} pending hai. ;; C: Haan haan, {DATE2} ko de dun(ga|gi)."],
  "dispute": ["A: {AMOUNT} baaki dikh raha hai. ;; C: Nahi, paise kat chuke hain, dekhiye.", "A: Due date {DATE} thi. ;; C: Maine {DATE2} ko bhar diya hai."],
  "address_update": ["A: (Sir|Madam), aapki details update karni hain. ;; C: Haan, bataiye kya chahiye."],
  "payment_promise": ["A: Aap {AMOUNT} kab tak bharenge? ;; C: Main {DATE2} tak bhar dun(ga|gi), pakka.", "A: Aaj bhar sakte hain? ;; C: Aaj nahi, {DATE2} tak."]},
 ["C: Theek hai, thank you. ;; A: Thank you (sir|madam).", "C: Haan haan, theek hai. ;; A: Aapka din shubh ho.", "C: Achha achha, baad mein baat kart(a|i) hoon."],
 ["Hello?", "Haan ji.", "Achha.", "Hmm.", "Ek minute.", "Boliye.", "Phir se boliye."],
 {"NAME": {"single": ["A: Kya meri baat {NAME} ji se ho rahi hai? ;; C: Haan, bol (raha|rahi) hoon.", "A: (Sir|Madam), aapka naam? ;; C: {NAME}."]},
  "PHONE": {"single": ["A: Aapka registered mobile number bataiye. ;; C: Ji, {PHONE}.", "A: Doosra number dijiye. ;; C: Likhiye, {PHONE}."],
            "split": ["A: Mobile number bataiye. ;; C: {PHONE_P1} ;; A: Haan, aage? ;; C: {PHONE_P2}"], "partial": ["A: Mobile number ke aakhri chaar ank bataiye. ;; C: {PHONE}"]},
  "AADHAAR": {"single": ["A: Verification ke liye Aadhaar number bataiye. ;; C: Mera Aadhaar {AADHAAR} hai."], "split": ["A: Aadhaar number bataiye. ;; C: {AADHAAR_P1} ;; A: Aage? ;; C: {AADHAAR_P2}"], "partial": ["A: Aadhaar ke aakhri chaar ank? ;; C: {AADHAAR}"]},
  "PAN": {"single": ["A: Aapka PAN number bataiye. ;; C: PAN {PAN} hai."], "split": ["A: PAN number? ;; C: {PAN_P1} ;; A: Ji? ;; C: {PAN_P2}"]},
  "ACCOUNT": {"single": ["A: Kis bank account se payment karenge? ;; C: Account number {ACCOUNT} hai."], "split": ["A: Account number bataiye. ;; C: {ACCOUNT_P1} ;; A: Aage? ;; C: {ACCOUNT_P2}"], "partial": ["A: Account number ke aakhri chaar ank? ;; C: {ACCOUNT}"]},
  "LOAN": {"single": ["A: Aapka loan account number bataiye. ;; C: {LOAN}."], "split": ["A: Loan number bataiye. ;; C: {LOAN_P1} ;; A: Ji? ;; C: {LOAN_P2}"]},
  "ADDRESS": {"single": ["A: Kya aapka address wahi hai? ;; C: Nahi, ab main {ADDRESS} mein reht(a|i) hoon."]},
  "UPI": {"single": ["A: Aapki UPI id? ;; C: {UPI}."]}, "EMAIL": {"single": ["A: Email id bataiye. ;; C: {EMAIL}."]}, "DOB": {"single": ["A: Aapki date of birth? ;; C: {DOB}."]}})

MR = _pack(
 ["C: हॅलो। ;; A: हॅलो, नमस्कार (सर|मॅडम).", "A: (नमस्कार|हॅलो) (सर|मॅडम), (शुभ सकाळ|शुभ संध्याकाळ). ;; C: हो, बोला.", "C: हॅलो, कोण बोलतंय? ;; A: मी {ORG} मधून बोलत(ोय|ेय)."],
 ["A: (सर|मॅडम), मी {ORG} मधून बोलत(ोय|ेय). हा कॉल रेकॉर्ड होत आहे.", "A: (सर|मॅडम), तुमच्या कर्जाबद्दल कॉल आहे. ;; C: हो, सांगा.", "A: नमस्कार, {ORG} कडून आठवण करून देणारा कॉल आहे."],
 {"emi_reminder": ["A: तुमचा हप्ता {AMOUNT} {DATE} रोजी आहे. ;; C: ठीक आहे, मी {DATE2} पर्यंत भरत(ो|े).", "A: (सर|मॅडम), {AMOUNT} बाकी आहे. ;; C: हो हो, {DATE2} ला देत(ो|े)."],
  "dispute": ["A: {AMOUNT} बाकी दिसतंय. ;; C: नाही, पैसे आधीच कापले गेलेत, बघा.", "A: ड्यू डेट {DATE} होती. ;; C: मी {DATE2} ला भरलंय."],
  "address_update": ["A: (सर|मॅडम), तुमची माहिती अपडेट करायची आहे. ;; C: हो, काय हवंय सांगा."],
  "payment_promise": ["A: तुम्ही {AMOUNT} कधी भरणार? ;; C: मी {DATE2} पर्यंत भरत(ो|े), नक्की.", "A: आज भरू शकता का? ;; C: आज नाही, {DATE2} पर्यंत."]},
 ["C: ठीक आहे, धन्यवाद. ;; A: धन्यवाद (सर|मॅडम).", "C: हो हो, ठीक आहे. ;; A: तुमचा दिवस शुभ जावो.", "C: बरं बरं, नंतर बोलतो."],
 ["हॅलो?", "हो.", "बरं.", "हम्म.", "एक मिनिट.", "बोला.", "पुन्हा सांगा."],
 {"NAME": {"single": ["A: मी {NAME} यांच्याशी बोलत(ोय|ेय) का? ;; C: हो, बोलत(ोय|ेय).", "A: (सर|मॅडम), तुमचं नाव? ;; C: {NAME}."]},
  "PHONE": {"single": ["A: तुमचा नोंदणीकृत मोबाईल नंबर सांगा. ;; C: हो, {PHONE}.", "A: दुसरा नंबर द्या. ;; C: लिहा, {PHONE}."],
            "split": ["A: मोबाईल नंबर सांगा. ;; C: {PHONE_P1} ;; A: हो, पुढे? ;; C: {PHONE_P2}"], "partial": ["A: मोबाईल नंबरचे शेवटचे चार अंक सांगा. ;; C: {PHONE}"]},
  "AADHAAR": {"single": ["A: पडताळणीसाठी आधार नंबर सांगा. ;; C: माझा आधार {AADHAAR} आहे."], "split": ["A: आधार नंबर सांगा. ;; C: {AADHAAR_P1} ;; A: पुढे? ;; C: {AADHAAR_P2}"], "partial": ["A: आधारचे शेवटचे चार अंक? ;; C: {AADHAAR}"]},
  "PAN": {"single": ["A: तुमचा पॅन नंबर सांगा. ;; C: पॅन {PAN} आहे."], "split": ["A: पॅन नंबर? ;; C: {PAN_P1} ;; A: हो? ;; C: {PAN_P2}"]},
  "ACCOUNT": {"single": ["A: कोणत्या बँक खात्यातून पेमेंट कराल? ;; C: खाते क्रमांक {ACCOUNT} आहे."], "split": ["A: खाते क्रमांक सांगा. ;; C: {ACCOUNT_P1} ;; A: पुढे? ;; C: {ACCOUNT_P2}"], "partial": ["A: खाते क्रमांकाचे शेवटचे चार अंक? ;; C: {ACCOUNT}"]},
  "LOAN": {"single": ["A: तुमचा कर्ज खाते क्रमांक सांगा. ;; C: {LOAN}."], "split": ["A: कर्ज क्रमांक सांगा. ;; C: {LOAN_P1} ;; A: हो? ;; C: {LOAN_P2}"]},
  "ADDRESS": {"single": ["A: तुमचा पत्ता तोच आहे का? ;; C: नाही, आता मी {ADDRESS} इथे राहत(ो|े)."]},
  "UPI": {"single": ["A: तुमचा यूपीआय आयडी? ;; C: {UPI}."]}, "EMAIL": {"single": ["A: ईमेल आयडी सांगा. ;; C: {EMAIL}."]}, "DOB": {"single": ["A: तुमची जन्मतारीख? ;; C: {DOB}."]}})

MR_ROM = _pack(
 ["C: Hello. ;; A: Hello, namaskar (sir|madam).", "A: (Namaskar|Hello) (sir|madam), (good morning|good evening). ;; C: Ho, bola.", "C: Hello, kon bolat ahe? ;; A: Mi {ORG} madhun bolt(o|e)."],
 ["A: (Sir|Madam), mi {ORG} madhun bolt(o|e). Ha call record hot ahe.", "A: (Sir|Madam), tumchya loan baddal call ahe. ;; C: Ho, sanga.", "A: Namaskar, {ORG} kadun reminder call ahe."],
 {"emi_reminder": ["A: Tumcha hapta {AMOUNT} {DATE} roji ahe. ;; C: Theek ahe, mi {DATE2} paryant bhart(o|e).", "A: (Sir|Madam), {AMOUNT} baaki ahe. ;; C: Ho ho, {DATE2} la det(o|e)."],
  "dispute": ["A: {AMOUNT} baaki distay. ;; C: Nahi, paise adhich kaple geleet, bagha.", "A: Due date {DATE} hoti. ;; C: Mi {DATE2} la bharlay."],
  "address_update": ["A: (Sir|Madam), tumchi mahiti update karaychi ahe. ;; C: Ho, kay havay sanga."],
  "payment_promise": ["A: Tumhi {AMOUNT} kadhi bharnar? ;; C: Mi {DATE2} paryant bhart(o|e), nakki.", "A: Aaj bharu shakta ka? ;; C: Aaj nahi, {DATE2} paryant."]},
 ["C: Theek ahe, thank you. ;; A: Thank you (sir|madam).", "C: Ho ho, theek ahe. ;; A: Tumcha divas shubh jaavo.", "C: Barr barr, nantar bolt(o|e)."],
 ["Hello?", "Ho.", "Barr.", "Hmm.", "Ek minute.", "Bola.", "Punha sanga."],
 {"NAME": {"single": ["A: Mi {NAME} yanchyashi bolt(oy|ey) ka? ;; C: Ho, bolt(oy|ey).", "A: (Sir|Madam), tumche naav? ;; C: {NAME}."]},
  "PHONE": {"single": ["A: Tumcha registered mobile number sanga. ;; C: Ho, {PHONE}.", "A: Dusra number dya. ;; C: Liha, {PHONE}."],
            "split": ["A: Mobile number sanga. ;; C: {PHONE_P1} ;; A: Ho, pudhe? ;; C: {PHONE_P2}"], "partial": ["A: Mobile number che shevatche char ank sanga. ;; C: {PHONE}"]},
  "AADHAAR": {"single": ["A: Padtalnisathi Aadhaar number sanga. ;; C: Maza Aadhaar {AADHAAR} ahe."], "split": ["A: Aadhaar number sanga. ;; C: {AADHAAR_P1} ;; A: Pudhe? ;; C: {AADHAAR_P2}"], "partial": ["A: Aadhaar che shevatche char ank? ;; C: {AADHAAR}"]},
  "PAN": {"single": ["A: Tumcha PAN number sanga. ;; C: PAN {PAN} ahe."], "split": ["A: PAN number? ;; C: {PAN_P1} ;; A: Ho? ;; C: {PAN_P2}"]},
  "ACCOUNT": {"single": ["A: Konatya bank khatyatun payment karal? ;; C: Account number {ACCOUNT} ahe."], "split": ["A: Account number sanga. ;; C: {ACCOUNT_P1} ;; A: Pudhe? ;; C: {ACCOUNT_P2}"], "partial": ["A: Account number che shevatche char ank? ;; C: {ACCOUNT}"]},
  "LOAN": {"single": ["A: Tumcha loan account number sanga. ;; C: {LOAN}."], "split": ["A: Loan number sanga. ;; C: {LOAN_P1} ;; A: Ho? ;; C: {LOAN_P2}"]},
  "ADDRESS": {"single": ["A: Tumcha patta toch ahe ka? ;; C: Nahi, ata mi {ADDRESS} ithe rahat(o|e)."]},
  "UPI": {"single": ["A: Tumcha UPI id? ;; C: {UPI}."]}, "EMAIL": {"single": ["A: Email id sanga. ;; C: {EMAIL}."]}, "DOB": {"single": ["A: Tumchi janmatarikh? ;; C: {DOB}."]}})

KN = _pack(
 ["C: ಹಲೋ. ;; A: ಹಲೋ, ನಮಸ್ಕಾರ (ಸರ್|ಮೇಡಂ).", "A: (ನಮಸ್ಕಾರ|ಹಲೋ) (ಸರ್|ಮೇಡಂ). ;; C: ಹೌದು, ಹೇಳಿ.", "C: ಹಲೋ, ಯಾರು ಮಾತಾಡ್ತಿರೋದು? ;; A: ನಾನು {ORG} ನಿಂದ ಮಾತಾಡ್ತಿದ್ದೀನಿ."],
 ["A: (ಸರ್|ಮೇಡಂ), ನಾನು {ORG} ನಿಂದ ಕರೆ ಮಾಡ್ತಿದ್ದೀನಿ. ಈ ಕರೆ ರೆಕಾರ್ಡ್ ಆಗ್ತಿದೆ.", "A: (ಸರ್|ಮೇಡಂ), ನಿಮ್ಮ ಸಾಲದ ಬಗ್ಗೆ ಕರೆ. ;; C: ಹೌದು, ಹೇಳಿ.", "A: ನಮಸ್ಕಾರ, {ORG} ನಿಂದ ನೆನಪಿಸುವ ಕರೆ."],
 {"emi_reminder": ["A: ನಿಮ್ಮ ಇಎಂಐ {AMOUNT} {DATE} ರಂದು ಬಾಕಿ ಇದೆ. ;; C: ಸರಿ, ನಾನು {DATE2} ರೊಳಗೆ ಕಟ್ಟುತ್ತೇನೆ.", "A: (ಸರ್|ಮೇಡಂ), {AMOUNT} ಬಾಕಿ ಇದೆ. ;; C: ಹೌದು ಹೌದು, {DATE2} ಕ್ಕೆ ಕಟ್ಟುತ್ತೇನೆ."],
  "dispute": ["A: {AMOUNT} ಬಾಕಿ ತೋರಿಸ್ತಿದೆ. ;; C: ಇಲ್ಲ, ಹಣ ಈಗಾಗಲೇ ಕಟ್ ಆಗಿದೆ, ನೋಡಿ.", "A: ಕೊನೆಯ ದಿನಾಂಕ {DATE} ಆಗಿತ್ತು. ;; C: ನಾನು {DATE2} ಕ್ಕೆ ಕಟ್ಟಿದ್ದೇನೆ."],
  "address_update": ["A: (ಸರ್|ಮೇಡಂ), ನಿಮ್ಮ ವಿವರಗಳನ್ನು ಅಪ್ಡೇಟ್ ಮಾಡಬೇಕು. ;; C: ಸರಿ, ಏನು ಬೇಕು ಹೇಳಿ."],
  "payment_promise": ["A: ನೀವು {AMOUNT} ಯಾವಾಗ ಕಟ್ಟುತ್ತೀರಿ? ;; C: ನಾನು {DATE2} ರೊಳಗೆ ಕಟ್ಟುತ್ತೇನೆ, ಖಂಡಿತ.", "A: ಇಂದು ಕಟ್ಟಬಹುದಾ? ;; C: ಇಂದು ಆಗಲ್ಲ, {DATE2} ರೊಳಗೆ."]},
 ["C: ಸರಿ, ಧನ್ಯವಾದ. ;; A: ಧನ್ಯವಾದ (ಸರ್|ಮೇಡಂ).", "C: ಹೌದು ಹೌದು, ಸರಿ. ;; A: ಶುಭ ದಿನ.", "C: ಸರಿ ಸರಿ, ಆಮೇಲೆ ಮಾತಾಡ್ತೀನಿ."],
 ["ಹಲೋ?", "ಹೌದು.", "ಸರಿ.", "ಹ್ಮ್.", "ಒಂದು ನಿಮಿಷ.", "ಹೇಳಿ.", "ಮತ್ತೆ ಹೇಳಿ."],
 {"NAME": {"single": ["A: ನಾನು {NAME} ಅವರ ಜೊತೆ ಮಾತಾಡ್ತಿದ್ದೀನಾ? ;; C: ಹೌದು, ಹೇಳಿ.", "A: (ಸರ್|ಮೇಡಂ), ನಿಮ್ಮ ಹೆಸರು? ;; C: {NAME}."]},
  "PHONE": {"single": ["A: ನಿಮ್ಮ ನೋಂದಾಯಿತ ಮೊಬೈಲ್ ನಂಬರ್ ಹೇಳಿ. ;; C: ಹೌದು, {PHONE}.", "A: ಬೇರೆ ನಂಬರ್ ಕೊಡಿ. ;; C: ಬರೆದುಕೊಳ್ಳಿ, {PHONE}."],
            "split": ["A: ಮೊಬೈಲ್ ನಂಬರ್ ಹೇಳಿ. ;; C: {PHONE_P1} ;; A: ಹೌದು, ಮುಂದೆ? ;; C: {PHONE_P2}"], "partial": ["A: ಮೊಬೈಲ್ ನಂಬರ್‌ನ ಕೊನೆಯ ನಾಲ್ಕು ಅಂಕಿ ಹೇಳಿ. ;; C: {PHONE}"]},
  "AADHAAR": {"single": ["A: ಪರಿಶೀಲನೆಗೆ ಆಧಾರ್ ನಂಬರ್ ಹೇಳಿ. ;; C: ನನ್ನ ಆಧಾರ್ {AADHAAR}."], "split": ["A: ಆಧಾರ್ ನಂಬರ್ ಹೇಳಿ. ;; C: {AADHAAR_P1} ;; A: ಮುಂದೆ? ;; C: {AADHAAR_P2}"], "partial": ["A: ಆಧಾರ್‌ನ ಕೊನೆಯ ನಾಲ್ಕು ಅಂಕಿ? ;; C: {AADHAAR}"]},
  "PAN": {"single": ["A: ನಿಮ್ಮ ಪ್ಯಾನ್ ನಂಬರ್ ಹೇಳಿ. ;; C: ಪ್ಯಾನ್ {PAN}."], "split": ["A: ಪ್ಯಾನ್ ನಂಬರ್? ;; C: {PAN_P1} ;; A: ಹೌದು? ;; C: {PAN_P2}"]},
  "ACCOUNT": {"single": ["A: ಯಾವ ಬ್ಯಾಂಕ್ ಖಾತೆಯಿಂದ ಪಾವತಿ ಮಾಡ್ತೀರಿ? ;; C: ಖಾತೆ ಸಂಖ್ಯೆ {ACCOUNT}."], "split": ["A: ಖಾತೆ ಸಂಖ್ಯೆ ಹೇಳಿ. ;; C: {ACCOUNT_P1} ;; A: ಮುಂದೆ? ;; C: {ACCOUNT_P2}"], "partial": ["A: ಖಾತೆ ಸಂಖ್ಯೆಯ ಕೊನೆಯ ನಾಲ್ಕು ಅಂಕಿ? ;; C: {ACCOUNT}"]},
  "LOAN": {"single": ["A: ನಿಮ್ಮ ಸಾಲದ ಖಾತೆ ಸಂಖ್ಯೆ ಹೇಳಿ. ;; C: {LOAN}."], "split": ["A: ಸಾಲದ ಸಂಖ್ಯೆ ಹೇಳಿ. ;; C: {LOAN_P1} ;; A: ಹೌದು? ;; C: {LOAN_P2}"]},
  "ADDRESS": {"single": ["A: ನಿಮ್ಮ ವಿಳಾಸ ಅದೇನಾ? ;; C: ಇಲ್ಲ, ಈಗ ನಾನು {ADDRESS} ಇಲ್ಲಿ ಇದ್ದೀನಿ."]},
  "UPI": {"single": ["A: ನಿಮ್ಮ ಯುಪಿಐ ಐಡಿ? ;; C: {UPI}."]}, "EMAIL": {"single": ["A: ಇಮೇಲ್ ಐಡಿ ಹೇಳಿ. ;; C: {EMAIL}."]}, "DOB": {"single": ["A: ನಿಮ್ಮ ಜನ್ಮ ದಿನಾಂಕ? ;; C: {DOB}."]}})

KN_ROM = _pack(
 ["C: Hello. ;; A: Hello, namaskara (sir|madam).", "A: (Namaskara|Hello) (sir|madam). ;; C: Haudu, heli.", "C: Hello, yaaru matadtiddira? ;; A: Naanu {ORG} inda matadtiddini."],
 ["A: (Sir|Madam), naanu {ORG} inda call madtiddini. Ee call record agtide.", "A: (Sir|Madam), nimma loan bagge call. ;; C: Haudu, heli.", "A: Namaskara, {ORG} inda reminder call."],
 {"emi_reminder": ["A: Nimma EMI {AMOUNT} {DATE} ge baaki ide. ;; C: Sari, naanu {DATE2} olage kattuttene.", "A: (Sir|Madam), {AMOUNT} pending ide. ;; C: Haudu haudu, {DATE2} ge kattuttene."],
  "dispute": ["A: {AMOUNT} baaki torisutide. ;; C: Illa, haNa already cut aagide, nodi.", "A: Due date {DATE} aagittu. ;; C: Naanu {DATE2} ge kattidde."],
  "address_update": ["A: (Sir|Madam), nimma details update madbeku. ;; C: Sari, enu beku heli."],
  "payment_promise": ["A: Neevu {AMOUNT} yaavaga kattuttiri? ;; C: Naanu {DATE2} olage kattuttene, khandita.", "A: Indu kattabahuda? ;; C: Indu aagalla, {DATE2} olage."]},
 ["C: Sari, dhanyavada. ;; A: Dhanyavada (sir|madam).", "C: Haudu haudu, sari. ;; A: Shubha dina.", "C: Sari sari, aamele matadtini."],
 ["Hello?", "Haudu.", "Sari.", "Hmm.", "Ondu nimisha.", "Heli.", "Matte heli."],
 {"NAME": {"single": ["A: Naanu {NAME} avara jote matadtiddinaa? ;; C: Haudu, heli.", "A: (Sir|Madam), nimma hesaru? ;; C: {NAME}."]},
  "PHONE": {"single": ["A: Nimma registered mobile number heli. ;; C: Haudu, {PHONE}.", "A: Bere number kodi. ;; C: Bareyiri, {PHONE}."],
            "split": ["A: Mobile number heli. ;; C: {PHONE_P1} ;; A: Haudu, munde? ;; C: {PHONE_P2}"], "partial": ["A: Mobile number ge kone naalku anki heli. ;; C: {PHONE}"]},
  "AADHAAR": {"single": ["A: Verification ge Aadhaar number heli. ;; C: Nanna Aadhaar {AADHAAR}."], "split": ["A: Aadhaar number heli. ;; C: {AADHAAR_P1} ;; A: Munde? ;; C: {AADHAAR_P2}"], "partial": ["A: Aadhaar kone naalku anki? ;; C: {AADHAAR}"]},
  "PAN": {"single": ["A: Nimma PAN number heli. ;; C: PAN {PAN}."], "split": ["A: PAN number? ;; C: {PAN_P1} ;; A: Haudu? ;; C: {PAN_P2}"]},
  "ACCOUNT": {"single": ["A: Yaava bank account inda payment madtira? ;; C: Account number {ACCOUNT}."], "split": ["A: Account number heli. ;; C: {ACCOUNT_P1} ;; A: Munde? ;; C: {ACCOUNT_P2}"], "partial": ["A: Account number kone naalku anki? ;; C: {ACCOUNT}"]},
  "LOAN": {"single": ["A: Nimma loan account number heli. ;; C: {LOAN}."], "split": ["A: Loan number heli. ;; C: {LOAN_P1} ;; A: Haudu? ;; C: {LOAN_P2}"]},
  "ADDRESS": {"single": ["A: Nimma address adene? ;; C: Illa, eega naanu {ADDRESS} alli iddini."]},
  "UPI": {"single": ["A: Nimma UPI id? ;; C: {UPI}."]}, "EMAIL": {"single": ["A: Email id heli. ;; C: {EMAIL}."]}, "DOB": {"single": ["A: Nimma huttida dina? ;; C: {DOB}."]}})

TA = _pack(
 ["C: ஹலோ. ;; A: ஹலோ, வணக்கம் (சார்|மேடம்).", "A: (வணக்கம்|ஹலோ) (சார்|மேடம்). ;; C: ஆமா, சொல்லுங்க.", "C: ஹலோ, யார் பேசுறீங்க? ;; A: நான் {ORG} லிருந்து பேசுறேன்."],
 ["A: (சார்|மேடம்), நான் {ORG} லிருந்து அழைக்கிறேன். இந்த அழைப்பு பதிவு செய்யப்படுகிறது.", "A: (சார்|மேடம்), உங்கள் கடன் பற்றிய அழைப்பு. ;; C: ஆமா, சொல்லுங்க.", "A: வணக்கம், {ORG} லிருந்து நினைவூட்டல் அழைப்பு."],
 {"emi_reminder": ["A: உங்கள் இஎம்ஐ {AMOUNT} {DATE} அன்று நிலுவையில் உள்ளது. ;; C: சரி, நான் {DATE2} க்குள் கட்டுகிறேன்.", "A: (சார்|மேடம்), {AMOUNT} நிலுவை. ;; C: ஆமா ஆமா, {DATE2} க்கு கட்டுறேன்."],
  "dispute": ["A: {AMOUNT} நிலுவை காட்டுது. ;; C: இல்லை, பணம் ஏற்கனவே பிடிச்சாச்சு, பாருங்க.", "A: கடைசி தேதி {DATE}. ;; C: நான் {DATE2} அன்று கட்டிட்டேன்."],
  "address_update": ["A: (சார்|மேடம்), உங்க விவரங்களை புதுப்பிக்கணும். ;; C: சரி, என்ன வேணும் சொல்லுங்க."],
  "payment_promise": ["A: நீங்க {AMOUNT} எப்போ கட்டுவீங்க? ;; C: நான் {DATE2} க்குள் கட்டுறேன், நிச்சயமா.", "A: இன்னைக்கு கட்ட முடியுமா? ;; C: இன்னைக்கு முடியாது, {DATE2} க்குள்."]},
 ["C: சரி, நன்றி. ;; A: நன்றி (சார்|மேடம்).", "C: ஆமா ஆமா, சரி. ;; A: நல்ல நாள்.", "C: சரி சரி, அப்புறம் பேசுறேன்."],
 ["ஹலோ?", "ஆமா.", "சரி.", "ம்ம்.", "ஒரு நிமிஷம்.", "சொல்லுங்க.", "மறுபடி சொல்லுங்க."],
 {"NAME": {"single": ["A: நான் {NAME} அவர்களிடம் பேசுறேனா? ;; C: ஆமா, சொல்லுங்க.", "A: (சார்|மேடம்), உங்க பேரு? ;; C: {NAME}."]},
  "PHONE": {"single": ["A: உங்க பதிவு செய்த மொபைல் நம்பர் சொல்லுங்க. ;; C: ஆமா, {PHONE}.", "A: வேற நம்பர் கொடுங்க. ;; C: எழுதிக்கோங்க, {PHONE}."],
            "split": ["A: மொபைல் நம்பர் சொல்லுங்க. ;; C: {PHONE_P1} ;; A: ஆமா, அடுத்து? ;; C: {PHONE_P2}"], "partial": ["A: மொபைல் நம்பரின் கடைசி நான்கு இலக்கம் சொல்லுங்க. ;; C: {PHONE}"]},
  "AADHAAR": {"single": ["A: சரிபார்ப்புக்கு ஆதார் நம்பர் சொல்லுங்க. ;; C: என் ஆதார் {AADHAAR}."], "split": ["A: ஆதார் நம்பர் சொல்லுங்க. ;; C: {AADHAAR_P1} ;; A: அடுத்து? ;; C: {AADHAAR_P2}"], "partial": ["A: ஆதாரின் கடைசி நான்கு இலக்கம்? ;; C: {AADHAAR}"]},
  "PAN": {"single": ["A: உங்க பான் நம்பர் சொல்லுங்க. ;; C: பான் {PAN}."], "split": ["A: பான் நம்பர்? ;; C: {PAN_P1} ;; A: ஆமா? ;; C: {PAN_P2}"]},
  "ACCOUNT": {"single": ["A: எந்த வங்கி கணக்கிலிருந்து கட்டுவீங்க? ;; C: கணக்கு எண் {ACCOUNT}."], "split": ["A: கணக்கு எண் சொல்லுங்க. ;; C: {ACCOUNT_P1} ;; A: அடுத்து? ;; C: {ACCOUNT_P2}"], "partial": ["A: கணக்கு எண்ணின் கடைசி நான்கு இலக்கம்? ;; C: {ACCOUNT}"]},
  "LOAN": {"single": ["A: உங்க கடன் கணக்கு எண் சொல்லுங்க. ;; C: {LOAN}."], "split": ["A: கடன் எண் சொல்லுங்க. ;; C: {LOAN_P1} ;; A: ஆமா? ;; C: {LOAN_P2}"]},
  "ADDRESS": {"single": ["A: உங்க முகவரி அதேவா? ;; C: இல்லை, இப்போ நான் {ADDRESS} இல் இருக்கேன்."]},
  "UPI": {"single": ["A: உங்க யுபிஐ ஐடி? ;; C: {UPI}."]}, "EMAIL": {"single": ["A: மின்னஞ்சல் ஐடி சொல்லுங்க. ;; C: {EMAIL}."]}, "DOB": {"single": ["A: உங்க பிறந்த தேதி? ;; C: {DOB}."]}})

TA_ROM = _pack(
 ["C: Hello. ;; A: Hello, vanakkam (sir|madam).", "A: (Vanakkam|Hello) (sir|madam). ;; C: Aama, sollunga.", "C: Hello, yaaru pesuringa? ;; A: Naan {ORG} la irundhu pesuren."],
 ["A: (Sir|Madam), naan {ORG} la irundhu call pannuren. Indha call record aagudhu.", "A: (Sir|Madam), ungal loan pathi call. ;; C: Aama, sollunga.", "A: Vanakkam, {ORG} la irundhu reminder call."],
 {"emi_reminder": ["A: Ungal EMI {AMOUNT} {DATE} anru baaki irukku. ;; C: Sari, naan {DATE2} kulla katturen.", "A: (Sir|Madam), {AMOUNT} pending. ;; C: Aama aama, {DATE2} ku katturen."],
  "dispute": ["A: {AMOUNT} baaki kaattudhu. ;; C: Illa, panam already pudichaachu, paarunga.", "A: Last date {DATE}. ;; C: Naan {DATE2} anru kattitten."],
  "address_update": ["A: (Sir|Madam), unga details update pannanum. ;; C: Sari, enna venum sollunga."],
  "payment_promise": ["A: Neenga {AMOUNT} eppo kattuvinga? ;; C: Naan {DATE2} kulla katturen, nichayama.", "A: Innaikku katta mudiyuma? ;; C: Innaikku mudiyadhu, {DATE2} kulla."]},
 ["C: Sari, nandri. ;; A: Nandri (sir|madam).", "C: Aama aama, sari. ;; A: Nalla naal.", "C: Sari sari, appuram pesuren."],
 ["Hello?", "Aama.", "Sari.", "Hmm.", "Oru nimisham.", "Sollunga.", "Marubadi sollunga."],
 {"NAME": {"single": ["A: Naan {NAME} kitta pesuren aa? ;; C: Aama, sollunga.", "A: (Sir|Madam), ungal peyar? ;; C: {NAME}."]},
  "PHONE": {"single": ["A: Ungal registered mobile number sollunga. ;; C: Aama, {PHONE}.", "A: Vera number kudunga. ;; C: Ezhudhikonga, {PHONE}."],
            "split": ["A: Mobile number sollunga. ;; C: {PHONE_P1} ;; A: Aama, adutthu? ;; C: {PHONE_P2}"], "partial": ["A: Mobile number oda kadaisi naalu ilakkam sollunga. ;; C: {PHONE}"]},
  "AADHAAR": {"single": ["A: Verification ku Aadhaar number sollunga. ;; C: En Aadhaar {AADHAAR}."], "split": ["A: Aadhaar number sollunga. ;; C: {AADHAAR_P1} ;; A: Adutthu? ;; C: {AADHAAR_P2}"], "partial": ["A: Aadhaar oda kadaisi naalu ilakkam? ;; C: {AADHAAR}"]},
  "PAN": {"single": ["A: Unga PAN number sollunga. ;; C: PAN {PAN}."], "split": ["A: PAN number? ;; C: {PAN_P1} ;; A: Aama? ;; C: {PAN_P2}"]},
  "ACCOUNT": {"single": ["A: Entha bank account la irundhu kattuvinga? ;; C: Account number {ACCOUNT}."], "split": ["A: Account number sollunga. ;; C: {ACCOUNT_P1} ;; A: Adutthu? ;; C: {ACCOUNT_P2}"], "partial": ["A: Account number oda kadaisi naalu ilakkam? ;; C: {ACCOUNT}"]},
  "LOAN": {"single": ["A: Unga loan account number sollunga. ;; C: {LOAN}."], "split": ["A: Loan number sollunga. ;; C: {LOAN_P1} ;; A: Aama? ;; C: {LOAN_P2}"]},
  "ADDRESS": {"single": ["A: Unga address adhe aa? ;; C: Illa, ippo naan {ADDRESS} la irukken."]},
  "UPI": {"single": ["A: Unga UPI id? ;; C: {UPI}."]}, "EMAIL": {"single": ["A: Email id sollunga. ;; C: {EMAIL}."]}, "DOB": {"single": ["A: Unga pirandha thethi? ;; C: {DOB}."]}})

CALL_PACKS = {"en": EN, "hi": HI, "hi_rom": HI_ROM, "mr": MR, "mr_rom": MR_ROM, "kn": KN, "kn_rom": KN_ROM, "ta": TA, "ta_rom": TA_ROM}

# =========================================================== MESSAGES (chat / SMS / WhatsApp)
MSG_EN = {
 "open": [T(x) for x in [
  "A: Dear customer, your EMI of {AMOUNT} for your loan with {ORG} is due on {DATE}. Pay on time to avoid late fees.",
  "A: (Hi|Hello) (sir|madam), (this is|msg from) {ORG}. EMI {AMOUNT} due {DATE}. Pls pay.",
  "A: Reminder: {AMOUNT} overdue since {DATE}. Please clear it today. - {ORG}",
  "C: hi (sir|madam) ;; A: Hello, how can I help you?",
  "C: (hello|hi|hey) ;; C: i got a (msg|message) about my emi ;; A: Yes sir, {AMOUNT} is due on {DATE}.",
  "A: Thank you for your payment of {AMOUNT} towards your loan. - {ORG}",
 ]],
 "close": [T(x) for x in [
  "C: ok thanks ;; A: Thank you.", "C: ok will pay by {DATE2}", "C: k", "C: thx 🙏", "A: Thank you, have a nice day. 🙂",
  "C: pls dont call again, i will pay by {DATE2}", "C: ok done", "C: i will pay tomorrow ;; A: Noted, thank you.",
 ]],
 "ask": {
  "NAME": {"single": [T(x) for x in ["A: May I know your (full |)name pls? ;; C: {NAME}", "A: Pls confirm name ;; C: {NAME} here", "C: this is {NAME}, regarding my loan", "C: hi {NAME} here"]]},
  "PHONE": {"single": [T(x) for x in ["A: pls share ur registered (mobile|contact) (no|number) ;; C: {PHONE}", "A: Which number to call you on? ;; C: {PHONE} this one", "C: my new no is {PHONE}", "C: call me on {PHONE}"]],
            "split": [T(x) for x in ["A: pls share ur mobile no ;; C: {PHONE_P1} ;; C: {PHONE_P2}", "A: number? ;; C: {PHONE_P1} ;; C: sorry ;; C: {PHONE_P2}"]],
            "partial": [T(x) for x in ["A: last 4 digits of ur mobile? ;; C: {PHONE}"]]},
  "AADHAAR": {"single": [T(x) for x in ["A: pls share Aadhaar for KYC ;; C: {AADHAAR}", "C: my aadhaar is {AADHAAR}", "A: Aadhaar number? ;; C: here {AADHAAR}"]],
              "split": [T(x) for x in ["A: pls share Aadhaar for KYC ;; C: {AADHAAR_P1} ;; C: {AADHAAR_P2}"]],
              "partial": [T(x) for x in ["A: last 4 digits of aadhaar? ;; C: {AADHAAR}"]]},
  "PAN": {"single": [T(x) for x in ["A: PAN pls ;; C: {PAN}", "C: my pan {PAN}", "A: share ur PAN card no ;; C: {PAN} thanks"]], "split": [T(x) for x in ["A: PAN pls ;; C: {PAN_P1} ;; C: {PAN_P2}"]]},
  "ACCOUNT": {"single": [T(x) for x in ["A: Account no for auto debit? ;; C: {ACCOUNT}", "C: a/c no {ACCOUNT}", "C: pay from account {ACCOUNT}"]],
              "split": [T(x) for x in ["A: Account no? ;; C: {ACCOUNT_P1} ;; C: {ACCOUNT_P2}"]], "partial": [T(x) for x in ["A: last 4 digits of a/c? ;; C: {ACCOUNT}"]]},
  "LOAN": {"single": [T(x) for x in ["A: pls share loan (no|number|LAN) ;; C: {LOAN}", "C: my loan no is {LOAN}", "A: Which loan? ;; C: {LOAN}"]], "split": [T(x) for x in ["A: loan no? ;; C: {LOAN_P1} ;; C: {LOAN_P2}"]]},
  "ADDRESS": {"single": [T(x) for x in ["A: Pls share current address ;; C: {ADDRESS}", "C: my new address: {ADDRESS}", "C: i shifted. {ADDRESS}"]]},
  "UPI": {"single": [T(x) for x in ["A: UPI id to send collect request? ;; C: {UPI}", "C: pay to {UPI}"]]},
  "EMAIL": {"single": [T(x) for x in ["A: email id for statement? ;; C: {EMAIL}", "C: send it to {EMAIL}"]]},
  "DOB": {"single": [T(x) for x in ["A: dob for verification ;; C: {DOB}", "C: my dob is {DOB}"]]},
 },
}
MSG_ROM = {   # hinglish/roman code-mixed chat, reused for hi_rom, mr_rom, kn_rom, ta_rom
 "open": [T(x) for x in ["A: Dear customer, aapki EMI {AMOUNT} {DATE} ko due hai. - {ORG}", "C: hello sir ;; C: emi ke baare me msg aaya ;; A: Haan {AMOUNT} due hai {DATE} ko."]],
 "close": [T(x) for x in ["C: ok thanks ;; A: Thank you.", "C: theek hai, {DATE2} tak bhar dun(ga|gi)", "C: sari sir", "C: haan ji ok", "C: ok done"]],
 "ask": {
  "NAME": {"single": [T(x) for x in ["A: aapka naam? ;; C: {NAME}", "C: main {NAME} bol (raha|rahi) hu"]]},
  "PHONE": {"single": [T(x) for x in ["A: apna mobile no bhejiye ;; C: {PHONE}", "C: mera naya number {PHONE}"]], "split": [T(x) for x in ["A: number? ;; C: {PHONE_P1} ;; C: {PHONE_P2}"]], "partial": [T(x) for x in ["A: last 4 digit? ;; C: {PHONE}"]]},
  "AADHAAR": {"single": [T(x) for x in ["A: KYC ke liye aadhaar bhejiye ;; C: {AADHAAR}"]], "split": [T(x) for x in ["A: aadhaar? ;; C: {AADHAAR_P1} ;; C: {AADHAAR_P2}"]], "partial": [T(x) for x in ["A: aadhaar ke last 4 digit ;; C: {AADHAAR}"]]},
  "PAN": {"single": [T(x) for x in ["A: PAN bhejiye ;; C: {PAN}"]], "split": [T(x) for x in ["A: PAN? ;; C: {PAN_P1} ;; C: {PAN_P2}"]]},
  "ACCOUNT": {"single": [T(x) for x in ["A: account number? ;; C: {ACCOUNT}"]], "split": [T(x) for x in ["A: a/c no? ;; C: {ACCOUNT_P1} ;; C: {ACCOUNT_P2}"]], "partial": [T(x) for x in ["A: a/c ke last 4 digit ;; C: {ACCOUNT}"]]},
  "LOAN": {"single": [T(x) for x in ["A: loan number bhejiye ;; C: {LOAN}"]], "split": [T(x) for x in ["A: loan no? ;; C: {LOAN_P1} ;; C: {LOAN_P2}"]]},
  "ADDRESS": {"single": [T(x) for x in ["A: naya address bhejiye ;; C: {ADDRESS}"]]},
  "UPI": {"single": [T(x) for x in ["A: UPI id? ;; C: {UPI}"]]}, "EMAIL": {"single": [T(x) for x in ["A: email id? ;; C: {EMAIL}"]]}, "DOB": {"single": [T(x) for x in ["A: dob? ;; C: {DOB}"]]},
 },
}
MSG_NATIVE = {   # short native-script chat lines, per language (hi/mr/kn/ta)
 "hi": {"open": [T("A: प्रिय ग्राहक, आपकी ईएमआई {AMOUNT} {DATE} को देय है। - {ORG}")], "close": [T("C: ठीक है, धन्यवाद"), T("C: {DATE2} तक भर दूँ(गा|गी)")],
        "ask": {"NAME": {"single": [T("A: आपका नाम? ;; C: {NAME}")]}, "PHONE": {"single": [T("A: अपना मोबाइल नंबर भेजिए ;; C: {PHONE}")], "split": [T("A: नंबर? ;; C: {PHONE_P1} ;; C: {PHONE_P2}")]},
                "ADDRESS": {"single": [T("A: नया पता भेजिए ;; C: {ADDRESS}")]}}},
 "mr": {"open": [T("A: प्रिय ग्राहक, तुमचा हप्ता {AMOUNT} {DATE} रोजी देय आहे. - {ORG}")], "close": [T("C: ठीक आहे, धन्यवाद"), T("C: {DATE2} पर्यंत भरत(ो|े)")],
        "ask": {"NAME": {"single": [T("A: तुमचं नाव? ;; C: {NAME}")]}, "PHONE": {"single": [T("A: तुमचा मोबाईल नंबर पाठवा ;; C: {PHONE}")], "split": [T("A: नंबर? ;; C: {PHONE_P1} ;; C: {PHONE_P2}")]},
                "ADDRESS": {"single": [T("A: नवीन पत्ता पाठवा ;; C: {ADDRESS}")]}}},
 "kn": {"open": [T("A: ಪ್ರಿಯ ಗ್ರಾಹಕರೇ, ನಿಮ್ಮ ಇಎಂಐ {AMOUNT} {DATE} ರಂದು ಬಾಕಿ ಇದೆ. - {ORG}")], "close": [T("C: ಸರಿ, ಧನ್ಯವಾದ"), T("C: {DATE2} ರೊಳಗೆ ಕಟ್ಟುತ್ತೇನೆ")],
        "ask": {"NAME": {"single": [T("A: ನಿಮ್ಮ ಹೆಸರು? ;; C: {NAME}")]}, "PHONE": {"single": [T("A: ನಿಮ್ಮ ಮೊಬೈಲ್ ನಂಬರ್ ಕಳುಹಿಸಿ ;; C: {PHONE}")], "split": [T("A: ನಂಬರ್? ;; C: {PHONE_P1} ;; C: {PHONE_P2}")]},
                "ADDRESS": {"single": [T("A: ಹೊಸ ವಿಳಾಸ ಕಳುಹಿಸಿ ;; C: {ADDRESS}")]}}},
 "ta": {"open": [T("A: அன்புள்ள வாடிக்கையாளரே, உங்கள் இஎம்ஐ {AMOUNT} {DATE} அன்று நிலுவையில் உள்ளது. - {ORG}")], "close": [T("C: சரி, நன்றி"), T("C: {DATE2} க்குள் கட்டுறேன்")],
        "ask": {"NAME": {"single": [T("A: உங்க பேரு? ;; C: {NAME}")]}, "PHONE": {"single": [T("A: உங்க மொபைல் நம்பர் அனுப்புங்க ;; C: {PHONE}")], "split": [T("A: நம்பர்? ;; C: {PHONE_P1} ;; C: {PHONE_P2}")]},
                "ADDRESS": {"single": [T("A: புது முகவரி அனுப்புங்க ;; C: {ADDRESS}")]}}},
}

# =========================================================== PROMPTS (to an LLM, with borrower data embedded)
PROMPT_INSTR = [
 "Summarise the following (call|conversation) with the borrower in (two|three) lines.",
 "Draft a (polite |)(WhatsApp|SMS|email) reminder for the borrower below about the EMI of {AMOUNT} due on the {DATE}.",
 "Classify the intent of this customer (message|record): (dispute|promise to pay|address change|other).",
 "You are a collections assistant. Using the customer record below, write a short reply.",
 "Extract the promise-to-pay date and the amount from this (note|record).",
 "Rewrite this CRM note in formal English.",
 "Is the following borrower (eligible for a settlement|likely to default)? Answer yes or no with a reason.",
 "Translate the following note into (Hindi|Kannada|Tamil|Marathi).",
 "Based on the case below, suggest the next best action for the agent.",
]
PROMPT_SNIP = {
 "NAME": ["Customer name: {NAME}", "Name - {NAME}", "borrower: {NAME}", "\"name\": \"{NAME}\"", "Mr/Ms {NAME}"],
 "PHONE": ["Phone: {PHONE}", "Mobile - {PHONE}", "contact no. {PHONE}", "\"phone\": \"{PHONE}\"", "call back on {PHONE}"],
 "AADHAAR": ["Aadhaar: {AADHAAR}", "UID {AADHAAR}", "\"aadhaar\": \"{AADHAAR}\"", "KYC id (Aadhaar) {AADHAAR}"],
 "PAN": ["PAN: {PAN}", "PAN card - {PAN}", "\"pan\": \"{PAN}\""],
 "ACCOUNT": ["A/c no: {ACCOUNT}", "Bank account - {ACCOUNT}", "\"account_number\": \"{ACCOUNT}\"", "account {ACCOUNT}"],
 "LOAN": ["Loan ID: {LOAN}", "LAN {LOAN}", "\"loan_id\": \"{LOAN}\"", "agreement no. {LOAN}"],
 "ADDRESS": ["Address: {ADDRESS}", "resides at {ADDRESS}", "\"address\": \"{ADDRESS}\""],
 "UPI": ["UPI: {UPI}", "\"upi\": \"{UPI}\""], "EMAIL": ["Email: {EMAIL}", "\"email\": \"{EMAIL}\""], "DOB": ["DOB: {DOB}", "born on {DOB}", "\"dob\": \"{DOB}\""],
}
PROMPT_SPOKEN = ["Customer: (my|the) {CUE} is {X}", "Customer: {X}, (that's it|yes)", "Customer: it is {X}, (okay|yes)"]
PROMPT_CUE = {"PHONE": "mobile number", "AADHAAR": "Aadhaar", "ACCOUNT": "account number", "PAN": "PAN", "LOAN": "loan number"}


# Roman-script chat packs for Marathi / Kannada / Tamil (before, every *_rom language got Hinglish chat lines)
MSG_MR_ROM = {
 "open": [T(x) for x in ["A: Dear customer, tumcha hapta {AMOUNT} {DATE} la due ahe. - {ORG}", "C: hello sir ;; C: hapta baddal msg aala ;; A: Ho {AMOUNT} due ahe {DATE} la."]],
 "close": [T(x) for x in ["C: ok thanks ;; A: Thank you.", "C: theek ahe, {DATE2} paryant bharto", "C: ho sir ok", "C: ok done"]],
 "ask": {"NAME": {"single": [T(x) for x in ["A: tumche naav? ;; C: {NAME}", "C: mi {NAME} bolto ahe"]]},
         "PHONE": {"single": [T(x) for x in ["A: tumcha mobile no pathva ;; C: {PHONE}", "C: maza navin number {PHONE}"]], "split": [T(x) for x in ["A: number? ;; C: {PHONE_P1} ;; C: {PHONE_P2}"]], "partial": [T(x) for x in ["A: last 4 digit sanga ;; C: {PHONE}"]]},
         "ADDRESS": {"single": [T(x) for x in ["A: navin patta pathva ;; C: {ADDRESS}"]]}},
}
MSG_KN_ROM = {
 "open": [T(x) for x in ["A: Dear customer, nimma EMI {AMOUNT} {DATE} ge baaki ide. - {ORG}", "C: hello sir ;; C: EMI bagge msg bandide ;; A: Haudu {AMOUNT} baaki ide {DATE} ge."]],
 "close": [T(x) for x in ["C: ok thanks ;; A: Thank you.", "C: sari, {DATE2} olage kattuttene", "C: haudu sir ok", "C: ok done"]],
 "ask": {"NAME": {"single": [T(x) for x in ["A: nimma hesaru? ;; C: {NAME}", "C: naanu {NAME} matadtiddini"]]},
         "PHONE": {"single": [T(x) for x in ["A: nimma mobile no kalisi ;; C: {PHONE}", "C: nanna hosa number {PHONE}"]], "split": [T(x) for x in ["A: number? ;; C: {PHONE_P1} ;; C: {PHONE_P2}"]], "partial": [T(x) for x in ["A: kone 4 anki heli ;; C: {PHONE}"]]},
         "ADDRESS": {"single": [T(x) for x in ["A: hosa address kalisi ;; C: {ADDRESS}"]]}},
}
MSG_TA_ROM = {
 "open": [T(x) for x in ["A: Dear customer, ungal EMI {AMOUNT} {DATE} anru baaki irukku. - {ORG}", "C: hello sir ;; C: EMI pathi msg vandhuchu ;; A: Aama {AMOUNT} baaki irukku {DATE} ku."]],
 "close": [T(x) for x in ["C: ok thanks ;; A: Thank you.", "C: sari, {DATE2} kulla katturen", "C: aama sir ok", "C: ok done"]],
 "ask": {"NAME": {"single": [T(x) for x in ["A: ungal peyar? ;; C: {NAME}", "C: naan {NAME} pesuren"]]},
         "PHONE": {"single": [T(x) for x in ["A: ungal mobile no anuppunga ;; C: {PHONE}", "C: en pudhu number {PHONE}"]], "split": [T(x) for x in ["A: number? ;; C: {PHONE_P1} ;; C: {PHONE_P2}"]], "partial": [T(x) for x in ["A: kadaisi 4 ilakkam sollunga ;; C: {PHONE}"]]},
         "ADDRESS": {"single": [T(x) for x in ["A: pudhu address anuppunga ;; C: {ADDRESS}"]]}},
}
MSG_BY_LANG = {"en": (MSG_EN, "en"), "hi_rom": (MSG_ROM, "hi_rom"), "mr_rom": (MSG_MR_ROM, "mr_rom"),
               "kn_rom": (MSG_KN_ROM, "kn_rom"), "ta_rom": (MSG_TA_ROM, "ta_rom")}


# =========================================================== pool registry + extension lines
def pool_registry():
    """{pool_name: the actual list object a builder draws from}. Names are stable ids used by extension files."""
    out = {}

    def pack(name, p):
        for k in ("open", "intro", "close"):
            if k in p:
                out[f"{name}.{k}"] = p[k]
        for sc, v in p.get("due", {}).items():
            out[f"{name}.due.{sc}"] = v
        for t, kinds in p.get("ask", {}).items():
            for kind, v in kinds.items():
                out[f"{name}.ask.{t}.{kind}"] = v
    for n, p in CALL_PACKS.items():
        pack("call:" + n, p)
    for n, (p, _tag) in MSG_BY_LANG.items():
        pack("msg:" + n, p)
    for n, p in MSG_NATIVE.items():
        pack("msgnative:" + n, p)
    out["prompt.instr"] = PROMPT_INSTR
    for t, v in PROMPT_SNIP.items():
        out[f"prompt.snip.{t}"] = v
    return out


def block_to_str(block):
    r = {"Agent": "A", "Customer": "C", "User": "U"}
    return " ;; ".join(f"{r[sp]}: {tx}" for sp, tx in block)


EXT_IDS = set()          # id() of every agent-written (extension) line currently in a pool


def is_ext(item):
    return id(item) in EXT_IDS


def _apply_extensions(path=None):
    """Append validated extra lines (layer0_data/skeleton_ext.json: {pool_name: [line, ...]}) to their pools.
    Block pools take 'A: ... ;; C: ...' strings (parsed with T); prompt pools take plain strings."""
    import json
    import os
    path = path or os.path.join(os.path.dirname(__file__), "skeleton_ext.json")
    if not os.path.exists(path):
        return 0
    ext = json.load(open(path, encoding="utf-8"))
    reg, n = pool_registry(), 0
    EXT_IDS.clear()
    for name, lines in ext.items():
        pool = reg.get(name)
        if pool is None:
            continue
        for line in lines:
            item = line if name.startswith("prompt.") else T(line)
            if item not in pool:
                pool.append(item); n += 1
                EXT_IDS.add(id(item))
    _ZCACHE.clear()
    return n


EXT_LINES_ADDED = _apply_extensions()
