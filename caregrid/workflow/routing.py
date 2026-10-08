"""Routing (CONTRACTS §8). A hard override can never yield route="auto"."""
from __future__ import annotations

from datetime import datetime

from caregrid.models import ActionTier, Band, Case, DecisionCode, ReasonCode, Risk, State, TrustRecord
from caregrid.reasoning.propose import not_enough_evidence_text


def set_state(case: Case, state: State, when: datetime | None = None) -> None:
    if case.state != state or not case.state_history:
        case.state = state
        case.state_history = [*case.state_history, (state, when or datetime.now())]


def _add(case: Case, code: ReasonCode) -> None:
    if code not in case.reason_codes:
        case.reason_codes = [*case.reason_codes, code]


def decide_route(case: Case, trust: TrustRecord) -> Case:
    """Mutates and returns `case`. Needs case.rules, case.confidence and case.proposal."""
    rules, conf, prop = case.rules, case.confidence, case.proposal
    assert rules is not None and conf is not None and prop is not None, "decide_route needs rules, confidence and proposal"
    case.trust_level = trust.level
    case.assigned_team = rules.route_team
    case.approver_role = rules.approver_role
    case.reason_codes = list(rules.reason_codes)
    case.routing = "human"

    if rules.hard_override:
        set_state(case, State.IN_REVIEW)
    elif conf.band == Band.LOW:
        case.proposal = prop.model_copy(update={"decision_code": DecisionCode.NOT_ENOUGH_EVIDENCE,
                                                "answer_text": not_enough_evidence_text(rules.route_team),
                                                "questions_for_requester": []})
        _add(case, ReasonCode.LOW_CONFIDENCE)
        has_policy = conf.breakdown.get("policy", 0) > 0
        known_type = bool(case.classification and case.classification.request_type != "unknown")
        if not has_policy and known_type:               # an unknown/vague request is an intent problem, not a policy gap
            _add(case, ReasonCode.POLICY_GAP)
        set_state(case, State.IN_REVIEW)
    elif rules.missing_fields or rules.invalid_fields:
        set_state(case, State.NEEDS_INFO)
    elif conf.band == Band.MEDIUM:
        set_state(case, State.IN_REVIEW)
    elif (conf.band == Band.HIGH and rules.risk == Risk.LOW and trust.level >= 1
          and rules.action_tier in (ActionTier.READ, ActionTier.DRAFT)):
        case.routing = "auto"
        if rules.action_tier == ActionTier.READ:
            set_state(case, State.ANSWERED)
        else:
            set_state(case, State.READY)               # PROPOSED -> READY
    else:
        set_state(case, State.IN_REVIEW)
    return case
