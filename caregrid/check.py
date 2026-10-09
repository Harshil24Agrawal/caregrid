"""`cli check`: the BUILD_PLAN midnight checklist, headless. One PASS/FAIL line per item; any FAIL -> exit code 1.

It leaves the machine in the clean demo state (it runs `reset` twice). Everything else runs on throw-away copies / in-memory stores.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from caregrid import config
from caregrid.knowledge.brain import Brain
from caregrid.llm import MockLLM
from caregrid.models import Case
from caregrid.seed import load_users, seed_trust
from caregrid.store import SQLiteStore, Store

_TIME_KEYS = {"created_at", "state_history", "ts", "updated_at", "decided_at", "forwarded_at"}
_ISO = re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?")
NEVER_CITED = (("KA-60", None), ("KA-12", 2))         # (page id, version or None = any): a draft and an expired version


def _normalise(value):
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in sorted(value.items()) if k not in _TIME_KEYS}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return _ISO.sub("T", value) if isinstance(value, str) else value


def brain_hash(brain_dir: Path) -> str:
    """Every page file (log.md is a timestamped journal, so it is left out)."""
    h = hashlib.sha256()
    for path in sorted(p for p in brain_dir.rglob("*") if p.is_file() and p.name != "log.md"):
        h.update(path.relative_to(brain_dir).as_posix().encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:16]


def cases_hash(store: Store) -> str:
    cases = sorted((json.loads(c.model_dump_json()) for c in store.list_cases()), key=lambda c: c["id"])
    return hashlib.sha256(json.dumps(_normalise(cases), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def audit_gaps(store: Store, cases: list[Case]) -> list[str]:
    """Cases whose audit trail does not run request_received -> ... -> a change into their final state."""
    bad = []
    for c in cases:
        events = store.list_audit(c.id)
        reached = {e.details.get("to") for e in events if e.event == "state_changed"}
        if not events or events[0].event != "request_received":
            bad.append(f"{c.id}: first audit event is {events[0].event if events else 'missing'}")
        elif c.state.value not in reached:
            bad.append(f"{c.id}: no state_changed into {c.state.value}")
    return bad


def forbidden_citations(cases: list[Case]) -> list[str]:
    hits = []
    for c in cases:
        cites = list(c.citations_considered) + list(c.proposal.citations if c.proposal else [])
        for cit in cites:
            for pid, ver in NEVER_CITED:
                if cit.page_id == pid and (ver is None or cit.version == ver):
                    hits.append(f"{c.id} cites {pid}" + (f" v{ver}" if ver else ""))
    return sorted(set(hits))


def _run_eval_files(brain_dir: Path, data_dir: Path, files: list[Path]) -> tuple[Store, list[Case]]:
    """Every eval / held-out row through the real pipeline (mock LLM) on a COPY of the brain; returns the throw-away store and its cases."""
    from caregrid.reasoning.pipeline import run

    tmp_brain = Path(tempfile.mkdtemp(prefix="cg_check_")) / "brain"
    shutil.copytree(brain_dir, tmp_brain)
    brain, store, llm, users = Brain(tmp_brain), SQLiteStore(":memory:"), MockLLM(), load_users(data_dir)
    seed_trust(store, data_dir)
    for f in files:
        with open(f, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                run(row["text"], users[row["requester_id"]], store, brain, llm)
    return store, store.list_cases()


def _pytest_summary() -> tuple[bool, str]:
    from dotenv import dotenv_values

    env = {k: v for k, v in os.environ.items() if k not in dotenv_values(config.ROOT / ".env")}      # config loaded .env into os.environ: undo that
    env.update({"LLM_PROVIDER": "mock", "CAREGRID_NO_DOTENV": "1"})
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=config.ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    failed = [ln.split(" - ")[0] for ln in lines if ln.startswith("FAILED")]
    return proc.returncode == 0, (lines[-1] if lines else "no pytest output") + (f"; {', '.join(failed[:5])}" if failed else "")


def story_violations(store, brain, users) -> list[str]:
    """Every case tells a coherent story: a non-empty problem, a decision that never says "0 more details", flags that agree with the reason codes
    (a safety reason code shows a flag, no reason code shows none) and a timeline of at least two entries."""
    from caregrid.insights.story import story, timeline
    from caregrid.models import ReasonCode

    viewer = next(u for u in users.values() if u.role.value == "senior_reviewer")
    names = {u.id: u.name for u in users.values()}
    safety = {"CLINICAL", "ACCOUNT_SPECIFIC", "SENSITIVE", "ACCESS_DENIED"}
    out = []
    for c in store.list_cases():
        s = story(c, brain, viewer)
        flags = next((r for r in s["checks"] if r["label"] == "Flags"), None)
        wants_flag = bool({r.value for r in c.reason_codes} & safety)
        if not s["problem"].strip():
            out.append(f"{c.id}: empty problem")
        if "0 more detail" in s["decision"]["text"] or "send 0" in s["decision"]["text"]:
            out.append(f"{c.id}: decision says '0 more details'")
        if c.classification and c.classification.model_used != "guard" and wants_flag != bool(flags and flags["status"] != "ok"):
            out.append(f"{c.id}: flags disagree with reason codes {sorted(r.value for r in c.reason_codes)}")
        if len(timeline(c, store.list_audit(c.id), names, brain)) < 2:
            out.append(f"{c.id}: timeline has fewer than 2 entries")
    return out


def dashboard_audit(client, store, brain, users) -> list[str]:
    """Recompute every dashboard number from the raw cases (rbac and plain state tests, not the dashboard module) and compare with /api/dashboard."""
    from datetime import datetime, timedelta

    from caregrid.knowledge.lint import lint
    from caregrid.rbac import can_approve, can_view

    now, cases, bad = datetime.now(), store.list_cases(), []
    done_states, open_states, wait_states = {"answered", "approved", "actioned", "notified", "closed", "rejected"}, {"proposed", "needs_info", "in_review", "escalated"}, {"in_review", "escalated"}
    events = store.list_audit()
    for uid, u in users.items():
        seen = [c for c in cases if can_view(u, c, "summary")]
        want: dict[str, int] = {}
        role = u.role.value
        if role == "ops_employee":
            want = {"needs_action": sum((c.state.value == "needs_info" and c.requester.id == u.id) or (c.state.value == "proposed" and c.requester.id == u.id) for c in seen),
                    "with_reviewers": sum(c.state.value in wait_states for c in seen), "answered_auto": sum(c.routing == "auto" for c in seen),
                    "done": sum(c.state.value in done_states for c in seen)}
        elif role == "knowledge_owner":
            found = [f for f in lint(brain, store) if f.severity != "info"]
            want = {"prs": len(store.list_prs("open")), "conflicts": sum(f.code == "CONTRADICTION" for f in found), "gaps": sum(f.code == "ESCALATION_HOTSPOT" for f in found)}
        elif role == "auditor":
            kinds = [e.event for e in events]
            want = {"blocked": kinds.count("guard_blocked"), "reveals": kinds.count("record_revealed"),
                    "denials": sum(kinds.count(k) for k in ("review_denied", "pr_denied", "reset_denied", "record_lookup_denied"))}
        else:
            def entered(c):
                return next((ts for st, ts in reversed(c.state_history) if st == c.state), c.created_at)

            want = {"waiting_for_you": sum(c.state.value in wait_states and can_approve(u, c) for c in seen),
                    "forwarded_today": sum(bool(c.forwarded_at) and timedelta(0) <= now - c.forwarded_at < timedelta(hours=24) for c in seen),
                    "high_risk": sum(c.state.value in open_states and c.rules is not None and c.rules.risk.value in ("high", "critical") for c in seen),
                    "overdue": sum(c.state.value in wait_states and now - entered(c) > timedelta(hours=24) for c in seen)}
        got = client.get("/api/dashboard", headers={"X-CareGrid-User": uid}).json()
        have = {t["key"]: t["count"] for t in got["tiles"]}
        bad += [f"{uid}/{k}: tile {have.get(k)} != recomputed {v}" for k, v in want.items() if have.get(k) != v]
        if got["visible"] != len(seen):
            bad.append(f"{uid}/visible: {got['visible']} != {len(seen)}")
        if got["queue"]["count"] != sum(c.state.value in open_states for c in seen):
            bad.append(f"{uid}/queue: {got['queue']['count']} != recomputed")
    return bad


def state_rule_violations(store) -> list[str]:
    """A1: a case's state always matches its rules. NEEDS_INFO means something is missing or invalid (and says what to send); a case with
    something missing never sits answered / proposed / in review (unless a safety override sent it straight to a person)."""
    from caregrid.models import State
    from caregrid.workflow.routing import safety_override

    out = []
    for c in store.list_cases():
        bad = bool(c.rules and (c.rules.missing_fields or c.rules.invalid_fields))
        if c.state == State.NEEDS_INFO and not bad:
            out.append(f"{c.id}: needs_info but nothing is missing")
        if c.state == State.NEEDS_INFO and not (c.proposal and c.proposal.questions_for_requester):
            out.append(f"{c.id}: needs_info but there is nothing to ask for")
        if bad and c.state in (State.PROPOSED, State.ANSWERED, State.READY):
            out.append(f"{c.id}: {c.state.value} with missing or invalid fields")
        if bad and c.state == State.IN_REVIEW and not safety_override(c) and not c.forwarded_by and c.classification and c.classification.model_used != "seed":
            out.append(f"{c.id}: in review with missing or invalid fields")
    return out


def run_check(echo: Callable[[str], None] = print, skip_pytest: bool = False) -> int:
    from caregrid import alerts

    with alerts.suppressed():                                    # the checklist never publishes an alert
        return _run_check(echo, skip_pytest)


def _run_check(echo: Callable[[str], None] = print, skip_pytest: bool = False) -> int:
    from caregrid.admin import reset_demo
    from caregrid.demo import run_scenarios
    from caregrid.ingest.leakscan import leak_scan, leak_scan_store

    results: list[tuple[str, bool, str]] = []

    def item(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))
        echo(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    # 1 reset is repeatable
    reset_demo()
    first = (brain_hash(config.BRAIN_DIR), cases_hash(SQLiteStore()))
    reset_demo()
    store = SQLiteStore()
    second = (brain_hash(config.BRAIN_DIR), cases_hash(store))
    item("reset is repeatable (two resets -> identical hashes)", first == second,
         f"brain {first[0]} / {second[0]}, cases {first[1]} / {second[1]}")

    # 2 pytest
    if skip_pytest:
        item("pytest", True, "skipped (--skip-pytest)")
    else:
        ok, summary = _pytest_summary()
        item("pytest (mock LLM)", ok, summary)

    # 3 leak scan: brain + the SQLite database
    brain_leaks = leak_scan(config.BRAIN_DIR, config.DATA_DIR)
    db_leaks = leak_scan_store(store, config.DATA_DIR)
    item("leak scan 0 (brain + SQLite)", not brain_leaks and not db_leaks, f"brain {len(brain_leaks)}, sqlite {len(db_leaks)}")

    # 4 + 5 + 6 on every case we can produce: seeded DB cases, the demo scenarios, every eval and held-out row
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(config.BRAIN_DIR, Path(tmp) / "brain")
        demo_store = SQLiteStore(":memory:")
        seed_trust(demo_store, config.DATA_DIR)
        scenarios = run_scenarios(demo_store, Brain(Path(tmp) / "brain"), MockLLM(), config.DATA_DIR)
    eval_files = [p for p in (config.EVAL_DIR / "requests_eval.csv", config.EVAL_DIR / "heldout.csv") if p.exists()]
    eval_store, eval_cases = _run_eval_files(config.BRAIN_DIR, config.DATA_DIR, eval_files)
    groups = [("seeded db", store, store.list_cases()), ("demo", demo_store, demo_store.list_cases()), ("eval rows", eval_store, eval_cases)]
    total = sum(len(g[2]) for g in groups)

    hits = [h for _, _, cs in groups for h in forbidden_citations(cs)]
    item("KA-60 (draft) and KA-12 v2 (expired) never cited", not hits, f"{total} cases scanned" + (f"; {hits[:5]}" if hits else ""))

    gaps = [f"{label}/{g}" for label, st, cs in groups for g in audit_gaps(st, cs)]
    item("every case: request_received -> final-state audit", not gaps,
         f"{total} cases checked" + (f"; {len(gaps)} gap(s), e.g. {gaps[:3]}" if gaps else ""))

    bad_state = [f"{label}/{v}" for label, st, _ in groups for v in state_rule_violations(st)]
    item("state matches rules in every case (needs_info <=> something missing)", not bad_state,
         f"{total} cases checked" + (f"; {len(bad_state)} violation(s), e.g. {bad_state[:3]}" if bad_state else ""))

    brain_for_story = Brain(config.BRAIN_DIR)
    users_for_story = load_users(config.DATA_DIR)
    bad_story = [f"{label}/{v}" for label, st, _ in groups for v in story_violations(st, brain_for_story, users_for_story)]
    item("every case tells a coherent story (problem, decision, flags, timeline)", not bad_story,
         f"{total} cases checked" + (f"; {len(bad_story)} problem(s), e.g. {bad_story[:3]}" if bad_story else ""))

    failed = [s.key for s in scenarios if not s.passed]
    item("demo 12/12 on mock", not failed and len(scenarios) == 12, f"{len(scenarios) - len(failed)}/{len(scenarios)} scenarios passed"
         + (f"; FAILED {failed}" if failed else ""))

    # 7 API smoke (read-only on the clean demo state): auth, RBAC and the amount rule through HTTP
    try:
        from fastapi.testclient import TestClient

        from caregrid import api

        api.reset_process_state()
        c = TestClient(api.app)
        h = lambda u: {"X-CareGrid-User": u}   # noqa: E731
        asha_all = c.get("/api/cases/CASE-1024", headers=h("U1")).text + c.get("/api/cases/CASE-1024/audit", headers=h("U1")).text             + c.post("/api/cases/CASE-1024/assistant", headers=h("U1"), json={"question": "What is the amount?"}).text
        probes = {
            "users open": c.get("/api/users").status_code == 200, "no header -> 401": c.get("/api/cases").status_code == 401,
            "bad header -> 401": c.get("/api/cases", headers=h("U99")).status_code == 401,
            "senior sees the case": c.get("/api/cases/CASE-1024", headers=h("U4")).status_code == 200,
            "other team -> 403": c.get("/api/cases/CASE-1024", headers=h("U7")).status_code == 403,
            "ops employee never gets the amount": "62,500" not in asha_all and "62500" not in asha_all,
            "assistant restricts the amount question": "ACCESS RESTRICTED" in asha_all,
            "only the knowledge owner decides PRs": c.post("/api/prs/PR-none/decision", headers=h("U4"), json={"approve": True}).status_code == 403,
            "reset refused to an ops employee": c.post("/api/reset", headers=h("U1")).status_code == 403,
            "header must match U1..U7 exactly": c.get("/api/cases", headers=h("u1")).status_code == 401,
            "duplicate user header -> 400": c.get("/api/cases", headers=[("X-CareGrid-User", "U1"), ("X-CareGrid-User", "U2")]).status_code == 400,
            "whitespace-only request -> 422": c.post("/api/requests", headers=h("U1"), json={"text": "   "}).status_code == 422,
            "drafts hidden from an ops employee": not any(p["status"] == "draft" for p in c.get("/api/pages", headers=h("U1")).json())
            and c.get("/api/lint", headers=h("U1")).status_code == 403,
        }
        failed_probes = [k for k, v in probes.items() if not v]
        item("API smoke (auth, RBAC, amount rule)", not failed_probes, f"{len(probes) - len(failed_probes)}/{len(probes)} probes"
             + (f"; FAILED {failed_probes}" if failed_probes else ""))
    except Exception as e:                       # noqa: BLE001 - a broken import must show up as a FAIL, not a crash
        item("API smoke (auth, RBAC, amount rule)", False, f"{type(e).__name__}: {e}")

    try:
        bad_dash = dashboard_audit(c, SQLiteStore(), Brain(config.BRAIN_DIR), load_users(config.DATA_DIR))
        item("data audit: every dashboard number recomputed from the raw cases", not bad_dash, "7 users x all tiles, visible and queue counts"
             + (f"; MISMATCH {bad_dash[:3]}" if bad_dash else ""))
    except Exception as e:                       # noqa: BLE001
        item("data audit: every dashboard number recomputed from the raw cases", False, f"{type(e).__name__}: {e}")

    bad = [name for name, ok, _ in results if not ok]
    echo(f"\n{len(results) - len(bad)}/{len(results)} checks passed" + (f"; FAILED: {len(bad)}" if bad else ""))
    return 1 if bad else 0
