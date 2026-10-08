"""The request pipeline (CONTRACTS §4). Raw text enters only check_input; everything after sees the masked text.

guards -> classify -> retrieve -> rules -> propose -> verify citations -> score -> output guard -> route -> save,
with a state_history entry and an audit event for every step.
"""
from __future__ import annotations

from datetime import datetime

from caregrid.knowledge.brain import Brain
from caregrid.knowledge.retrieve import attach_howto, find_howto_workflow, retrieve
from caregrid.llm import LLM
from caregrid.models import (
    Band, Case, Channel, Citation, Classification, Confidence, ReasonCode, RetrievalResult, Role, State, User,
)
from caregrid.reasoning.citations import DOWNGRADED, verify_citations
from caregrid.reasoning.classify import FALLBACK_MODEL, classify
from caregrid.reasoning.confidence import MAX, score
from caregrid.reasoning.guards import check_input, check_output
from caregrid.reasoning.propose import build_context, context_ids, propose, refusal_proposal
from caregrid.reasoning.rules import apply_rules
from caregrid.store import Store
from caregrid.workflow.audit import log
from caregrid.workflow.routing import decide_route, set_state

# Output is checked for PII and medical advice before storage. Amount redaction is a DISPLAY concern (per viewer role),
# so the stored text is checked as a role that may see amounts.
_STORAGE_VIEWER = User(id="system", name="System", role=Role.SENIOR_REVIEWER)


def _to(store: Store, case: Case, state: State) -> None:
    before = case.state
    set_state(case, state)
    if case.state != before:
        log(store, "state_changed", None, case.id, **{"from": before.value, "to": case.state.value})


def _clean_output(case: Case, store: Store) -> None:
    prop = case.proposal
    assert prop is not None
    issues: list[str] = []

    def clean(text: str) -> str:
        ok, out, found = check_output(text, _STORAGE_VIEWER)
        issues.extend(found)
        return out

    case.proposal = prop.model_copy(update={
        "answer_text": clean(prop.answer_text), "next_steps": [clean(s) for s in prop.next_steps],
        "questions_for_requester": [clean(q) for q in prop.questions_for_requester],
        "summary_for_reviewer": clean(prop.summary_for_reviewer)})
    if issues:
        log(store, "output_checked", None, case.id, issues=sorted(set(issues)))


def _answered(llm: LLM) -> list[str]:
    """Tiers that actually answered (a strong call retried on light counts as light); providers without it fall back to attempted calls."""
    answered = getattr(llm, "tiers_used", None)
    return list(answered if isinstance(answered, list) else getattr(llm, "calls", []))


def _failed(llm: LLM) -> list[str]:
    failed = getattr(llm, "failures", None)
    return list(failed) if isinstance(failed, list) else []


def log_llm_failures(store: Store, case: Case, types: list[str]) -> None:
    """Failure TYPES only (timeout | http_503 | http_429 | parse_error | other), never payloads, in the audit log and the case notes."""
    if not types:
        return
    log(store, "llm_failure", None, case.id, types=types)
    if case.rules is not None:
        for t in dict.fromkeys(types):
            note = f"llm_failure: {t}"
            if note not in case.rules.notes:
                case.rules.notes.append(note)


def _route_and_save(case: Case, store: Store, llm: LLM, calls_before: tuple[int, int]) -> Case:
    log_llm_failures(store, case, _failed(llm)[calls_before[1]:])
    before = case.state
    decide_route(case, store.get_trust(case.classification.request_type if case.classification else "unknown"))
    if case.state != before:
        log(store, "state_changed", None, case.id, **{"from": before.value, "to": case.state.value})
    log(store, "routed", None, case.id, routing=case.routing, state=case.state.value, team=case.assigned_team,
        approver_role=case.approver_role.value if case.approver_role else None, trust_level=case.trust_level,
        reason_codes=[c.value for c in case.reason_codes])
    if case.routing == "auto":
        log(store, "auto_with_audit", None, case.id, state=case.state.value, trust_level=case.trust_level)
    calls = _answered(llm)[calls_before[0]:]
    case.llm_tiers_used = list(dict.fromkeys(calls))
    store.save_case(case)
    return case


