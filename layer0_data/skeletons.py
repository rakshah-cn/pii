"""Hand-written skeleton library (placeholders only, no PII). Modelled on the real
translated call transcripts: short, repetitive, noisy, English-translation style.

Slots: {NAME} {PHONE} {AADHAAR} {PAN} {ACCOUNT} {LOAN} {ADDRESS}  (PII)
       {AMOUNT} {DATE} {DATE2} {ORG}                               (NOT PII)
Split numbers use {X_P1} / {X_P2}.
"""

OPENING = [
    [("Customer", "Hello."), ("Agent", "Hello."), ("Customer", "Hello?")],
    [("Agent", "Hello?"), ("Customer", "Hello, who is this?")],
    [("Customer", "Hello"), ("Agent", "Hello"), ("Agent", "Hello sir, good morning.")],
    [("Agent", "Hello, good afternoon sir."), ("Customer", "Good afternoon, tell me.")],
    [("Customer", "Hello, yes?"), ("Agent", "Hello sir.")],
    [("Agent", "Hello."), ("Agent", "Hello?"), ("Customer", "Yes, please speak.")],
    [("Customer", "Hello hello."), ("Agent", "Hello sir, can you hear me?"), ("Customer", "Yes, I can hear you.")],
    [("Agent", "Good evening sir."), ("Customer", "Yes, good evening.")],
]

INTRO = [
    [("Agent", "Sir, I am calling from {ORG}. This is a reminder call, it is being recorded.")],
    [("Agent", "Sir, this is {ORG} calling. The call is being recorded, sir.")],
    [("Agent", "Hello sir, I am speaking from {ORG}. It's a recorded call.")],
    [("Agent", "Sir, I am calling regarding your loan with {ORG}.")],
    [("Agent", "This is a reminder call from {ORG}, sir."), ("Customer", "Yes, tell me.")],
    [("Agent", "Sir, you had taken a personal loan from {ORG}, right sir?"), ("Customer", "Yes.")],
]

DUE = {
    "emi_reminder": [
        [("Agent", "Your EMI of {AMOUNT} is due on the {DATE}."), ("Customer", "Okay, I will pay by the {DATE2}.")],
        [("Agent", "Your due date is the {DATE}, amount {AMOUNT}."), ("Customer", "What? Say again.")],
        [("Agent", "Sir, {AMOUNT} is pending."), ("Customer", "Yes yes, I will pay on the {DATE2}.")],
        [("Agent", "Sir, your EMI due date is the {DATE}. For that..."), ("Customer", "Yes, I know, I will pay.")],
        [("Agent", "The amount due now is {AMOUNT}, sir."), ("Customer", "How much? {AMOUNT}?"), ("Agent", "Yes sir.")],
        [("Agent", "Your payment is overdue since the {DATE}, sir."), ("Customer", "Give me time till the {DATE2}.")],
    ],
    "dispute": [
        [("Agent", "Your EMI of {AMOUNT} is due on the {DATE}."), ("Customer", "The money was already deducted, look into it.")],
        [("Agent", "It shows {AMOUNT} pending, sir."), ("Customer", "No, I have paid. I received a message that it was cut.")],
        [("Agent", "Sir, the due date is the {DATE}."), ("Customer", "I paid on the {DATE2}. Check again.")],
        [("Agent", "The system shows {AMOUNT} as unpaid."), ("Customer", "That is wrong. Why are you calling again and again?")],
    ],
    "address_update": [
        [("Agent", "Sir, we need to update your details. Your EMI of {AMOUNT} is due on the {DATE}."), ("Customer", "Okay, tell me what is needed.")],
        [("Agent", "We have not been able to reach you by post, sir."), ("Customer", "I moved recently.")],
        [("Agent", "Your next due date is the {DATE}, sir."), ("Customer", "Yes, but update my details first.")],
    ],
    "payment_promise": [
        [("Agent", "When will you pay the {AMOUNT}, sir?"), ("Customer", "I will pay on the {DATE2}, I promise.")],
        [("Agent", "Sir, can you pay {AMOUNT} today?"), ("Customer", "Not today. By the {DATE2} I will do it.")],
        [("Agent", "The due date was the {DATE}. When can you pay?"), ("Customer", "Wait, I will arrange it by the {DATE2}.")],
        [("Agent", "Sir, will you pay {AMOUNT} by the {DATE}?"), ("Customer", "Yes, I will pay, do not worry.")],
    ],
}

