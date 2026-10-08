"""Input and output guards (CONTRACTS §3-4, CLAUDE.md rules 1-6).

check_input runs on RAW text: it validates structured ids, detects injection / clinical / account-specific / sensitive
requests, then masks. Only flags and masked text leave this module; raw values are never returned or logged.
"""
from __future__ import annotations

import re
from datetime import date

from caregrid.ingest.anonymize import DOB, EMAIL, MEMBER_ID, NPI_CONTEXT, Gazetteer, anonymize
from caregrid.models import GuardResult, ReasonCode, Role, User
from caregrid.reasoning.extract import find_cost, find_effective_date

CLINICAL_REFUSAL = ("I can't give medical advice. This question has been routed to Clinical Review.")
# Roles that must never read other people's records through the assistant (RBAC before retrieval).
NO_RECORD_ACCESS = {Role.OPS_EMPLOYEE, Role.AUDITOR, Role.KNOWLEDGE_OWNER}
# Roles that may not see billing amounts.
NO_BILLING_VIEW = {Role.OPS_EMPLOYEE, Role.AUDITOR, Role.KNOWLEDGE_OWNER}

INJECTION_PATTERNS = [re.compile(p, re.I) for p in (
    r"\b(?:ignore|disregard|override)\b[^.?!]{0,30}\b(?:previous|prior|above|earlier|all|your)\b[^.?!]{0,30}"
    r"\b(?:instructions?|rules?|polic(?:y|ies)|guidelines?)\b",
    r"\bforget\b[^.?!]{0,20}\b(?:polic(?:y|ies)|rules?|instructions?)\b",
    r"\bi(?: am|'m) (?:the |an? )?(?:admin|administrator|root|superuser|sysadmin|developer)\b",
    r"\byou are now\b",
    r"\bsystem prompt\b",
    r"\bdeveloper mode\b",
    r"\bjailbreak\b",
    r"\bdisable (?:your )?(?:rules|guardrails|safety|filters)\b",
    r"\bpretend (?:to be|you are)\b",
    r"\bact as\b[^.?!]{0,30}\b(?:admin|root|unrestricted)\b",
)]

CLINICAL = re.compile(
    r"\b(?:dose|doses|dosage|medications?|symptoms?|diagnos\w*|treatments?|prescribe\w*|insulin|ibuprofen|blood thinners?|"
    r"chest pain|surgery|fever|rash)\b|should .{0,40} take|safe to (?:stop|take)|double .{0,20}dose", re.I)
SENSITIVE = re.compile(
    r"complain\w*|grievance|\blawyers?\b|\battorney\b|\blegal\b|\bcourt\b|discriminat\w*|\bfraud\w*|privacy breach|\bdistress\w*",
    re.I)
LEGAL_WORDS = re.compile(r"\blawyers?\b|\blegal\b|\bcourt\b|\battorney\b|\bsue\b|\blawsuit\b", re.I)

_VERB = r"(?:show|tell|give|share|reveal|read|display|provide|send|list|look up|what(?:'s| is| are)|get)"
_ATTR = r"(?:phone|e-?mail|home address|address|dob|date of birth|birth ?date|details|balance|plan|amounts?|ssn)"
_SUBJ = (r"(?:member|patient|M\d{5,12}|Dr\.?\s+[A-Z]\w+|(?:Ms|Mr|Mrs)\.?\s+[A-Z]\w+|CLM-\d+|INV-\d+|invoice|claim)")
ACCOUNT_REQUEST = re.compile(
    rf"\b{_VERB}\b[^.?!]{{0,70}}?\b{_SUBJ}[^.?!]{{0,70}}?\b{_ATTR}\b|"
    rf"\b{_VERB}\b[^.?!]{{0,70}}?\b{_ATTR}\b[^.?!]{{0,70}}?\b{_SUBJ}", re.I)

