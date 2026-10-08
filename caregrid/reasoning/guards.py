"""Input and output guards (CONTRACTS §3-4, CLAUDE.md rules 1-6).

check_input runs on RAW text: it validates structured ids, detects injection / clinical / account-specific / sensitive
requests, then masks. Only flags and masked text leave this module; raw values are never returned or logged.
Detection runs on a normalised view (NFKC, zero-width stripped, look-alike letters folded) and on a letter-collapsed view
("i g n o r e" -> "ignore"), so unicode and spacing tricks do not hide a pattern.
"""
from __future__ import annotations

import re
from datetime import date

from caregrid import config, health_id
from caregrid.ingest.anonymize import DOB, EMAIL, NPI_CONTEXT, PROVIDER_NPI, Gazetteer, anonymize
from caregrid.ingest.names import AMBIGUOUS, CAP_WORD, STOP_STATIC, first_names
from caregrid.ingest.normalize import collapse_letters, fold_leet, iso_effective_dates, normalize_text
from caregrid.models import GuardResult, ReasonCode, Role, User
from caregrid.reasoning.extract import find_cost, find_effective_date

CLINICAL_REFUSAL = ("I can't give medical advice. This question has been routed to Clinical Review.")
MAX_INPUT_CHARS = 4000
# Roles that must never read other people's records through the assistant (RBAC before retrieval).
NO_RECORD_ACCESS = {Role.OPS_EMPLOYEE, Role.AUDITOR, Role.KNOWLEDGE_OWNER}
# Roles that may not see billing amounts.
NO_BILLING_VIEW = {Role.OPS_EMPLOYEE, Role.AUDITOR, Role.KNOWLEDGE_OWNER}

INJECTION_PATTERNS = [re.compile(p, re.I) for p in (
    r"\b(?:ignore|disregard|override)\b[^.?!]{0,30}\b(?:previous|prior|above|earlier|all|your)\b[^.?!]{0,30}"
    r"\b(?:instructions?|rules?|polic(?:y|ies)|guidelines?)\b",
    r"\bdisregard\s+(?:everything|all)\b",
    r"\bforget\b[^.?!]{0,20}\b(?:polic(?:y|ies)|rules?|instructions?)\b",
    r"\bnew\s+instructions?\b",
    r"\b(?:no|without|any)\s+restrictions?\b",
    r"\boverrid(?:e|es|ing)\b",
    r"\bpretend\b",
    r"\bbypass\w*\b",
    r"\bapprove\b[^.?!]{0,20}\bCASE-\d+",
    r"\bi(?: am|'m) (?:the |an? )?(?:admin|administrator|root|superuser|sysadmin|developer)\b",
    r"\byou are now\b",
    r"\bsystem prompt\b",
    r"\bdeveloper mode\b",
    r"\bjailbreak\w*\b",
    r"\bdisable (?:your )?(?:rules|guardrails|safety|filters)\b",
    r"\bact as\b[^.?!]{0,30}\b(?:admin|root|unrestricted)\b",
    # round 2: chat-template / pseudo-system tags
    r"<\s*/?\s*(?:system|assistant|developer|instructions?|im_start|im_end)\b[^>]*>",
    r"<\|\s*(?:im_start|im_end|system)\s*\|>",
    r"\[\s*/?\s*(?:system|inst|instructions?)\s*\]",
    r"(?:^|\n)\s*#{2,}\s*(?:system|instructions?)\b",
    # round 2: key=value approval attempts (approved=true, status=closed, "approved": true)
    r"\b(?:approved|approve|auto_?approve|override|decision|status|state)\s*=\s*[\"']?(?:true|1|yes|closed|approved|done|complete\w*)\b",
    r"[\"'](?:approved|status|decision|override)[\"']\s*:\s*[\"']?(?:true|1|yes|closed|approved|done)\b",
    # round 2: "do not follow your rules", "reveal/show/translate your hidden prompt"
    r"\b(?:do not|don't|dont|never|stop)\s+(?:follow\w*|obey\w*|apply\w*|us(?:e|ing)|respect\w*)\s+(?:your|the|any|these)\s+"
    r"(?:rules|instructions|guidelines|polic\w+)\b",
    r"\b(?:reveal|show|print|display|translate|repeat|output|tell me|give me|leak)\b[^.?!]{0,25}\b(?:your|the)\s+"
    r"(?:system|hidden|initial|original|secret|internal)\s+(?:prompt|instructions?|message|rules)\b",
)]

