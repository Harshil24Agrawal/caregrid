"""Dashboard numbers that are provably the list they link to.

ONE place defines every named view of the cases ("needs_action", "waiting_for_you", ...). `/api/cases?view=<name>` returns exactly the cases of that
view, and every tile, banner count and "See all N" on the dashboard is `len(view)` computed here from `visible_cases` for the viewer. So a number can
never count a case the viewer cannot open, and clicking it lists exactly that many rows. Knowledge and audit tiles use the same predicates as the
lists they open (lint findings by code, open policy updates, audit events visible to the viewer).
"""
from __future__ import annotations

from datetime import datetime

from caregrid.insights.story import bucket
from caregrid.models import Case, Role, State, User
from caregrid.rbac import can_approve
from caregrid.workflow.decisions import DECIDABLE

OPEN = {State.PROPOSED, State.NEEDS_INFO, State.IN_REVIEW, State.ESCALATED}
WITH_REVIEWERS = {State.IN_REVIEW, State.ESCALATED}
OVERDUE_HOURS = 24.0
APPROVERS = {Role.TEAM_SPECIALIST, Role.OPS_MANAGER, Role.SENIOR_REVIEWER}
DENIAL_EVENTS = ("review_denied", "pr_denied", "reset_denied", "record_lookup_denied")


def state_age_hours(case: Case, now: datetime | None = None) -> float:
    """Hours since the case entered its current state."""
    now = now or datetime.now()
    entered = next((ts for st, ts in reversed(case.state_history) if st == case.state), case.created_at)
    return round((now - entered).total_seconds() / 3600, 1)


def case_views(user: User, cases: list[Case], now: datetime | None = None) -> dict[str, list[Case]]:
    """Every named view over the cases this viewer may open."""
    now = now or datetime.now()
    risky = ("high", "critical")
    return {
        "all": list(cases),
        "queue": [c for c in cases if c.state in OPEN],
        "needs_action": [c for c in cases if bucket(c, user) == "action"],
        "with_reviewers": [c for c in cases if c.state in WITH_REVIEWERS],
        "answered_auto": [c for c in cases if c.routing == "auto"],
        "done": [c for c in cases if bucket(c, user) == "done"],
        "waiting_for_you": [c for c in cases if c.state in DECIDABLE and can_approve(user, c)],
        "forwarded_today": [c for c in cases if c.forwarded_at and 0 <= (now - c.forwarded_at).total_seconds() < 24 * 3600],      # "today" = the last 24 hours
        "high_risk": [c for c in cases if c.state in OPEN and c.rules and c.rules.risk.value in risky],
        "overdue": [c for c in cases if c.state in WITH_REVIEWERS and state_age_hours(c, now) > OVERDUE_HOURS],
    }


def _tile(key: str, label: str, count: int, cap: str, href: str) -> dict:
    return {"key": key, "label": label, "count": count, "cap": cap, "href": href}


def tiles_for(user: User, views: dict, prs_open: int, lint_counts: dict, audit_counts: dict) -> list[dict]:
    n = lambda k: len(views[k])                                                   # noqa: E731
    case_href = lambda k: f"case.html?view={k}"                                    # noqa: E731
    if user.role == Role.OPS_EMPLOYEE:
        return [_tile("needs_action", "Needs your action", n("needs_action"), "add details or send it on", case_href("needs_action")),
                _tile("with_reviewers", "With reviewers", n("with_reviewers"), "a person is deciding", case_href("with_reviewers")),
                _tile("answered_auto", "Answered automatically", n("answered_auto"), "no person needed", case_href("answered_auto")),
                _tile("done", "Done", n("done"), "finished", case_href("done"))]
    if user.role == Role.KNOWLEDGE_OWNER:
        return [_tile("prs", "Policy updates waiting", prs_open, "for your decision", "knowledge.html?tab=prs"),
                _tile("conflicts", "Conflicts", lint_counts.get("CONTRADICTION", 0), "policies that disagree", "knowledge.html?tab=lint&code=CONTRADICTION"),
                _tile("gaps", "Gaps", lint_counts.get("ESCALATION_HOTSPOT", 0), "topics without a policy", "knowledge.html?tab=lint&code=ESCALATION_HOTSPOT")]
    if user.role == Role.AUDITOR:
        return [_tile("blocked", "Blocked attempts", audit_counts["blocked"], "stopped by the safety check", "audit.html?event=guard_blocked"),
                _tile("reveals", "Reveals", audit_counts["reveals"], "personal details shown", "audit.html?event=record_revealed"),
                _tile("denials", "Denials", audit_counts["denials"], "refused actions", "audit.html?event=" + ",".join(DENIAL_EVENTS))]
    return [_tile("waiting_for_you", "Waiting for you", n("waiting_for_you"), "you can approve these", case_href("waiting_for_you")),
            _tile("forwarded_today", "Forwarded today", n("forwarded_today"), "sent to a team in the last 24 h", case_href("forwarded_today")),
            _tile("high_risk", "High risk", n("high_risk"), "open cases", case_href("high_risk")),
            _tile("overdue", "Overdue", n("overdue"), f"in review over {int(OVERDUE_HOURS)} h", case_href("overdue"))]


