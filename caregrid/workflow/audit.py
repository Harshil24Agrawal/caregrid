"""Audit log helper (CLAUDE.md rule 9). Details carry ids, codes and counts, never raw text or personal data."""
from __future__ import annotations

from datetime import datetime

from caregrid.models import AuditEvent, User
from caregrid.store import Store, new_audit_id


def log(store: Store, event: str, actor: User | None, case_id: str | None, ts: datetime | None = None, **details) -> None:
    store.append_audit(AuditEvent(
        id=new_audit_id(), ts=ts or datetime.now(), case_id=case_id,
        actor_id=actor.id if actor else "system", actor_role=actor.role.value if actor else "system", event=event, details=details))
