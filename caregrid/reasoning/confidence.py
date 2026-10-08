"""Deterministic confidence score (CONTRACTS §5). The LLM never contributes to this number."""
from __future__ import annotations

from caregrid import config
from caregrid.models import (
    Band, Classification, Confidence, PageStatus, PageType, Proposal, RetrievalResult, RuleResult, ScoredPage, ScoredPrecedent,
)

MAX = {"policy": 30, "precedent": 25, "fields": 20, "clarity": 15, "no_conflict": 10}
CLARITY_LLM_MIN = 0.7


def band_for(score: int) -> Band:
    return Band.HIGH if score >= 75 else Band.MEDIUM if score >= 45 else Band.LOW


def relevant_policies(ret: RetrievalResult) -> list[ScoredPage]:
    """Approved current POLICY pages that count as evidence: their relevance to THIS query reaches POLICY_MIN_SCORE.
    Being linked from the workflow is not enough on its own (P4.1)."""
    return [s for s in ret.policies
            if s.page.type == PageType.POLICY and s.page.status == PageStatus.APPROVED and s.relevance >= config.POLICY_MIN_SCORE]


def similar_precedents(ret: RetrievalResult) -> list[ScoredPrecedent]:
    return [s for s in ret.precedents_active
            if s.precedent.status == PageStatus.ACTIVE and s.similarity >= config.PRECEDENT_MIN_SIM]


def policy_component(ret: RetrievalResult) -> int:
    pols = relevant_policies(ret)
    if any(s.linked for s in pols):
        return 30
    return 15 if pols else 0


def precedent_component(ret: RetrievalResult, proposal: Proposal) -> int:
    n = sum(1 for s in similar_precedents(ret)
            if s.precedent.decision_code == proposal.decision_code and s.precedent.route_team == proposal.route_team)
    return 0 if n == 0 else 15 if n == 1 else 20 if n == 2 else 25


def fields_component(rules: RuleResult) -> int:
    required = len(rules.required_fields)
    if not required:
        return 20
    bad = set(rules.missing_fields) | set(rules.invalid_fields)   # invalid counts as missing
    return round(20 * (required - len(bad & set(rules.required_fields))) / required)


def clarity_component(cls: Classification) -> int:
    if cls.request_type == "unknown":
        return 0
    holds = [cls.rules_type == cls.request_type, cls.llm_confidence >= CLARITY_LLM_MIN]
    return 15 if all(holds) else 8 if any(holds) else 0


def build_confidence(breakdown: dict[str, int], has_conflicts: bool) -> Confidence:
    total = sum(breakdown.values())
    band = band_for(total)
    capped = has_conflicts and band == Band.HIGH
    if capped:
        band = Band.MEDIUM          # a conflict always needs an expert
    gaps = {k: MAX[k] - breakdown[k] for k in MAX}
    worst = max(MAX, key=lambda k: gaps[k])
    parts = ", ".join(f"{k.replace('_', ' ')} {breakdown[k]}/{MAX[k]}" for k in MAX)
    gap_txt = f"Biggest missing piece: {worst.replace('_', ' ')} ({gaps[worst]} points)." if gaps[worst] else "Nothing missing."
    cap_txt = " Capped at Medium because the policies conflict." if capped else ""
    return Confidence(score=total, band=band, breakdown=dict(breakdown),
                      explanation=f"Evidence score {total}/100 ({band.value}): {parts}. {gap_txt}{cap_txt}")


def score(cls: Classification, ret: RetrievalResult, rules: RuleResult, p: Proposal) -> Confidence:
    breakdown = {
        "policy": policy_component(ret),
        "precedent": precedent_component(ret, p),
        "fields": fields_component(rules),
        "clarity": clarity_component(cls),
        "no_conflict": 0 if rules.conflicts else 10,
    }
    return build_confidence(breakdown, bool(rules.conflicts))
