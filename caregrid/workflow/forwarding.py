"""The requester confirms the handoff (CONTRACTS section 8).

A case that a person must decide, with NO safety override (clinical, injection / access denied, sensitive, account-specific), stops at PROPOSED:
CareGrid suggests where it goes and why, and the REQUESTER either sends it to the team (IN_REVIEW, audited `forwarded`) or withdraws it (CLOSED,
audited `withdrawn`). A forward is not a review: no trust update, no precedent. An ops manager may act for a requester. The optional note is
masked before it is stored.
"""
from __future__ import annotations

from datetime import datetime

from caregrid.ingest.anonymize import anonymize
from caregrid.models import Case, Role, State, User
from caregrid.store import Store
from caregrid.workflow.audit import log
from caregrid.workflow.routing import set_state

FORWARDABLE = {State.PROPOSED}


class NotForwardableError(ValueError):
    """The case is not waiting for the requester's confirmation (already sent, decided or withdrawn)."""


def can_confirm(user: User, case: Case) -> bool:
    return user.id == case.requester.id or user.role == Role.OPS_MANAGER


def _guard(case: Case | None, user: User, states: set[State], action: str, store: Store) -> Case:
    if case is None:
        raise KeyError("unknown case")
    if not can_confirm(user, case):
        log(store, "review_denied", user, case.id, action=action, role=user.role.value, reason="only the requester (or an ops manager) confirms", state=case.state.value)
        raise PermissionError(f"{user.role.value} may not {action} {case.id}")
    if case.state not in states or case.routing != "human":
        raise NotForwardableError(f"{case.id} is {case.state.value}; it is not waiting for the requester")
    return case


def forward_case(case_id: str, user: User, note: str, store: Store) -> Case:
    case = _guard(store.get_case(case_id), user, FORWARDABLE, "forward", store)
    masked_note = anonymize(note.strip()[:500])[0] if note and note.strip() else ""
    before = case.state
    set_state(case, State.IN_REVIEW)
    case.forwarded_by, case.forwarded_at, case.forward_note = user.name, datetime.now(), masked_note
    store.save_case(case)
    log(store, "forwarded", user, case.id, team=case.assigned_team, note=bool(masked_note), on_behalf=user.id != case.requester.id)
    log(store, "state_changed", user, case.id, **{"from": before.value, "to": case.state.value, "by": "requester"})
    return case


def withdraw_case(case_id: str, user: User, store: Store) -> Case:
    case = _guard(store.get_case(case_id), user, FORWARDABLE | {State.NEEDS_INFO}, "withdraw", store)
    before = case.state
    set_state(case, State.CLOSED)
    store.save_case(case)
    log(store, "withdrawn", user, case.id, from_state=before.value)
    log(store, "state_changed", user, case.id, **{"from": before.value, "to": case.state.value, "by": "requester"})
    return case
