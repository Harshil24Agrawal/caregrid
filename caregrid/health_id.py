"""CareGrid Health ID: a synthetic patient ID in the style of an ABHA number (a concept only; nothing here is a real identifier).

Format CG-XXXX-XXXX-XXXX: 12 digits, the last one a Verhoeff check digit over the first 11. The 11 digits are drawn at random from a seeded
generator and are NEVER derived from any personal attribute (name, date of birth, phone, member id ...): knowing a person tells you nothing
about their ID. The mapping profile key <-> Health ID lives only in data/profiles.json (it stands in for a vault).

`find` is for RAW text (the input guard): it returns validity flags and, for a valid known ID, the profile key. The ID itself is never
returned by anything that is stored, logged or sent to a model; they see [HEALTH_ID].
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

# Verhoeff tables (dihedral group D5)
_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
      [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1], [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
      [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
      [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1], [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]

# Detection runs on NFKC-normalised text with invisible/format/NUL characters removed. A "skeleton" of that text (same length, so positions line up)
# folds the letters that look like C or G (Greek Gamma/Sigma/lunate sigma, Cyrillic Es/Ge, Roman numeral C, Komi Ge ...; the fullwidth and
# mathematical variants are already ASCII after NFKC). A C/G pair that is not part of a longer word, followed within WINDOW characters by digit groups
# that total exactly 12 digits, is a Health ID typing whatever sits between the groups (separators, words, line breaks). The groups stop counting
# at an ISO date, an amount (rupee sign, Rs, INR, thousands commas) or a business id (CASE-1024, INV-..., KA-...), which are never touched.
# "CG" and 3+ digits of any other length is a malformed typing ("invalid: format"). Both are masked.
_C_LIKE = "C\u0421\u0441\u03f9\u03f2\u03a3\u03c3\u216d\u217d\ua4da\u2282"
_G_LIKE = "G\u0393\u03b3\u0413\u0433\u0261\u050c\u050d\ua4d6\u210a"
_CONFUSE = {**{ord(ch): "C" for ch in _C_LIKE[1:]}, **{ord(ch): "G" for ch in _G_LIKE[1:]}, ord("c"): "C", ord("g"): "G"}
WINDOW = 48
_SEP = r"[\W_]{0,3}"
_PREFIX = re.compile(r"(?i)(?<![A-Za-z0-9])(?:C[\W_]?G|G[\W_]?C)(?![A-Za-z])")      # CG, or GC when a look-alike swapped the pair (Greek Gamma + Sigma)
_LOOSE_TAIL = re.compile(r"[\W_]{0,3}\d(?:[\W_]{0,3}\d){2,22}")
_ISO = re.compile(r"\d{4}-\d\d-\d\d")
_AMOUNT = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?!\d)")
_BUSINESS = re.compile(r"(?i)(?:CASE|REQ|INV|CLM|PA|KA|WF|FIELD|REG|TEAM|RR|RB|PRF|NPI|[A-Z]{1,6})-$")
_MONEY = re.compile(r"(?i)(?:\u20b9|\brs\.?|\binr)\s{0,2}$")
STRICT = re.compile(r"(?i)(?<![\w-])CG[- ]?(\d{4})[- ]?(\d{4})[- ]?(\d{4})(?!\w)")      # the canonical typings (used by normalise / is_valid)
BARE12 = re.compile(r"(?<![\w\u20b9-])\d(?:[ -]?\d){11}(?!\d)")                             # a bare 12-digit run: masked when it passes the checksum


def skeleton(text: str) -> str:
    return text.translate(_CONFUSE)


def _blocked(sk: str, prefix_end: int, gs: int, ge: int, protected: list[tuple[int, int]]) -> bool:
    if any(s <= gs and ge <= e for s, e in protected):
        return True
    before = sk[max(prefix_end, gs - 9):gs]
    return bool(_MONEY.search(before) or _BUSINESS.search(before) or sk[max(0, gs - 1):gs] == "\ue000" or sk[ge:ge + 1] == "\ue001")


def find(text: str) -> list[tuple[int, int, str]]:
    """[(start, end, 12 digits)] of every Health ID typing (valid or not) in already-normalised text."""
    sk = skeleton(text)
    protected = [m.span() for rx in (_ISO, _AMOUNT) for m in rx.finditer(sk)]
    out: list[tuple[int, int, str]] = []
    pos = 0
    for pm in _PREFIX.finditer(sk):
        if pm.start() < pos:
            continue
        total, digits, end = 0, [], None
        for gm in re.finditer(r"\d+", sk[pm.end():pm.end() + WINDOW + 24]):
            gs, ge = pm.end() + gm.start(), pm.end() + gm.end()
            if gs - pm.end() > WINDOW or _blocked(sk, pm.end(), gs, ge, protected):
                break
            total += len(gm.group())
            digits.append(gm.group())
            if total >= 12:
                end = ge if total == 12 and not re.match(r"[\W_]{0,3}\d", sk[ge:]) else None      # a 13th digit makes it a malformed typing
                break
        if end is not None:
            out.append((pm.start(), end, re.sub(r"\D", "", "".join(digits))))
            pos = end
    return out


def find_loose(text: str, taken: list[tuple[int, int, str]]) -> list[tuple[int, int]]:
    """Malformed typings: C/G then 3+ digits that are not a 12-digit ID and not an ISO date or an amount."""
    sk = skeleton(text)
    out = []
    for pm in _PREFIX.finditer(sk):
        if any(s <= pm.start() < e for s, e, _ in taken):
            continue
        tail = _LOOSE_TAIL.match(sk, pm.end())
        if tail is None:
            continue
        ds = pm.end() + re.search(r"\d", sk[pm.end():tail.end()]).start()
        if _ISO.match(sk, ds) or _blocked(sk, pm.end(), ds, tail.end(), []):
            continue
        out.append((pm.start(), tail.end()))
    return out


def mask_text(text: str) -> tuple[str, bool]:
    """Replace every Health ID typing (and malformed one) with [HEALTH_ID]; (new text, anything replaced)."""
    spans = [(s, e) for s, e, _ in find(text)]
    spans += find_loose(text, find(text))
    if not spans:
        return text, False
    out, last = [], 0
    for s, e in sorted(spans):
        if s < last:
            continue
        out.append(text[last:s])
        out.append("[HEALTH_ID]")
        last = e
    out.append(text[last:])
    return "".join(out), True


def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_digit(payload: str) -> int:
    c = 0
    for i, ch in enumerate(reversed(payload)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return _INV[c]


def normalise_text(text: str) -> str:
    """The same normalisation the masker uses (NFKC, invisible/format/NUL characters removed, look-alikes folded)."""
    from caregrid.ingest.normalize import normalize_text

    return normalize_text(text)


def format_id(digits: str) -> str:
    return f"CG-{digits[0:4]}-{digits[4:8]}-{digits[8:12]}"


def generate(rng: random.Random) -> str:
    """11 random digits + the Verhoeff check digit. Nothing about a person goes in."""
    payload = "".join(str(rng.randrange(10)) for _ in range(11))
    return format_id(payload + str(verhoeff_digit(payload)))


def is_valid(health_id: str) -> bool:
    m = STRICT.fullmatch(health_id.strip())
    return bool(m) and verhoeff_valid("".join(m.groups()))


def normalise(health_id: str) -> str | None:
    """CG-XXXX-XXXX-XXXX (upper case, hyphens) for any well-formed typing, else None. The checksum is NOT checked here."""
    m = STRICT.fullmatch(health_id.strip())
    return format_id("".join(m.groups())) if m else None


def masked(health_id: str) -> str:
    """CG-XXXX-XXXX-1956: only the last four digits are shown."""
    n = normalise(health_id)
    return f"CG-XXXX-XXXX-{n[-4:]}" if n else "CG-XXXX-XXXX-XXXX"


def _digits(m: re.Match) -> str:
    return re.sub(r"\D", "", m.group(1))


def validate(raw_text: str) -> str | None:
    """'valid' | 'invalid: checksum' | 'invalid: format' | None (no Health ID in the text). Flags only; the value never leaves."""
    found = find(raw_text)
    results = ["valid" if verhoeff_valid(d) else "invalid: checksum" for _, _, d in found]
    results += ["invalid: format" for _ in find_loose(raw_text, found)]
    if not results:
        return None
    return next((r for r in results if r != "valid"), "valid")


def masked_ids(raw_text: str) -> list[str]:
    """CG-XXXX-XXXX-nnnn for every 12-digit Health ID typing, valid or not (independent of whether it exists: used to show the requester what was noted)."""
    return list(dict.fromkeys(masked(format_id(d)) for _, _, d in find(raw_text)))


# ------------------------------------------------------------------ the profiles data stands in for the vault
_cache: dict[str, tuple[float, dict]] = {}


def _members(data_dir: Path) -> list[dict]:
    path = Path(data_dir) / "profiles.json"
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    hit = _cache.get(str(path))
    if hit is None or hit[0] != mtime:
        hit = (mtime, json.loads(path.read_text(encoding="utf-8")))
        _cache[str(path)] = hit
    return hit[1].get("members", [])


def member_by_id(health_id: str, data_dir: Path) -> dict | None:
    n = normalise(health_id)
    return next((m for m in _members(data_dir) if n and m.get("health_id") == n), None) if n and is_valid(n) else None


def member_by_key(profile_key: str, data_dir: Path) -> dict | None:
    return next((m for m in _members(data_dir) if m.get("profile_key") == profile_key), None)


def linked_profile(raw_text: str, data_dir: Path) -> str | None:
    """The profile key of the first VALID, known Health ID in the raw text (unknown but valid IDs link nothing and say nothing)."""
    for _, _, digits in find(raw_text):
        member = member_by_id(format_id(digits), data_dir)
        if member is not None:
            return member["profile_key"]
    return None
