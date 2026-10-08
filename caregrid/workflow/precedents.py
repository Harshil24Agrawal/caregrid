"""Precedent capture (CLAUDE.md rule 7): only a HUMAN-APPROVED decision becomes a precedent, never raw AI output."""
from __future__ import annotations

import uuid

from caregrid import config
from caregrid.ingest.anonymize import anonymize
from caregrid.knowledge.brain import Brain
from caregrid.knowledge.retrieve import derive_case_facts
from caregrid.models import Case, PageStatus, PageType, Precedent, ReviewAction, ReviewDecision

CAPTURABLE = {ReviewAction.APPROVE, ReviewAction.EDIT_APPROVE}
_ALIASES = {"npi": "provider_npi", "provider_npi": "npi"}


def capture_precedent(case: Case, d: ReviewDecision, brain: Brain) -> Precedent:
    """Writes the precedent to the Brain (memory AND disk, so a cached Brain sees it on the very next request) and returns it."""
    if d.action not in CAPTURABLE:
        raise ValueError(f"only approved decisions become precedents, not {d.action.value}")
    if case.classification is None or case.rules is None or case.proposal is None:
        raise ValueError("case is incomplete: nothing to learn from")

    cls, prop = case.classification, case.proposal
    wf = brain.workflow_for(cls.request_type)

    # the same facts retrieval derives for a new request, so the next similar request matches this precedent
    policy_cites = [c for c in prop.citations if c.page_type == PageType.POLICY]
    topic = None
    if cls.request_type == "general_policy_question":
        topic = policy_cites[0].page_id if policy_cites else "none"
    facts = derive_case_facts(wf, cls, topic)

    # top cited POLICY citation at its CURRENT version
    policy = brain.get(policy_cites[0].page_id) if policy_cites else None
    required = set(wf.meta.get("required_fields", [])) if wf else set()
    have = {k for k, v in cls.extracted_fields.items() if v}
    provided = sorted(f for f in required if f in have or _ALIASES.get(f) in have)

    note = anonymize(d.note, cueless=True)[0].strip() if d.note else ""
    verb = "Approved" if d.action == ReviewAction.APPROVE else "Edited and approved"
    prec = Precedent(
        id="P-" + uuid.uuid4().hex[:6], request_type=cls.request_type, facts=facts, fields_provided=provided,
        summary=case.masked_text, decision_code=prop.decision_code, route_team=prop.route_team,
        policy_id=policy.id if policy else None, policy_version=policy.version if policy else None,
        reason_codes=list(case.reason_codes), approver_role=d.reviewer.role, risk=case.rules.risk,
        status=PageStatus.ACTIVE, date=config.TODAY, outcome=f"{verb} by {d.reviewer.role.value}." + (f" {note}" if note else ""),
        source_case_id=case.id)
    brain.write_precedent(prec)
    return prec