CLOSING = [
    [("Customer", "Okay, thank you."), ("Agent", "Thank you sir.")],
    [("Customer", "Hmm. Okay."), ("Agent", "Okay, have a good day.")],
    [("Customer", "Yes yes, thank you, thank you.")],
    [("Customer", "Okay, I will do it."), ("Agent", "Thank you, sir. Please do it on time.")],
    [("Customer", "Okay okay. Bye."), ("Agent", "Thank you.")],
    [("Agent", "Thank you for your time, sir."), ("Customer", "Yes.")],
    [("Customer", "Fine, call me later."), ("Agent", "Sure sir, have a good day.")],
]

NOISE = ["Hello?", "Hmm.", "Hello hello.", "Yes?", "Okay.", "What?", "Hmm hmm.", "One minute.", "Stay on the line.",
         "I am here.", "Sir, hello?"]

# ---- one question block per PII type (single number / split across turns) ----
ASK = {
    "NAME": {"single": [
        [("Agent", "Hello sir, am I speaking with Mr. {NAME}?"), ("Customer", "Yes, speaking.")],
        [("Agent", "Namaste, {NAME} ji? I am calling from {ORG}."), ("Customer", "Yes, tell me.")],
        [("Agent", "Hello, is this {NAME}?"), ("Customer", "Yes, who is this?")],
        [("Agent", "Sir, am I talking to {NAME}?"), ("Customer", "Yes, that is me.")],
        [("Agent", "Is this Mrs. {NAME}'s number?"), ("Customer", "No, I am her relative. Tell me.")],
        [("Customer", "Hello, who are you calling?"), ("Agent", "I am calling for {NAME}, sir.")],
    ]},
    "PHONE": {
        "single": [
            [("Agent", "Can you confirm your registered mobile number?"), ("Customer", "Yes, it is {PHONE}.")],
            [("Agent", "Which number should we call you on?"), ("Customer", "{PHONE}, call me on this.")],
            [("Agent", "Please share an alternate contact number."), ("Customer", "Note it down, {PHONE}.")],
            [("Agent", "Sir, your number is not reachable. Give another number."), ("Customer", "Okay, my other number is {PHONE}.")],
        ],
        "split": [
            [("Agent", "Please confirm your registered mobile number."), ("Customer", "{PHONE_P1}"), ("Agent", "Yes, go on."), ("Customer", "{PHONE_P2}")],
            [("Agent", "Tell me your alternate number."), ("Customer", "It is {PHONE_P1}"), ("Agent", "Okay?"), ("Customer", "{PHONE_P2}, that's it.")],
            [("Agent", "Your contact number please?"), ("Customer", "{PHONE_P1}..."), ("Agent", "Hello? Continue."), ("Customer", "...{PHONE_P2}")],
        ]},
    "AADHAAR": {
        "single": [
            [("Agent", "Please tell me your Aadhaar number for verification."), ("Customer", "My Aadhaar is {AADHAAR}.")],
            [("Agent", "For KYC, I need your Aadhaar."), ("Customer", "Yes, {AADHAAR}.")],
        ],
        "split": [
            [("Agent", "Please tell me your Aadhaar number for verification."), ("Customer", "{AADHAAR_P1}"), ("Agent", "Okay, and the rest?"), ("Customer", "{AADHAAR_P2}")],
            [("Agent", "Your Aadhaar number, sir?"), ("Customer", "Wait. {AADHAAR_P1}"), ("Agent", "Yes."), ("Customer", "{AADHAAR_P2}")],
        ]},
    "PAN": {
        "single": [
            [("Agent", "Your PAN card number please."), ("Customer", "PAN is {PAN}.")],
            [("Agent", "Please confirm your PAN."), ("Customer", "It is {PAN}.")],
        ],
        "split": [
            [("Agent", "Your PAN card number please."), ("Customer", "{PAN_P1}"), ("Agent", "Yes?"), ("Customer", "{PAN_P2}")],
        ]},
    "ACCOUNT": {
        "single": [
            [("Agent", "Which bank account will you pay from?"), ("Customer", "My account number is {ACCOUNT}.")],
            [("Agent", "Please confirm the account for the auto-debit."), ("Customer", "Account number {ACCOUNT}.")],
        ],
        "split": [
            [("Agent", "Which bank account will you pay from?"), ("Customer", "Account number {ACCOUNT_P1}"), ("Agent", "Hello?"), ("Customer", "{ACCOUNT_P2}")],
            [("Agent", "Your bank account number?"), ("Customer", "{ACCOUNT_P1}"), ("Agent", "Go on, sir."), ("Customer", "{ACCOUNT_P2}")],
        ]},
    "LOAN": {
        "single": [
            [("Agent", "Please tell me your loan account number."), ("Customer", "{LOAN}")],
            [("Agent", "It will come in the loan account number, sir. Tell me the number."), ("Customer", "It is {LOAN}.")],
        ],
        "split": [
            [("Agent", "Your loan account number is?"), ("Customer", "{LOAN_P1}"), ("Agent", "Okay."), ("Customer", "{LOAN_P2}")],
            [("Agent", "Please read out your loan number."), ("Customer", "{LOAN_P1}"), ("Agent", "Your loan number. Tomorrow? Go on."), ("Customer", "{LOAN_P2}")],
        ]},
    "ADDRESS": {"single": [
        [("Agent", "Is your address still the same?"), ("Customer", "No, I have moved. It is {ADDRESS}.")],
        [("Agent", "Where do you live now, sir?"), ("Customer", "I live at {ADDRESS}.")],
        [("Agent", "Please give your current address."), ("Customer", "Write down: {ADDRESS}.")],
    ]},
}

