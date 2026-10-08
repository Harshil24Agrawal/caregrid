"""Patient record: the masked Health ID, plan, consent flags and a timeline of the cases linked to the patient, filtered by role.

Who sees what (RBAC before retrieval; an unauthorized caller gets exactly what an unknown id gets, so there is no existence oracle):
  ops_employee      the patient only if one of THEIR OWN cases is linked; the timeline holds only their own cases
  team_specialist   the patient only if a case of their team is linked (or the patient consented to cross-team sharing); the timeline holds the
                    cases of their own team, plus other teams' cases only when consent.share_across_teams is true
  ops_manager,
  senior_reviewer   the full timeline
  auditor           the record header and the access log (who viewed or revealed this record), no timeline
  knowledge_owner   no access
Personal details (name, phone, date of birth) are never in the record. A senior reviewer may reveal them FOR ONE LINKED CASE with a mandatory
reason; the reveal is audited (record_revealed, reason masked) and the values are returned once, never stored. Every view is audited
(record_viewed). Audit details hold keys, roles and counts only.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from caregrid import health_id
from caregrid.ingest.anonymize import anonymize
from caregrid.insights.explain import one_liner
from caregrid.models import Case, Role, User
from caregrid.store import Store
from caregrid.workflow.audit import log

MIN_REASON_CHARS = 10
ACCESS_EVENTS = ("record_viewed", "record_revealed")


MAX_LOOKUPS_PER_MINUTE = 10


class LookupRateLimited(Exception):
    """More than MAX_LOOKUPS_PER_MINUTE lookups by Health ID from one user in the last minute (guessing IDs)."""


class BadHealthId(ValueError):
    """The text looks like a Health ID but its checksum is wrong."""


def resolve(ref: str, data_dir: Path) -> dict | None:
    """The member for a profile key (PRF-2xxx). A Health ID is never a reference: it only goes through `member_for_id` (POST body)."""
    ref = (ref or "").strip()
    return health_id.member_by_key(ref, data_dir) if ref.startswith("PRF-2") else None


def member_for_id(text: str, data_dir: Path) -> dict | None:
    """The member for a typed Health ID (any typing). A well-formed ID with a wrong checksum raises BadHealthId."""
    found = health_id.find(health_id.normalise_text(text or ""))
    if not found:
        return None
    normal = health_id.format_id(found[0][2])
    if not health_id.is_valid(normal):
        raise BadHealthId()
    return health_id.member_by_id(normal, data_dir)


def linked_cases(member: dict, store: Store) -> list[Case]:
    return sorted((c for c in store.list_cases() if member["profile_key"] in c.related.get("profile", [])), key=lambda c: c.created_at, reverse=True)


def _timeline_cases(member: dict, viewer: User, cases: list[Case]) -> list[Case]:
    share = bool(member.get("consent", {}).get("share_across_teams"))
    if viewer.role == Role.OPS_EMPLOYEE:
        return [c for c in cases if c.requester.id == viewer.id]
    if viewer.role == Role.TEAM_SPECIALIST:
        return [c for c in cases if share or (viewer.team is not None and c.assigned_team == viewer.team)]
    if viewer.role in (Role.OPS_MANAGER, Role.SENIOR_REVIEWER):
        return list(cases)
    return []


def authorized(member: dict, viewer: User, cases: list[Case]) -> bool:
    if viewer.role in (Role.OPS_MANAGER, Role.SENIOR_REVIEWER, Role.AUDITOR):
        return True
    if viewer.role == Role.TEAM_SPECIALIST:
        return any(viewer.team is not None and c.assigned_team == viewer.team for c in cases)
    if viewer.role == Role.OPS_EMPLOYEE:
        return any(c.requester.id == viewer.id for c in cases)
    return False


def _row(c: Case) -> dict:
    return {"case_id": c.id, "request_type": c.classification.request_type if c.classification else "unknown", "state": c.state.value,
            "team": c.assigned_team, "date": c.created_at.date().isoformat(), "summary": one_liner(c)}


def access_log(member: dict, store: Store) -> list[dict]:
    rows = [e for e in store.list_audit() if e.event in ACCESS_EVENTS and e.details.get("profile") == member["profile_key"]]
    return [{"ts": e.ts.isoformat(), "event": e.event, "actor_id": e.actor_id, "actor_role": e.actor_role, "case_id": e.case_id}
            for e in sorted(rows, key=lambda e: e.ts, reverse=True)]


def _denied(store: Store, viewer: User, via: str, reason: str) -> None:
    """record_lookup_denied: role, how the record was asked for and why it was refused. Never the id or the ref."""
    log(store, "record_lookup_denied", viewer, None, role=viewer.role.value, via=via, reason=reason)


def lookup(health_id_text: str, viewer: User, store: Store, data_dir: Path, audit: bool = True) -> dict | None:
    """Open a record by a typed Health ID (the ID travels in a POST body, never in a URL). None = unknown or not allowed (same answer);
    a wrong checksum raises BadHealthId. Refusals are audited without the ID."""
    if audit:
        since = datetime.now() - timedelta(minutes=1)
        if len([e for e in store.list_audit() if e.event == "record_lookup" and e.actor_id == viewer.id and e.ts >= since]) >= MAX_LOOKUPS_PER_MINUTE:
            log(store, "lookup_rate_limited", viewer, None, role=viewer.role.value, limit=MAX_LOOKUPS_PER_MINUTE)
            raise LookupRateLimited()
        log(store, "record_lookup", viewer, None, role=viewer.role.value)             # a lookup happened: no ID, no outcome
    try:
        member = member_for_id(health_id_text, data_dir)
    except BadHealthId:
        if audit:
            _denied(store, viewer, "id", "checksum")
        raise
    out = _record_for(member, viewer, store, audit)
    if out is None and audit:
        _denied(store, viewer, "id", "unknown_or_denied")
    return out


def record(ref: str, viewer: User, store: Store, data_dir: Path, audit: bool = True) -> dict | None:
    """The record for a profile key as this viewer may see it, or None (the caller answers 404, same as for an unknown ref)."""
    out = _record_for(resolve(ref, data_dir), viewer, store, audit)
    if out is None and audit:
        _denied(store, viewer, "ref", "unknown_or_denied")
    return out


def _record_for(member: dict | None, viewer: User, store: Store, audit: bool) -> dict | None:
    if member is None:
        return None
    cases = linked_cases(member, store)
    if not authorized(member, viewer, cases):
        return None
    rows = [_row(c) for c in _timeline_cases(member, viewer, cases)]
    detail = viewer.role != Role.OPS_EMPLOYEE              # plan and consent flags: team specialists and above (an ops employee sees the masked ID and own cases only)
    out = {
        "ref": member["profile_key"], "masked_id": health_id.masked(member["health_id"]), "plan": member.get("plan") if detail else None,
        "consent": dict(member.get("consent", {})) if detail else None, "timeline": rows,
        "personal": {"hidden": True, "reveal_allowed": viewer.role == Role.SENIOR_REVIEWER,
                     "note": "Name, phone and date of birth are never shown by default."},
        "access_log": access_log(member, store) if viewer.role == Role.AUDITOR else None,
    }
    if audit:
        log(store, "record_viewed", viewer, None, profile=member["profile_key"], role=viewer.role.value, cases=len(rows))
    return out


def case_patient(case: Case, viewer: User, store: Store, data_dir: Path) -> dict | None:
    """{ref, masked_id} for the patient linked to this case when the viewer may open that patient record, else None.
    An ops employee only sees the masked ID of what was TYPED in the request (valid or not, known or not), with no ref and no link: the
    request response and the case view are identical whether or not the ID matched a patient (linking happens silently)."""
    if viewer.role == Role.OPS_EMPLOYEE:
        first = next((e for e in store.list_audit(case.id) if e.event == "request_received"), None)
        typed = first.details.get("health_id_masked", []) if first else []
        return {"ref": None, "masked_id": typed[0]} if typed else None
    for key in case.related.get("profile", []):
        member = health_id.member_by_key(key, data_dir)
        if member is not None and authorized(member, viewer, linked_cases(member, store)):
            return {"ref": key, "masked_id": health_id.masked(member["health_id"])}
    return None


def patients_for(viewer: User, store: Store, data_dir: Path) -> list[dict]:
    """Every patient this viewer may open (masked), for the Patients page."""
    out = []
    for m in health_id._members(data_dir):
        cases = linked_cases(m, store)
        if cases and authorized(m, viewer, cases):
            out.append({"ref": m["profile_key"], "masked_id": health_id.masked(m["health_id"]),
                        "plan": m.get("plan") if viewer.role != Role.OPS_EMPLOYEE else None,
                        "cases": len(_timeline_cases(m, viewer, cases))})
    return out


MAX_REVEALS_PER_HOUR = 5
MIN_REASON_WORDS = 3


class RateLimited(Exception):
    """More than MAX_REVEALS_PER_HOUR reveals by one user in the last hour."""


def _real_words(reason: str) -> int:
    """Words made of letters with at least two different letters ("aaaa" and "xxx xxx xxx" are filler)."""
    return sum(1 for w in re.findall(r"[^\W\d_]{2,}", reason) if len(set(w.lower())) >= 2)


def reveal(ref: str, viewer: User, case_id: str, reason: str, store: Store, data_dir: Path) -> dict:
    """Name, phone and date of birth for one linked case. Senior reviewers only; a real reason (3+ words) is mandatory and audited (masked);
    at most 5 reveals per user per hour (429, audited as reveal_rate_limited). `ref` is a profile key, never a Health ID."""
    if viewer.role != Role.SENIOR_REVIEWER:
        raise PermissionError("only a senior reviewer may reveal personal details")
    member = resolve(ref, data_dir)
    cases = linked_cases(member, store) if member else []
    if member is None or not authorized(member, viewer, cases):
        _denied(store, viewer, "reveal", "unknown_or_denied")
        raise KeyError(ref)
    if case_id not in {c.id for c in cases}:
        raise ValueError("Pick one of this patient's linked cases to reveal details for.")
    reason = " ".join((reason or "").split())
    if len(reason) < MIN_REASON_CHARS or _real_words(reason) < MIN_REASON_WORDS:
        raise ValueError("Please give a real reason: at least 3 words that say why you need these details.")
    since = datetime.now() - timedelta(hours=1)
    recent = [e for e in store.list_audit() if e.event == "record_revealed" and e.actor_id == viewer.id and e.ts >= since]
    if len(recent) >= MAX_REVEALS_PER_HOUR:
        log(store, "reveal_rate_limited", viewer, case_id, role=viewer.role.value, limit=MAX_REVEALS_PER_HOUR)
        raise RateLimited()
    masked_reason = anonymize(reason[:200], None)[0]
    log(store, "record_revealed", viewer, case_id, profile=member["profile_key"], reason=masked_reason)
    return {"ref": member["profile_key"], "case_id": case_id, "name": member["name"], "phone": member["phone"], "dob": member["dob"],
            "stored": False, "note": "Shown once for this case. Nothing is stored; the reveal is in the audit log."}
