"""Dashboard numbers (CONTRACTS §3). Read-only, derived from the store (and the brain for precedents). Plain dict/list results so the UI
can render them as tables or charts without knowing the models.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from caregrid.constants import REQUEST_TYPES
from caregrid.knowledge.brain import Brain
from caregrid.knowledge.lint import topic_clusters
from caregrid.models import Case, ReasonCode, State, TrustRecord
from caregrid.store import Store

OPEN_STATES = {State.PROPOSED, State.IN_REVIEW, State.NEEDS_INFO, State.ESCALATED}     # waiting for a person (a queue)
DONE_STATES = {State.APPROVED, State.ACTIONED, State.NOTIFIED, State.CLOSED, State.ANSWERED, State.REJECTED}


def _entered_state_at(case: Case) -> datetime:
    return case.state_history[-1][1] if case.state_history else case.created_at


def hours_in_state(case: Case, now: datetime | None = None) -> float:
    return max(((now or datetime.now()) - _entered_state_at(case)).total_seconds() / 3600.0, 0.0)


def dashboard_counts(store: Store) -> dict:
    """{total, by_state, by_team (open cases), by_routing, open, awaiting_review, needs_info, escalated, auto_answered, completed,
    hard_override_open, avg_confidence}"""
    cases = store.list_cases()
    open_cases = [c for c in cases if c.state in OPEN_STATES]
    scored = [c.confidence.score for c in cases if c.confidence and c.classification and c.classification.model_used != "seed"]
    return {
        "total": len(cases),
        "by_state": dict(sorted(Counter(c.state.value for c in cases).items())),
        "by_team": dict(sorted(Counter(c.assigned_team or "unassigned" for c in open_cases).items())),
        "by_routing": dict(Counter(c.routing for c in cases)),
        "open": len(open_cases),
        "awaiting_review": sum(c.state == State.IN_REVIEW for c in cases),
        "needs_info": sum(c.state == State.NEEDS_INFO for c in cases),
        "escalated": sum(c.state == State.ESCALATED for c in cases),
        "auto_answered": sum(c.routing == "auto" for c in cases),
        "completed": sum(c.state in DONE_STATES for c in cases),
        "hard_override_open": sum(bool(c.rules and c.rules.hard_override) for c in open_cases),
        "avg_confidence": round(sum(scored) / len(scored), 1) if scored else None,
    }


def trust_overview(store: Store) -> list[TrustRecord]:
    """One record per request type (a type never reviewed shows level 0 with zero counts)."""
    return [store.get_trust(t) for t in REQUEST_TYPES]


def queue_aging(store: Store, now: datetime | None = None) -> list[dict]:
    """Rows {state, team, count, avg_hours, max_hours} for cases waiting for a person. Hours = time since the case entered
    its current state."""
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for c in store.list_cases():
        if c.state in OPEN_STATES:
            groups[(c.state.value, c.assigned_team or "unassigned")].append(hours_in_state(c, now))
    rows = [{"state": s, "team": t, "count": len(h), "avg_hours": round(sum(h) / len(h), 1), "max_hours": round(max(h), 1)}
            for (s, t), h in groups.items()]
    return sorted(rows, key=lambda r: (-r["max_hours"], r["state"], r["team"]))


def gap_radar(store: Store, brain: Brain | None = None, now: datetime | None = None) -> list[dict]:
    """Rows {request_type, reason_code, topic, count, avg_hours_in_queue, est_hours_saved}, biggest saving first.

    * POLICY_GAP rows are clustered by request_type + topic token; they count POLICY_GAP cases from the store AND (when a brain is
      given) POLICY_GAP precedents, because a past escalation is the same missing article.
    * Every other reason code that sent an OPEN case to a human gets a row per (request_type, reason_code) with topic "".
    * avg_hours_in_queue = mean hours-in-state of the OPEN cases in the row; a row with no open case uses the mean over ALL open cases.
    * est_hours_saved = count x avg_hours_in_queue: the queue time that one approved article / form change could remove if every
      one of those requests had been answered without a human. It is an estimate for prioritising, not a measurement."""
    cases = store.list_cases()
    open_cases = [c for c in cases if c.state in OPEN_STATES]
    portfolio = [hours_in_state(c, now) for c in open_cases]
    portfolio_avg = sum(portfolio) / len(portfolio) if portfolio else 0.0
    by_id = {c.id: c for c in cases}
    rows: list[dict] = []

    def row(rtype: str, reason: str, topic: str, count: int, case_ids: list[str]) -> None:
        hours = [hours_in_state(by_id[i], now) for i in case_ids if i in by_id and by_id[i].state in OPEN_STATES]
        avg = sum(hours) / len(hours) if hours else portfolio_avg
        rows.append({"request_type": rtype, "reason_code": reason, "topic": topic, "count": count,
                     "avg_hours_in_queue": round(avg, 1), "est_hours_saved": round(count * avg, 1)})

    items = [(c.classification.request_type, c.masked_text, c.id) for c in cases
             if ReasonCode.POLICY_GAP in c.reason_codes and c.classification]
    if brain is not None:
        items += [(p.request_type, p.summary, p.id) for p in brain.precedents() if ReasonCode.POLICY_GAP in p.reason_codes]
    corpus = [p.summary for p in brain.precedents()] if brain is not None else [i[1] for i in items]
    clustered: set[str] = set()
    for rtype, topic, refs in topic_clusters(items, corpus, min_items=2):
        row(rtype, ReasonCode.POLICY_GAP.value, topic, len(refs), refs)
        clustered.update(refs)
    leftovers = defaultdict(list)
    for rtype, _text, ref in items:
        if ref not in clustered:
            leftovers[rtype].append(ref)
    for rtype, refs in leftovers.items():
        row(rtype, ReasonCode.POLICY_GAP.value, "other", len(refs), refs)

    other: dict[tuple[str, str], list[str]] = defaultdict(list)
    for c in open_cases:
        rtype = c.classification.request_type if c.classification else "unknown"
        for code in c.reason_codes:
            if code != ReasonCode.POLICY_GAP:
                other[(rtype, code.value)].append(c.id)
    for (rtype, code), ids in other.items():
        row(rtype, code, "", len(ids), ids)
    return sorted(rows, key=lambda r: (-r["est_hours_saved"], -r["count"], r["request_type"], r["reason_code"], r["topic"]))


def cost_split(store: Store) -> dict:
    """Share of pipeline requests that used no LLM, the light tier only, or light + strong (seeded history excluded)."""
    cases = [c for c in store.list_cases() if not (c.classification and c.classification.model_used == "seed")]
    n = len(cases)
    buckets = Counter("no_llm" if not c.llm_tiers_used else "light_and_strong" if "strong" in c.llm_tiers_used else "light_only" for c in cases)
    pct = lambda k: round(100.0 * buckets[k] / n, 1) if n else 0.0  # noqa: E731
    return {"requests": n, "light_only_pct": pct("light_only"), "light_and_strong_pct": pct("light_and_strong"),
            "no_llm_pct": pct("no_llm"), "counts": {k: buckets[k] for k in ("no_llm", "light_only", "light_and_strong")}}