# Round 2, recall over precision: a false CLINICAL only costs a human glance, a miss answers a medical question.
_SYMPTOMS = (r"pains?|aches?|aching|lumps?|bleed\w*|fever\w*|rash(?:es)?|swell\w*|swollen|dizz\w*|breathless\w*|short of breath|chest|"
             r"nausea\w*|vomit\w*|cough\w*|faint\w*|numb(?:ness|ing|ed)?|seizure\w*|cramp\w*|headache\w*|migraine\w*|infect\w*|wounds?|fractur\w*|"
             r"injur\w*|bruis\w*|itch\w*|sore throat|diarrh\w*|palpitation\w*|fatigue|tumou?rs?|cancer\w*|diabet\w*|asthma\w*|"
             r"hypertension|blood pressure|heart\w*|stroke|allerg\w*|pregnan\w*|depress\w*|anxiety|suicid\w*|symptoms?|diagnos\w*|"
             r"treatments?|surgery|surgical|overdos\w*|side effects?")
_DRUGS = (r"insulin|ibuprofen|aspirin|paracetamol|acetaminophen|warfarin|metformin|antibiotics?|amoxicillin|statins?|opioids?|"
          r"morphine|codeine|steroids?|antidepressants?|xanax|prozac|lisinopril|atorvastatin|omeprazole|tylenol|advil|"
          r"blood thinners?|painkillers?|antihistamines?|vitamins?")
_MEDS = r"medications?|meds?|medicines?|pills?|tablets?|capsules?|drugs?|doses?|dosage|dosing|prescribe\w*|" + _DRUGS
CLINICAL = re.compile(
    rf"\b(?:{_SYMPTOMS}|{_MEDS})\b|"
    r"(?<![A-Za-z])mg\b(?!\s+(?:road|rd|street|st|lane|avenue)\b)|"
    r"\bshould (?:i|he|she|they|we|the patient|my \w+)\b[^.?!]{0,30}\b(?:take|stop|skip|start|use|mix|combine|continue|increase|reduce|double)\b|"
    r"\bsafe (?:to|for) (?:mix|combine|take|stop|use|skip|continue|him|her|them|the patient)\b|"
    r"\bis it (?:okay|ok|fine|safe|alright) (?:to (?:take|stop|skip|mix|combine|use|drink|eat|give|continue|start|double)|"
    r"for (?:him|her|them|me|the patient|a patient|my \w+))\b|"
    rf"\b(?:alcohol|drink\w*|beer|wine|liquor)\b[^.?!]{{0,40}}\b(?:{_MEDS})\b|\b(?:{_MEDS})\b[^.?!]{{0,40}}\b(?:alcohol|drink\w*|beer|wine|liquor)\b|"
    r"\bkeep taking\b|\bstop taking\b|\bdouble .{0,20}dose", re.I)
SENSITIVE = re.compile(
    r"complain\w*|grievance|\blawyers?\b|\battorney\b|\bsolicitor\b|\blegal\b|\bcourt\b|\bsue\b|\bsuing\b|\blawsuit\b|ombudsman|"
    r"harass\w*|discriminat\w*|\bfraud\w*|privacy breach|\bdistress\w*|"
    r"\bregulators?\b|\breport (?:this|it|you|them) to\b|\bconsumer court\b|\bpress release\b|\bsocial media\b|\bthe (?:media|press)\b|"
    r"\b(?:to|contact|call|tell|alert|inform|involve|leak\w*)\s+(?:the\s+)?(?:media|press|newspapers?|journalists?|reporters?)\b", re.I)
LEGAL_WORDS = re.compile(r"\blawyers?\b|\blegal\b|\bcourt\b|\battorney\b|\bsolicitor\b|\bsue\b|\bsuing\b|\blawsuit\b|"
                         r"\bregulators?\b|\bombudsman\b", re.I)

