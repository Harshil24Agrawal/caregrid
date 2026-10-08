"""Deterministic request-type keywords and field extraction (PROMPTS §6).

Works on MASKED text (tokens like [NPI], [MEMBER_ID], [EMAIL], [ADDRESS], [PERSON_1]). Code-extracted values always win
for structured ids; an LLM may only ADD fields code did not find (see `merge_fields`).
"""
from __future__ import annotations

import re

# Order matters: first hit wins. Complaint words outrank "claim" so "claim denials" + "lawyer" is a complaint.
TYPE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("provider_address_change", re.compile(r"address", re.I)),
    ("provider_name_change", re.compile(r"name change|changed (?:her |his |their |the )?(?:last )?name|\brename|legally changed", re.I)),
    ("portal_access_reset", re.compile(r"password|log ?in\b|can't log|portal access|locked", re.I)),
    ("prior_auth_status", re.compile(r"prior auth|authori[sz]ation status", re.I)),
    ("dme_equipment_request", re.compile(r"wheelchair|oxygen|equipment|\bDME\b|\bCPAP\b|\bE\d{4}\b", re.I)),
    ("complaint_grievance", re.compile(r"complain|grievance|lawyer|unacceptable", re.I)),
    ("claim_status_inquiry", re.compile(r"\bclaims?\b|\bCLM-", re.I)),
    ("general_policy_question", re.compile(
        r"what documents|which documents|supporting documents|how do i|polic(?:y|ies)|where can i find|\bforms?\b|\bSLAs?\b|"
        r"turnaround|escalation|what is the process|what are the", re.I)),
]


_INFO_QUESTION = re.compile(
    r"(?i)^\s*(?:what(?:'s| is| are| does| do)?|how|where|when|who|which|why|explain|can you (?:explain|tell me)|could you (?:explain|tell me)|"
    r"is there|are there|do we have|do you have)\b")
_QUESTION_FILLER = frozenset("what whats where when which there have does this that with from about your would could should please".split())


def _is_info_question(text: str) -> bool:
    """A clear question with some content ("What's on the cafeteria menu?") is a general question, so a missing policy is a
    POLICY GAP. Vague input ("help", "Can you look at this?") is not. Clinical/sensitive wording is never a general question."""
    if "?" not in text or not _INFO_QUESTION.search(text):
        return False
    content = [w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _QUESTION_FILLER]
    if len(content) < 2:
        return False
    from caregrid.reasoning.guards import CLINICAL, SENSITIVE      # lazy: guards imports this module

    return not (CLINICAL.search(text) or SENSITIVE.search(text))


def keyword_type(masked_text: str) -> tuple[str, bool]:
    """-> (request_type | "unknown", hit)"""
    for rtype, rx in TYPE_RULES:
        if rx.search(masked_text):
            return rtype, True
    if _is_info_question(masked_text):
        return "general_policy_question", True
    return "unknown", False


_DATE = r"\d{4}-\d{2}-\d{2}"
_EFFECTIVE = re.compile(r"effective(?:\s+date)?(?:\s+(?:of|on|from|is))?[\s:]*(" + _DATE + ")", re.I)
_ANY_DATE = re.compile(r"(?<![\w-])(" + _DATE + r")(?![\w-])")
_COST = [
    re.compile(r"₹\s*([\d,]+)(?:\.\d+)?"),
    re.compile(r"\b(?:rs\.?|inr)\s*([\d,]+)(?:\.\d+)?", re.I),
    re.compile(r"\bcost(?:\s+(?:is|of|about|around))?\s*(?:₹|rs\.?)?\s*([\d,]{3,})", re.I),
]
_PERSON_TOKEN = r"(\[(?:PERSON|PROVIDER|MEMBER)_\d+\])"
_NAME_PAIR = re.compile(r"\bfrom\s+" + _PERSON_TOKEN + r"\s+to\s+" + _PERSON_TOKEN, re.I)


_NEW_NAME_ONLY = re.compile(r"\bname\b[^.\n\[]{0,25}?\bto\s+" + _PERSON_TOKEN, re.I)
_NO_PRESCRIPTION = re.compile(r"\b(?:no|without|missing)\s+(?:a\s+)?prescription|prescription\s+(?:is\s+)?(?:missing|not)", re.I)


def find_effective_date(text: str) -> str | None:
    """Date after the word 'effective', else the first YYYY-MM-DD in the text. Shape only; validity is checked elsewhere."""
    m = _EFFECTIVE.search(text) or _ANY_DATE.search(text)
    return m.group(1) if m else None


def find_cost(text: str) -> str | None:
    for rx in _COST:
        m = rx.search(text)
        if m:
            digits = m.group(1).replace(",", "").strip()
            if digits:
                return digits
    return None


def extract_fields(masked_text: str) -> dict[str, str]:
    t, out = masked_text, {}
    if "[NPI]" in t:
        out["npi"] = out["provider_npi"] = "[NPI]"
    if "[MEMBER_ID]" in t:
        out["member_id"] = "[MEMBER_ID]"
    if m := re.search(r"\bPA-\d{4}-\d{5}\b", t):
        out["auth_id"] = m.group(0)
    if m := re.search(r"\bCLM-\d{8}\b", t):
        out["claim_id"] = m.group(0)
    if d := find_effective_date(t):
        out["effective_date"] = d
    if "[EMAIL]" in t:
        out["user_email"] = "[EMAIL]"
    if m := re.search(r"(?<![\w-])E\d{4}(?!\w)", t):
        out["equipment_code"] = m.group(0)
    if c := find_cost(t):
        out["estimated_cost_inr"] = c
    if "[ADDRESS]" in t:
        out["new_address"] = "[ADDRESS]"
    if m := _NAME_PAIR.search(t):
        out["old_name"], out["new_name"] = m.group(1), m.group(2)
    elif m := _NEW_NAME_ONLY.search(t):
        out["new_name"] = m.group(1)
    if re.search(r"\bW-?9\b", t, re.I):
        out["supporting_document"] = "W-9"
    elif re.search(r"bank[ _]letter", t, re.I):
        out["supporting_document"] = "bank_letter"
    elif re.search(r"licen[sc]e[ _]copy", t, re.I):
        out["supporting_document"] = "licence_copy"
    if re.search(r"prescription (?:is )?(?:on file|attached)", t, re.I) and not _NO_PRESCRIPTION.search(t):
        out["prescription_on_file"] = "yes"
    return out


def merge_fields(code_fields: dict[str, str], llm_fields: dict[str, str]) -> dict[str, str]:
    """Code-extracted values always win; the LLM may only add fields code did not find (and only non-empty ones)."""
    merged = dict(code_fields)
    for k, v in llm_fields.items():
        if v and not merged.get(k):
            merged[k] = v
    return merged
