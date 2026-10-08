from pathlib import Path


def edit(path, pairs):
    p = Path(path)
    t = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert old in t, (path, old[:90])
        t = t.replace(old, new, 1)
    p.write_text(t, encoding="utf-8")


# ---------------------------------------------------------------- names.py: one token per name, whatever its shape
edit("caregrid/ingest/names.py", [
    (r'''CAP_WORD = re.compile(r"(?<![\w\[\]-])[A-Z][a-z]+(?!\w)")''',
     r'''# A name word: O'Brien, D'Souza, McDonald, Rao-Iyer, Anne-Marie. Surnames may carry lowercase particles: de la Cruz, van der Berg.
NAME_PARTS = r"(?:de la|del|della|de|van der|van den|van de|van|von|der|den|bin|bint|al|el|le|la|di|da|dos|das|du)"
CAPW = r"(?:[A-Z]['’][A-Z][a-z]+|Mc[A-Z][a-z]+|[A-Z][a-z]+)(?:[-'’][A-Z][a-z]+)*"
SURNAME = r"(?:" + NAME_PARTS + r"\s+)*" + CAPW
CAP_WORD = re.compile(r"(?<![\w\[\]-])" + SURNAME + r"(?!\w)")'''),
])

# ---------------------------------------------------------------- normalize.py: leetspeak fold (detection copies only)
edit("caregrid/ingest/normalize.py", [
    (r'''def normalize_text(text: str, fold: bool = True) -> str:''',
     r'''_LEET = str.maketrans({"1": "i", "0": "o", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a"})


def fold_leet(text: str) -> str:
    """1gn0re -> ignore. For DETECTION copies only (injection patterns); never used for masking or output."""
    return text.translate(_LEET)


def normalize_text(text: str, fold: bool = True) -> str:'''),
])

# ---------------------------------------------------------------- anonymize.py
edit("caregrid/ingest/anonymize.py", [
    (r'''from caregrid.ingest.names import STOP_STATIC, mask_cueless_names, stop_terms_from_titles''',
     r'''from caregrid.ingest.names import CAPW, STOP_STATIC, SURNAME, mask_cueless_names, stop_terms_from_titles'''),
    (r'''MEMBER_ID = re.compile(r"(?<![\w-])M[- ]?\d{5,12}(?!\w)")''',
     r'''MEMBER_ID = re.compile(r"(?i)(?<![\w-])m[- ]?\d{5,12}(?!\w)")
# more member-id shapes: MBR12345678, MEM-AB12345, "member id: 12345678"
MEMBER_ID_VARIANTS = re.compile(
    r"(?i)(?<![\w-])(?:(?:MBR|MEM)[- ]?(?=[A-Z0-9]*\d)[A-Z0-9]{5,14}|member\s*(?:id|no\.?|number|#)\s*[:#-]?\s*\d{5,12})(?!\w)")
# Indian PAN (ABCDE1234F) and passport (A1234567). IFSC codes (SBIN0001234) are not personal and are left alone.
PAN = re.compile(r"(?<![A-Za-z0-9-])[A-Z]{5}\d{4}[A-Z](?!\w)")
PASSPORT = re.compile(r"(?<![A-Za-z0-9-])[A-Z]\d{7}(?!\w)")
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")'''),
    (r'''PHONE_IN = re.compile(r"(?<![\w-])(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\w)")''',
     r'''# +91 / 0 prefix optionally bracketed, the first 5 digits optionally bracketed, separators ' ', '-', '.' : (+91)9876543210, 98765.43210, +91 (98765) 43210
PHONE_IN = re.compile(r"(?<![\w-])(?:\(?\+91\)?[\s.-]?|0)?\(?[6-9]\d{4}\)?[\s.-]?\d{5}(?!\w)")'''),
    (r'''NPI_CONTEXT = re.compile(
    r"(?i)(\b(?:NPI|National Provider Identifier)\b[^0-9\n]{0,15}?)(?<!\d)(\d(?:[ -]?\d){4,11})(?!\d)")''',
     r'''NPI_CONTEXT = re.compile(
    r"(?i)(\b(?:NPI|National Provider Identifier)\b[^0-9\n]{0,15}?)(?<!\d)(\d(?:[ -]?\d){4,11})(?!\d)")
# "provider 1234567890" / "prov id 1234567890" also cues an NPI (NPI length only, so "provider cost 62500" is untouched)
PROVIDER_NPI = re.compile(
    r"(?i)(\b(?:provider|prov)\b\.?(?:\s+(?:id|no\.?|number|#))?[^0-9\n]{0,15}?)(?<!\d)(\d(?:[ -]?\d){8,9})(?!\d)")'''),
    (r'''DOB = re.compile(
    r"(?i)\b(dob|d\.o\.b\.?|born(?:\s+on)?|date of birth)\b(\s*[:\-]?\s*)"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|[A-Za-z]+\.? \d{1,2},? \d{4}|\d{1,2} [A-Za-z]+ \d{4})"
)''',
     r'''_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
          r"nov(?:ember)?|dec(?:ember)?)")
# anything date-shaped after a birth label: 1980-03-03, 1980/03/03, 3/3/80, 3rd March 1980, 3 of March, 1980, March 3rd, 1980
_DOB_DATE = (r"\d{4}[/.-]\d{1,2}[/.-]\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|"
             r"\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH + r"\.?,?\s+\d{4}|" + _MONTH + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}")
DOB = re.compile(r"(?i)\b(dob|d\.o\.b\.?|born(?:\s+on)?|date of birth|birth\s?date|birthday)\b(\s*[:\-]?\s*)(" + _DOB_DATE + ")")'''),
    (r'''_NAME_WORD = r"[A-Z][a-z'’]+(?:-[A-Z][a-z]+)?"
_NAME = _NAME_WORD + r"(?:\s+" + _NAME_WORD + r"){0,2}"''',
     r'''# a name = first word + up to two more words/chunks; O'Brien, Rao-Iyer, de la Cruz, van der Berg are each ONE chunk
_NAME = CAPW + r"(?:\s+" + SURNAME + r"){0,2}"'''),
    (r'''    s = sub(MEMBER_ID, "[MEMBER_ID]", s, "MEMBER_ID")
    s = sub(NPI_CONTEXT, lambda m: m.group(1) + "[NPI]", s, "NPI")''',
     r'''    s = sub(MEMBER_ID, "[MEMBER_ID]", s, "MEMBER_ID")
    s = sub(MEMBER_ID_VARIANTS, "[MEMBER_ID]", s, "MEMBER_ID")
    s = sub(NPI_CONTEXT, lambda m: m.group(1) + "[NPI]", s, "NPI")
    s = sub(PROVIDER_NPI, lambda m: m.group(1) + "[NPI]", s, "NPI")
    s = sub(PAN, "[ID]", s, "ID")
    s = sub(PASSPORT, "[ID]", s, "ID")'''),
    (r'''    s = sub(PHONE_US, "[PHONE]", s, "PHONE")
    s = sub(OTHER_DATE,''',
     r'''    s = sub(PHONE_US, "[PHONE]", s, "PHONE")

    def ip(m: re.Match[str]) -> str:
        if all(int(o) <= 255 for o in m.group(0).split(".")):
            found.add("ID")
            return "[ID]"
        return m.group(0)

    s = IPV4.sub(ip, s)
    s = sub(OTHER_DATE,'''),
])
print("ok")
