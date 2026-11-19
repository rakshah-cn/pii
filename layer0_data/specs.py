"""Coverage grid: the (language x PII type x form x scenario) cells we want data for.
Weak cells get more rows. 'NONE' cells are PII-free calls (real calls are mostly these)."""
import itertools
import random

LANGS = ["en", "hi", "hi_rom", "mr", "mr_rom", "kn", "kn_rom", "ta", "ta_rom"]   # 5th language = Marathi (our pick; ask organisers)
SCENARIOS = ["emi_reminder", "dispute", "address_update", "payment_promise"]

ALL_FORMS = ["written", "spoken", "split_turns", "spoken_fillers"]
FORMS_FOR = {
    "PHONE": ALL_FORMS, "AADHAAR": ALL_FORMS, "ACCOUNT": ALL_FORMS,
    "PAN": ["written", "spoken", "split_turns"],    # 'spoken' = letters spelled out
    "LOAN": ["written", "spoken", "split_turns"],
    "NAME": ["written"], "ADDRESS": ["written"], "NONE": ["written"],
    "UPI": ["written"], "EMAIL": ["written"], "DOB": ["written"],
}
DIGIT_TYPES = {"PHONE", "AADHAAR", "ACCOUNT"}
SPELLED_TYPES = {"PAN", "LOAN"}


def weight(cell):
    if cell["pii"] == "NONE":
        return 0.5
    w = 1
    if cell["form"] != "written":
        w += 2
    if cell["lang"].startswith(("kn", "ta")):
        w += 1
    if cell["form"] == "split_turns":
        w += 1
    return w


def difficulty(cell):
    w = weight(cell)
    return "easy" if w <= 1 else ("medium" if w <= 3 else "hard")


def build_grid():
    cells = []
    for lang, sc, (t, forms) in itertools.product(LANGS, SCENARIOS, FORMS_FOR.items()):
        for f in forms:
            cells.append({"lang": lang, "pii": t, "form": f, "scenario": sc})
    return cells


CHANNELS = ("call", "message", "prompt")


def pick_channel(form, rng):
    """Spoken-word forms only make sense in calls (or a transcript embedded in a prompt);
    split numbers need separate turns (call / chat messages), so not prompts."""
    if form in ("spoken", "spoken_fillers"):
        return rng.choices(["call", "prompt"], [0.85, 0.15])[0]
    if form == "split_turns":
        return rng.choices(["call", "message"], [0.7, 0.3])[0]
    return rng.choices(CHANNELS, [0.45, 0.30, 0.25])[0]


def sample_cells(n, seed=0, pii_free_share=0.30):
    """Hard cells sampled more often; a fixed share of calls are PII-free (like real calls).
    Returns COPIES (earlier versions shared dict objects, so later edits leaked across rows)."""
    rng = random.Random(seed)
    grid = build_grid()
    none = [c for c in grid if c["pii"] == "NONE"]
    pii = [c for c in grid if c["pii"] != "NONE"]
    out = []
    for _ in range(n):
        if rng.random() < pii_free_share:
            c = dict(rng.choice(none))
        else:
            c = dict(rng.choices(pii, weights=[weight(c) for c in pii])[0])
        c["channel"] = pick_channel(c["form"], rng)
        out.append(c)
    return out


if __name__ == "__main__":
    g = build_grid()
    print(len(g), "cells;", sum(c["pii"] == "NONE" for c in g), "PII-free")
