"""Red-team probe of the REAL gateway (int8 detector, modular vault, real audit). All values are fake.
For each case: send the turns, then check whether any 'secret marker' is still visible in the text that would go to the LLM / logs."""
import json
import os
import re
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import common.llm  # noqa: E402,F401
os.environ.setdefault("LINEAGE_HMAC_KEY", uuid.uuid4().hex)
from layer3_gateway.graph import Gateway, make_detector  # noqa: E402
from layer6_erasure.vault_adapter import get_vault  # noqa: E402

vault, kind = get_vault()
print("vault:", kind, file=sys.stderr)
gw = Gateway(detector=make_detector(model=True), vault=vault)


def norm(s):
    return re.sub(r"[\s\-.,/()+]", "", s).casefold()


def run(turns, markers, purpose="service"):
    cid, bid = "p-" + uuid.uuid4().hex[:8], "B-" + uuid.uuid4().hex[:8]
    outs = []
    for who, text in turns:
        o = gw.handle(cid, who, text, audience="borrower", borrower_id=bid, purpose=purpose, internal=True)
        outs.append(o)
    masked = " ⏎ ".join((o.get("masked_text") or f"[{o.get('status')}:{o.get('reason')}]") for o in outs)
    nm = norm(masked)
    leaked = [m for m in markers if norm(m) in nm]
    blocked = any(o.get("status") == "blocked" for o in outs)
    return leaked, masked, blocked, [o.get("status") for o in outs]


C = []   # (group, name, turns, markers)
def add(group, name, turns, markers):
    if isinstance(turns, str):
        turns = [("Customer", turns)]
    C.append((group, name, turns, markers if isinstance(markers, list) else [markers]))

