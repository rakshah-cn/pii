"""Demo: run short calls in several languages through the gateway with the REAL layer-1 detector.
All values below are invented. Run from the project root:   python -m layer3_gateway.demo
Also writes the graph diagram (Mermaid) to layer3_gateway/gateway_graph.mmd
"""
import os

from layer3_gateway.graph import Gateway, ROOT

CALLS = {
    "English": [("Agent", "Please confirm your registered mobile number."),
                ("Customer", "Sure, my number is nine eight four five"),
                ("Agent", "Go ahead."),
                ("Customer", "zero one two three four five. I am Ramesh Kumar.")],
    "Hindi (Roman)": [("Agent", "Aapka registered number bata dijiye."),
                      ("Customer", "Haan ji, mera number hai nau aath chaar paanch"),
                      ("Agent", "Boliye."),
                      ("Customer", "shunya ek do teen chaar paanch. Main Ramesh Kumar bol raha hoon.")],
    "Hindi (Devanagari)": [("Agent", "कृपया अपना नंबर बताइए।"),
                           ("Customer", "हाँ जी, मेरा नंबर है नौ आठ चार पाँच"),
                           ("Agent", "जी बोलिए।"),
                           ("Customer", "शून्य एक दो तीन चार पाँच। मेरा नाम रमेश कुमार है।")],
    "Kannada": [("Agent", "ದಯವಿಟ್ಟು ನಿಮ್ಮ ಮೊಬೈಲ್ ನಂಬರ್ ಹೇಳಿ."),
                ("Customer", "ನನ್ನ ನಂಬರ್ ಒಂಬತ್ತು ಎಂಟು ನಾಲ್ಕು ಐದು"),
                ("Agent", "ಮುಂದುವರಿಸಿ."),
                ("Customer", "ಸೊನ್ನೆ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು. ನನ್ನ ಹೆಸರು ಲಕ್ಷ್ಮಿ ದೇವಿ.")],
    "Tamil": [("Agent", "உங்கள் மொபைல் எண்ணைச் சொல்லுங்கள்."),
              ("Customer", "என் நம்பர் ஒன்பது எட்டு நான்கு ஐந்து"),
              ("Agent", "தொடருங்கள்."),
              ("Customer", "பூஜ்யம் ஒன்று இரண்டு மூன்று நான்கு ஐந்து. என் பெயர் பிரியா நாயர்.")],
    "Look-alikes (should stay)": [("Customer", "My order 4410293 was delivered on 12 March for ₹4,200.")],
}

if __name__ == "__main__":
    gw = Gateway()
    path = os.path.join(ROOT, "layer3_gateway", "gateway_graph.mmd")
    open(path, "w", encoding="utf-8").write(gw.diagram())
    print("diagram written to", path, "\n")
    for name, turns in CALLS.items():
        print("=" * 78, "\n" + name)
        for i, (who, text) in enumerate(turns):
            out = gw.handle(f"demo-{name}", who, text)
            print(f"  [{who}] {text}")
            print(f"     -> {out.get('masked_text', '(blocked: ' + out.get('reason', '') + ')')}   "
                  f"[{out['status']}, {out['ms']:.0f} ms, {' > '.join(out['trace'])}]")
    import re
    from layer3_gateway.graph import TOKEN
    leaked = [e["text"] for e in gw.log if "text" in e and re.search(r"\d{4,}", TOKEN.sub("", e["text"]).replace("4410293", ""))]
    print("\nlog entries with a 4+ digit run left (besides the order number kept on purpose):", len(leaked))
