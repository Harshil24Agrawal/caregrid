"""Second Brain lint (CONTRACTS §3). Pure reads: never modifies pages."""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from caregrid.knowledge.retrieve import tokenize
from caregrid.models import LintFinding, PageStatus, PageType, ReasonCode
from caregrid.store import Store

HOTSPOT_MIN = 5
_ORPHAN_EXEMPT = {PageType.WORKFLOW, PageType.REGULATORY, PageType.PRECEDENT}
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def lint(brain, store: Store | None = None) -> list[LintFinding]:
    findings: list[LintFinding] = []
    findings += _contradictions(brain)
    findings += _expired_linked(brain)
    findings += _missing_team(brain)
    findings += _stale_precedents(brain)
    findings += _hotspots(brain, store)
    findings += _orphans(brain)
    return sorted(findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.code, f.page_ids))


def _contradictions(brain) -> list[LintFinding]:
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for p in brain.current_policies():
        key = p.meta.get("rule_key")
        if key:
            for rt in p.request_types:
                groups[(rt, key)].append(p)
    out = []
    for (rt, key), pages in sorted(groups.items()):
        values = {str(p.meta.get("rule_value")) for p in pages}
        if len(values) > 1:
            detail = "; ".join(f"{p.key} says {p.meta.get('rule_value')}" for p in sorted(pages, key=lambda p: p.id))
            out.append(LintFinding(severity="error", code="CONTRADICTION", page_ids=sorted(p.id for p in pages),
                                   message=f"Approved policies disagree on '{key}' for {rt}: {detail}"))
    return out


def _expired_linked(brain) -> list[LintFinding]:
    out = []
    for wf in brain.current_pages(PageType.WORKFLOW):
        for link in wf.links:
            if brain.get(link) is not None:
                continue
            versions = [p for p in brain.all_pages() if p.id == link]
            if versions:
                latest = max(versions, key=lambda p: p.version)
                out.append(LintFinding(
                    severity="warning", code="EXPIRED_LINKED", page_ids=[wf.id, link],
                    message=f"{wf.id} links {link}, which has no approved current version (latest v{latest.version} is {latest.status.value})."))
    return out


def _missing_team(brain) -> list[LintFinding]:
    return [LintFinding(severity="error", code="MISSING_TEAM", page_ids=[wf.id],
                        message=f"{wf.id} routes to team '{wf.meta.get('team')}', which has no team page.")
            for wf in brain.current_pages(PageType.WORKFLOW) if brain.team(str(wf.meta.get("team"))) is None]


def _stale_precedents(brain) -> list[LintFinding]:
    out = []
    for p in brain.precedents(status=PageStatus.STALE):
        current = brain.get(p.policy_id) if p.policy_id else None
        now = f"current is v{current.version}" if current else "no approved current version"
        out.append(LintFinding(severity="warning", code="STALE_PRECEDENT", page_ids=[p.id],
                               message=f"{p.id} is stale: it relied on {p.policy_id} v{p.policy_version} ({now}); never used for scoring."))
    return out


def _hotspots(brain, store: Store | None) -> list[LintFinding]:
    """POLICY_GAP escalations clustered by request_type + topic. A topic is the token that covers the most
    escalations, weighted by how rare it is across all precedents (so 'telehealth' beats 'provider')."""
    items: list[tuple[str, str, str]] = []  # (request_type, text, ref)
    for p in brain.precedents():
        if ReasonCode.POLICY_GAP in p.reason_codes:
            items.append((p.request_type, p.summary, p.id))
    if store is not None:
        for c in store.list_cases():
            if ReasonCode.POLICY_GAP in c.reason_codes and c.classification:
                items.append((c.classification.request_type, c.masked_text, c.id))
    if not items:
        return []

    out: list[LintFinding] = []
    for rt, topic, refs in topic_clusters(items, [p.summary for p in brain.precedents()], HOTSPOT_MIN):
        drafts = [p.id for p in brain.all_pages() if p.type == PageType.POLICY and p.status == PageStatus.DRAFT
                  and topic in tokenize(p.title + " " + p.body)]
        note = f" Only draft {', '.join(drafts)} exists." if drafts else ""
        out.append(LintFinding(
            severity="warning", code="ESCALATION_HOTSPOT", page_ids=refs,
            message=f"{len(refs)} POLICY_GAP escalations for {rt} about '{topic}' ({', '.join(refs[:3])}...). "
                    f"No approved article covers it.{note} Consider drafting one."))
    return out


def topic_clusters(items: list[tuple[str, str, str]], corpus_texts: list[str], min_items: int) -> list[tuple[str, str, list[str]]]:
    """Cluster (request_type, text, ref) items by topic token, per request type. A topic is the token that covers the most
    items, weighted by how rare it is across `corpus_texts` (so 'telehealth' beats 'provider'). Returns (request_type, topic, refs)
    for every cluster with >= min_items items; items left over are not returned."""
    corpus = [set(tokenize(t)) for t in corpus_texts] or [set()]
    df = Counter(t for toks in corpus for t in toks)
    n_docs = len(corpus)
    out: list[tuple[str, str, list[str]]] = []
    for rt in sorted({i[0] for i in items}):
        remaining = {ref: set(tokenize(text)) for r, text, ref in items if r == rt}
        while len(remaining) >= min_items:
            cover = Counter(t for toks in remaining.values() for t in toks)
            scored = [(c * math.log((n_docs + 1) / (df.get(t, 0) + 1)), c, t) for t, c in cover.items() if c >= min_items]
            if not scored:
                break
            _, _, topic = max(scored, key=lambda x: (x[0], x[2]))
            refs = sorted(r for r, toks in remaining.items() if topic in toks)
            out.append((rt, topic, refs))
            for r in refs:
                remaining.pop(r)
    return out


def _orphans(brain) -> list[LintFinding]:
    inbound: set[str] = set()
    for p in brain.all_pages():
        inbound.update(p.links)
    for prec in brain.precedents():
        if prec.policy_id:
            inbound.add(prec.policy_id)
    out = []
    seen: set[str] = set()
    for p in brain.all_pages():
        if p.type in _ORPHAN_EXEMPT or p.id in inbound or p.id in seen:
            continue
        seen.add(p.id)
        out.append(LintFinding(severity="info", code="ORPHAN", page_ids=[p.id], message=f"{p.id} ({p.title}) has no inbound links."))
    return out
