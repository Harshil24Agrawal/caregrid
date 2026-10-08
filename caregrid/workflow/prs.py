"""Knowledge PR flow (CONTRACTS section 3, PROMPTS section 4): a human reviewer's resolution can propose a small edit to a policy.

The strong-tier LLM only drafts WORDING. Anything with numbers, dates or approvals that is not already in the reviewer's note or the
current article is rejected by code, and the draft falls back to "current body + reviewer note". Structured changes (rule_key /
rule_value, retire) come only from the human's ReviewDecision.meta_changes - never from the LLM. Only a knowledge_owner can decide a PR.
"""
from __future__ import annotations

import difflib
import uuid
from datetime import datetime

from caregrid.ingest.anonymize import anonymize
from caregrid.knowledge.brain import Brain
from caregrid.llm import LLM, complete_json_tiered, model_name
from caregrid.models import Case, KnowledgePR, PageStatus, PageType, ReviewDecision, Role, User
from caregrid.reasoning.prompts import PR_SYSTEM, PR_USER
from caregrid.reasoning.propose import llm_text_problem
from caregrid.store import Store
from caregrid.workflow.audit import log

MAX_GROWTH = 1500          # a minimal edit may add at most this many characters
MIN_KEEP = 0.5             # ... and may not delete more than half of the article
META_KEYS = {"rule_key", "rule_value", "retire", "target_page"}


class PRStateError(ValueError):
    """The PR is not open, or the page it was written against has moved on."""


def cited_policies(case: Case) -> list[str]:
    return [c.page_id for c in (case.proposal.citations if case.proposal else []) if c.page_type == PageType.POLICY]


def target_policy(case: Case, meta: dict | None = None) -> str | None:
    """The policy page a PR edits: the one the reviewer names in meta_changes["target_page"] if the case cited it, else the first cited."""
    cited = cited_policies(case)
    named = (meta or {}).get("target_page")
    return named if named in cited else (cited[0] if cited else None)


def clean_meta(meta: dict) -> dict:
    """Only the three known keys, only simple values. Applied exactly as the human typed them."""
    out: dict = {}
    for k, v in (meta or {}).items():
        if k not in META_KEYS:
            continue
        if k == "retire":
            if v is True:
                out[k] = True
        elif k == "target_page":
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()[:40]
        elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
            out[k] = anonymize(str(v))[0]
    if ("rule_key" in out) != ("rule_value" in out):
        out.pop("rule_key", None)
        out.pop("rule_value", None)
    return out


def _fallback_body(body: str, note: str) -> str:
    return f"{body}\n\nReviewer clarification: {note}" if note else body


def _draft_body(current_body: str, page_id: str, version: int, case: Case, d: ReviewDecision, note: str, llm: LLM) -> tuple[str, str, str]:
    """-> (proposed_body, reason, model_used). Falls back to current body + note whenever the LLM draft is unusable."""
    fallback = _fallback_body(current_body, note)
    summary = case.masked_text[:600] if case.masked_text else ""
    user = PR_USER.format(page_id=page_id, version=version, body=current_body, summary=summary, action=d.action.value, note=note)
    try:
        raw, used = complete_json_tiered(llm, PR_SYSTEM, user, "strong")
        body, reason = raw.get("proposed_body"), raw.get("reason")
        if not isinstance(body, str) or not body.strip():
            return fallback, "draft unusable (empty); kept current text plus the reviewer note", "fallback:deterministic"
    except Exception:
        return fallback, "drafter unavailable; kept current text plus the reviewer note", "fallback:deterministic"
    # the diff the drafter introduced must be safe: judge only the NEW lines against note + current article
    old_lines = set(current_body.splitlines())
    added = [ln for ln in body.splitlines() if ln not in old_lines]
    problem = llm_text_problem(added, current_body + "\n" + note) if added else None
    if problem is None and (len(body) - len(current_body) > MAX_GROWTH or len(body) < len(current_body) * MIN_KEEP):
        problem = "not_a_minimal_edit"
    if problem is not None:
        return fallback, f"draft rejected by guard ({problem}); kept current text plus the reviewer note", "fallback:deterministic"
    return body, reason.strip() if isinstance(reason, str) and reason.strip() else "Reviewer-driven clarification.", model_name(llm, used)


