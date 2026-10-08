"""Request classification (PROMPTS §1): light-tier LLM on MASKED text, validated and merged with deterministic code.

Code owns structured ids; the LLM may only ADD free-text fields (address / names) and only with values that literally
occur in the masked request. Guard flags are OR-ed in by the pipeline, so the LLM can never clear them.
"""
from __future__ import annotations

from caregrid.constants import REQUEST_TYPES
from caregrid.llm import LLM, complete_json_tiered, model_name
from caregrid.models import Classification
from caregrid.reasoning.extract import extract_fields, keyword_type
from caregrid.reasoning.guards import CLINICAL, SENSITIVE
from caregrid.reasoning.prompts import CLASSIFIER_SYSTEM

FREE_TEXT_FIELDS = ("new_address", "old_name", "new_name")
FALLBACK_MODEL = "fallback:deterministic"


def _clamp01(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _flag(value) -> bool:
    return value is True


def deterministic_classification(masked_text: str) -> Classification:
    rtype, hit = keyword_type(masked_text)
    return Classification(
        request_type=rtype, llm_confidence=0.9 if hit else 0.3, rules_type=rtype if hit else None,
        extracted_fields=extract_fields(masked_text),
        sentiment="negative" if rtype == "complaint_grievance" else "neutral",
        is_clinical=bool(CLINICAL.search(masked_text)), is_sensitive=bool(SENSITIVE.search(masked_text)),
        is_account_specific=rtype in {"prior_auth_status", "claim_status_inquiry"}, model_used=FALLBACK_MODEL)


def classify(masked_text: str, llm: LLM) -> Classification:
    kw_type, hit = keyword_type(masked_text)
    code_fields = extract_fields(masked_text)
    try:
        raw, _ = complete_json_tiered(llm, CLASSIFIER_SYSTEM, f"REQUEST:\n{masked_text}", "light")
        if not isinstance(raw, dict):
            raise ValueError("classifier did not return a JSON object")
    except Exception:  # timeout, provider error, unparseable output after the provider's own retry
        return deterministic_classification(masked_text)

    rtype = raw.get("request_type")
    if rtype not in REQUEST_TYPES:
        rtype = "unknown"
    if rtype == "unknown" and hit:
        rtype = kw_type                              # the LLM gave up but a keyword rule is certain

    fields = dict(code_fields)                       # code-extracted values always win
    llm_fields = raw.get("extracted_fields")
    if isinstance(llm_fields, dict):
        for key in FREE_TEXT_FIELDS:
            val = llm_fields.get(key)
            if key not in fields and isinstance(val, str) and val.strip() and val.strip() in masked_text:
                fields[key] = val.strip()

    urgency = raw.get("urgency") if raw.get("urgency") in ("low", "normal", "high") else "normal"
    sentiment = raw.get("sentiment") if raw.get("sentiment") in ("negative", "neutral", "positive") else "neutral"
    return Classification(
        request_type=rtype, llm_confidence=_clamp01(raw.get("confidence")), rules_type=kw_type if hit else None,
        extracted_fields=fields, urgency=urgency, sentiment=sentiment, is_clinical=_flag(raw.get("is_clinical")),
        is_account_specific=_flag(raw.get("is_account_specific")), is_sensitive=_flag(raw.get("is_sensitive")),
        model_used=model_name(llm, "light"))
