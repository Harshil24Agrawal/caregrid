"""Text normalisation used before any PII or injection matching.

normalize_text: strip zero-width/soft-hyphen/bidi controls, NFKC (full-width -> ASCII), fold look-alike Cyrillic/Greek
letters to Latin. collapse_letters: joins letter-spaced words ("i g n o r e") for DETECTION only, never for output.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

_INVISIBLE = re.compile("[​-‏⁠­﻿‪-‮⁦-⁩]")

_LOOKALIKES = {
    # Cyrillic lower / upper
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i",
    "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "к": "k", "м": "m", "т": "t", "в": "b",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S", "У": "Y",
    # Greek
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
    "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X", "ο": "o", "ν": "v",
    "ι": "i", "ρ": "p", "α": "a", "κ": "k", "υ": "u",
}
_FOLD = str.maketrans(_LOOKALIKES)

# four or more single letters joined by single separators: "i g n o r e", "d.o.s.a.g.e"
_SPACED_LETTERS = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z][ .\-_]){3,}[A-Za-z](?![A-Za-z0-9])")
_SEP = re.compile(r"[ .\-_]")


def normalize_text(text: str, fold: bool = True) -> str:
    out = unicodedata.normalize("NFKC", _INVISIBLE.sub("", text))
    return out.translate(_FOLD) if fold else out


def collapse_letters(text: str) -> str:
    return _SPACED_LETTERS.sub(lambda m: _SEP.sub("", m.group(0)), text)


def detection_views(text: str) -> tuple[str, str]:
    """(normalised, normalised+letter-collapsed): patterns should be tried on both."""
    norm = normalize_text(text)
    return norm, collapse_letters(norm)


# "effective 12/03/2026", "from 12-03-2026", "w.e.f. 12.03.2026": the label says it is an effective date, not a birth date
_LABELLED_DMY = re.compile(
    r"(?i)\b(effective(?:\s+date)?(?:\s+(?:on|from|of|is))?|starting(?:\s+(?:on|from))?|from|w\.?e\.?f\.?)"
    r"(\s{0,2}[:\-]?\s{0,2})(\d{1,2})([/.-])(\d{1,2})\4(\d{4})(?!\d)")


def iso_effective_dates(text: str) -> str:
    """Rewrite LABELLED dd/mm/yyyy (or dd-mm-yyyy, dd.mm.yyyy) dates to ISO so they survive masking as effective dates.
    Day-first is assumed (decision logged); an impossible date is left alone and will be masked like any other date."""
    def fix(m: re.Match[str]) -> str:
        try:
            iso = date(int(m.group(6)), int(m.group(5)), int(m.group(3))).isoformat()
        except ValueError:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{iso}"

    return _LABELLED_DMY.sub(fix, text)
