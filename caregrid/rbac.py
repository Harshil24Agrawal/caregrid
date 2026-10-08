"""Role-based access (CONTRACTS §12). Pure functions of (user, case): no I/O except visible_cases(), which asks the store.

| Role            | View                                                           | Approve                       |
| ops_employee    | OWN cases (requester.id), summary only                         | never                         |
| team_specialist | cases assigned to their team, every section (billing: view only)| risk LOW, own team            |
| ops_manager     | every case, every section                                       | risk LOW or MEDIUM            |
| senior_reviewer | every case, every section                                       | any risk                      |
| knowledge_owner | every case, summary only                                        | never (PRs only, not here)    |
| auditor         | every case, summary only (read-only)                            | never                         |

Separation of duties: nobody approves (or otherwise decides) a case they requested.
"""
from __future__ import annotations

from typing import Literal

from caregrid.models import Case, Risk, Role, User
from caregrid.store import Store

Section = Literal["summary", "profile", "billing", "logs", "full"]
SECTIONS: tuple[str, ...] = ("summary", "profile", "billing", "logs", "full")

_APPROVABLE_RISKS = {
    Role.TEAM_SPECIALIST: {Risk.LOW},
    Role.OPS_MANAGER: {Risk.LOW, Risk.MEDIUM},
    Role.SENIOR_REVIEWER: {Risk.LOW, Risk.MEDIUM, Risk.HIGH, Risk.CRITICAL},
}


def can_view(user: User, case: Case, section: Section) -> bool:
    if section not in SECTIONS:
        raise ValueError(f"unknown section {section!r} (expected one of {', '.join(SECTIONS)})")
    role = user.role
    if role == Role.OPS_EMPLOYEE:
        return case.requester.id == user.id and section == "summary"
    if role == Role.TEAM_SPECIALIST:
        return user.team is not None and case.assigned_team == user.team
    if role in (Role.OPS_MANAGER, Role.SENIOR_REVIEWER):
        return True
    if role in (Role.KNOWLEDGE_OWNER, Role.AUDITOR):
        return section == "summary"
    return False


def can_approve(user: User, case: Case) -> bool:
    if case.requester.id == user.id:                       # separation of duties
        return False
    risks = _APPROVABLE_RISKS.get(user.role)
    if not risks or case.rules is None or case.rules.risk not in risks:
        return False
    if user.role == Role.TEAM_SPECIALIST:
        return user.team is not None and case.assigned_team == user.team
    return True


def visible_cases(user: User, store: Store) -> list[Case]:
    """RBAC before retrieval: the store is only asked for what the role may see (summary level at least)."""
    return [c for c in store.list_cases() if can_view(user, c, "summary")]
