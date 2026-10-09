"""The requester adds the missing details (CONTRACTS section 8).

On a NEEDS_INFO case the requester (or an ops manager) sends exactly the fields the rules asked for. Every value goes through the SAME guards as a
new request, on the raw text: validated, masked, never stored raw. The accepted fields are merged into the classification, and the rules, scoring
and routing run again, so the case moves on (or asks only for what is still missing). Audit `details_added` holds field names only.
"""
from __future__ import annotations

import re
from datetime import datetime

from caregrid.knowledge.brain import Brain
from caregrid.llm import LLM
from caregrid.models import Case, GuardResult, Role, State, User
from caregrid.reasoning import pipeline
from caregrid.reasoning.extract import extract_fields
from caregrid.reasoning.guards import check_input
from caregrid.reasoning.propose import FORMAT_HINT
from caregrid.reasoning.rules import FIELD_ALIAS, label
from caregrid.store import Store
from caregrid.workflow.audit import log

DOCUMENT_CHOICES = ["W-9", "bank_letter", "licence_copy"]
MAX_VALUE_CHARS = 200
# a phrase the existing extractors understand, one per field (the value is the only thing the requester typed)
PHRASE = {
    "npi": "NPI {v}", "provider_npi": "NPI {v}", "member_id": "member {v}", "auth_id": "authorization {v}", "claim_id": "claim {v}",
    "effective_date": "effective {v}", "user_email": "portal user {v}", "equipment_code": "equipment {v}", "estimated_cost_inr": "estimated cost ₹{v}",
    "supporting_document": "{v} attached", "health_id": "patient {v}",
}
_NAME = re.compile(r"^[^\W\d_][^\W\d_ .'\-]*(?:[ .'\-]+[^\W\d_][^\W\d_ .'\-]*){0,5}$")


class NotAwaitingDetailsError(ValueError):
    """The case is not waiting for details (already complete, decided or withdrawn)."""


def can_add(user: User, case: Case) -> bool:
    return user.id == case.requester.id or user.role == Role.OPS_MANAGER


def form_for(case: Case) -> list[dict]:
    """The form: exactly the missing and invalid fields, each with a label, a format hint and (for the document) the allowed values."""
    rules = case.rules
    if rules is None:
        return []
    out = []
    for f in [*rules.missing_fields, *rules.invalid_fields]:
        item = {"field": f, "label": label(f), "hint": FORMAT_HINT.get(f, "see the form"), "kind": "text", "invalid": f in rules.invalid_fields,
                "problem": rules.invalid_fields.get(f)}
        if f == "supporting_document":
            item.update(kind="select", options=DOCUMENT_CHOICES)
        elif f == "prescription_on_file":
            item.update(kind="select", options=["yes", "no"])
        out.append(item)
    return out


def _free_text(field: str, value: str) -> str | None:
    """Names and addresses: accepted when they look like one, stored only as a masked token."""
    from caregrid.ingest.anonymize import anonymize

    if field == "new_address":
        return "[ADDRESS]" if "[ADDRESS]" in anonymize(value)[0] else None
    return "[PERSON]" if _NAME.match(value) and len(re.findall(r"[^\W\d_]", value)) >= 2 else None


def add_details(case_id: str, user: User, values: dict[str, str], store: Store, brain: Brain, llm: LLM) -> Case:
    case = store.get_case(case_id)
    if case is None:
        raise KeyError(case_id)
    if not can_add(user, case):
        log(store, "review_denied", user, case.id, action="add_details", role=user.role.value, reason="only the requester (or an ops manager) adds details",
            state=case.state.value)
        raise PermissionError(f"{user.role.value} may not add details to {case.id}")
    if case.state != State.NEEDS_INFO or case.rules is None or case.classification is None:
        raise NotAwaitingDetailsError(f"{case.id} is {case.state.value}; it is not waiting for details")
    wanted = {*case.rules.missing_fields, *case.rules.invalid_fields}
    given = {k: str(v).strip() for k, v in (values or {}).items() if str(v or "").strip()}
    if not given:
        raise ValueError("Please fill in at least one detail.")
    if set(given) - wanted:
        raise ValueError("Only the details that were asked for can be added: " + ", ".join(sorted(label(f) for f in wanted)) + ".")
    if any(len(v) > MAX_VALUE_CHARS for v in given.values()):
        raise ValueError("A detail is too long.")
    if "supporting_document" in given and given["supporting_document"] not in DOCUMENT_CHOICES:
        raise ValueError("The supporting document must be one of: " + ", ".join(DOCUMENT_CHOICES) + ".")
    calls_before = (len(pipeline._answered(llm)), len(pipeline._failed(llm)))

    phrases, manual = [], {}
    for f, v in given.items():
        if f in PHRASE:
            phrases.append(PHRASE[f].format(v=v))
        elif f in ("new_address", "old_name", "new_name"):
            token = _free_text(f, v)
            if token:
                manual[f] = token
        elif f == "prescription_on_file" and v in ("yes", "no"):
            manual[f] = v if v == "yes" else ""
    g = check_input(". ".join(phrases) if phrases else "-", user)                   # the RAW values are read here and nowhere else
    if phrases and (not g.allowed or g.overrides or g.injection):
        raise ValueError("That text can't be accepted as a detail. Please send only the value that was asked for.")
    found = extract_fields(g.masked_text) if phrases else {}
    new_fields = {k: v for k, v in {**found, **manual}.items() if k in wanted or FIELD_ALIAS.get(k) in wanted}
    for k in list(new_fields):                                                       # npi / provider_npi are one thing
        if FIELD_ALIAS.get(k) and FIELD_ALIAS[k] in wanted:
            new_fields.setdefault(FIELD_ALIAS[k], new_fields[k])
    cls = case.classification
    fields = {k: v for k, v in cls.extracted_fields.items() if k not in wanted and FIELD_ALIAS.get(k) not in wanted}
    fields.update(new_fields)
    cls = cls.model_copy(update={"extracted_fields": fields})
    case.classification = cls
    if phrases:
        case.masked_text = (case.masked_text + " | " + g.masked_text)[:6000]          # masked values only
    if g.patient_key:
        case.related = {**case.related, "profile": [g.patient_key]}
        log(store, "patient_linked", user, case.id)
    log(store, "details_added", user, case.id, fields=sorted(given), on_behalf=user.id != case.requester.id)
    guard = GuardResult(allowed=True, masked_text=case.masked_text, pii_types_found=g.pii_types_found, validated_fields=g.validated_fields)
    previous_tiers = list(case.llm_tiers_used)
    case.rules.notes = [n for n in case.rules.notes if not n.startswith("llm_")]
    done = pipeline.finish(case, cls, guard, None, store, brain, llm, calls_before)
    done.llm_tiers_used = list(dict.fromkeys([*previous_tiers, *done.llm_tiers_used]))
    store.save_case(done)
    return done
