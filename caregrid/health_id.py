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

# a well-formed ID, in any of the usual typings (hyphens, spaces or none); looser shapes that start CG- are "invalid: format"
STRICT = re.compile(r"(?i)(?<![\w-])CG[- ]?(\d{4})[- ]?(\d{4})[- ]?(\d{4})(?!\w)")
LOOSE = re.compile(r"(?i)(?<![\w-])CG-\d[\d -]{1,18}\d(?!\w)")


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


def validate(raw_text: str) -> str | None:
    """'valid' | 'invalid: checksum' | 'invalid: format' | None (no Health ID in the text). Flags only; the value never leaves."""
    results = []
    for m in STRICT.finditer(raw_text):
        results.append("valid" if verhoeff_valid("".join(m.groups())) else "invalid: checksum")
    strict_spans = [m.span() for m in STRICT.finditer(raw_text)]
    for m in LOOSE.finditer(raw_text):
        if not any(s <= m.start() and m.end() <= e for s, e in strict_spans):
            results.append("invalid: format")
    if not results:
        return None
    return next((r for r in results if r != "valid"), "valid")


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
    for m in STRICT.finditer(raw_text):
        member = member_by_id(format_id("".join(m.groups())), data_dir)
        if member is not None:
            return member["profile_key"]
    return None
