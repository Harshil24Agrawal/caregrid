"""Test helper: guard -> extract -> retrieve -> rules -> decide_code -> confidence with a stub Proposal.

Phase 4 replaces the classification stub with the real classifier and adds the proposer.
"""
from __future__ import annotations

from dataclasses import dataclass

from caregrid.knowledge.retrieve import retrieve
from caregrid.models import (
    Classification, Confidence, GuardResult, Proposal, ReasonCode, RetrievalResult, Role, RuleResult, User,
)
from caregrid.reasoning.confidence import score
from caregrid.reasoning.extract import extract_fields, keyword_type
from caregrid.reasoning.guards import check_input
from caregrid.reasoning.rules import apply_rules, decide_code

USERS = {
    "asha": User(id="U1", name="Asha", role=Role.OPS_EMPLOYEE, team="TEAM-OPS-TRIAGE"),
    "vikram": User(id="U2", name="Vikram", role=Role.TEAM_SPECIALIST, team="TEAM-ENROLL"),
    "rahul": User(id="U4", name="Rahul", role=Role.SENIOR_REVIEWER, team="TEAM-SENIOR-OPS"),
}


@dataclass
class Chain:
    guard: GuardResult
    cls: Classification
    ret: RetrievalResult
    rules: RuleResult
    proposal: Proposal
    conf: Confidence


def run_chain(text: str, user: User, brain, llm) -> Chain:
    guard = check_input(text, user)
    # pipeline step 1: a blocked request is never classified
    rtype, hit = keyword_type(guard.masked_text) if guard.allowed else ("unknown", False)
    cls = Classification(
        request_type=rtype, llm_confidence=0.9 if hit else 0.3, rules_type=rtype if hit else None,
        extracted_fields=extract_fields(guard.masked_text),
        is_clinical=ReasonCode.CLINICAL in guard.overrides, is_sensitive=ReasonCode.SENSITIVE in guard.overrides,
        is_account_specific=ReasonCode.ACCOUNT_SPECIFIC in guard.overrides)
    ret = retrieve(brain, cls, guard.masked_text, llm)
    rules = apply_rules(cls, ret, brain, guard)
    code = decide_code(rules, ret, cls)
    proposal = Proposal(decision_code=code, route_team=rules.route_team, answer_text="stub", summary_for_reviewer="stub")
    return Chain(guard, cls, ret, rules, proposal, score(cls, ret, rules, proposal))
