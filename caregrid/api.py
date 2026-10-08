"""HTTP API for the web UI: a thin layer over the existing backend functions (pipeline, rbac, decisions, prs, metrics, lint, graph, assistant).

No business logic lives here or in the browser. What this module adds:
  * demo authentication: the acting user is named by the header `X-CareGrid-User: U1..U7`. Missing / unknown -> 401. This is NOT real
    authentication (anyone can send any id); it exists to demonstrate server-side RBAC. /api/users and /api/config are open so the user
    switcher can be drawn before a user is chosen.
  * server-side RBAC on every endpoint (rbac.can_view / can_approve / visible_cases); a section the user may not see is returned as
    {"restricted": true, ...}; PermissionError from the backend -> 403 with the ACCESS RESTRICTED line.
  * every free-text field is passed through guards.check_output(text, viewer) before it leaves the server.
  * one Brain per process, shared by run / submit_decision / decide_pr, guarded by a lock for the writes.
Request bodies are never logged and the 422 handler does not echo them back.
"""
from __future__ import annotations

import csv
import json
import os
import re
import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from caregrid import config
from caregrid.insights import metrics
from caregrid.insights.explain import case_summary, one_liner, workflow_guidance
from caregrid.insights.provenance import format_source, page_sections, provenance, source_for_citation
from caregrid.insights.graph import case_graph
from caregrid.knowledge.brain import Brain
from caregrid.knowledge.lint import lint
from caregrid.llm import get_llm, model_name
from caregrid.models import Case, Channel, DecisionCode, PageStatus, ReviewAction, ReviewDecision, Role, State, User
from caregrid.rbac import can_approve, can_view, visible_cases
from caregrid.reasoning.guards import check_output
from caregrid.reasoning.pipeline import run
from caregrid.seed import load_users
from caregrid.store import SQLiteStore
from caregrid.workflow import assistant as assistant_mod
from caregrid.workflow import patients as patients_mod
from caregrid.workflow.audit import log as audit_log
from caregrid.workflow.decisions import DECIDABLE, AlreadyDecidedError, submit_decision
from caregrid.workflow.prs import PRStateError, decide_pr

RESTRICTED_LINE = "ACCESS RESTRICTED: you don't have permission to view this information."
MAX_REQUEST_CHARS = 4000
RESET_ROLES = {Role.OPS_MANAGER, Role.SENIOR_REVIEWER}                                   # who may reset the demo (when DEMO_MODE=1)
KNOWLEDGE_ADMIN_ROLES = {Role.KNOWLEDGE_OWNER, Role.OPS_MANAGER, Role.SENIOR_REVIEWER, Role.AUDITOR}   # may see drafts, lint, PRs, the brain log
DEMO_USER_IDS = {"U1", "U2", "U3", "U4", "U5", "U6", "U7"}


def demo_mode() -> bool:
    """DEMO_MODE=1 enables /api/reset. `cli serve` sets it to 1 by default; any other process has it off unless the environment says so."""
    return os.environ.get("DEMO_MODE", "0") == "1"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="CareGrid API", version="1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
_lock = threading.RLock()
_state: dict[str, Any] = {"brain": None, "llm": None}


# ------------------------------------------------------------------ process-wide pieces
def get_brain() -> Brain:
    """ONE Brain per process; run(), submit_decision() and decide_pr() all use this instance."""
    with _lock:
        if _state["brain"] is None:
            _state["brain"] = Brain(config.BRAIN_DIR)
        return _state["brain"]


def get_llm_client():
    with _lock:
        if _state["llm"] is None:
            _state["llm"] = get_llm()
        return _state["llm"]


def reset_process_state() -> None:
    """Tests: forget the cached Brain / LLM (e.g. after config paths changed)."""
    with _lock:
        _state["brain"] = None
        _state["llm"] = None


def get_store() -> SQLiteStore:
    return SQLiteStore()                       # one short-lived connection per operation


# ------------------------------------------------------------------ auth, errors
def actor(request: Request) -> User:
    values = request.headers.getlist("x-caregrid-user")
    if len(values) > 1:
        raise HTTPException(status_code=400, detail="Send the X-CareGrid-User header once.")
    value = values[0] if values else ""
    user = load_users(config.DATA_DIR).get(value) if value in DEMO_USER_IDS else None          # exact U1..U7: no trimming, no case folding
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown or missing X-CareGrid-User header (demo auth: exactly U1..U7).")
    return user


@app.exception_handler(PermissionError)
async def _forbidden(_: Request, exc: PermissionError):
    return JSONResponse(status_code=403, content={"detail": RESTRICTED_LINE, "restricted": True})


