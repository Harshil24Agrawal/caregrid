"""Demo seed data: trust ladder, ~20 historical cases (backdated, mixed states) and CASE-1024 through the real pipeline."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from caregrid.knowledge.brain import Brain
from caregrid.llm import LLM
from caregrid.models import (
    Case, Channel, Classification, DecisionCode, Proposal, Risk, Role, RuleResult, State, TrustRecord, User,
)
from caregrid.reasoning.pipeline import run
from caregrid.reasoning.rules import APPROVER_BY_RISK
from caregrid.store import Store
from caregrid.workflow.audit import log

DEMO_CASE_ID = "CASE-1024"


def load_users(data_dir: Path) -> dict[str, User]:
    with open(Path(data_dir) / "users.csv", encoding="utf-8", newline="") as f:
        return {r["id"]: User(id=r["id"], name=r["name"], role=Role(r["role"]), team=r["team"] or None) for r in csv.DictReader(f)}


def _load(data_dir: Path, name: str):
    return json.loads((Path(data_dir) / name).read_text(encoding="utf-8"))


def seed_trust(store: Store, data_dir: Path) -> int:
    now = datetime.now()
    rows = _load(data_dir, "trust_seed.json")
    for r in rows:
        store.save_trust(TrustRecord(**r, updated_at=now))
    return len(rows)


def seed_historical_cases(store: Store, data_dir: Path) -> int:
    """Cases inserted directly (they pre-date this run): backdated, in mixed states, each with its audit trail."""
    users, now, n = load_users(data_dir), datetime.now(), 0
    for seed in _load(data_dir, "seed_cases.json"):
        if seed["id"] == DEMO_CASE_ID:
            continue
        created = now - timedelta(hours=seed["hours_ago"])
        final = State(seed["state"])
        entered = min(created + timedelta(minutes=20), now)
        history = [(State.NEW, created), (State.CLASSIFIED, created + timedelta(minutes=2)), (State.PROPOSED, created + timedelta(minutes=10))]
        if final not in (State.NEW, State.CLASSIFIED, State.PROPOSED):
            history.append((final, entered))
        risk = Risk(seed["risk"])
        rtype = seed["request_type"]
        case = Case(
            id=seed["id"], created_at=created, requester=users[seed["requester_id"]], channel=Channel(seed["channel"]),
            masked_text=seed["text"], state=final, state_history=history,
            classification=Classification(request_type=rtype, llm_confidence=0.9, rules_type=rtype, extracted_fields=seed["fields"],
                                          model_used="seed"),
            rules=RuleResult(risk=risk, route_team=seed["assigned_team"], approver_role=APPROVER_BY_RISK[risk]),
            proposal=Proposal(decision_code=DecisionCode.ROUTE_TO_TEAM, route_team=seed["assigned_team"],
                              answer_text="Seeded historical case.", summary_for_reviewer="Seeded historical case for queue ageing."),
            routing="human", assigned_team=seed["assigned_team"], approver_role=APPROVER_BY_RISK[risk], related=seed["related"])
        store.save_case(case)
        log(store, "request_received", case.requester, case.id, ts=created, channel=case.channel.value, pii_types=[], seeded=True)
        for (a, _), (b, ts) in zip(history, history[1:]):
            log(store, "state_changed", None, case.id, ts=ts, **{"from": a.value, "to": b.value})
        n += 1
    return n


def seed_demo_case(store: Store, brain: Brain, llm: LLM, data_dir: Path) -> Case:
    """CASE-1024 goes through the REAL pipeline with a fixed id; related evidence keys and age are then attached."""
    seed = next(s for s in _load(data_dir, "seed_cases.json") if s["id"] == DEMO_CASE_ID)
    user = load_users(data_dir)[seed["requester_id"]]
    case = run(seed["text"], user, store, brain, llm, channel=Channel(seed["channel"]), case_id=DEMO_CASE_ID)
    shift = timedelta(hours=seed["hours_ago"])
    case.created_at = case.created_at - shift
    case.state_history = [(s, t - shift) for s, t in case.state_history]
    case.related = seed["related"]
    store.save_case(case)
    return case


def seed_all(store: Store, brain: Brain, llm: LLM, data_dir: Path) -> dict[str, int]:
    return {"trust": seed_trust(store, data_dir), "historical_cases": seed_historical_cases(store, data_dir),
            "demo_case": 1 if seed_demo_case(store, brain, llm, data_dir) else 0}