def run(text: str, user: User, store: Store, brain: Brain, llm: LLM, channel: Channel = Channel.PORTAL,
        case_id: str | None = None) -> Case:
    now = datetime.now()
    case_id = case_id or store.next_case_id()
    calls_before = (len(_answered(llm)), len(_failed(llm)))
    case = Case(id=case_id, created_at=now, requester=user, channel=channel, masked_text="", state=State.NEW,
                state_history=[(State.NEW, now)])

    # 1 guards: the only place raw text is read
    guard = check_input(text, user)
    case.masked_text = guard.masked_text
    log(store, "request_received", user, case_id, channel=channel.value, pii_types=guard.pii_types_found,
        health_id_masked=guard.health_id_masked)
    if guard.patient_key:                                      # a valid, known Health ID: the case belongs on that patient's timeline
        case.related = {"profile": [guard.patient_key]}
        log(store, "patient_linked", user, case_id)

    if not guard.allowed:                                      # injection / access denied / too long: refuse and route
        cls = Classification(request_type="unknown", model_used="guard",
                             is_clinical=ReasonCode.CLINICAL in guard.overrides,
                             is_sensitive=ReasonCode.SENSITIVE in guard.overrides,
                             is_account_specific=ReasonCode.ACCOUNT_SPECIFIC in guard.overrides)
        case.classification = cls
        log(store, "guard_blocked", user, case_id, overrides=[c.value for c in guard.overrides], injection=guard.injection,
            notes=guard.notes)
        rules = apply_rules(cls, RetrievalResult(), brain, guard)
        case.rules = rules
        case.proposal = refusal_proposal(rules, cls)
        case.confidence = Confidence(score=0, band=Band.LOW,
                                     breakdown={k: 0 for k in MAX}, explanation="Blocked by the input guard; no evidence was gathered.")
        log(store, "proposal_generated", None, case_id, decision_code=case.proposal.decision_code.value, model_used="deterministic")
        _clean_output(case, store)
        return _route_and_save(case, store, llm, calls_before)

    # 2 classify (light tier, masked text only); guard flags can only be added to, never cleared
    cls = classify(guard.masked_text, llm)
    cls = cls.model_copy(update={
        "is_clinical": cls.is_clinical or ReasonCode.CLINICAL in guard.overrides,
        "is_account_specific": cls.is_account_specific or ReasonCode.ACCOUNT_SPECIFIC in guard.overrides,
        "is_sensitive": cls.is_sensitive or ReasonCode.SENSITIVE in guard.overrides})
    howto = find_howto_workflow(brain, guard.masked_text, llm) if not (cls.is_clinical or cls.is_sensitive) else None
    if howto is not None:                                      # "how do I ...": a question about the process, answered with the workflow's steps
        cls = cls.model_copy(update={"request_type": "general_policy_question", "rules_type": "general_policy_question",
                                     "is_account_specific": False, "extracted_fields": {}})
    case.classification = cls
    _to(store, case, State.CLASSIFIED)
    log(store, "classified", None, case_id, request_type=cls.request_type, llm_confidence=cls.llm_confidence,
        rules_type=cls.rules_type, model_used=cls.model_used, fields=sorted(cls.extracted_fields), howto=howto.id if howto else None)

    # 3 retrieve
    ret = retrieve(brain, cls, guard.masked_text, llm)
    attach_howto(ret, brain, howto)
    rules = apply_rules(cls, ret, brain, guard)             # computed here: a conflicting policy joins the context
    if cls.model_used == FALLBACK_MODEL and "llm_fallback" not in rules.notes:
        rules.notes.append("llm_fallback")
    ctx = build_context(ret, rules)
    case.citations_considered = [Citation(page_id=c.id, version=c.version, page_type=c.type, title=c.title) for c in ctx]
    log(store, "context_assembled", None, case_id, workflow=ret.workflow.id if ret.workflow else None,
        team=ret.team.id if ret.team else None, context=[c.id for c in ctx], case_facts=ret.case_facts)
    log(store, "policy_identified", None, case_id, linked=[f"{s.page.id}@v{s.page.version}" for s in ret.policies if s.linked],
        searched=[f"{s.page.id}@v{s.page.version}" for s in ret.policies if not s.linked])
    log(store, "precedent_identified", None, case_id,
        active=[[s.precedent.id, s.similarity] for s in ret.precedents_active[:5]],
        stale=[s.precedent.id for s in ret.precedents_stale])

    # 4 rules (applied above, logged here to keep the audit order)
    log(store, "rules_applied", None, case_id, risk=rules.risk.value, hard_override=rules.hard_override,
        missing=rules.missing_fields, invalid=sorted(rules.invalid_fields), conflicts=len(rules.conflicts),
        reason_codes=[c.value for c in rules.reason_codes])

    # 5-6 propose, then verify every citation against the Brain
    proposal = propose(guard.masked_text, cls, ret, rules, llm)
    proposal, issues = verify_citations(proposal, brain, context_ids(ret, rules))
    if DOWNGRADED in issues and ReasonCode.POLICY_GAP not in rules.reason_codes:
        rules.reason_codes = [*rules.reason_codes, ReasonCode.POLICY_GAP]
    log(store, "proposal_generated", None, case_id, decision_code=proposal.decision_code.value, team=proposal.route_team,
        model_used=proposal.model_used, notes=[n for n in rules.notes if n.startswith("llm_")])
    log(store, "citations_verified", None, case_id,
        kept=[f"{c.page_id}" + (f"@v{c.version}" if c.version else "") for c in proposal.citations], issues=issues)

    # 7 score (deterministic)
    conf = score(cls, ret, rules, proposal)
    log(store, "confidence_scored", None, case_id, score=conf.score, band=conf.band.value, breakdown=conf.breakdown)
    case.rules, case.proposal, case.confidence = rules, proposal, conf
    _to(store, case, State.PROPOSED)

    # 8 output guard, 9 route, 10 save
    _clean_output(case, store)
    return _route_and_save(case, store, llm, calls_before)
