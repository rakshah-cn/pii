"""Fake PII generator. Every value is made up by code (never by an LLM),
so labels are exact and there is no real-person risk.

NOTE: the Aadhaar numbers made here have a VALID Verhoeff checksum, so they look
real to a detector. They are random, not issued to anyone.
"""
import random
import re
import string

# ---- Verhoeff checksum (the check digit Aadhaar uses) ----
_D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],
      [3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
      [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],
      [9,8,7,6,5,4,3,2,1,0]]
_P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],
      [8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
      [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
_INV = [0,4,3,2,1,5,6,7,8,9]


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def fake_aadhaar(rng=random) -> str:
    base = str(rng.randint(2, 9)) + "".join(rng.choices(string.digits, k=10))
    return base + verhoeff_check_digit(base)


def fake_phone(rng=random) -> str:
    return str(rng.randint(6, 9)) + "".join(rng.choices(string.digits, k=9))


def fake_pan(rng=random) -> str:
    # AAAAA9999A ; 4th letter = holder type (P = individual is by far most common)
    return ("".join(rng.choices(string.ascii_uppercase, k=3)) + rng.choice("PPPPCHF")
            + rng.choice(string.ascii_uppercase) + "".join(rng.choices(string.digits, k=4))
            + rng.choice(string.ascii_uppercase))


def fake_account(rng=random) -> str:
    return "".join(rng.choices(string.digits, k=rng.choice([9, 11, 12, 14, 16])))


def fake_ifsc(rng=random) -> str:
    return "".join(rng.choices(string.ascii_uppercase, k=4)) + "0" + "".join(
        rng.choices(string.ascii_uppercase + string.digits, k=6))


# ---- Names: big pools, native script + romanised, several shapes ----
_N = {}
def _w(s): return s.split()
_N["en"] = (_w("Ramesh Suresh Mahesh Rajesh Sunita Anita Priya Kavitha Lakshmi Arjun Vikram Deepak Amit Rahul Neha Pooja Sanjay Manoj Anil Sandeep Ravi Kiran Geeta Meena Rekha Divya Naveen Prakash Venkatesh Srinivas Mohammed Imran Farhan Ayesha Shabana Joseph Thomas Mary John Harpreet Gurpreet Simran Karthik Balaji Ganesh Shweta Nitin Swati Rohit Ashok"),
            _w("Kumar Sharma Reddy Iyer Gowda Naidu Patil Khan Singh Verma Gupta Yadav Nair Menon Pillai Rao Shetty Hegde Joshi Kulkarni Deshmukh Jadhav Mishra Pandey Das Banerjee Chatterjee Ansari Qureshi D'Souza Fernandes Thomas Chauhan Mehta Shah Patel Agarwal Bhat Kamath Acharya"))
_N["hi_rom"] = (_w("Rakesh Sunita Mukesh Geeta Vikas Pooja Ajay Seema Ramesh Kavita Dinesh Anita Sanjay Rekha Manoj Poonam Anil Shweta Pankaj Neelam Sunil Savita Rajendra Usha Vinod Meena Harish Kiran Naresh Shanti"),
                _w("Sharma Verma Gupta Yadav Singh Mishra Pandey Tiwari Chauhan Agarwal Joshi Saxena Srivastava Dubey Thakur Kumar Rajput Bansal Jain Kushwaha"))
_N["mr_rom"] = (_w("Prashant Sunil Anita Smita Sachin Vaishali Mahesh Suvarna Amit Pooja Rahul Jyoti Deepak Savita Ajay Nita Vinayak Sonali Ganesh Rekha Kishor Meena Sagar Priya Yogesh Ashwini Nitin Kalpana Umesh Sheetal"),
                _w("Patil Deshmukh Jadhav Shinde Kulkarni Joshi Pawar Gaikwad Kadam More Bhosale Chavan Kale Sawant Deshpande Lokhande Mane Thorat Naik Gokhale"))
_N["kn_rom"] = (_w("Manjunath Shivakumar Lakshmi Rajeshwari Nagaraj Basavaraj Suma Kavitha Ramesh Shobha Mallesh Geetha Prakash Vijaya Satish Anitha Girish Savitha Ravi Pushpa Mahadevappa Renuka Venkatesh Sudha Chandrashekar Shruthi Anand Rathna Raghavendra Bhagya"),
                _w("Gowda Shetty Hegde Naik Bhat Rao Reddy Patil Kamath Acharya Murthy Swamy Hiremath Kulkarni Desai Poojary Nayak Achar Shastri Angadi"))
_N["ta_rom"] = (_w("Murugan Selvam Lakshmi Meenakshi Karthik Saravanan Priya Kavitha Rajesh Revathi Senthil Gayathri Arun Vijayalakshmi Bala Sumathi Ganesh Dhanalakshmi Venkat Malathi Kumaresan Anbu Sivakami Prabhu Janani Mani Radha Suresh Vasanthi Ramesh"),
                _w("Subramanian Krishnan Iyer Iyengar Pillai Nadar Thevar Chettiar Gounder Naidu Mudaliar Rajan Raman Narayanan Murthy Sundaram Balasubramanian Natarajan Venkatesan Kannan"))
_N["hi"] = (_w("रमेश सुनीता अर्जुन लक्ष्मी मोहम्मद प्रिया सुरेश कविता राकेश गीता मुकेश पूजा अजय सीमा दिनेश अनीता संजय रेखा मनोज पूनम अनिल श्वेता पंकज नीलम सुनील सविता राजेंद्र उषा विनोद मीना हरीश किरण नरेश शांति विकास ममता"),
            _w("कुमार शर्मा वर्मा गुप्ता यादव सिंह मिश्रा पांडेय तिवारी चौहान अग्रवाल जोशी सक्सेना श्रीवास्तव दुबे ठाकुर राजपूत बंसल जैन खान"))
_N["mr"] = (_w("सुनील अनिता प्रशांत स्मिता सचिन वैशाली महेश सुवर्णा अमित पूजा राहुल ज्योती दीपक सविता अजय नीता विनायक सोनाली गणेश रेखा किशोर मीना सागर प्रिया योगेश अश्विनी नितीन कल्पना उमेश शीतल"),
            _w("पाटील देशमुख जाधव शिंदे कुलकर्णी जोशी पवार गायकवाड कदम मोरे भोसले चव्हाण काळे सावंत देशपांडे लोखंडे माने थोरात नाईक गोखले"))
_N["kn"] = (_w("ಮಂಜುನಾಥ ಶಿವಕುಮಾರ್ ಲಕ್ಷ್ಮಿ ರಾಜೇಶ್ವರಿ ನಾಗರಾಜ್ ಬಸವರಾಜ್ ಸುಮಾ ಕವಿತಾ ರಮೇಶ್ ಶೋಭಾ ಮಲ್ಲೇಶ್ ಗೀತಾ ಪ್ರಕಾಶ್ ವಿಜಯಾ ಸತೀಶ್ ಅನಿತಾ ಗಿರೀಶ್ ಸವಿತಾ ರವಿ ಪುಷ್ಪಾ ಮಹದೇವಪ್ಪ ರೇಣುಕಾ ವೆಂಕಟೇಶ್ ಸುಧಾ ಚಂದ್ರಶೇಖರ್ ಶ್ರುತಿ ಆನಂದ್ ರತ್ನ ರಾಘವೇಂದ್ರ ಭಾಗ್ಯ"),
            _w("ಗೌಡ ಶೆಟ್ಟಿ ಹೆಗಡೆ ನಾಯಕ್ ಭಟ್ ರಾವ್ ರೆಡ್ಡಿ ಪಾಟೀಲ್ ಕಾಮತ್ ಆಚಾರ್ಯ ಮೂರ್ತಿ ಸ್ವಾಮಿ ಹಿರೇಮಠ ಕುಲಕರ್ಣಿ ದೇಸಾಯಿ ಪೂಜಾರಿ ನಾಯ್ಕ್ ಆಚಾರ್ ಶಾಸ್ತ್ರಿ ಅಂಗಡಿ"))
_N["ta"] = (_w("முருகன் செல்வம் லட்சுமி மீனாட்சி கார்த்திக் சரவணன் பிரியா கவிதா ராஜேஷ் ரேவதி செந்தில் காயத்ரி அருண் விஜயலட்சுமி பாலா சுமதி கணேஷ் தனலட்சுமி வெங்கட் மாலதி குமரேசன் அன்பு சிவகாமி பிரபு ஜனனி மணி ராதா சுரேஷ் வசந்தி ரமேஷ்"),
            _w("சுப்பிரமணியன் கிருஷ்ணன் ஐயர் ஐயங்கார் பிள்ளை நாடார் தேவர் செட்டியார் கவுண்டர் நாயுடு முதலியார் ராஜன் ராமன் நாராயணன் மூர்த்தி சுந்தரம் பாலசுப்ரமணியன் நடராஜன் வெங்கடேசன் கண்ணன்"))


import hashlib


def name_zone(word):
    """Every name WORD belongs to exactly one part (dev 60% / test 20% / final 20%), so test and final contain only
    names whose words never occur in dev, in any shape (full, first-only, initial+surname)."""
    h = int(hashlib.md5(word.casefold().encode()).hexdigest(), 16) % 10
    return "dev" if h < 6 else ("test" if h < 8 else "final")


def _zone_words(words, part):
    if not part:
        return words
    z = [w for w in words if name_zone(w) == ("dev" if part == "train" else part)]
    return z or words


def fake_name(lang, rng=random, part=None) -> str:
    """Several shapes: full name, first name only, initial + surname, middle initial, ALL CAPS (roman only)."""
    first, last = _N[lang]
    first, last = _zone_words(first, part), _zone_words(last, part)
    f, l = rng.choice(first), rng.choice(last)
    r = rng.random()
    roman = lang in ("en", "hi_rom", "mr_rom", "kn_rom", "ta_rom")
    if r < 0.15:
        return f
    if roman and r < 0.27:
        return f"{f[0]}. {l}"
    if roman and r < 0.37:
        return f"{f} {rng.choice(string.ascii_uppercase)}. {l}"
    if roman and r < 0.42:
        return f"{f} {l}".upper()
    if not roman and r < 0.27:
        return f"{f} {rng.choice(first)} {l}"            # three-part name
    return f"{f} {l}"


# ---- Addresses: structured, region-aware, native script + roman, several shapes ----
_AD = {
 "roman": dict(
    house=["{n}", "H.No. {n}", "Flat {n}", "{n}/{m}", "#{n}", "No. {n}", "Door No {n}"],
    bld=["Sai Krupa Apartments", "Lakshmi Nivas", "Green Valley Residency", "Shree Complex", "Ganga Heights", "Royal Enclave", "Sunrise Towers", "Krishna Kunj"],
    street=["MG Road", "Gandhi Nagar", "Temple Street", "Main Road", "Station Road", "Park Street", "Nehru Road", "Market Road", "{k}th Cross", "{k}th Main"],
    land=["near {x} Temple", "opp. {x} School", "behind {x} Bus Stand", "near {x} Petrol Pump", "next to {x} Hospital"],
    x=["Ganesh", "Hanuman", "City", "Govt", "Bharat", "Shiva"],
    reg={"kn": (["Jayanagar", "Koramangala", "Indiranagar", "Whitefield", "Rajajinagar", "BTM Layout"], ["Bengaluru", "Mysuru", "Mangaluru", "Hubballi"], "Karnataka", (560, 577)),
         "ta": (["Adyar", "T. Nagar", "Anna Nagar", "Velachery", "Tambaram", "RS Puram"], ["Chennai", "Coimbatore", "Madurai", "Trichy"], "Tamil Nadu", (600, 643)),
         "mr": (["Kothrud", "Hadapsar", "Andheri", "Thane West", "Dadar", "Nashik Road"], ["Pune", "Mumbai", "Nashik", "Nagpur"], "Maharashtra", (400, 445)),
         "hi": (["Civil Lines", "Gandhi Nagar", "Indira Nagar", "Rajajipuram", "Karol Bagh", "Vaishali"], ["Lucknow", "Delhi", "Jaipur", "Kanpur", "Patna"], "Uttar Pradesh", (110, 302))}),
 "hi": dict(house=["मकान नंबर {n}", "फ्लैट {n}", "{n}"], bld=["साईं अपार्टमेंट", "गंगा हाइट्स", "शिव कॉम्प्लेक्स"],
            street=["एम जी रोड", "मंदिर मार्ग", "स्टेशन रोड", "मेन रोड"], loc=["सिविल लाइन्स", "गांधी नगर", "इंदिरा नगर", "राजाजीपुरम", "करोल बाग", "वैशाली"],
            city=["लखनऊ", "दिल्ली", "जयपुर", "कानपुर", "पटना"], pin=(110, 302)),
 "mr": dict(house=["घर क्रमांक {n}", "फ्लॅट {n}", "{n}"], bld=["साई अपार्टमेंट", "गणेश निवास", "शिवकृपा"],
            street=["एम जी रोड", "मंदिर रस्ता", "स्टेशन रोड", "मुख्य रस्ता"], loc=["कोथरूड", "हडपसर", "अंधेरी", "ठाणे", "दादर", "नाशिक रोड"],
            city=["पुणे", "मुंबई", "नाशिक", "नागपूर"], pin=(400, 445)),
 "kn": dict(house=["ಮನೆ ಸಂಖ್ಯೆ {n}", "ಫ್ಲ್ಯಾಟ್ {n}", "{n}"], bld=["ಸಾಯಿ ಅಪಾರ್ಟ್ಮೆಂಟ್", "ಲಕ್ಷ್ಮಿ ನಿವಾಸ", "ಶ್ರೀ ಕಾಂಪ್ಲೆಕ್ಸ್"],
            street=["ಎಂ ಜಿ ರಸ್ತೆ", "ದೇವಸ್ಥಾನ ರಸ್ತೆ", "ಮುಖ್ಯ ರಸ್ತೆ", "ಸ್ಟೇಷನ್ ರಸ್ತೆ"], loc=["ಜಯನಗರ", "ಕೋರಮಂಗಲ", "ಇಂದಿರಾನಗರ", "ರಾಜಾಜಿನಗರ", "ಬಿಟಿಎಂ ಲೇಔಟ್"],
            city=["ಬೆಂಗಳೂರು", "ಮೈಸೂರು", "ಮಂಗಳೂರು", "ಹುಬ್ಬಳ್ಳಿ"], pin=(560, 577)),
 "ta": dict(house=["வீட்டு எண் {n}", "பிளாட் {n}", "{n}"], bld=["சாய் அபார்ட்மெண்ட்", "லட்சுமி இல்லம்"],
            street=["எம் ஜி ரோடு", "கோயில் தெரு", "மெயின் ரோடு", "ஸ்டேஷன் ரோடு"], loc=["அடையாறு", "தி நகர்", "அண்ணா நகர்", "வேளச்சேரி", "தாம்பரம்", "ஆர் எஸ் புரம்"],
            city=["சென்னை", "கோயம்புத்தூர்", "மதுரை", "திருச்சி"], pin=(600, 643)),
}
_NATIVE_DIGITS = {"hi": "०१२३४५६७८९", "mr": "०१२३४५६७८९", "kn": "೦೧೨೩೪೫೬೭೮೯", "ta": "௦௧௨௩௪௫௬௭௮௯"}


def _fmt(t, rng):
    return t.format(n=rng.randint(1, 999), m=rng.randint(1, 60), k=rng.randint(1, 20), x=rng.choice(_AD["roman"]["x"]))


def _natdig(s, lang):
    return "".join(_NATIVE_DIGITS[lang][int(c)] if c.isdigit() else c for c in s)


_CITY_PIN = {"Bengaluru": "560", "Mysuru": "570", "Mangaluru": "575", "Hubballi": "580", "Chennai": "600", "Coimbatore": "641",
             "Madurai": "625", "Trichy": "620", "Pune": "411", "Mumbai": "400", "Nashik": "422", "Nagpur": "440",
             "Lucknow": "226", "Delhi": "110", "Jaipur": "302", "Kanpur": "208", "Patna": "800",
             "ಬೆಂಗಳೂರು": "560", "ಮೈಸೂರು": "570", "ಮಂಗಳೂರು": "575", "ಹುಬ್ಬಳ್ಳಿ": "580", "சென்னை": "600", "கோயம்புத்தூர்": "641",
             "மதுரை": "625", "திருச்சி": "620", "पुणे": "411", "मुंबई": "400", "नाशिक": "422", "नागपूर": "440",
             "लखनऊ": "226", "दिल्ली": "110", "जयपुर": "302", "कानपुर": "208", "पटना": "800"}
_CITY_STATE = {"Bengaluru": "Karnataka", "Mysuru": "Karnataka", "Mangaluru": "Karnataka", "Hubballi": "Karnataka",
               "Chennai": "Tamil Nadu", "Coimbatore": "Tamil Nadu", "Madurai": "Tamil Nadu", "Trichy": "Tamil Nadu",
               "Pune": "Maharashtra", "Mumbai": "Maharashtra", "Nashik": "Maharashtra", "Nagpur": "Maharashtra",
               "Lucknow": "Uttar Pradesh", "Delhi": "Delhi", "Jaipur": "Rajasthan", "Kanpur": "Uttar Pradesh", "Patna": "Bihar"}


def fake_address(lang, rng=random) -> str:
    base = lang.replace("_rom", "")
    native = lang in _AD and lang != "en"
    if native:                                              # hi / mr / kn / ta in native script
        a = _AD[lang]
        city = rng.choice(a["city"])
        parts = [_fmt(rng.choice(a["house"]), rng), rng.choice(a["bld"]), rng.choice(a["street"]), rng.choice(a["loc"]), city]
        keep = [parts[0]] + [p for p in parts[1:] if rng.random() < 0.7]
        if len(keep) < 3:
            keep = parts[:3]
        pin = _CITY_PIN[city] + f"{rng.randint(0, 999):03d}"
        addr = ", ".join(keep) + (f" - {pin}" if rng.random() < 0.75 else "")
        return _natdig(addr, lang) if rng.random() < 0.2 else addr
    r = _AD["roman"]
    locs, cities, _state, _pr = r["reg"].get(base if base in r["reg"] else rng.choice(list(r["reg"])))
    city = rng.choice(cities)
    state = _CITY_STATE[city]
    parts = [_fmt(rng.choice(r["house"]), rng), rng.choice(r["bld"]), _fmt(rng.choice(r["street"]), rng), rng.choice(locs), city]
    keep = [parts[0]] + [p for p in parts[1:] if rng.random() < 0.7]
    if len(keep) < 3:
        keep = parts[:3]
    if rng.random() < 0.3:
        keep.insert(rng.randint(1, len(keep)), _fmt(rng.choice(r["land"]), rng))
    pin = _CITY_PIN[city] + f"{rng.randint(0, 999):03d}"
    out = ", ".join(keep)
    if rng.random() < 0.8:
        out += rng.choice([f" {pin}", f" - {pin}", f", {state} {pin}", f", {state} - {pin}"])
    return out


# ---- Loan / agreement number: many formats ----
_LOAN_SCHEMES = ["{L5}{D2}{L1}{D3}", "{L2}{D8}", "LAN{Y}{D8}", "LAN-{Y}-{D8}", "PL/{L3}/{Y}/{D6}", "{D13}", "HL{D10}",
                 "BL-{D6}", "{D4}-{D4}-{D4}", "{L3}{D9}", "AGR{D7}", "{L2}/{Y}/{D5}", "{L4}-{D8}", "LN{D12}",
                 "{D2}{L2}{D8}", "TL{Y2}{L2}{D6}", "{L3}-{L2}-{D7}", "PLN{D9}X", "{L2}{D3}-{D6}", "RF/{Y}/{L3}/{D4}",
                 "{D10}", "{L1}{D9}", "{L3}{D4}{L2}{D4}", "{D6}{L2}{D4}", "LOAN{D8}", "{L2}-{D2}-{D8}"]
_TOKEN = re.compile(r"\{([A-Z]+)(\d*)\}")


def _expand_scheme(sch, rng):
    def rep(m):
        k, n = m.group(1), int(m.group(2) or 1)
        if k == "L":
            return "".join(rng.choices(string.ascii_uppercase, k=n))
        if k == "D":
            return "".join(rng.choices(string.digits, k=n))
        if k == "Y":
            return str(rng.randint(2015, 2025))
        if k == "Y2":
            return f"{rng.randint(15, 25)}"
    return _TOKEN.sub(rep, sch)


def fake_loan(rng=random) -> str:
    """Written form of a loan/agreement number. 50%: one of ~26 named formats; 50%: a freshly composed one.
    The real format(s) are unknown, so we cover many shapes (with - / separators, years, pure digits)."""
    if rng.random() < 0.5:
        out = _expand_scheme(rng.choice(_LOAN_SCHEMES), rng)
    else:
        sep = rng.choice(["", "", "-", "/", "_"])
        parts = []
        if rng.random() < 0.85:
            parts.append("".join(rng.choices(string.ascii_uppercase, k=rng.randint(2, 5))))
        if rng.random() < 0.35:
            parts.append(str(rng.randint(2015, 2025)))
        parts.append("".join(rng.choices(string.digits, k=rng.randint(4, 12))))
        if rng.random() < 0.2:
            parts.append(rng.choice(string.ascii_uppercase))
        out = sep.join(parts)
    alnum = re.sub(r"[^A-Za-z0-9]", "", out)
    return out if len(alnum) >= 8 else out + "".join(rng.choices(string.digits, k=8 - len(alnum)))


# ---- UPI, email, date of birth ----
def fake_upi(rng=random, part=None) -> str:
    h = rng.choice(["ybl", "oksbi", "okaxis", "okhdfcbank", "paytm", "upi", "icici", "apl"])
    if rng.random() < 0.4:
        return f"{fake_phone(rng)}@{h}"
    f = rng.choice(_zone_words(_N["en"][0], part)).lower()
    return f"{f}{rng.choice(['.', '', '_'])}{rng.choice(_zone_words(_N['en'][1], part)).lower()}{rng.randint(1, 99) if rng.random() < .5 else ''}@{h}"


def fake_email(rng=random, part=None) -> str:
    f, l = rng.choice(_zone_words(_N["en"][0], part)).lower(), rng.choice(_zone_words(_N["en"][1], part)).lower()
    d = rng.choice(["gmail.com", "yahoo.com", "outlook.com", "rediffmail.com", "hotmail.com"])
    return rng.choice([f"{f}.{l}", f"{f}{l}", f"{f}_{l}", f"{f}{rng.randint(1, 99)}", f"{f[0]}{l}{rng.randint(1, 999)}"]) + "@" + d


_MONTHS = "January February March April May June July August September October November December".split()


def fake_dob(rng=random) -> str:
    d, m, y = rng.randint(1, 28), rng.randint(1, 12), rng.randint(1955, 2003)
    return rng.choice([f"{d:02d}/{m:02d}/{y}", f"{d:02d}-{m:02d}-{y}", f"{d} {_MONTHS[m - 1]} {y}",
                       f"{d}th {_MONTHS[m - 1]} {y}", f"{_MONTHS[m - 1]} {d}, {y}", f"{d:02d}.{m:02d}.{y}"])


DIGIT_TYPES = {"PHONE": fake_phone, "AADHAAR": fake_aadhaar, "ACCOUNT": fake_account}