def banner_for(user: User, views: dict, tiles: dict) -> dict:
    """The single most important action for THIS role, with the number it shows and the list it opens."""
    def b(tone, count, text, button, href):
        return {"tone": tone, "count": count, "text": text, "button": button, "href": href}

    plural = lambda c, one, many: f"{c} {one if c == 1 else many}"                  # noqa: E731
    if user.role == Role.OPS_EMPLOYEE:
        na, wr = tiles["needs_action"]["count"], tiles["with_reviewers"]["count"]
        if na:
            return b("amber", na, f"{plural(na, 'request needs', 'requests need')} your action: add the missing details or send it to the team.", "Do it now", tiles["needs_action"]["href"])
        if wr:
            return b("green", wr, f"Nothing for you to do. {plural(wr, 'request is', 'requests are')} with reviewers.", "See them", tiles["with_reviewers"]["href"])
        return b("green", 0, "You have no open requests. Submit one to see it flow through.", "New request", "intake.html")
    if user.role == Role.KNOWLEDGE_OWNER:
        for key, text, one, many in (("prs", "waiting for your decision.", "policy update", "policy updates"), ("conflicts", "to resolve.", "policy conflict", "policy conflicts"),
                                     ("gaps", "without an approved policy.", "knowledge gap", "knowledge gaps")):
            t = tiles[key]
            if t["count"]:
                return b("red" if key == "conflicts" else "amber", t["count"], f"{plural(t['count'], one, many)} {text}", "Open", t["href"])
        return b("green", 0, "The Second Brain is in good shape: no conflicts, gaps or policy updates waiting.", "Open Knowledge", "knowledge.html")
    if user.role == Role.AUDITOR:
        t = tiles["blocked"]
        if t["count"]:
            return b("amber", t["count"], f"{plural(t['count'], 'blocked attempt', 'blocked attempts')} in the log.", "See them", t["href"])
        return b("green", 0, "No blocked attempts in the log.", "Open audit", "audit.html")
    wf = views["waiting_for_you"]
    if wf:
        top = max(wf, key=lambda c: ({"critical": 4, "high": 3, "medium": 2, "low": 1}.get(c.rules.risk.value if c.rules else "low", 0), c.created_at.timestamp() * -1))
        risk = top.rules.risk.value if top.rules else "low"
        return b("red" if risk in ("high", "critical") else "amber", len(wf),
                 f"{plural(len(wf), 'case is', 'cases are')} waiting for you to approve. Highest risk: {top.id} ({risk} risk).", f"Review {len(wf)}", tiles["waiting_for_you"]["href"])
    if views["overdue"]:
        return b("amber", len(views["overdue"]), f"Nothing for you to approve, but {plural(len(views['overdue']), 'case is', 'cases are')} overdue with other reviewers.", "See them", tiles["overdue"]["href"])
    return b("green", 0, "Nothing is waiting for you. New requests that need a person will show up here.", "See all cases", "case.html")


def audit_counts(events: list) -> dict:
    kinds = [e.event for e in events]
    return {"blocked": kinds.count("guard_blocked"), "reveals": kinds.count("record_revealed"), "denials": sum(kinds.count(k) for k in DENIAL_EVENTS)}


def dashboard(user: User, cases: list[Case], prs_open: int, lint_counts: dict, events: list) -> dict:
    views = case_views(user, cases)
    tiles = tiles_for(user, views, prs_open, lint_counts, audit_counts(events))
    by_key = {t["key"]: t for t in tiles}
    queue = views["queue"]
    return {"role": user.role.value, "visible": len(cases), "tiles": tiles, "banner": banner_for(user, views, by_key),
            "queue": {"count": len(queue), "href": "case.html?view=queue"}, "views": {k: [c.id for c in v] for k, v in views.items()}}
