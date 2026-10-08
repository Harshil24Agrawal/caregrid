"""Plain-language explanations of a case, built by CODE from the stored case and the Second Brain: the case summary card, the
one-line summary for lists, the "How this is handled" stepper and the "Why this decision" provenance (see provenance below).

Nothing here calls a model and nothing here invents a number: every figure and id comes from the case, the rules result or a page.
The API runs every returned string through check_output for the viewer.
"""
from __future__ import annotations

import re

from caregrid.knowledge.brain import Brain
from caregrid.ingest.pagefmt import page_relpath
from caregrid.models import Case, DecisionCode, Page, PageType, Role, State, User
from caregrid.rbac import can_view
from caregrid.reasoning.rules import label

TYPE_LABEL = {
    "general_policy_question": "a policy question", "provider_address_change": "a provider address change",
    "provider_name_change": "a provider name change", "portal_access_reset": "a portal access reset",
    "prior_auth_status": "a prior authorization status check", "dme_equipment_request": "an equipment (DME) request",
    "claim_status_inquiry": "a claim status check", "complaint_grievance": "a complaint", "unknown": "a request we could not classify",
}
ROLE_LABEL = {Role.OPS_EMPLOYEE: "ops employee", Role.TEAM_SPECIALIST: "team specialist", Role.OPS_MANAGER: "ops manager",
              Role.SENIOR_REVIEWER: "senior reviewer", Role.KNOWLEDGE_OWNER: "knowledge owner", Role.AUDITOR: "auditor"}
REASON_PLAIN = {
    "CLINICAL": "it is a medical question", "ACCOUNT_SPECIFIC": "it concerns a specific account", "SENSITIVE": "it is sensitive",
    "IRREVERSIBLE_ACTION": "it cannot be undone", "ACCESS_DENIED": "it asks for information the requester may not see",
    "POLICY_CONFLICT": "the policies disagree", "POLICY_GAP": "no approved policy covers it", "HIGH_RISK": "the risk is high",
    "LOW_CONFIDENCE": "the evidence is weak", "UNCLEAR_INTENT": "the request is unclear", "MISSING_DATA": "details are missing",
}
_MAX_ONE_LINE = 220


def _team_name(brain: Brain, team_id: str | None) -> str:
    page = brain.team(team_id) if team_id else None
    return page.title if page else (team_id or "the right team")


def _words(items: list[str]) -> str:
    return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else (items[0] if items else "")


def one_liner(case: Case) -> str:
    """What was asked, on one line (masked text, whitespace collapsed). The UI truncates it and shows the whole line in a tooltip."""
    text = " ".join((case.masked_text or "").split())
    return text if len(text) <= _MAX_ONE_LINE else text[:_MAX_ONE_LINE - 1].rstrip() + "…"


def _llm_summary(case: Case) -> str | None:
    """The reviewer summary the proposer wrote, only when a model (not the deterministic template) wrote it; clipped to 3 sentences."""
    prop = case.proposal
    if prop is None or prop.model_used.startswith("mock") or prop.model_used in ("deterministic", "seed", "") or not prop.summary_for_reviewer.strip():
        return None
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", " ".join(prop.summary_for_reviewer.split())) if s]
    return " ".join(sentences[:3]) if len(sentences) >= 2 else None       # one thin sentence is not a summary: use the template