_MEDICAL_ADVICE = re.compile(
    r"\b(?:take|taking|increase|reduce|decrease|double|halve|stop|skip)\b[^.\n]{0,40}\b\d+(?:\.\d+)?\s?(?:mg|mcg|ml|units?|tablets?|pills?|doses?)\b|"
    r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|ml|units?)\b[^.\n]{0,20}\b(?:daily|twice|per day|every)\b|"
    r"\byou should (?:take|stop|increase|reduce|double)\b", re.I)
_AMOUNT = re.compile(r"₹\s?[\d,]+(?:\.\d+)?|\bRs\.?\s?[\d,]+", re.I)


# ------------------------------------------------------------------ id validation (raw text; flags only)
def validate_ids(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if m := NPI_CONTEXT.search(raw):
        n = len(m.group(2))
        out["npi"] = "valid" if n == 10 else f"invalid: {n} digits"
    if m := re.search(r"(?<![\w-])M(\d{4,})(?!\w)", raw):
        out["member_id"] = "valid" if len(m.group(1)) == 8 else f"invalid: {len(m.group(1))} digits after M (expected 8)"
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
    elif re.search(r"\S+@\S+", raw):
        out["user_email"] = "invalid: malformed email"
    if m := re.search(r"(?<![\w-])E(\d{3,6})(?!\w)", raw):
        out["equipment_code"] = "valid" if len(m.group(1)) == 4 else "invalid: expected E and 4 digits"
    if (cost := find_cost(raw)) is not None:
        out["estimated_cost_inr"] = "valid" if cost.isdigit() and int(cost) > 0 else "invalid: must be a positive number"
    return out


# ------------------------------------------------------------------ input guard
def check_input(text: str, user: User, gazetteer: Gazetteer | None = None) -> GuardResult:
    validated = validate_ids(text)
    overrides: list[ReasonCode] = []
    notes: list[str] = []
    allowed = True

    def add(code: ReasonCode) -> None:
        if code not in overrides:
            overrides.append(code)

    injection = any(p.search(text) for p in INJECTION_PATTERNS)
    if injection:
        allowed = False
        add(ReasonCode.ACCESS_DENIED)
        add(ReasonCode.SENSITIVE)
        notes.append("prompt-injection pattern detected")
    if ACCOUNT_REQUEST.search(text):
        add(ReasonCode.ACCOUNT_SPECIFIC)
        notes.append("request for a specific record's personal details")
        if user.role in NO_RECORD_ACCESS:
            allowed = False
            add(ReasonCode.ACCESS_DENIED)
            notes.append(f"role {user.role.value} may not view other people's records")
    if CLINICAL.search(text):
        add(ReasonCode.CLINICAL)
        notes.append("clinical question: not answered, routed to Clinical Review")
    if SENSITIVE.search(text):
        add(ReasonCode.SENSITIVE)
        notes.append("sensitive content (complaint/legal/privacy)")

    masked, pii_types = anonymize(text, gazetteer)
    return GuardResult(allowed=allowed, masked_text=masked, pii_types_found=pii_types, overrides=overrides,
                       injection=injection, validated_fields=validated, notes=notes)


# ------------------------------------------------------------------ output guard
def check_output(text: str, user: User) -> tuple[bool, str, list[str]]:
    """-> (ok, cleaned, issues). ok is False when anything had to be changed."""
    issues: list[str] = []
    # staff first names in our own generated text are legitimate, so the cue-less name pass is off for output
    cleaned, _ = anonymize(text, None, cueless=False)
    if cleaned != text:
        issues.append("pii_in_output")
    if _MEDICAL_ADVICE.search(cleaned):
        issues.append("medical_advice_blocked")
        cleaned = CLINICAL_REFUSAL
    if user.role in NO_BILLING_VIEW and _AMOUNT.search(cleaned):
        issues.append("amount_redacted")
        cleaned = _AMOUNT.sub("[amount hidden]", cleaned)
    return (not issues), cleaned, issues