_VERB = r"(?:show|tell|give|share|reveal|read|display|provide|send|list|look up|what(?:'s| is| are)|get)"
_ATTR = r"(?:phone|e-?mail|home address|address|dob|date of birth|birth ?date|details|balance|plan|amounts?|ssn)"
_SUBJ = (r"(?:member|patient|M[- ]?\d{5,12}|PA-[\d-]+|Dr\.?\s+[A-Z]\w+|(?:Ms|Mr|Mrs)\.?\s+[A-Z]\w+|CLM-\d+|INV-\d+|invoice|claim)")
ACCOUNT_REQUEST = re.compile(
    rf"\b{_VERB}\b[^.?!]{{0,70}}?\b{_SUBJ}[^.?!]{{0,70}}?\b{_ATTR}\b|"
    rf"\b{_VERB}\b[^.?!]{{0,70}}?\b{_ATTR}\b[^.?!]{{0,70}}?\b{_SUBJ}", re.I)
# no-verb form: "Sunita Sharma phone number and DOB please". Narrower attributes, and never a change request.
_ATTR_BARE = re.compile(r"\b(?:phone|e-?mail|dob|date of birth|birth ?date|ssn|home address|residential address|details|balance)\b", re.I)
_SUBJECT_TOKEN = re.compile(r"(?i:(?<!staff )(?<!team )\b(?:member|patient)\b)|\bM[- ]?\d{5,12}\b|\b(?:CLM|PA|INV)-\d+|(?i:\bNPI\b)\W{0,3}\d{5,12}|\b(?:Dr|Ms|Mr|Mrs)\.?\s+[A-Z]\w+")
_CHANGE_VERB = re.compile(r"\b(?:updat\w*|chang\w*|correct\w*|edit\w*|replac\w*|modif\w*|new|add|moved?|submit\w*)\b", re.I)
# a sentence that already contains the value (an email address, a long number, a date) is providing details, not asking
_PROVIDES_VALUE = re.compile(r"[^\s@]{1,64}@[^\s@]{1,64}|(?<![A-Za-z0-9-])\d{6,}|\d{4}-\d{2}-\d{2}")   # an M-id's digits are not a value
_NPI_DIGITS = re.compile(r"(?i)\bNPI\b\W{0,3}[\d -]{5,}")   # an NPI's digits identify the target, not a provided value
_SENTENCE_SPLIT = re.compile(r"(?<!\bDr)(?<!\bMs)(?<!\bMr)(?<!\bMrs)[.?!;\n]+")

# round 2: "What does the file say about Ms Rao's address?" - verbs that are also used in ordinary change requests
# ("please check and update the address for Dr. Rao"), so they only count when the sentence has no change verb
_WEAK_VERB = r"(?:what does|what do|say|says|check|view|pull up|look at|lookup)"
ACCOUNT_WEAK = re.compile(
    rf"\b{_WEAK_VERB}\b[^.?!]{{0,70}}?\b{_SUBJ}[^.?!]{{0,70}}?\b{_ATTR}\b|"
    rf"\b{_WEAK_VERB}\b[^.?!]{{0,70}}?\b{_ATTR}\b[^.?!]{{0,70}}?\b{_SUBJ}", re.I)
# something the request points at, after masking: a person/member/provider token, a masked id, or a claim/auth/invoice id
_TARGET = re.compile(r"\[(?:PERSON|MEMBER|PROVIDER)_\d+\]|\[MEMBER_ID\]|\[HEALTH_ID\]|\[NPI\]|\b(?:CLM|PA|INV)-\d+")
# a Health ID plus a personal attribute, or an ownership / identity question ("who owns CG-..."), is a request for that patient's record whatever the verb
_HEALTH_ID_ASKED = re.compile(
    r"(?i)\b(?:name|full name|phone|phone no\.?|number|contact|mobile|dob|birth|birthday|address|e-?mail|who|whose|which patient|owner|owns|owned|"
    r"details|identity|identify|ssn|plan)\b")

_STATUS_ID = re.compile(r"\b(?:CLM|PA)-\d+", re.I)
_PERSON_TOKEN = re.compile(r"\[(?:PERSON|MEMBER|PROVIDER)_\d+\]|\[MEMBER_ID\]|\[HEALTH_ID\]|\[NPI\]")
# attributes that are personal data about a member/provider (claim "details" and "amounts" are record content, not personal data)
_PERSONAL_ATTR = re.compile(r"\b(?:phone|e-?mail|home address|address|dob|date of birth|birth ?date|ssn|plan|name)\b", re.I)

