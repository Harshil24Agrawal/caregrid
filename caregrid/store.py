"""Store interface and SQLite implementation (JSON blobs per table)."""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Protocol

from caregrid import config
from caregrid.models import AuditEvent, Case, Communication, KnowledgePR, TrustRecord


class Store(Protocol):
    def save_case(self, case: Case) -> None: ...
    def get_case(self, case_id: str) -> Case | None: ...
    def list_cases(self, **filters) -> list[Case]: ...
    def append_audit(self, ev: AuditEvent) -> None: ...
    def list_audit(self, case_id: str | None = None) -> list[AuditEvent]: ...
    def get_trust(self, request_type: str) -> TrustRecord: ...
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

_REQ_ID = re.compile(r"^REQ-(\d+)$")


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
        self._exec(
            "INSERT OR REPLACE INTO cases (id, created_at, state, data) VALUES (?,?,?,?)",
            (case.id, case.created_at.isoformat(), case.state.value, case.model_dump_json()),
        )

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

    # -- audit (append-only)
    def append_audit(self, ev: AuditEvent) -> None:
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
        self._exec(
            "INSERT OR REPLACE INTO comms (id, case_id, ts, data) VALUES (?,?,?,?)",
            (c.id, c.case_id, c.ts.isoformat(), c.model_dump_json()),
        )

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
