"""Step L0 of detection: make text easier to match WITHOUT changing its length,
so span positions found on the normalised text are valid on the original too."""
import unicodedata

_ZW = set("​‌‍⁠﻿")


def normalise(text: str) -> str:
    """Indic/full-width digits -> 0-9; a zero-width char between two digits -> a plain space (all 1:1).
    Zero-width chars inside words are kept (the spoken engine ignores them when classifying)."""
    out = []
    n = len(text)
    for i, ch in enumerate(text):
        if ord(ch) > 127 and ch.isdigit():
            try:
                out.append(str(unicodedata.digit(ch)))
                continue
            except ValueError:
                pass
        if ch in _ZW and 0 < i < n - 1 and text[i - 1].isdigit() and text[i + 1].isdigit():
            out.append(" ")
            continue
        out.append(ch)
    return "".join(out)
