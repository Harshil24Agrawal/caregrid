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

_TIME_KEYS = {"created_at", "state_history", "ts", "updated_at", "decided_at"}
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


def run_check(echo: Callable[[str], None] = print, skip_pytest: bool = False) -> int:
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

    failed = [s.key for s in scenarios if not s.passed]
    item("demo 10/10 on mock", not failed and len(scenarios) == 10, f"{len(scenarios) - len(failed)}/{len(scenarios)} scenarios passed"
         + (f"; FAILED {failed}" if failed else ""))

    bad = [name for name, ok, _ in results if not ok]
    echo(f"\n{len(results) - len(bad)}/{len(results)} checks passed" + (f"; FAILED: {len(bad)}" if bad else ""))
    return 1 if bad else 0