def unified_diff(old: str, new: str, page_id: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=f"{page_id} (current)", tofile=f"{page_id} (proposed)"))


def draft_pr(case: Case, d: ReviewDecision, brain: Brain, llm: LLM, store: Store | None = None) -> KnowledgePR | None:
    """Open a KnowledgePR for the policy the case cited. None when there is nothing to edit (no cited current policy, or no change)."""
    meta = clean_meta(d.meta_changes)
    page_id = target_policy(case, meta)
    page = brain.get(page_id) if page_id else None
    if page is None:
        return None
    note = anonymize(d.note)[0].strip() if d.note else ""
    failed_before = len(getattr(llm, "failures", []))
    body, reason, _model = _draft_body(page.body, page.id, page.version, case, d, note, llm)
    failed = list(getattr(llm, "failures", []))[failed_before:]
    if failed and store is not None:
        log(store, "llm_failure", d.reviewer, case.id, types=failed, step="pr_draft")
    if body == page.body and not (meta.keys() - {"target_page"}):
        return None
    pr = KnowledgePR(id=f"PR-{uuid.uuid4().hex[:6]}", target_page_id=page.id, base_version=page.version, proposed_body=body,
                     diff=unified_diff(page.body, body, page.id), reason=reason, author_id=d.reviewer.id, status="open",
                     created_at=datetime.now(), meta_changes=meta)
    if store is not None:
        store.save_pr(pr)
    return pr


def decide_pr(pr_id: str, approve: bool, user: User, store: Store, brain: Brain) -> KnowledgePR:
    """Knowledge-owner decision. Approve publishes the next page version (old version expired, dependent precedents stale, log.md and
    index updated); a meta `retire` retires the page instead. Reject changes only the PR. Both are audited."""
    pr = next((p for p in store.list_prs() if p.id == pr_id), None)
    if pr is None:
        raise KeyError(f"unknown PR {pr_id}")
    if user.role != Role.KNOWLEDGE_OWNER:
        log(store, "pr_denied", user, None, pr=pr.id, role=user.role.value)
        raise PermissionError(f"{user.role.value} may not decide knowledge PRs")
    if pr.status != "open":
        log(store, "pr_rejected_request", user, None, pr=pr.id, reason=f"already {pr.status}")
        raise PRStateError(f"{pr.id} is already {pr.status}")

    stale: list[str] = []
    new_version: int | None = None
    if approve:
        current = brain.get(pr.target_page_id)
        if current is None or current.version != pr.base_version:
            log(store, "pr_rejected_request", user, None, pr=pr.id, reason="target page changed since the PR was drafted")
            raise PRStateError(f"{pr.target_page_id} changed since {pr.id} was drafted (base v{pr.base_version}); reject and redraft")
        meta = clean_meta(pr.meta_changes)
        if meta.get("retire"):
            stale = brain.retire_page(current.id)
        else:
            update: dict = {"body": pr.proposed_body, "status": PageStatus.DRAFT}
            if "rule_key" in meta:
                update["meta"] = {**current.meta, str(meta["rule_key"]): meta["rule_value"]}
            before = {x.id for x in brain.precedents(status=PageStatus.STALE)}
            brain.write_page(current.model_copy(update=update), publish=True)
            new_version = (brain.get(current.id) or current).version
            stale = [x.id for x in brain.precedents(status=PageStatus.STALE) if x.id not in before]
    pr = pr.model_copy(update={"status": "approved" if approve else "rejected", "decided_by": user.id})
    store.save_pr(pr)
    log(store, "pr_decided", user, None, pr=pr.id, page=pr.target_page_id, decision=pr.status, new_version=new_version,
        retired=bool(approve and clean_meta(pr.meta_changes).get("retire")), stale_precedents=stale)
    return pr
