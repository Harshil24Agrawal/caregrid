"""Regex-first PII masker (Presidio PERSON pass only if it is already importable and initialises).

Pipeline: normalise (NFKC, zero-width strip, look-alike fold) -> labelled DOB -> protect ISO dates -> emails (incl. spaced)
-> member IDs -> NPI in context -> card/SSN/Aadhaar -> phones -> non-ISO dates -> long digit runs -> PIN/addresses
-> names -> restore ISO dates.
Business identifiers (CASE-/INV-/CLM-/PA-/KA-/WF-/E-codes), rupee amounts and ISO dates are never touched.
Every pattern is length-bounded so adversarial 50k-character inputs stay linear.
Returns (masked, pii_types); types only, never values.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from caregrid.ingest.names import CAPW, STOP_STATIC, SURNAME, mask_cueless_names, stop_terms_from_titles
from caregrid.ingest.normalize import iso_effective_dates, normalize_text

# ---- emails: anchored on '@', bounded, tolerant of spaces around dots and '@' ("jane . doe @ clinic .example")
_L = r"[A-Za-z0-9_%+-]{1,64}"
_D = r"[A-Za-z0-9-]{1,63}"
EMAIL = re.compile(
    r"(?<![A-Za-z0-9_%+-])" + _L + r"(?:[ \t]*\.[ \t]*" + _L + r"){0,5}[ \t]*@[ \t]*" + _D + r"(?:[ \t]*\.[ \t]*" + _D + r"){1,5}")
# malformed IDs (M + 5..12 digits, optional '-' or space) are masked too; validity is judged by the guard on the raw text
MEMBER_ID = re.compile(r"(?i)(?<![\w-])m[- ]?\d{5,12}(?!\w)")
# more member-id shapes: MBR12345678, MEM-AB12345, "member id: 12345678"
MEMBER_ID_VARIANTS = re.compile(
    r"(?i)(?<![\w-])(?:(?:MBR|MEM)[- ]?(?=[A-Z0-9]*\d)[A-Z0-9]{5,14}|member\s*(?:id|no\.?|number|#)\s*[:#-]?\s*\d{5,12})(?!\w)")
# Indian PAN (ABCDE1234F) and passport (A1234567). IFSC codes (SBIN0001234) are not personal and are left alone.
PAN = re.compile(r"(?<![A-Za-z0-9-])[A-Z]{5}\d{4}[A-Z](?!\w)")
PASSPORT = re.compile(r"(?<![A-Za-z0-9-])[A-Z]\d{7}(?!\w)")
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
NPI_CONTEXT = re.compile(
    r"(?i)(\b(?:NPI|National Provider Identifier)\b[^0-9\n]{0,15}?)(?<!\d)(\d(?:[ -]?\d){4,11})(?!\d)")
# "provider 1234567890" / "prov id 1234567890" also cues an NPI (NPI length only, so "provider cost 62500" is untouched)
PROVIDER_NPI = re.compile(
    r"(?i)(\b(?:provider|prov)\b\.?(?:\s+(?:id|no\.?|number|#))?[^0-9\n]{0,15}?)(?<!\d)(\d(?:[ -]?\d){8,9})(?!\d)")
# +91 / 0 prefix optionally bracketed, the first 5 digits optionally bracketed, separators ' ', '-', '.' : (+91)9876543210, 98765.43210, +91 (98765) 43210
PHONE_IN = re.compile(r"(?<![\w-])(?:\(?\+91\)?[\s.-]?|0)?\(?[6-9]\d{4}\)?[\s.-]?\d{5}(?!\w)")
PHONE_US = re.compile(r"(?<![\w-])(?:\+1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\w)")
SSN = re.compile(r"(?<![\w-])\d{3}-\d{2}-\d{4}(?!\w)")
AADHAAR = re.compile(r"(?<![\w-])\d{4} \d{4} \d{4}(?!\w)")
CARD = re.compile(r"(?<![\w₹-])\d(?:[ -]?\d){12,18}(?!\d)")
DIGIT_RUN = re.compile(r"(?<![\w₹-])\d(?:[ -]?\d){8,}(?!\d)")
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
          r"nov(?:ember)?|dec(?:ember)?)")
# anything date-shaped after a birth label: 1980-03-03, 1980/03/03, 3/3/80, 3rd March 1980, 3 of March, 1980, March 3rd, 1980
_DOB_DATE = (r"\d{4}[/.-]\d{1,2}[/.-]\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|"
             r"\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH + r"\.?,?\s+\d{4}|" + _MONTH + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}")
DOB = re.compile(r"(?i)\b(dob|d\.o\.b\.?|born(?:\s+on)?|date of birth|birth\s?date|birthday)\b(\s*[:\-]?\s*)(" + _DOB_DATE + ")")
ISO_DATE = re.compile(r"(?<![\w-])\d{4}-\d{2}-\d{2}(?!\d)")
# dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy (same separator twice; 2-digit years only with '/')
OTHER_DATE = re.compile(r"(?<![\w/.:-])\d{1,2}([/.-])\d{1,2}\1(?:\d{4}|(?<=/)\d{2})(?![\w/.-])")
_SUFFIX = (r"(?:Road|Rd|Street|St|Lane|Ln|Nagar|Avenue|Ave|Colony|Marg|Cross|Boulevard|Blvd|Drive|"
           r"Layout|Block|Sector|Path|Chowk|Circle)")
ADDRESS = re.compile(
    r"(?<![\w₹,.-])\d{1,5}[A-Za-z]?(?:[/-]\d{1,4})?,?\s+(?:[A-Za-z][\w.'-]{0,30}\s+){0,4}?" + _SUFFIX + r"\b"
    r"(?:,\s*[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)?(?:,?\s+[A-Z]{2}\s+\d{5}|,?\s+\d{6}(?!\d))?"
)
# Indian styles: "Flat 4B, Sunrise Apartments, Anna Nagar, Chennai 600040" / "Plot 12, Green Colony 560001"
ADDRESS_UNIT = re.compile(
    r"(?<!\w)(?:Flat|House|Plot|Door|Apt|Apartment)s?\.?\s{0,2}(?:No\.?|#)?\s{0,2}(?=[A-Za-z0-9/-]{0,7}\d)[A-Za-z0-9/-]{1,8}"
    r"(?:\s{0,2},\s{0,2}[A-Z][^,.\n]{1,40}){0,4}")
ADDRESS_PLACE = re.compile(
    r"(?<![\w])(?:[A-Z][\w'.-]{0,30}\s+){1,3}(?:Apartments?|Nagar|Colony|Layout|Residency|Towers?|Society|Enclave|Gardens?|Heights)\b"
    r"(?:\s{0,2},\s{0,2}[A-Z][^,.\n]{1,40}){0,2}(?:,?\s{0,2}\d{6}(?!\d))?")
PIN_LABEL = re.compile(r"(?i)\b(?:pin(?:\s?code)?|zip(?:\s?code)?|postal code)\b\s{0,2}[:#-]?\s{0,2}\d{5,6}(?!\d)")
# a name = first word + up to two more words/chunks; O'Brien, Rao-Iyer, de la Cruz, van der Berg are each ONE chunk
_NAME = CAPW + r"(?:\s+" + SURNAME + r"){0,2}"
HONORIFIC_NAME = re.compile(r"\b(Dr|Mr|Ms|Mrs|Miss|Prof)\b\.?\s+(" + _NAME + ")")
# "changed name from Priya Nair to Priya Menon": both names are personal data even without an honorific
NAME_PAIR = re.compile(r"(?i:\bname\b)([^.\n]{0,25}?)(?i:\bfrom\b)\s+(" + _NAME + r")\s+(?i:to)\s+(" + _NAME + ")")
NAME_TO = re.compile(r"(?i:\bname\b)([^.\n\[]{0,25}?)(?i:\bto\b)\s+(" + _NAME + ")")
_HONORIFIC_PREFIX = r"(?:(?:Dr|Mr|Ms|Mrs|Miss|Prof)\.?\s+)?"
_AMOUNT_BEFORE = re.compile(r"(?i)(?:\u20b9|\brs\.?|\binr)\s{0,2}$")
_DIGIT = re.compile(r"\d")
_AT = re.compile("@")
_SENTINEL = re.compile("\ue000(\\d+)\ue001")


@dataclass
class Gazetteer:
    """Known fake identities. names: full name -> kind (provider|member|person); the rest are literals."""

    names: dict[str, str] = field(default_factory=dict)
    addresses: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    stop_terms: set[str] = field(default_factory=set)   # business words that are never name parts

    def add_name(self, name: str, kind: str) -> None:
        name = " ".join(name.split())
        if name and name not in self.names:
            self.names[name] = kind


def _business_terms(data_dir: Path) -> set[str]:
    """Words from team names, workflow/policy titles, runbook headings and field names (never name parts)."""
    import csv

    texts: list[str] = []
    for fname, cols in (("teams.csv", ("name",)), ("knowledge_articles.csv", ("title",)), ("field_definitions.csv", ("field",))):
        path = data_dir / fname
        if path.exists():
            with open(path, encoding="utf-8", newline="") as f:
                texts += [row[c] for row in csv.DictReader(f) for c in cols]
    wf = data_dir / "workflows.json"
    if wf.exists():
        texts += [w["title"] for w in json.loads(wf.read_text(encoding="utf-8"))]
    rb = data_dir / "runbooks.md"
    if rb.exists():
        texts += re.findall(r"^## RB-\d+ (.+)$", rb.read_text(encoding="utf-8"), re.M)
    return stop_terms_from_titles(*texts)


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
    g.stop_terms |= _business_terms(Path(data_dir))
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


def luhn_ok(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if flip:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        flip = not flip
    return total % 10 == 0


def anonymize(text: str, gazetteer: Gazetteer | None = None, cueless: bool = True) -> tuple[str, list[str]]:
    gaz = gazetteer if gazetteer is not None else default_gazetteer()
    found: set[str] = set()

    def sub(pattern: re.Pattern[str], repl, s: str, kind: str, needs: re.Pattern[str] | None = None) -> str:
        if needs is not None and not needs.search(s):
            return s                                   # cheap pre-check: skip passes that cannot match (keeps 50k inputs fast)
        out, n = pattern.subn(repl, s)
        if n:
            found.add(kind)
        return out

    s = iso_effective_dates(normalize_text(text))
    s = sub(DOB, lambda m: f"{m.group(1)}{m.group(2)}[DATE_OF_BIRTH]", s, "DATE_OF_BIRTH", _DIGIT)

    # ISO dates (effective dates) are protected while the digit rules run, then restored
    iso: list[str] = []

    def stash(m: re.Match[str]) -> str:
        iso.append(m.group(0))
        return f"\ue000{len(iso) - 1}\ue001"

    if _DIGIT.search(s):
        s = ISO_DATE.sub(stash, s)

    s = sub(EMAIL, "[EMAIL]", s, "EMAIL", _AT)
    s = sub(MEMBER_ID, "[MEMBER_ID]", s, "MEMBER_ID", _DIGIT)
    s = sub(MEMBER_ID_VARIANTS, "[MEMBER_ID]", s, "MEMBER_ID", _DIGIT)
    s = sub(NPI_CONTEXT, lambda m: m.group(1) + "[NPI]", s, "NPI", _DIGIT)
    s = sub(PROVIDER_NPI, lambda m: m.group(1) + "[NPI]", s, "NPI", _DIGIT)
    s = sub(PAN, "[ID]", s, "ID", _DIGIT)
    s = sub(PASSPORT, "[ID]", s, "ID", _DIGIT)

    def card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            found.add("CARD")
            return "[ID]"
        return m.group(0)

    if _DIGIT.search(s):
        s = CARD.sub(card, s)
    s = sub(SSN, "[ID]", s, "ID", _DIGIT)
    s = sub(AADHAAR, "[ID]", s, "ID", _DIGIT)
    s = sub(PHONE_IN, "[PHONE]", s, "PHONE", _DIGIT)
    s = sub(PHONE_US, "[PHONE]", s, "PHONE", _DIGIT)

    def ip(m: re.Match[str]) -> str:
        if all(int(o) <= 255 for o in m.group(0).split(".")):
            found.add("ID")
            return "[ID]"
        return m.group(0)

    if _DIGIT.search(s):
        s = IPV4.sub(ip, s)
    s = sub(OTHER_DATE, "[DATE_OF_BIRTH]", s, "DATE_OF_BIRTH", _DIGIT)

    def long_digits(m: re.Match[str]) -> str:
        if _AMOUNT_BEFORE.search(m.string[max(0, m.start() - 8):m.start()]):
            return m.group(0)                      # a rupee amount, never touched
        if len(re.sub(r"\D", "", m.group(0))) == 10:
            found.add("PHONE")
            return "[PHONE]"
        found.add("ID")
        return "[ID]"

    if _DIGIT.search(s):
        s = DIGIT_RUN.sub(long_digits, s)

    for lit in sorted(gaz.emails, key=len, reverse=True):
        s = sub(re.compile(re.escape(lit), re.I), "[EMAIL]", s, "EMAIL")
    for lit in sorted(gaz.phones, key=len, reverse=True):
        s = sub(re.compile(r"(?<!\w)" + re.escape(lit) + r"(?!\w)"), "[PHONE]", s, "PHONE")
    for lit in sorted(gaz.addresses, key=len, reverse=True):
        s = sub(re.compile(re.escape(lit), re.I), "[ADDRESS]", s, "ADDRESS")
    s = sub(ADDRESS, "[ADDRESS]", s, "ADDRESS")           # number-first ("22 Gandhi Nagar, Chennai 600001") before the looser ones
    s = sub(ADDRESS_UNIT, "[ADDRESS]", s, "ADDRESS")
    s = sub(ADDRESS_PLACE, "[ADDRESS]", s, "ADDRESS")
    s = sub(PIN_LABEL, "[ADDRESS]", s, "ADDRESS")

    # --- names: stable per-document tokens
    tokens: dict[str, str] = {}
    counters = {"PROVIDER": 0, "MEMBER": 0, "PERSON": 0}

    firsts: dict[str, str] = {}   # first name -> token of the first full name seen with it

    def token(name: str, kind: str) -> str:
        key = " ".join(name.lower().split())
        if key not in tokens:
            counters[kind] += 1
            tokens[key] = f"[{kind}_{counters[kind]}]"
            firsts.setdefault(key.split()[0], tokens[key])
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

    if cueless:
        s, changed = mask_cueless_names(s, STOP_STATIC | gaz.stop_terms, lambda n: token(n, "PERSON"), firsts.get)
        if changed:
            found.add("PERSON")

    spans = _presidio_persons(s)
    for start, end in sorted(spans, reverse=True):
        if "[" not in s[start:end]:
            s = s[:start] + token(s[start:end], "PERSON") + s[end:]
            found.add("PERSON")
    s = _SENTINEL.sub(lambda m: iso[int(m.group(1))], s)
    return s, sorted(found)
