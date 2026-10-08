"""Human decisions (CONTRACTS §9, §12). AI prepares the decision; a human owns it; the workflow executes what was approved.

submit_decision order: permission (denied -> PermissionError + audit, nothing changes) -> state (decided already -> AlreadyDecidedError,
nothing changes) -> validation -> transitions, each with a state_history entry and an audit event.
"""
from __future__ import annotations

from caregrid.knowledge.brain import Brain
from caregrid.llm import LLM
from caregrid.ingest.anonymize import anonymize
from caregrid.models import Case, ReviewAction, ReviewDecision, Role, State, User
from caregrid.rbac import can_approve, can_view
from caregrid.reasoning.guards import check_output
from caregrid.store import Store
from caregrid.workflow.audit import log
from caregrid.workflow.comms import send_communications, validate_contacts
from caregrid.workflow.precedents import capture_precedent
from caregrid.workflow.prs import draft_pr, target_policy
from caregrid.workflow.routing import set_state
from caregrid.workflow.trust import record_review

# stored text is checked as a viewer who may see amounts; redaction is a display concern per viewer role
_STORAGE_VIEWER = User(id="system", name="System", role="senior_reviewer")

DECIDABLE = {State.IN_REVIEW, State.ESCALATED}          # ESCALATED: a senior has to be able to decide what was escalated to them
ASKABLE = DECIDABLE | {State.NEEDS_INFO}

ACTION_DESCRIPTIONS = {
    "general_policy_question": "Sent the cited policy answer to the requester.",
    "provider_address_change": "Queued the provider billing-address update for {team} to apply.",
    "provider_name_change": "Queued the provider name update for {team} to apply.",
    "portal_access_reset": "Queued the portal access reset for {team} to carry out.",
    "prior_auth_status": "Passed the authorization status request to {team}.",
    "claim_status_inquiry": "Passed the claim status request to {team}.",
    "dme_equipment_request": "Released the equipment request to {team} for ordering. Amounts and source records were not changed.",
    "complaint_grievance": "Logged the complaint for {team}.",
}


class AlreadyDecidedError(ValueError):
    """The case is not waiting for this decision (already decided, or in a state that cannot take it)."""


def simulate_action(case: Case) -> str:
    """The approved action, simulated: it only describes what the owning team will do. It never mutates billing or source records."""
    rtype = case.classification.request_type if case.classification else "unknown"
    template = ACTION_DESCRIPTIONS.get(rtype, "Routed to {team}.")
    return template.format(team=case.assigned_team or "the owning team")


def _move(store: Store, case: Case, state: State, actor: User) -> None:
    before = case.state
    set_state(case, state)
    if case.state != before:
        log(store, "state_changed", actor, case.id, **{"from": before.value, "to": case.state.value, "by": "review"})


def _check_text(text: str) -> tuple[str, list[str]]:
    _, cleaned, issues = check_output(text, _STORAGE_VIEWER)
    return cleaned, issues