_COUNT = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|half a|half|a couple of)"
_UNIT = (r"(?:mg|mcg|ml|milligrams?|micrograms?|grams?|millilit\w+|units?|tablets?|pills?|capsules?|doses?|drops?|"
         r"teaspoons?|tablespoons?|puffs?|patch(?:es)?|injections?|shots?)")
_ACT = r"(?:take|taking|gave|give|giving|administer\w*|inject\w*|prescribe\w*|increase|reduce|decrease|double|halve|stop|skip)"
_MEDICAL_ADVICE = re.compile(
    rf"\b{_ACT}\b[^.\n]{{0,40}}\b\d+(?:\.\d+)?\s?{_UNIT}\b|"
    rf"\b{_ACT}\b[^.\n]{{0,40}}\b{_COUNT}\s+{_UNIT}\b|"
    rf"\b{_ACT}\b[^.\n]{{0,25}}\b(?:\d+(?:\.\d+)?|{_COUNT})\s+(?:{_DRUGS})\b|"
    r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|ml|units?)\b[^.\n]{0,20}\b(?:daily|twice|per day|every)\b|"
    r"\byou should (?:take|stop|increase|reduce|double)\b", re.I)
_AMOUNT = re.compile(
    r"₹\s?[\d,]+(?:\.\d+)?|\b(?:Rs|INR)\.?\s?[\d,]+(?:\.\d+)?|\b[\d,]+(?:\.\d+)?\s?(?:rupees?|rs|inr)\b|\brupees?\s?[\d,]+", re.I)


def _hit(rx: re.Pattern[str], *views: str) -> bool:
    return any(rx.search(v) for v in views)


# ------------------------------------------------------------------ id validation (raw text; flags only)
def validate_ids(raw: str) -> dict[str, str]:
    raw = normalize_text(raw)
    out: dict[str, str] = {}
    npi_lengths = [len(re.sub(r"\D", "", m.group(2))) for m in NPI_CONTEXT.finditer(raw)]
    npi_lengths += [len(re.sub(r"\D", "", m.group(2))) for m in PROVIDER_NPI.finditer(raw)]
    if npi_lengths:
        bad = next((n for n in npi_lengths if n != 10), None)
        out["npi"] = "valid" if bad is None else f"invalid: {bad} digits"
    if m := re.search(r"(?<![\w-])M[- ]?(\d{4,})(?!\w)", raw):
        out["member_id"] = "valid" if len(m.group(1)) == 8 else f"invalid: {len(m.group(1))} digits after M (expected 8)"
    if (hid := health_id.validate(raw)) is not None:
        out["health_id"] = hid
    if m := re.search(r"\bPA-[\d-]+", raw):
        out["auth_id"] = "valid" if re.fullmatch(r"PA-\d{4}-\d{5}", m.group(0)) else "invalid: expected PA-YYYY-NNNNN"
    if m := re.search(r"\bCLM-\d+", raw):
        out["claim_id"] = "valid" if re.fullmatch(r"CLM-\d{8}", m.group(0)) else "invalid: expected CLM- and 8 digits"
    no_dob = DOB.sub("", raw)
    if d := find_effective_date(no_dob):
        try:
            date.fromisoformat(d)
            out["effective_date"] = "valid"
        except ValueError:
            out["effective_date"] = "invalid: not a real date"
    if EMAIL.search(raw):
        out["user_email"] = "valid"
    elif re.search(r"[^\s@]{1,64}@[^\s@]{1,64}", raw):
        out["user_email"] = "invalid: malformed email"
    if m := re.search(r"(?<![\w-])E(\d{3,6})(?!\w)", raw):
        out["equipment_code"] = "valid" if len(m.group(1)) == 4 else "invalid: expected E and 4 digits"
    if (cost := find_cost(raw)) is not None:
        out["estimated_cost_inr"] = "valid" if cost.isdigit() and int(cost) > 0 else "invalid: must be a positive number"
    return out


def _weak_account_request(*views: str) -> bool:
    return any(ACCOUNT_WEAK.search(s) and not _CHANGE_VERB.search(s)
               for v in views for s in _SENTENCE_SPLIT.split(v))


def _bare_account_request(*views: str) -> bool:
    pool = first_names()
    for view in views:
        for sentence in _SENTENCE_SPLIT.split(view):
            if not _ATTR_BARE.search(sentence) or _CHANGE_VERB.search(sentence) or _PROVIDES_VALUE.search(_NPI_DIGITS.sub("NPI", sentence)):
                continue
            if _SUBJECT_TOKEN.search(sentence):
                return True
            if any(w.lower() in pool and w.lower() not in STOP_STATIC and w.lower() not in AMBIGUOUS
                   for w in CAP_WORD.findall(sentence)):
                return True
    return False


