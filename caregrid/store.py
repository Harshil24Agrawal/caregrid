"""Store interface and SQLite implementation (JSON blobs per table)."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Protocol

from caregrid import config
from caregrid.ingest.anonymize import anonymize
from caregrid.ingest.leakscan import detect_pii
from caregrid.models import AuditEvent, Case, Communication, KnowledgePR, TrustRecord


class Store(Protocol):
    def save_case(self, case: Case) -> None: ...
    def get_case(self, case_id: str) -> Case | None: ...
    def list_cases(self, **filters) -> list[Case]: ...
    def append_audit(self, ev: AuditEvent) -> None: ...
    def list_audit(self, case_id: str | None = None) -> list[AuditEvent]: ...
    def get_trust(self, request_type: str) -> TrustRecord: ...
    def list_trust(self) -> list[TrustRecord]: ...
    def save_trust(self, t: TrustRecord) -> None: ...
    def save_pr(self, pr: KnowledgePR) -> None: ...
    def list_prs(self, status: str | None = None) -> list[KnowledgePR]: ...
    def save_comm(self, c: Communication) -> None: ...
    def list_comms(self, case_id: str | None = None) -> list[Communication]: ...
    def next_case_id(self) -> str: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases  (id TEXT PRIMARY KEY, created_at TEXT, state TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit  (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, ts TEXT, case_id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trust  (request_type TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prs    (id TEXT PRIMARY KEY, status TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS comms  (id TEXT PRIMARY KEY, case_id TEXT, ts TEXT, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_case ON audit(case_id);
CREATE INDEX IF NOT EXISTS comms_case ON comms(case_id);
"""

def new_audit_id() -> str:
    """Prefixed so an id such as 410102805984 can never be mistaken for a phone/ID number by the PII detectors."""
    return "AUD-" + uuid.uuid4().hex[:12]


_REQ_ID = re.compile(r"^REQ-(\d+)$")


def _remask(obj, types: set[str]):
    """Safety net: walk a dumped model, re-mask any string in which PII is still detected. Collects types only."""
    if isinstance(obj, str):
        found = detect_pii(obj)
        if not found:
            return obj
        types.update(found)
        return anonymize(obj, cueless=False)[0]
    if isinstance(obj, dict):
        return {k: _remask(v, types) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_remask(v, types) for v in obj]
    return obj


class SQLiteStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = str(path) if path is not None else str(config.DB_PATH)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # ":memory:" is per-connection, so keep one connection open in that case.
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # -- connection handling: one short-lived connection per operation (Streamlit-thread safe)
    def _conn(self):
        if self._mem is not None:
            return _Keep(self._mem)
        return closing(_Tx(sqlite3.connect(self.path)))

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._conn() as c:
            c.execute(sql, args)
            c.commit()

    def _all(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._conn() as c:
            return c.execute(sql, args).fetchall()

    # -- cases
    def save_case(self, case: Case) -> None:
        types: set[str] = set()
        dumped = _remask(json.loads(case.model_dump_json()), types)
        if types:
            case = Case.model_validate(dumped)
        self._exec(
            "INSERT OR REPLACE INTO cases (id, created_at, state, data) VALUES (?,?,?,?)",
            (case.id, case.created_at.isoformat(), case.state.value, case.model_dump_json()),
        )
        if types:
            self.append_audit(AuditEvent(
                id=new_audit_id(), ts=datetime.now(), case_id=case.id, actor_id="system", actor_role="system",
                event="pii_remasked", details={"pii_remasked": sorted(types), "source": "save_case"}))

    def get_case(self, case_id: str) -> Case | None:
        rows = self._all("SELECT data FROM cases WHERE id=?", (case_id,))
        return Case.model_validate_json(rows[0][0]) if rows else None

    def list_cases(self, **filters) -> list[Case]:
        """Filter on top-level Case attributes by equality (enums compare by value). Newest first."""
        cases = [Case.model_validate_json(r[0]) for r in self._all("SELECT data FROM cases ORDER BY created_at DESC, id DESC")]
        for key, want in filters.items():
            want = getattr(want, "value", want)
            cases = [c for c in cases if getattr(getattr(c, key), "value", getattr(c, key)) == want]
        return cases

    def next_case_id(self) -> str:
        nums = [int(m.group(1)) for (i,) in self._all("SELECT id FROM cases") if (m := _REQ_ID.match(i))]
        return f"REQ-{max(nums, default=0) + 1:04d}"

    def shift_time(self, case_id: str, delta) -> None:
        """Move a case, its audit events and its communications back in time by `delta` (a timedelta). Used only to age SEEDED cases that
        were produced by the real pipeline, so queue ageing and timelines look like real history."""
        case = self.get_case(case_id)
        if case is None:
            raise KeyError(case_id)
        case.created_at -= delta
        case.state_history = [(st, ts - delta) for st, ts in case.state_history]
        if case.forwarded_at:
            case.forwarded_at -= delta
        self._exec("INSERT OR REPLACE INTO cases (id, created_at, state, data) VALUES (?,?,?,?)",
                   (case.id, case.created_at.isoformat(), case.state.value, case.model_dump_json()))
        for seq, data in self._all("SELECT seq, data FROM audit WHERE case_id=?", (case_id,)):
            ev = AuditEvent.model_validate_json(data)
            ev = ev.model_copy(update={"ts": ev.ts - delta})
            self._exec("UPDATE audit SET ts=?, data=? WHERE seq=?", (ev.ts.isoformat(), ev.model_dump_json(), seq))
        for cid, data in self._all("SELECT id, data FROM comms WHERE case_id=?", (case_id,)):
            c = Communication.model_validate_json(data)
            c = c.model_copy(update={"ts": c.ts - delta})
            self._exec("UPDATE comms SET ts=?, data=? WHERE id=?", (c.ts.isoformat(), c.model_dump_json(), cid))

    # -- audit (append-only)
    def append_audit(self, ev: AuditEvent) -> None:
        types: set[str] = set()
        details = _remask(dict(ev.details), types)
        if types:
            ev = ev.model_copy(update={"details": {**details, "pii_remasked": sorted(types)}})
        self._exec(
            "INSERT INTO audit (id, ts, case_id, data) VALUES (?,?,?,?)",
            (ev.id, ev.ts.isoformat(), ev.case_id, ev.model_dump_json()),
        )

    def list_audit(self, case_id: str | None = None) -> list[AuditEvent]:
        if case_id is None:
            rows = self._all("SELECT data FROM audit ORDER BY seq")
        else:
            rows = self._all("SELECT data FROM audit WHERE case_id=? ORDER BY seq", (case_id,))
        return [AuditEvent.model_validate_json(r[0]) for r in rows]

    # -- trust
    def get_trust(self, request_type: str) -> TrustRecord:
        rows = self._all("SELECT data FROM trust WHERE request_type=?", (request_type,))
        if rows:
            return TrustRecord.model_validate_json(rows[0][0])
        return TrustRecord(request_type=request_type, updated_at=datetime.now())

    def save_trust(self, t: TrustRecord) -> None:
        self._exec("INSERT OR REPLACE INTO trust (request_type, data) VALUES (?,?)", (t.request_type, t.model_dump_json()))

    def list_trust(self) -> list[TrustRecord]:
        return [TrustRecord.model_validate_json(r[0]) for r in self._all("SELECT data FROM trust ORDER BY request_type")]

    # -- knowledge PRs
    def save_pr(self, pr: KnowledgePR) -> None:
        self._exec("INSERT OR REPLACE INTO prs (id, status, data) VALUES (?,?,?)", (pr.id, pr.status, pr.model_dump_json()))

    def list_prs(self, status: str | None = None) -> list[KnowledgePR]:
        if status is None:
            rows = self._all("SELECT data FROM prs ORDER BY id")
        else:
            rows = self._all("SELECT data FROM prs WHERE status=? ORDER BY id", (status,))
        return [KnowledgePR.model_validate_json(r[0]) for r in rows]

    # -- communications
    def save_comm(self, c: Communication) -> None:
        """Safety net for outgoing messages: NO exception. Raw contact details never reach the database; anything that still looks like PII
        is re-masked and the re-mask is audited (types only)."""
        types: set[str] = set()
        c = c.model_copy(update={"message": _remask(c.message, types), "recipient": _remask(c.recipient, types)})
        self._exec(
            "INSERT OR REPLACE INTO comms (id, case_id, ts, data) VALUES (?,?,?,?)",
            (c.id, c.case_id, c.ts.isoformat(), c.model_dump_json()),
        )
        if types:
            self.append_audit(AuditEvent(
                id=new_audit_id(), ts=datetime.now(), case_id=c.case_id, actor_id="system", actor_role="system",
                event="pii_remasked", details={"pii_remasked": sorted(types), "source": "save_comm", "comm": c.id}))

    def list_comms(self, case_id: str | None = None) -> list[Communication]:
        if case_id is None:
            rows = self._all("SELECT data FROM comms ORDER BY ts, id")
        else:
            rows = self._all("SELECT data FROM comms WHERE case_id=? ORDER BY ts, id", (case_id,))
        return [Communication.model_validate_json(r[0]) for r in rows]

    # -- maintenance
    def wipe(self) -> None:
        with self._conn() as c:
            for t in ("cases", "audit", "trust", "prs", "comms"):
                c.execute(f"DELETE FROM {t}")
            c.commit()


class _Tx:
    """sqlite3 connection wrapper so `closing(...)` yields an object usable as `with ... as c`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def __getattr__(self, name):
        return getattr(self._c, name)

    def close(self) -> None:
        self._c.close()


class _Keep:
    """Context manager over the shared in-memory connection (never closed)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._c

    def __exit__(self, *exc) -> None:
        return None