# short code-mixed acknowledgements, per language style (inserted as extra noise turns)
FLAVOR = {
    "hi_rom": ["Haan ji.", "Theek hai.", "Boliye.", "Haan boliye.", "Achha achha."],
    "kn_rom": ["Haudu.", "Sari.", "Heli.", "Haudu sir, heli.", "Sari sari."],
    "ta_rom": ["Aama.", "Sari.", "Sollunga.", "Aama sir, sollunga.", "Sari sari."],
    "hi": ["हाँ जी।", "ठीक है।", "बोलिए।", "हाँ बोलिए।", "अच्छा अच्छा।"],
    "kn": ["ಹೌದು.", "ಸರಿ.", "ಹೇಳಿ.", "ಹೌದು ಸರ್, ಹೇಳಿ.", "ಸರಿ ಸರಿ."],
    "ta": ["ஆமா.", "சரி.", "சொல்லுங்க.", "ஆமா சார், சொல்லுங்க.", "சரி சரி."],
}


# "last four digits" style partial identifiers (real calls do this)
PARTIAL = {
    "PHONE": [[("Agent", "Tell me the last four digits of your mobile number."), ("Customer", "{PHONE}")],
              [("Agent", "Your number ends with?"), ("Customer", "It ends with {PHONE}.")]],
    "AADHAAR": [[("Agent", "Please give the last four digits of your Aadhaar."), ("Customer", "{AADHAAR}")],
                [("Agent", "Aadhaar ending with?"), ("Customer", "Ends with {AADHAAR}.")]],
    "ACCOUNT": [[("Agent", "Tell me the last four digits of your account number."), ("Customer", "{ACCOUNT}")],
                [("Agent", "Account number ending with?"), ("Customer", "It ends with {ACCOUNT}.")]],
}