def case_summary(case: Case, brain: Brain, viewer: User) -> str:
    """2-3 plain sentences: what was asked, what was checked, what happens next. A viewer who can see the whole case gets the model's
    reviewer summary when a model wrote one; everyone else (and the deterministic path) gets this template."""
    if can_view(viewer, case, "full") and (written := _llm_summary(case)):
        return written
    cls, rules, prop = case.classification, case.rules, case.proposal
    rtype = cls.request_type if cls else "unknown"
    asked = f"{case.requester.name} sent {TYPE_LABEL.get(rtype, 'a request')}."
    blocked = bool(cls and cls.model_used == "guard")

    if blocked:
        checked = "The safety check stopped it before any record or policy was read."
    else:
        bits: list[str] = []
        cited = [c.page_id for c in (prop.citations if prop else []) if c.page_type in (PageType.POLICY, PageType.WORKFLOW)]
        if cited:
            bits.append("checked it against " + _words(list(dict.fromkeys(cited))))
        if rules and rules.required_fields:
            gone = list(rules.missing_fields) + list(rules.invalid_fields)
            bits.append("all required details are present" if not gone else "details still needed: " + _words([label(f) for f in gone]))
        if rules and rules.conflicts:
            bits.append("found policies that disagree")
        if rules:
            bits.append(f"risk is {rules.risk.value}")
        checked = ("We " + "; ".join(bits) + ".") if bits else "No approved policy matched, so nothing could be checked."

    team = _team_name(brain, case.assigned_team)
    who = ROLE_LABEL.get(case.approver_role, "person") if case.approver_role else "person"
    if case.state == State.ANSWERED:
        nxt = "It was answered automatically from approved policy; no person is needed."
    elif case.state == State.NEEDS_INFO:
        nxt = "It is waiting for the requester to send the missing details."
    elif case.state in (State.IN_REVIEW, State.ESCALATED):
        why = next((REASON_PLAIN[r.value] for r in case.reason_codes if r.value in REASON_PLAIN), None)
        nxt = f"A {who} in {team} will decide" + (f", because {why}." if why else ".")
    elif case.state in (State.APPROVED, State.ACTIONED, State.NOTIFIED, State.CLOSED, State.REJECTED):
        nxt = f"A person has decided; the case is {case.state.value}."
    else:
        nxt = f"It is {case.state.value.replace('_', ' ')}."
    if prop and prop.decision_code == DecisionCode.REFUSE_AND_ROUTE and not blocked:
        nxt = f"It cannot be answered here and was sent to {team}. " + nxt
    return " ".join([asked, checked, nxt])


# ------------------------------------------------------------------ "How this is handled": the workflow's steps with progress
SOURCE_ROOT = "second_brain"                  # repo-relative prefix of every location we return
_FINISHED = {State.ANSWERED, State.APPROVED, State.ACTIONED, State.NOTIFIED, State.CLOSED}


def page_location(page: Page) -> str:
    return f"{SOURCE_ROOT}/{page_relpath(page)}"


def _cited_workflow(case: Case, brain: Brain) -> Page | None:
    for c in (case.proposal.citations if case.proposal else []):
        if c.page_type == PageType.WORKFLOW:
            page = brain.get(c.page_id)                        # current approved version only
            if page is not None and (c.version is None or c.version == page.version):
                return page
    rtype = case.classification.request_type if case.classification else None
    return brain.workflow_for(rtype) if rtype and rtype not in ("unknown", "general_policy_question") else None


def workflow_guidance(case: Case, brain: Brain, events: list) -> dict | None:
    """The workflow's steps (from page meta, never from a model) with done / current / next derived from the case state and the audit log,
    plus the required fields with ok / missing / invalid. A how-to answer (a general question answered from a workflow) lists the steps as
    information only. Returns None when no workflow applies (unknown request, input blocked)."""
    wf = _cited_workflow(case, brain)
    if wf is None or not wf.meta.get("steps"):
        return None
    steps = [str(s) for s in wf.meta["steps"]]
    rules = case.rules
    seen = {e.event for e in events}
    howto = bool(case.classification and case.classification.request_type == "general_policy_question" and "general_policy_question" not in wf.request_types)
    n = len(steps)
    if howto:
        done, current, mode = 0, None, "info"
    else:
        if case.state == State.NEEDS_INFO or (rules and (rules.missing_fields or rules.invalid_fields)):
            done = 0                                              # the first step (confirm the details) is still open
        elif case.state in _FINISHED:
            done = n
        elif case.state == State.REJECTED:
            done = n - 1
        elif "rules_applied" in seen or "routed" in seen:
            done = n - 1                                          # the checks ran; the last step (route / decide) waits for a person
        else:
            done = 0
        current = done if done < n and case.state != State.REJECTED else None
        mode = "live"
    out_steps = []
    for i, text in enumerate(steps):
        status = "info" if howto else "done" if i < done else "current" if i == current else "stopped" if case.state == State.REJECTED else "next"
        out_steps.append({"n": i + 1, "text": text, "status": status})
    bad = set(rules.missing_fields) if rules else set()
    invalid = set(rules.invalid_fields) if rules else set()
    required = list(wf.meta.get("required_fields", []))
    fields = [{"field": f, "label": label(f), "status": "info" if howto else "invalid" if f in invalid else "missing" if f in bad else "ok"}
              for f in required]
    return {"mode": mode, "workflow": {"id": wf.id, "version": wf.version, "title": wf.title, "location": page_location(wf)},
            "steps": out_steps, "fields": fields, "cite": f"{wf.id} v{wf.version}"}
