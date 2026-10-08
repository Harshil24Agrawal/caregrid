"""Cite-or-abstain (CLAUDE.md rule 2): every citation is verified in code against the Brain."""
from __future__ import annotations

from caregrid.knowledge.brain import Brain
from caregrid.models import Citation, DecisionCode, PageStatus, PageType, Proposal
from caregrid.reasoning.propose import not_enough_evidence_text

DOWNGRADED = "downgraded_not_enough_evidence"


def verify_citations(p: Proposal, brain: Brain, context_ids: set[str] | None = None) -> tuple[Proposal, list[str]]:
    """Keep a citation only if its id was in the context block (when given), the page exists, is approved (policies,
    workflows, ...) or active (precedents), and the cited version is the current one. Version, type and title are
    re-attached from the Brain. `issues` contain ids and reasons only.

    An answer_from_policy with zero surviving POLICY citations is downgraded to not_enough_evidence (POLICY_GAP is added
    by the pipeline when it sees the `downgraded_not_enough_evidence` issue)."""
    kept: list[Citation] = []
    issues: list[str] = []
    seen: set[str] = set()
    for c in p.citations:
        if c.page_id in seen:
            continue
        if context_ids is not None and c.page_id not in context_ids:
            issues.append(f"dropped {c.page_id}: not in the context")
            continue
        page = brain.get(c.page_id)                       # current APPROVED page / ACTIVE precedent, else None
        if page is None:
            issues.append(f"dropped {c.page_id}: unknown, draft, expired or stale")
            continue
        if page.status not in (PageStatus.APPROVED, PageStatus.ACTIVE):
            issues.append(f"dropped {c.page_id}: status {page.status.value}")
            continue
        if c.version is not None and c.version != page.version:
            issues.append(f"dropped {c.page_id}: cited v{c.version}, current is v{page.version}")
            continue
        seen.add(c.page_id)                               # only a KEPT citation blocks a later duplicate
        kept.append(Citation(page_id=page.id, version=None if page.type == PageType.PRECEDENT else page.version,
                             page_type=page.type, title=page.title))
    out = p.model_copy(update={"citations": kept})
    if out.decision_code == DecisionCode.ANSWER_FROM_POLICY and not any(c.page_type == PageType.POLICY for c in kept):
        out = out.model_copy(update={
            "decision_code": DecisionCode.NOT_ENOUGH_EVIDENCE, "answer_text": not_enough_evidence_text(p.route_team),
            "questions_for_requester": []})
        issues.append(DOWNGRADED)
    return out, issues
