"""Routing (CONTRACTS §8). A hard override can never yield route="auto"."""
from __future__ import annotations

from datetime import datetime

from caregrid import config
from caregrid.models import HARD_OVERRIDES, ActionTier, Band, Case, DecisionCode, ReasonCode, Risk, State, TrustRecord
from caregrid.reasoning.propose import not_enough_evidence_text


def set_state(case: Case, state: State, when: datetime | None = None) -> None:
    if case.state != state or not case.state_history:
        case.state = state
        case.state_history = [*case.state_history, (state, when or datetime.now())]


def _add(case: Case, code: ReasonCode) -> None:
    if code not in case.reason_codes:
        case.reason_codes = [*case.reason_codes, code]


def safety_override(case: Case) -> bool:
    """A safety override skips the requester's confirmation and goes straight to a person: clinical, injection / access denied, sensitive,
    account-specific (the HARD_OVERRIDES reason codes) or anything the input guard refused."""
    return any(c in HARD_OVERRIDES for c in case.reason_codes) or bool(case.classification and case.classification.model_used == "guard")


def _to_a_person(case: Case) -> None:
    """Human-routed: with a safety override the case goes to review at once; otherwise it stops at PROPOSED until the requester confirms
    (forward_case) or withdraws it."""
    set_state(case, State.PROPOSED if config.REQUIRE_CONFIRMATION and not safety_override(case) else State.IN_REVIEW)


def decide_route(case: Case, trust: TrustRecord) -> Case:
    """Mutates and returns `case`. Needs case.rules, case.confidence and case.proposal."""
    rules, conf, prop = case.rules, case.confidence, case.proposal
    assert rules is not None and conf is not None and prop is not None, "decide_route needs rules, confidence and proposal"
    case.trust_level = trust.level
    case.assigned_team = rules.route_team
    case.approver_role = rules.approver_role
    case.reason_codes = list(rules.reason_codes)
    case.routing = "human"

    incomplete = bool(rules.missing_fields or rules.invalid_fields)
    if safety_override(case):                                   # a safety override goes to a person at once, complete or not
        set_state(case, State.IN_REVIEW)
    elif incomplete:                                            # NEEDS_INFO <=> something is missing or invalid: ask the requester first
        set_state(case, State.NEEDS_INFO)
    elif rules.hard_override:
        _to_a_person(case)
    elif conf.band == Band.LOW or prop.decision_code == DecisionCode.NOT_ENOUGH_EVIDENCE:
        # An abstention is never automatic, whatever the band. A decision that was ALREADY not_enough_evidence (no relevant
        # policy for a general question, or every policy citation failed verification) is a policy gap by definition;
        # a LOW band that merely forced the abstention is a gap only when no policy scored.
        abstained = prop.decision_code == DecisionCode.NOT_ENOUGH_EVIDENCE
        case.proposal = prop.model_copy(update={"decision_code": DecisionCode.NOT_ENOUGH_EVIDENCE,
                                                "answer_text": not_enough_evidence_text(rules.route_team),
                                                "questions_for_requester": []})
        _add(case, ReasonCode.LOW_CONFIDENCE)
        known_type = bool(case.classification and case.classification.request_type != "unknown")
        # POLICY_GAP is reserved for KNOWN request types (incl. general_policy_question) with no relevant approved policy,
        # so Gap Radar stays meaningful. An "unknown" request is an intent problem: UNCLEAR_INTENT only (rules add it).
        if known_type and (abstained or conf.breakdown.get("policy", 0) == 0):
            _add(case, ReasonCode.POLICY_GAP)
        _to_a_person(case)
    elif rules.missing_fields or rules.invalid_fields:
        set_state(case, State.NEEDS_INFO)
    elif conf.band == Band.MEDIUM:
        _to_a_person(case)
    elif (conf.band == Band.HIGH and rules.risk == Risk.LOW and trust.level >= 1
          and rules.action_tier in (ActionTier.READ, ActionTier.DRAFT)):
        case.routing = "auto"
        if rules.action_tier == ActionTier.READ:
            set_state(case, State.ANSWERED)
        else:
            set_state(case, State.READY)               # PROPOSED -> READY
    else:
        _to_a_person(case)
    return case