# ------------------------------------------------------------------ input guard
def check_input(text: str, user: User, gazetteer: Gazetteer | None = None) -> GuardResult:
    overrides: list[ReasonCode] = []
    notes: list[str] = []
    allowed = True

    def add(code: ReasonCode) -> None:
        if code not in overrides:
            overrides.append(code)

    too_long = len(text) > MAX_INPUT_CHARS
    if too_long:
        allowed = False
        add(ReasonCode.UNCLEAR_INTENT)
        notes.append(f"input too long (max {MAX_INPUT_CHARS} chars)")
        text = text[:MAX_INPUT_CHARS]

    norm = iso_effective_dates(normalize_text(text))
    coll = collapse_letters(norm)
    validated = validate_ids(norm)

    leet = fold_leet(norm)                                  # detection-only copy: 1gn0re -> ignore
    leet_coll = collapse_letters(leet)
    injection = any(_hit(p, norm, coll, leet, leet_coll) for p in INJECTION_PATTERNS)
    if injection:
        allowed = False
        add(ReasonCode.ACCESS_DENIED)
        add(ReasonCode.SENSITIVE)
        notes.append("prompt-injection pattern detected")
    masked, pii_types = anonymize(norm, gazetteer)
    # a request for someone's details only counts when there is a TARGET in the masked text;
    # generic process questions ("what is the process to change a member's email?") stay allowed
    health_asked = "[HEALTH_ID]" in masked and bool(_HEALTH_ID_ASKED.search(masked))      # recall over precision: the word may be anywhere
    if ((_hit(ACCOUNT_REQUEST, norm, coll) or _weak_account_request(norm, coll) or _bare_account_request(norm, coll)) and _TARGET.search(masked)) \
            or health_asked:
        add(ReasonCode.ACCOUNT_SPECIFIC)
        notes.append("request for a specific record's personal details")
        # ACCESS_DENIED is for DISCLOSING personal data beyond the role. A status inquiry on a claim / authorization id (no person
        # attribute asked for, no person named) is ACCOUNT_SPECIFIC only: it goes to the owning team for identity verification.
        status_inquiry = bool(_STATUS_ID.search(masked)) and not _PERSON_TOKEN.search(masked) and not _PERSONAL_ATTR.search(norm)
        if user.role in NO_RECORD_ACCESS and not status_inquiry:
            allowed = False
            add(ReasonCode.ACCESS_DENIED)
            notes.append(f"role {user.role.value} may not view other people's records")
    if _hit(CLINICAL, norm, coll):
        add(ReasonCode.CLINICAL)
        notes.append("clinical question: not answered, routed to Clinical Review")
    if _hit(SENSITIVE, norm, coll):
        add(ReasonCode.SENSITIVE)
        notes.append("sensitive content (complaint/legal/privacy)")

    if too_long:
        masked += " [truncated]"
    return GuardResult(allowed=allowed, masked_text=masked, pii_types_found=pii_types, overrides=overrides,
                       injection=injection, validated_fields=validated, notes=notes,
                       health_id_masked=health_id.masked_ids(norm),
                       patient_key=health_id.linked_profile(norm, config.DATA_DIR) if allowed else None)


# ------------------------------------------------------------------ output guard
def check_output(text: str, user: User) -> tuple[bool, str, list[str]]:
    """-> (ok, cleaned, issues). ok is False when anything had to be changed."""
    issues: list[str] = []
    norm = normalize_text(text)
    # staff first names in our own generated text are legitimate, so the cue-less name pass is off for output
    cleaned, _ = anonymize(norm, None, cueless=False)
    if cleaned != norm:
        issues.append("pii_in_output")
    if _hit(_MEDICAL_ADVICE, cleaned, collapse_letters(cleaned)):
        issues.append("medical_advice_blocked")
        cleaned = CLINICAL_REFUSAL
    if user.role in NO_BILLING_VIEW and _AMOUNT.search(cleaned):
        issues.append("amount_redacted")
        cleaned = _AMOUNT.sub("[amount hidden]", cleaned)
    return (not issues), cleaned, issues