def submit_decision(d: ReviewDecision, store: Store, brain: Brain, llm: LLM) -> Case:
    case = store.get_case(d.case_id)
    if case is None:
        raise KeyError(f"unknown case {d.case_id}")
    reviewer = d.reviewer
    ask = d.action == ReviewAction.ASK_REQUESTER

    # 1 permission (RBAC + separation of duties) - before anything is touched
    allowed = (can_view(reviewer, case, "full") and case.requester.id != reviewer.id) if ask else can_approve(reviewer, case)
    if not allowed:
        log(store, "review_denied", reviewer, case.id, action=d.action.value, role=reviewer.role.value,
            reason="no full view of this case" if ask else "role, risk, team or separation of duties", state=case.state.value)
        raise PermissionError(f"{reviewer.role.value} may not {d.action.value} {case.id}")

    # 2 state: idempotent - a decided case cannot be decided again
    ok_states = ASKABLE if ask else DECIDABLE
    if case.state not in ok_states or (d.action == ReviewAction.ESCALATE and case.state == State.ESCALATED):
        log(store, "review_rejected", reviewer, case.id, action=d.action.value, reason="not awaiting this decision", state=case.state.value)
        raise AlreadyDecidedError(f"{case.id} is {case.state.value}; it is not awaiting {d.action.value}")

    # 3 validation (still nothing changed)
    validate_contacts(d)
    if d.action == ReviewAction.EDIT_APPROVE and not (d.edited_answer and d.edited_answer.strip()):
        raise ValueError("EDIT_APPROVE needs an edited_answer")
    note = anonymize(d.note)[0] if d.note else ""
    edited, edit_issues = (_check_text(d.edited_answer.strip()) if d.edited_answer and d.edited_answer.strip() else (None, []))
    log(store, "review_submitted", reviewer, case.id, action=d.action.value, role=reviewer.role.value, note=note,
        edited=edited is not None, edit_check=edit_issues, save_as_precedent=d.save_as_precedent, channels=[c.value for c in d.channels],
        propose_pr=d.propose_pr)
    if d.propose_pr:
        log(store, "pr_requested", reviewer, case.id, target_page=target_policy(case))
        pr = draft_pr(case, d, brain, llm, store)
        if pr is None:
            log(store, "pr_skipped", reviewer, case.id, reason="no cited current policy or nothing to change")
        else:
            log(store, "pr_opened", reviewer, case.id, pr=pr.id, target_page=pr.target_page_id, base_version=pr.base_version,
                structured=sorted(pr.meta_changes), reason=pr.reason)
    rtype = case.classification.request_type if case.classification else "unknown"

    # 4 act
    if d.action in (ReviewAction.APPROVE, ReviewAction.EDIT_APPROVE):
        if edited is not None and case.proposal is not None and d.action == ReviewAction.EDIT_APPROVE:
            case.proposal = case.proposal.model_copy(update={"answer_text": edited})
        _move(store, case, State.APPROVED, reviewer)
        description = simulate_action(case)
        _move(store, case, State.ACTIONED, reviewer)
        log(store, "action_executed", reviewer, case.id, simulated=True, description=description)
        if d.channels:
            comms = send_communications(case, d, store)
            if any(c.status in ("simulated", "sent") for c in comms):
                _move(store, case, State.NOTIFIED, reviewer)
        store.save_case(case)
        if d.save_as_precedent:
            prec = capture_precedent(case, d, brain)
            log(store, "precedent_saved", reviewer, case.id, precedent=prec.id, request_type=prec.request_type,
                decision_code=prec.decision_code.value, status=prec.status.value)
        record_review(store, rtype, d.action == ReviewAction.APPROVE, brain=brain, case_id=case.id, actor=reviewer)

    elif d.action == ReviewAction.REJECT:
        _move(store, case, State.REJECTED, reviewer)
        store.save_case(case)
        record_review(store, rtype, False, brain=brain, case_id=case.id, actor=reviewer)

    elif d.action == ReviewAction.ESCALATE:
        _move(store, case, State.ESCALATED, reviewer)
        case.approver_role = Role.SENIOR_REVIEWER
        case.assigned_team = "TEAM-SENIOR-OPS"
        store.save_case(case)
        record_review(store, rtype, False, brain=brain, case_id=case.id, actor=reviewer)

    else:  # ASK_REQUESTER: not a review, so no trust update and no precedent
        questions = list(case.proposal.questions_for_requester) if case.proposal else []
        if not questions:
            if not note:
                raise ValueError("ASK_REQUESTER needs the question in `note` when the case has no questions of its own")
            questions = [_check_text(d.note.strip())[0]]
        if case.proposal is not None:
            case.proposal = case.proposal.model_copy(update={"questions_for_requester": questions})
        _move(store, case, State.NEEDS_INFO, reviewer)
        log(store, "requester_asked", reviewer, case.id, questions=len(questions))
        store.save_case(case)
    return store.get_case(case.id)