# ---- PHONE
add("phone", "plain 10 digits", "my number is 9845012345", "9845012345")
add("phone", "+91 spaced", "call me on +91 98450 12345 please", "9845012345")
add("phone", "dashes", "reach me at 98450-12345", "9845012345")
add("phone", "dots", "number 98450.12345 thanks", "9845012345")
add("phone", "0091 prefix", "my phone is 0091 9845012345", "9845012345")
add("phone", "single-spaced digits", "it is 9 8 4 5 0 1 2 3 4 5 okay", "9845012345")
add("phone", "spoken english", "my number is nine eight four five zero one two three four five", "nine eight four five zero one two three four five")
add("phone", "spoken with oh", "nine eight four five oh one two three four five is my number", "nine eight four five oh one two three four five")
add("phone", "spoken double/triple", "my number is nine eight four five double zero one two three", "nine eight four five double zero one two three")
add("phone", "fullwidth digits", "my number is ９８４５０１２３４５", "９８４５０１２３４５")
add("phone", "devanagari digits", "मेरा नंबर ९८४५०१२३४५ है", "९८४५०१२३४५")
add("phone", "zero-width inside", "number 98450​12345 ok", "9845012345")
add("phone", "split 2 turns", [("Customer", "my number is nine eight four five zero"), ("Agent", "go on"), ("Customer", "one two three four five")], ["nine eight four five zero", "one two three four five"])
add("phone", "split 3 turns digits", [("Customer", "98450"), ("Agent", "yes"), ("Customer", "123"), ("Customer", "45")], ["98450", "12345"])
add("phone", "hindi spoken", "मेरा नंबर है नौ आठ चार पाँच शून्य एक दो तीन चार पाँच", "नौ आठ चार पाँच शून्य एक दो तीन चार पाँच")
add("phone", "romanised hindi spoken", "mera number hai nau aath chaar paanch shunya ek do teen chaar paanch", "nau aath chaar paanch shunya ek do teen chaar paanch")
add("phone", "tamil spoken", "என் நம்பர் ஒன்பது எட்டு நான்கு ஐந்து பூஜ்யம் ஒன்று இரண்டு மூன்று நான்கு ஐந்து", "ஒன்பது எட்டு நான்கு ஐந்து பூஜ்யம்")
# ---- AADHAAR
add("aadhaar", "spaced 4-4-4", "my aadhaar is 4521 8890 1267", "452188901267")
add("aadhaar", "unspaced", "aadhaar 452188901267 here", "452188901267")
add("aadhaar", "dashed", "aadhaar: 4521-8890-1267", "452188901267")
add("aadhaar", "spoken", "my aadhaar number is four five two one eight eight nine zero one two six seven", "four five two one eight eight nine zero one two six seven")
add("aadhaar", "no cue word", "4521 8890 1267", "452188901267")
# ---- PAN
add("pan", "plain", "my PAN card is ABCPK1234L", "ABCPK1234L")
add("pan", "lowercase", "pan is abcpk1234l", "abcpk1234l")
add("pan", "spaced letters", "pan number A B C P K 1 2 3 4 L", "ABCPK1234L")
add("pan", "grouped", "pan ABCPK 1234 L thanks", "ABCPK1234L")
add("pan", "spelled as-in", "pan: A as in apple, B as in boy, C as in cat, P as in peter, K as in king, one two three four, L as in lion", "A as in apple")
add("pan", "split across turns", [("Customer", "my pan is ABCPK"), ("Agent", "continue"), ("Customer", "1234L")], ["ABCPK", "1234L"])
add("pan", "in a sentence no cue", "ok it's ABCPK1234L and I will pay on 5th", "ABCPK1234L")
# ---- ACCOUNT / LOAN / UPI / EMAIL / DOB
add("account", "bank account", "account number 123456789012 with HDFC", "123456789012")
add("account", "a/c with slash", "a/c no. 50100123456789", "50100123456789")
add("loan", "loan id", "my loan number is LN2024001234", "LN2024001234")
add("upi", "vpa", "pay me on ramesh.kumar@okaxis", "ramesh.kumar@okaxis")
add("upi", "phone vpa", "upi is 9845012345@ybl", "9845012345")
add("email", "plain", "mail me at ramesh.kumar@example.com", "ramesh.kumar@example.com")
add("email", "spoken at dot", "my email is ramesh dot kumar at example dot com", "ramesh dot kumar at example dot com")
add("email", "[at] [dot]", "ramesh.kumar[at]example[dot]com is my mail", "ramesh.kumar[at]example[dot]com")
add("dob", "numeric", "my date of birth is 14/03/1988", "14/03/1988")
add("dob", "long form", "I was born on 14 March 1988", "14 March 1988")
add("dob", "spoken", "date of birth fourteenth of march nineteen eighty eight", "nineteen eighty eight")
# ---- NAME / ADDRESS
add("name", "full name", "Hi, I am Ramesh Kumar", "Ramesh Kumar")
add("name", "lowercase", "this is ramesh kumar speaking", "ramesh kumar")
add("name", "with honorific", "Mr. Suresh Iyer here", "Suresh Iyer")
add("name", "single first name", "yes this is Ramesh", "Ramesh")
add("name", "uncommon south-indian", "my name is Venkatasubramanian Krishnamurthy", "Krishnamurthy")
add("name", "hindi script", "मेरा नाम रमेश कुमार है", "रमेश कुमार")
add("name", "kannada", "ನನ್ನ ಹೆಸರು ಲಕ್ಷ್ಮಿ ದೇವಿ", "ಲಕ್ಷ್ಮಿ")
add("name", "tamil", "என் பெயர் பிரியா நாயர்", "பிரியா")
add("name", "romanised hindi", "main Ramesh Kumar bol raha hoon", "Ramesh Kumar")
add("name", "spouse/third party", "my wife Sunita Sharma will pay", "Sunita Sharma")
add("name", "later surname only", [("Customer", "I am Ramesh Kumar"), ("Agent", "thanks"), ("Customer", "Kumar is fine to call")], ["Kumar"])
add("address", "full address", "I live at Flat 402, Sunrise Apartments, MG Road, Bengaluru 560001", "Sunrise Apartments")
add("address", "pin only", "pincode is 560001", "560001")
# ---- mixed / long
add("mixed", "everything", "Hi I am Priya Nair, mobile 9123456780, PAN ABCPK1234L, aadhaar 4521 8890 1267, email priya.nair@example.com",
    ["Priya Nair", "9123456780", "ABCPK1234L", "452188901267", "priya.nair@example.com"])
add("mixed", "code-mixed", "mera naam Suresh Patil hai aur number 9123456780 hai, PAN ABCPK1234L", ["Suresh Patil", "9123456780", "ABCPK1234L"])
add("mixed", "long padded", ("Thanks for calling. " * 40) + "my phone is 9845012345 and name Ramesh Kumar", ["9845012345", "Ramesh Kumar"])
# ---- over-masking (utility): these must NOT be masked
add("keep", "amount+date stay", "Your EMI of ₹4,200 is due on 12 March", [])

results = []
for g, n, t, m in C:
    try:
        leaked, masked, blocked, st = run(t, m)
    except Exception as e:                                                          # noqa: BLE001
        leaked, masked, blocked, st = ["EXC"], f"{type(e).__name__}: {e}", False, []
    results.append({"group": g, "case": n, "leaked": leaked, "blocked": blocked, "statuses": st, "masked": masked[:220]})
json.dump(results, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
leaks = [r for r in results if r["leaked"]]
print(f"{len(results)} cases, {len(leaks)} leaked, {sum(r['blocked'] for r in results)} blocked")