@app.exception_handler(AlreadyDecidedError)
async def _conflict(_: Request, exc: AlreadyDecidedError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(PRStateError)
async def _pr_conflict(_: Request, exc: PRStateError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def _invalid(_: Request, exc: RequestValidationError):
    # the default handler echoes the submitted value back ("input"): never echo request text
    errors = [{"loc": list(e.get("loc", [])), "msg": str(e.get("msg", "")).removeprefix("Value error, "), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


# ------------------------------------------------------------------ output guard
_STRUCTURAL = re.compile(r"^(?:[A-Z]{1,8}-[A-Za-z0-9]+(?: v\d+)?|\d{4}-\d\d-\d\d(?:[T ][\d:.]+)?|[a-z_]+)$")


def scrub(obj: Any, user: User) -> Any:
    """check_output(text, viewer) on every string that is not an id / timestamp / enum value."""
    if isinstance(obj, str):
        return obj if (not obj or _STRUCTURAL.match(obj)) else check_output(obj, user)[1]
    if isinstance(obj, list):
        return [scrub(x, user) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v, user) for k, v in obj.items()}
    return obj


def restricted(section: str) -> dict:
    return {"restricted": True, "section": section, "message": RESTRICTED_LINE}


# ------------------------------------------------------------------ serializers
def hours_since(ts: datetime) -> float:
    return round((datetime.now() - ts).total_seconds() / 3600, 1)


def case_row(c: Case, user: User | None = None) -> dict:
    return {
        "can_approve": bool(user and c.state in DECIDABLE and can_approve(user, c)),
        "id": c.id, "request_type": c.classification.request_type if c.classification else "unknown", "state": c.state.value,
        "risk": c.rules.risk.value if c.rules else None, "band": c.confidence.band.value if c.confidence else None,
        "score": c.confidence.score if c.confidence else None, "team": c.assigned_team, "routing": c.routing,
        "approver_role": c.approver_role.value if c.approver_role else None, "age_hours": hours_since(c.created_at),
        "created_at": c.created_at.isoformat(), "reason_codes": [r.value for r in c.reason_codes], "requester": c.requester.name,
        "trust_level": c.trust_level, "summary": one_liner(c),
    }


_MASK_TOKEN = re.compile(r"\[[A-Z][A-Z_]*(?:_\d+)?\]")        # [PERSON_1] [MEMBER_ID] [NPI] [PHONE] [EMAIL] [ADDRESS] [DATE_OF_BIRTH] ...


def phi_masked(cases: list[Case]) -> int:
    """How many masked tokens the viewer's visible cases carry (counted, never shown: the values were never stored)."""
    return sum(len(_MASK_TOKEN.findall(c.masked_text or "")) for c in cases)


ROLE_PLURAL = {"ops_employee": "Ops employees", "team_specialist": "Team specialists", "ops_manager": "Ops managers",
               "senior_reviewer": "Senior reviewers", "knowledge_owner": "Knowledge owners", "auditor": "Auditors"}


def approval_gate(user: User, case: Case) -> dict:
    """Why the approve / ask actions are or are not allowed for this user (the UI only displays this)."""
    ask_ok = can_view(user, case, "full") and case.requester.id != user.id
    ask_why = "" if ask_ok else "Only someone who can see the whole case, and is not the requester, may ask for more information."
    ok = can_approve(user, case)
    if ok:
        why = ""
    else:
        # role rule first; separation of duties only when the role COULD approve this case but is its requester
        could = can_approve(user, case.model_copy(update={"requester": User(id="U-none", name="x", role=Role.OPS_EMPLOYEE)}))
        need = case.approver_role.value.replace("_", " ") if case.approver_role else "senior reviewer"
        risk = case.rules.risk.value.upper() if case.rules else "this"
        plural = ROLE_PLURAL.get(user.role.value, user.role.value.replace("_", " "))
        if could and case.requester.id == user.id:
            why = "Separation of duties: you can't approve your own request."
        elif user.role.value not in {"team_specialist", "ops_manager", "senior_reviewer"}:
            why = f"{plural} can't approve. A {need} decides {risk}-risk cases."
        elif user.role.value == "team_specialist" and case.rules and user.team != case.assigned_team and risk == "LOW":
            why = f"This case belongs to {case.assigned_team}; your team is {user.team}."
        else:
            why = f"{plural} can't approve {risk}-risk cases. A {need} decides them."
    decidable = case.state in DECIDABLE or case.state == State.NEEDS_INFO
    return {"decidable": decidable, "state": case.state.value,
            "approve": {"allowed": ok and case.state in DECIDABLE, "reason": why or ("" if case.state in DECIDABLE else f"Case is {case.state.value}.")},
            "ask": {"allowed": ask_ok and decidable, "reason": ask_why or ("" if decidable else f"Case is {case.state.value}.")}}


def evidence(case: Case, user: User, brain: Brain) -> dict:
    """profile / invoice / logs / jira / runbook, each gated by can_view. Free text from records is not shown (ids, status, dates only)."""
    out: dict = {}
    d = config.DATA_DIR

    def rows(name: str, key: str, refs: list[str]) -> list[dict]:
        path = d / name
        if not path.exists():
            return []
        with open(path, encoding="utf-8", newline="") as f:
            return [r for r in csv.DictReader(f) if r[key] in refs]

    for key, section in (("profile", "profile"), ("invoice", "billing"), ("logs", "logs"), ("jira", "logs"), ("runbook", "logs")):
        refs = case.related.get(key, [])
        if not refs:
            out[key] = []
        elif not can_view(user, case, section):
            out[key] = restricted(section)
        elif key == "profile":
            profiles = json.loads((d / "profiles.json").read_text(encoding="utf-8")).get("providers", []) if (d / "profiles.json").exists() else []
            out[key] = [{"id": p["profile_key"], "specialty": p.get("specialty")} for p in profiles if p.get("profile_key") in refs] \
                or [{"id": r} for r in refs]
        elif key == "invoice":
            out[key] = [{"id": r["invoice_id"], "status": r["status"], "due_date": r["due_date"], "amount_inr": int(r["amount_inr"])}
                        for r in rows("billing.csv", "invoice_id", refs)]
        elif key == "logs":
            out[key] = [{"id": r["log_id"], "system": r["system"], "level": r["level"], "ts": r["ts"]} for r in rows("system_logs.csv", "log_id", refs)]
        elif key == "jira":
            out[key] = [{"id": r["jira_id"], "status": r["status"]} for r in rows("jira_records.csv", "jira_id", refs)]
        else:
            out[key] = [{"id": r, "title": (brain.get(r).title if brain.get(r) else r)} for r in refs]
    return out


def pii_types(store: SQLiteStore, case_id: str) -> list[str]:
    ev = next((e for e in store.list_audit(case_id) if e.event == "request_received"), None)
    return sorted(set(ev.details.get("pii_types", []))) if ev else []


TRUST_LABEL = {0: "Shadow", 1: "Assist", 2: "Auto-with-audit"}


def case_detail(case: Case, user: User, store: SQLiteStore, brain: Brain) -> dict:
    """The case as this user may see it. Sections beyond `summary` are {"restricted": true} when can_view says no."""
    if not can_view(user, case, "summary"):
        raise PermissionError(case.id)
    full = can_view(user, case, "full")
    rules, prop, conf = case.rules, case.proposal, case.confidence
    rtype = case.classification.request_type if case.classification else "unknown"
    trust = store.get_trust(rtype)
    refused = bool(prop and prop.decision_code == DecisionCode.REFUSE_AND_ROUTE)
    kind = ("refused" if refused else "answered" if case.state == State.ANSWERED else "needs_info" if case.state == State.NEEDS_INFO
            else "in_review" if case.state in (State.IN_REVIEW, State.ESCALATED) else case.state.value)
    detail: dict = {
        "id": case.id, "result_kind": kind, "created_at": case.created_at.isoformat(), "age_hours": hours_since(case.created_at),
        "channel": case.channel.value, "state": case.state.value,
        "state_history": [{"state": s.value, "ts": ts.isoformat()} for s, ts in case.state_history],
        "requester": {"id": case.requester.id, "name": case.requester.name, "role": case.requester.role.value},
        "request_type": rtype, "urgency": case.classification.urgency if case.classification else None,
        "sentiment": case.classification.sentiment if case.classification else None,
        "summary": case_summary(case, brain, user), "masked_text": case.masked_text, "reason_codes": [r.value for r in case.reason_codes], "routing": case.routing,
        "assigned_team": case.assigned_team, "approver_role": case.approver_role.value if case.approver_role else None,
        "risk": rules.risk.value if rules else None, "trust": {"level": trust.level, "label": TRUST_LABEL.get(trust.level, str(trust.level)),
                                                              "consecutive_agreements": trust.consecutive_agreements},
        "confidence": {"score": conf.score, "band": conf.band.value} if conf else None,
        "pii_types": pii_types(store, case.id),
        "missing": {"missing": list(rules.missing_fields) if rules else [], "invalid": list(rules.invalid_fields) if rules else []},   # field names only
        "proposal": None,
        "reviewer": None,
        "evidence": evidence(case, user, brain),
        "guidance": workflow_guidance(case, brain, store.list_audit(case.id)),
        "patient": patients_mod.case_patient(case, user, store, config.DATA_DIR),
        "actions": approval_gate(user, case),
    }
    if prop:
        detail["proposal"] = {
            "decision_code": prop.decision_code.value, "route_team": prop.route_team, "answer_text": prop.answer_text,
            "next_steps": prop.next_steps, "questions_for_requester": prop.questions_for_requester,
            "citations": [{"page_id": c.page_id, "version": c.version, "page_type": c.page_type.value, "title": c.title} for c in prop.citations],
        }
    if not full:
        detail["reviewer"] = restricted("full")
    else:
        capped = bool(rules and rules.conflicts and conf and conf.band.value == "medium")
        considered = []
        for c in case.citations_considered:
            page = brain.get(c.page_id, c.version) if c.version else brain.get(c.page_id)
            prec = brain.get_precedent(c.page_id)
            considered.append({"page_id": c.page_id, "version": c.version, "page_type": c.page_type.value, "title": c.title,
                               "status": (page.status.value if page else prec.status.value if prec else "missing"),
                               "current_version": (brain.get(c.page_id).version if brain.get(c.page_id) else None)})
        detail["reviewer"] = {
            "summary_for_reviewer": prop.summary_for_reviewer if prop else "", "model_used": prop.model_used if prop else None,
            "llm_tiers_used": case.llm_tiers_used,
            "risk_reasons": rules.risk_reasons if rules else [], "conflicts": rules.conflicts if rules else [],
            "notes": rules.notes if rules else [], "required_fields": rules.required_fields if rules else [],
            "missing_fields": rules.missing_fields if rules else [], "invalid_fields": rules.invalid_fields if rules else {},
            "confidence": ({"score": conf.score, "band": conf.band.value, "breakdown": conf.breakdown, "explanation": conf.explanation,
                            "capped_at_medium": capped} if conf else None),
            "citations_considered": considered,
        }
    detail = scrub(detail, user)
    # claims go through check_output; locations (second_brain/policy/KA-12@v3.md) are code-built and would look like an e-mail address to a scrubber
    detail["provenance"] = provenance(case, brain, user, clean=lambda t: check_output(t, user)[1])
    return detail


def must_get_case(case_id: str, user: User, store: SQLiteStore) -> Case:
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Unknown case.")
    if not can_view(user, case, "summary"):
        raise PermissionError(case_id)
    return case


# ------------------------------------------------------------------ open endpoints
@app.get("/api/users")
def api_users():
    return [{"id": u.id, "name": u.name, "role": u.role.value, "team": u.team} for u in load_users(config.DATA_DIR).values()]


@app.get("/api/config")
def api_config():
    llm = get_llm_client()
    return {"llm_provider": config.LLM_PROVIDER, "models": {"light": model_name(llm, "light"), "strong": model_name(llm, "strong")},
            "embed_provider": config.EMBED_PROVIDER, "max_request_chars": MAX_REQUEST_CHARS, "demo_mode": demo_mode(),
            "reset_roles": sorted(r.value for r in RESET_ROLES), "knowledge_admin_roles": sorted(r.value for r in KNOWLEDGE_ADMIN_ROLES),
            "auth": "demo header X-CareGrid-User (not real authentication)"}


@app.post("/api/reset")
def api_reset(user: User = Depends(actor)):
    from caregrid.admin import reset_demo

    reason = None if demo_mode() else "DEMO_MODE is off"
    if reason is None and user.role not in RESET_ROLES:
        reason = f"role {user.role.value} may not reset"
    if reason is not None:
        audit_log(get_store(), "reset_denied", user, None, reason=reason, role=user.role.value)
        raise HTTPException(status_code=403, detail="Reset is only available in demo mode to an ops manager or a senior reviewer.")
    with _lock:
        result = reset_demo(brain=get_brain())
    return {"ok": True, "seeded": result["seeded"], "brain": result["brain"], "leak_findings": len(result["leak_findings"])}


# ------------------------------------------------------------------ requests and cases
class RequestIn(BaseModel):
    text: str = Field(max_length=MAX_REQUEST_CHARS)
    channel: Channel = Channel.PORTAL

    @field_validator("text", mode="before")
    @classmethod
    def _strip(cls, v):
        if isinstance(v, str):
            v = v.strip()
            # NFKC, then drop control / format / separator characters (zero-width space and joiner, BOM, word joiner, no-break and ideographic
            # spaces ...): what is left must contain at least one letter or digit. Never echo the input.
            visible = "".join(ch for ch in unicodedata.normalize("NFKC", v) if unicodedata.category(ch) not in ("Cc", "Cf", "Zs", "Zl", "Zp"))
            if not any(ch.isalnum() for ch in visible):
                raise ValueError("Please describe the request.")
        return v


@app.post("/api/requests")
def api_create_request(body: RequestIn, user: User = Depends(actor)):
    store, brain, llm = get_store(), get_brain(), get_llm_client()
    with _lock:
        case = run(body.text, user, store, brain, llm, body.channel)
    return {"case": case_detail(store.get_case(case.id), user, store, brain)}


@app.get("/api/cases")
def api_cases(user: User = Depends(actor)):
    store = get_store()
    rows = sorted(visible_cases(user, store), key=lambda c: c.created_at, reverse=True)
    return scrub([case_row(c, user) for c in rows], user)


@app.get("/api/cases/{case_id}")
def api_case(case_id: str, user: User = Depends(actor)):
    store = get_store()
    return case_detail(must_get_case(case_id, user, store), user, store, get_brain())


@app.get("/api/cases/{case_id}/graph")
def api_case_graph(case_id: str, user: User = Depends(actor)):
    store, brain = get_store(), get_brain()
    must_get_case(case_id, user, store)                      # anyone who can open the case gets a graph; case_graph() returns the summary-only shape to them
    return scrub(case_graph(case_id, user, store, brain).model_dump(), user)


def audit_row(e) -> dict:
    return {"id": e.id, "ts": e.ts.isoformat(), "case_id": e.case_id, "event": e.event, "actor_id": e.actor_id, "actor_role": e.actor_role,
            "details": e.details}


@app.get("/api/cases/{case_id}/audit")
def api_case_audit(case_id: str, user: User = Depends(actor)):
    store = get_store()
    must_get_case(case_id, user, store)
    return scrub([audit_row(e) for e in store.list_audit(case_id)], user)


class DecisionIn(BaseModel):
    action: ReviewAction
    edited_answer: str | None = None
    note: str = Field(default="", max_length=2000)
    save_as_precedent: bool = True
    propose_pr: bool = False
    contact_email: str | None = None
    contact_phone: str | None = None
    channels: list[Channel] = []
    meta_changes: dict = {}


@app.post("/api/cases/{case_id}/decision")
def api_decision(case_id: str, body: DecisionIn, user: User = Depends(actor)):
    store, brain, llm = get_store(), get_brain(), get_llm_client()
    case = must_get_case(case_id, user, store)
    before_len, before_prs = len(case.state_history), {p.id for p in store.list_prs()}
    d = ReviewDecision(case_id=case_id, reviewer=user, action=body.action, edited_answer=body.edited_answer, note=body.note,
                       save_as_precedent=body.save_as_precedent, propose_pr=body.propose_pr, contact_email=body.contact_email,
                       contact_phone=body.contact_phone, channels=body.channels, meta_changes=body.meta_changes)
    try:
        with _lock:
            done = submit_decision(d, store, brain, llm)
    except AlreadyDecidedError:
        raise                                      # -> 409 via the exception handler (it is a ValueError subclass)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Unknown case.") from e
    events = store.list_audit(case_id)
    prec = next((e.details.get("precedent") for e in reversed(events) if e.event == "precedent_saved"), None)
    trust = next((e.details for e in reversed(events) if e.event == "trust_updated"), None)
    result = {
        "state_path": [s.value for s, _ in done.state_history[before_len - 1:]],
        "precedent_id": prec,
        "trust": ({"request_type": trust.get("request_type"), "agreed": trust.get("agreed"), "level_before": trust.get("level_before"),
                   "level_after": trust.get("level_after"), "streak_before": trust.get("streak_before"),
                   "streak_after": trust.get("streak_after")} if trust else None),
        "pr_id": next((p.id for p in store.list_prs() if p.id not in before_prs), None),
        "communications": [{"id": c.id, "channel": c.channel.value, "status": c.status} for c in store.list_comms(case_id)],
    }
    return {"case": case_detail(done, user, store, brain), "result": result}


class AssistantIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@app.post("/api/cases/{case_id}/assistant")
def api_assistant(case_id: str, body: AssistantIn, user: User = Depends(actor)):
    store, brain, llm = get_store(), get_brain(), get_llm_client()
    case = must_get_case(case_id, user, store)
    reply = assistant_mod.ask(case, body.question, user, brain, store, llm)
    sources = [] if reply.restricted or reply.refused else [s for pid in reply.citations if (s := source_for_citation(brain, pid, case)) is not None]
    for s in sources:
        s["title"] = check_output(s["title"] or "", user)[1]
    text = reply.text + ("\n\nSources: " + "; ".join(format_source(s) for s in sources) if sources else "")     # same line the Case page shows
    return {"text": text, "sources": sources, "citations": reply.citations, "restricted": reply.restricted, "refused": reply.refused,
            "model_used": reply.model_used, "tier": reply.tier, "chips": reply.chips}


# ------------------------------------------------------------------ dashboard, scorecard
def scoped_store(user: User, store: SQLiteStore) -> SQLiteStore:
    """An in-memory store holding only the cases this user may see, so the existing metrics functions can be reused unchanged."""
    scoped = SQLiteStore(":memory:")
    for c in visible_cases(user, store):
        scoped.save_case(c)
    return scoped


@app.get("/api/metrics")
def api_metrics(user: User = Depends(actor)):
    store, brain = get_store(), get_brain()
    sc = scoped_store(user, store)
    waiting = [c for c in visible_cases(user, store) if c.state in (State.IN_REVIEW, State.ESCALATED, State.NEEDS_INFO)]
    trust = [t.model_dump(mode="json") for t in metrics.trust_overview(store)]
    for t in trust:
        t["agreement_pct"] = round(100 * t["agreements"] / t["total_reviews"], 1) if t["total_reviews"] else None
        t["label"] = TRUST_LABEL.get(t["level"])
    return scrub({
        "counts": metrics.dashboard_counts(sc), "queue": [case_row(c, user) for c in sorted(waiting, key=lambda c: c.created_at)],
        "phi_masked": phi_masked(visible_cases(user, store)),
        "visible_cases": len(visible_cases(user, store)), "trust": trust, "gap_radar": metrics.gap_radar(sc, brain),
        "queue_aging": metrics.queue_aging(sc), "cost_split": metrics.cost_split(sc),
        "trust_thresholds": {"l1_streak": config.TRUST_L1_STREAK, "l1_ratio": config.TRUST_L1_RATIO, "l2_reviews": config.TRUST_L2_REVIEWS},
    }, user)


def _card(path: Path) -> dict | None:
    if not path.exists():
        return None
    card = json.loads(path.read_text(encoding="utf-8"))
    card.pop("results", None)
    if card.get("adjudicated"):
        card["adjudicated"].pop("results", None)
    return card


@app.get("/api/scorecard")
def api_scorecard(user: User = Depends(actor)):
    e = config.EVAL_DIR
    return {"main": _card(e / "scorecard.json"),
            "heldout": {"mock": _card(e / "scorecard.heldout.mock.json"), "env": _card(e / "scorecard.heldout.env.json")}}


# ------------------------------------------------------------------ knowledge
def knowledge_admin(user: User) -> bool:
    return user.role in KNOWLEDGE_ADMIN_ROLES


def require_knowledge_admin(user: User) -> None:
    if not knowledge_admin(user):
        raise PermissionError("knowledge admin")


@app.get("/api/pages")
def api_pages(type: str | None = None, status: str | None = None, q: str | None = None, user: User = Depends(actor)):
    """Everyone sees APPROVED current pages and ACTIVE precedents; drafts, expired versions and stale precedents only for knowledge admins."""
    brain = get_brain()
    rows = [{"id": p.id, "version": p.version, "type": p.type.value, "status": p.status.value, "title": p.title,
             "effective_from": p.effective_from.isoformat() if p.effective_from else None, "request_types": p.request_types}
            for p in brain.all_pages()]
    rows += [{"id": r.id, "version": 1, "type": "precedent", "status": r.status.value, "title": f"{r.request_type}: {r.summary[:70]}",
              "effective_from": r.date.isoformat() if r.date else None, "request_types": [r.request_type]} for r in brain.precedents()]
    if not knowledge_admin(user):
        rows = [r for r in rows if r["status"] in (PageStatus.APPROVED.value, PageStatus.ACTIVE.value) and _is_current(brain, r)]
    rows = [r for r in rows if (not type or r["type"] == type) and (not status or r["status"] == status)
            and (not q or q.lower() in r["id"].lower() or q.lower() in r["title"].lower())]
    return scrub(sorted(rows, key=lambda r: (r["id"], -r["version"])), user)


def _is_current(brain: Brain, row: dict) -> bool:
    if row["type"] == "precedent":
        return True                                            # status ACTIVE already checked
    current = brain.get(row["id"])
    return current is not None and current.version == row["version"]


@app.get("/api/pages/{page_id}")
def api_page(page_id: str, version: int | None = None, user: User = Depends(actor)):
    brain = get_brain()
    admin = knowledge_admin(user)
    prec = brain.get_precedent(page_id)
    if prec is not None:
        if not admin and prec.status != PageStatus.ACTIVE:
            raise HTTPException(status_code=404, detail="Unknown page.")
        return scrub({"id": prec.id, "type": "precedent", "version": 1, "status": prec.status.value, "title": prec.summary[:80],
                      "body": prec.summary, "meta": {"request_type": prec.request_type, "decision_code": prec.decision_code.value,
                                                      "route_team": prec.route_team, "policy_id": prec.policy_id,
                                                      "policy_version": prec.policy_version, "outcome": prec.outcome},
                      "links": [], "versions": [{"version": 1, "status": prec.status.value}],
                      "sections": [{"slug": "case", "heading": "Past decision", "text": prec.summary[:300]}]}, user)
    page = brain.get(page_id, version)
    current = brain.get(page_id)
    if page is None or (not admin and (current is None or page.version != current.version or page.status != PageStatus.APPROVED)):
        raise HTTPException(status_code=404, detail="Unknown page.")           # hidden versions look the same as missing ones
    versions = [{"version": p.version, "status": p.status.value} for p in brain.all_pages() if p.id == page_id
                and (admin or (current is not None and p.version == current.version))]
    return scrub({"id": page.id, "type": page.type.value, "version": page.version, "status": page.status.value, "title": page.title,
                  "owner": page.owner, "effective_from": page.effective_from.isoformat() if page.effective_from else None,
                  "request_types": page.request_types, "links": page.links, "body": page.body, "meta": page.meta,
                  "versions": sorted(versions, key=lambda v: v["version"]), "sections": page_sections(page)}, user)


@app.get("/api/lint")
def api_lint(user: User = Depends(actor)):
    require_knowledge_admin(user)
    findings = lint(get_brain(), get_store())
    return scrub([f.model_dump() for f in findings], user)


@app.get("/api/brain/{name}")
def api_brain_file(name: str, user: User = Depends(actor)):
    if name not in ("index", "log"):
        raise HTTPException(status_code=404, detail="Unknown file.")
    require_knowledge_admin(user)                              # index.md lists drafts; log.md lists every change
    path = get_brain().dir / f"{name}.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if name == "log":
        text = "\n".join(text.splitlines()[-200:])
    return {"name": f"{name}.md", "text": scrub(text, user)}


def pr_row(p) -> dict:
    return {"id": p.id, "target_page_id": p.target_page_id, "base_version": p.base_version, "status": p.status, "author_id": p.author_id,
            "created_at": p.created_at.isoformat(), "decided_by": p.decided_by, "reason": p.reason, "diff": p.diff,
            "meta_changes": p.meta_changes}


@app.get("/api/prs")
def api_prs(status: str | None = None, user: User = Depends(actor)):
    require_knowledge_admin(user)
    return scrub([pr_row(p) for p in get_store().list_prs(status)], user)


class PRDecisionIn(BaseModel):
    approve: bool


@app.post("/api/prs/{pr_id}/decision")
def api_pr_decision(pr_id: str, body: PRDecisionIn, user: User = Depends(actor)):
    store, brain = get_store(), get_brain()
    try:
        with _lock:
            pr = decide_pr(pr_id, body.approve, user, store, brain)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Unknown PR.") from e
    return scrub(pr_row(pr), user)


@app.get("/api/demo/samples")
def api_demo_samples(user: User = Depends(actor)):
    """Demo mode AND an ops manager / senior reviewer only (anyone else gets the same 404): a synthetic Health ID, and the same ID with a wrong
    last digit, so scenario S8 can be clicked."""
    if not demo_mode() or user.role not in RESET_ROLES:
        raise HTTPException(status_code=404, detail="Not available.")
    from caregrid import health_id

    member = next((m for m in health_id._members(config.DATA_DIR) if m.get("health_id")), None)
    if member is None:
        raise HTTPException(status_code=404, detail="Not available.")
    good = member["health_id"]
    wrong = good[:-1] + str((int(good[-1]) + 1) % 10)
    return {"health_id_valid": good, "health_id_wrong_checksum": wrong}


# ------------------------------------------------------------------ patients (CareGrid Health ID)
@app.get("/api/patients")
def api_patients(user: User = Depends(actor)):
    """The patients this user may open (masked ids). Nothing here is derived from personal details."""
    return scrub(patients_mod.patients_for(user, get_store(), config.DATA_DIR), user)


class LookupIn(BaseModel):
    health_id: str = Field(min_length=1, max_length=64)


@app.post("/api/patients/lookup")
def api_patient_lookup(body: LookupIn, user: User = Depends(actor)):
    """Open a record by a typed Health ID. The ID is in the body, never in a URL or a log line. A wrong checksum is 422; an unknown id and an id
    the caller may not open give the SAME 404. Refusals are audited as record_lookup_denied (role and reason, never the ID)."""
    try:
        rec = patients_mod.lookup(body.health_id, user, get_store(), config.DATA_DIR)
    except patients_mod.BadHealthId as e:
        raise HTTPException(status_code=422, detail="The Health ID does not pass its checksum. Please re-check the digits.") from e
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown patient.")
    return scrub(rec, user)


@app.get("/api/patients/{ref}")
def api_patient(ref: str, user: User = Depends(actor)):
    """ref = the profile key a case or the patient list gives you (PRF-...). A Health ID is not accepted in the URL: use POST /api/patients/lookup.
    Unknown and not-allowed give the same 404. Every successful view is audited (record_viewed)."""
    if ref.upper().startswith("CG"):
        raise HTTPException(status_code=404, detail="Unknown patient.")
    rec = patients_mod.record(ref, user, get_store(), config.DATA_DIR)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown patient.")
    return scrub(rec, user)


class RevealIn(BaseModel):
    case_id: str = Field(min_length=1, max_length=40)
    reason: str = Field(default="", max_length=300)


@app.post("/api/patients/{ref}/reveal")
def api_patient_reveal(ref: str, body: RevealIn, user: User = Depends(actor)):
    """Name, phone and date of birth for ONE linked case: senior reviewers only, a real reason (3+ words), at most 5 per hour (429), audited as
    record_revealed, never stored. `ref` is a profile key."""
    if ref.upper().startswith("CG"):
        raise HTTPException(status_code=404, detail="Unknown patient.")
    try:
        return patients_mod.reveal(ref, user, body.case_id, body.reason, get_store(), config.DATA_DIR)      # deliberately not scrubbed: that is the point
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Unknown patient.") from e
    except patients_mod.RateLimited as e:
        raise HTTPException(status_code=429, detail="Too many reveals this hour (limit 5). Try again later.") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ------------------------------------------------------------------ comms, audit
@app.get("/api/comms")
def api_comms(user: User = Depends(actor)):
    store = get_store()
    out = []
    for c in sorted(store.list_comms(), key=lambda c: c.ts, reverse=True):
        case = store.get_case(c.case_id)
        if case is None or not can_view(user, case, "summary"):
            continue
        out.append({"id": c.id, "case_id": c.case_id, "channel": c.channel.value, "recipient": c.recipient, "message": c.message,
                    "status": c.status, "simulated": c.status == "simulated" or c.channel.value in ("whatsapp", "sms"),
                    "ts": c.ts.isoformat()})
    return scrub(out, user)


@app.get("/api/audit")
def api_audit(case: str | None = None, event: str | None = None, actor_id: str | None = Query(default=None, alias="actor"),
              limit: int = Query(default=300, ge=1, le=2000), user: User = Depends(actor)):
    """Events for cases this user may see. Events without a case (system, PR decisions) only for roles that see the whole system."""
    store = get_store()
    cases = {}
    out = []
    system_ok = user.role.value in ("auditor", "senior_reviewer", "ops_manager", "knowledge_owner")
    for e in sorted(store.list_audit(), key=lambda e: e.ts, reverse=True):
        if case and e.case_id != case:
            continue
        if event and e.event != event:
            continue
        if actor_id and e.actor_id != actor_id:
            continue
        if e.case_id is None:
            if not system_ok:
                continue
        else:
            if e.case_id not in cases:
                cases[e.case_id] = store.get_case(e.case_id)
            c = cases[e.case_id]
            if c is None or not can_view(user, c, "summary"):
                continue
        out.append(audit_row(e))
        if len(out) >= limit:
            break
    return {"events": scrub(out, user), "event_types": sorted({e["event"] for e in out})}


# ------------------------------------------------------------------ static web UI (last, so /api/* wins)
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
