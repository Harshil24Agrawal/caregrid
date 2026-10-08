"""Regex-first PII masker (Presidio PERSON pass only if it is already importable and initialises).

Order: emails -> member IDs -> NPI in context -> phones -> labelled DOB -> addresses -> names.
Business identifiers (CASE-/INV-/CLM-/PA-/KA-/WF-/E-codes), amounts and plain dates are never touched.
Returns (masked, pii_types); types only, never values.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# malformed IDs (M + 5..12 digits) are masked too; validity is judged by the guard on the raw text
MEMBER_ID = re.compile(r"(?<![\w-])M\d{5,12}(?!\w)")
NPI_CONTEXT = re.compile(r"(?i)(\b(?:NPI|National Provider Identifier)\b[^0-9\n]{0,15}?)(?<!\d)(\d{5,12})(?!\d)")
PHONE_IN = re.compile(r"(?<![\w-])(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\w)")
PHONE_US = re.compile(r"(?<![\w-])(?:\+1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\w)")
DOB = re.compile(
    r"(?i)\b(dob|d\.o\.b\.?|born(?:\s+on)?|date of birth)\b(\s*[:\-]?\s*)"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]+\.? \d{1,2},? \d{4}|\d{1,2} [A-Za-z]+ \d{4})"
)
_SUFFIX = (r"(?:Road|Rd|Street|St|Lane|Ln|Nagar|Avenue|Ave|Colony|Marg|Cross|Boulevard|Blvd|Drive|"
           r"Layout|Block|Sector|Path|Chowk|Circle)")
ADDRESS = re.compile(
    r"(?<![\w₹,.-])\d{1,5}[A-Za-z]?(?:[/-]\d{1,4})?,?\s+(?:[A-Za-z][\w.'-]*\s+){0,4}?" + _SUFFIX + r"\b"
    r"(?:,\s*[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)?(?:,?\s+[A-Z]{2}\s+\d{5}|,?\s+\d{6}(?!\d))?"
)
_NAME_WORD = r"[A-Z][a-z'\u2019]+(?:-[A-Z][a-z]+)?"
_NAME = _NAME_WORD + r"(?:\s+" + _NAME_WORD + r"){0,2}"
HONORIFIC_NAME = re.compile(r"\b(Dr|Mr|Ms|Mrs|Miss|Prof)\b\.?\s+(" + _NAME + ")")
# "changed name from Priya Nair to Priya Menon": both names are personal data even without an honorific
NAME_PAIR = re.compile(r"(?i:\bname\b)([^.\n]{0,25}?)(?i:\bfrom\b)\s+(" + _NAME + r")\s+(?i:to)\s+(" + _NAME + ")")
NAME_TO = re.compile(r"(?i:\bname\b)([^.\n\[]{0,25}?)(?i:\bto\b)\s+(" + _NAME + ")")
_HONORIFIC_PREFIX = r"(?:(?:Dr|Mr|Ms|Mrs|Miss|Prof)\.?\s+)?"


@dataclass
class Gazetteer:
    """Known fake identities. names: full name -> kind (provider|member|person); the rest are literals."""

    names: dict[str, str] = field(default_factory=dict)
    addresses: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)

    def add_name(self, name: str, kind: str) -> None:
        name = " ".join(name.split())
        if name and name not in self.names:
            self.names[name] = kind


def build_gazetteer(data_dir: Path) -> Gazetteer:
    """Every name/address in profiles.json plus every honorific name found in historical_cases raw text."""
    g = Gazetteer()
    prof = Path(data_dir) / "profiles.json"
    if prof.exists():
        data = json.loads(prof.read_text(encoding="utf-8"))
        for p in data.get("providers", []):
            g.add_name(p["name"], "provider")
            g.addresses.append(p["address"])
            g.phones.append(p["phone"])
            if p.get("email"):
                g.emails.append(p["email"])
        for m in data.get("members", []):
            g.add_name(m["name"], "member")
            g.phones.append(m["phone"])
            g.emails.append(m["email"])
        g.addresses.extend(data.get("other_addresses", []))
    hist = Path(data_dir) / "historical_cases.csv"
    if hist.exists():
        import csv

        with open(hist, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                for m in HONORIFIC_NAME.finditer(row["raw_text"]):
                    g.add_name(m.group(2), "provider" if m.group(1) == "Dr" else "person")
    return g


_default_gaz: Gazetteer | None = None


def default_gazetteer() -> Gazetteer:
    global _default_gaz
    if _default_gaz is None:
        from caregrid import config

        _default_gaz = build_gazetteer(config.DATA_DIR)
    return _default_gaz


_presidio = None  # None = untried, False = unavailable, else analyzer


def _presidio_persons(text: str) -> list[tuple[int, int]]:
    global _presidio
    if _presidio is None:
        try:  # only if already importable AND a spaCy model is already installed; never download anything
            from presidio_analyzer import AnalyzerEngine

            _presidio = AnalyzerEngine()
        except Exception:
            _presidio = False
    if not _presidio:
        return []
    try:
        return [(r.start, r.end) for r in _presidio.analyze(text=text, language="en", entities=["PERSON"])]
    except Exception:
        return []


def anonymize(text: str, gazetteer: Gazetteer | None = None) -> tuple[str, list[str]]:
    gaz = gazetteer if gazetteer is not None else default_gazetteer()
    found: set[str] = set()

    def sub(pattern: re.Pattern[str], repl, s: str, kind: str) -> str:
        out, n = pattern.subn(repl, s)
        if n:
            found.add(kind)
        return out

    s = text
    s = sub(EMAIL, "[EMAIL]", s, "EMAIL")
    s = sub(MEMBER_ID, "[MEMBER_ID]", s, "MEMBER_ID")
    s = sub(NPI_CONTEXT, lambda m: m.group(1) + "[NPI]", s, "NPI")
    s = sub(PHONE_IN, "[PHONE]", s, "PHONE")
    s = sub(PHONE_US, "[PHONE]", s, "PHONE")
    s = sub(DOB, lambda m: f"{m.group(1)}{m.group(2)}[DATE_OF_BIRTH]", s, "DATE_OF_BIRTH")
    for lit in sorted(gaz.emails, key=len, reverse=True):
        s = sub(re.compile(re.escape(lit), re.I), "[EMAIL]", s, "EMAIL")
    for lit in sorted(gaz.phones, key=len, reverse=True):
        s = sub(re.compile(r"(?<!\w)" + re.escape(lit) + r"(?!\w)"), "[PHONE]", s, "PHONE")
    for lit in sorted(gaz.addresses, key=len, reverse=True):
        s = sub(re.compile(re.escape(lit), re.I), "[ADDRESS]", s, "ADDRESS")
    s = sub(ADDRESS, "[ADDRESS]", s, "ADDRESS")

    # --- names: stable per-document tokens
    tokens: dict[str, str] = {}
    counters = {"PROVIDER": 0, "MEMBER": 0, "PERSON": 0}

    def token(name: str, kind: str) -> str:
        key = " ".join(name.lower().split())
        if key not in tokens:
            counters[kind] += 1
            tokens[key] = f"[{kind}_{counters[kind]}]"
        return tokens[key]

    if gaz.names:
        kinds = {n.lower(): k.upper() for n, k in gaz.names.items()}
        alt = "|".join(re.escape(n) for n in sorted(gaz.names, key=len, reverse=True))
        rx = re.compile(r"(?<!\w)" + _HONORIFIC_PREFIX + "(" + alt + r")(?!\w)", re.I)

        def by_gaz(m: re.Match[str]) -> str:
            found.add("PERSON")
            return token(m.group(1), kinds[" ".join(m.group(1).lower().split())])

        s = rx.sub(by_gaz, s)

    def by_honorific(m: re.Match[str]) -> str:
        found.add("PERSON")
        return token(m.group(2), "PROVIDER" if m.group(1) == "Dr" else "PERSON")

    s = HONORIFIC_NAME.sub(by_honorific, s)

    def by_pair(m: re.Match[str]) -> str:
        found.add("PERSON")
        return f"name{m.group(1)}from {token(m.group(2), 'PERSON')} to {token(m.group(3), 'PERSON')}"

    s = NAME_PAIR.sub(by_pair, s)

    def by_name_to(m: re.Match[str]) -> str:
        found.add("PERSON")
        return f"name{m.group(1)}to {token(m.group(2), 'PERSON')}"

    s = NAME_TO.sub(by_name_to, s)

    spans = _presidio_persons(s)
    for start, end in sorted(spans, reverse=True):
        if "[" not in s[start:end]:
            s = s[:start] + token(s[start:end], "PERSON") + s[end:]
            found.add("PERSON")
    return s, sorted(found)
