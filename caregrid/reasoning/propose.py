"""Proposal (PROMPTS §2). Code owns every decision; the LLM only writes wording.

- decision_code and route_team come from decide_code()/rules and are force-set AFTER the LLM call.
- questions_for_requester are built by code from missing/invalid fields, so one-shot completeness is guaranteed.
- The LLM's text is discarded (deterministic template used instead) if it contains a number/amount/date/ID that is not in
  the request, the RULES block or the context pages, or any PII, medical advice, or a claim that something is approved/done.
- Citations are built from the context pages in code; LLM-cited ids only count if they were in the context.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from caregrid import config
from caregrid.ingest.leakscan import detect_pii
from caregrid.llm import LLM, complete_json_tiered, model_name
from caregrid.models import (
    Citation, Classification, DecisionCode, PageStatus, PageType, Proposal, ReasonCode, RetrievalResult, Risk, RuleResult,
)
from caregrid.reasoning.confidence import relevant_policies, similar_precedents
from caregrid.reasoning.guards import _MEDICAL_ADVICE, CLINICAL
from caregrid.reasoning.prompts import PROPOSER_SYSTEM
from caregrid.reasoning.rules import decide_code, label

DETERMINISTIC = "deterministic"
MAX_CONTEXT_PRECEDENTS = 3

FORMAT_HINT = {
    "npi": "10 digits", "provider_npi": "10 digits", "member_id": "M followed by 8 digits",
    "auth_id": "PA-YYYY-NNNNN", "claim_id": "CLM- followed by 8 digits", "effective_date": "YYYY-MM-DD",
    "user_email": "a full email address", "equipment_code": "E followed by 4 digits",
    "estimated_cost_inr": "digits only, in rupees", "new_address": "street, city and PIN code",
    "old_name": "full legal name", "new_name": "full legal name",
    "supporting_document": "one of W-9, bank letter or licence copy", "prescription_on_file": "yes or no",
}


# ------------------------------------------------------------------ context
@dataclass(frozen=True)
class CtxItem:
    id: str
    version: int | None
    type: PageType
    title: str
    body: str

    @property
    def header(self) -> str:
        if self.type == PageType.PRECEDENT:
            return f"[{self.id} | precedent | {self.title}]"
        return f"[{self.id} v{self.version} | {self.type.value} | {self.title}]"


def build_context(ret: RetrievalResult, rules: RuleResult | None = None) -> list[CtxItem]:
    """Relevant approved current policies + workflow + team + ACTIVE similar precedents. Stale precedents never appear here.
    A policy named in a conflict is always included, even if it is below the relevance bar: the reviewer must see both sides."""
    in_conflict = " ".join(rules.conflicts) if rules else ""
    shown = [s for s in ret.policies if s.page.type == PageType.POLICY and s.page.status == PageStatus.APPROVED
             and (s.relevance >= config.POLICY_MIN_SCORE or re.search(rf"\b{re.escape(s.page.id)}\b", in_conflict))]
    items = [CtxItem(s.page.id, s.page.version, PageType.POLICY, s.page.title, s.page.body) for s in shown]
    how = ret.howto_workflow
    if how is not None:                    # a how-to answer is the matched workflow plus the policies it links to (approved, current)
        have = {i.id for i in items}
        items += [CtxItem(p.id, p.version, PageType.POLICY, p.title, p.body) for p in ret.howto_policies if p.id not in have]
        items.append(CtxItem(how.id, how.version, PageType.WORKFLOW, how.title, how.body))
    elif ret.workflow:
        w = ret.workflow
        items.append(CtxItem(w.id, w.version, PageType.WORKFLOW, w.title, w.body))
    if ret.team:
        t = ret.team
        items.append(CtxItem(t.id, t.version, PageType.TEAM, t.title, t.body))
    for s in similar_precedents(ret)[:MAX_CONTEXT_PRECEDENTS]:
        p = s.precedent
        body = f"{p.summary}\nDecision: {p.decision_code.value}; team {p.route_team}. Outcome: {p.outcome}"
        items.append(CtxItem(p.id, None, PageType.PRECEDENT, p.request_type, body))
    return items


def context_ids(ret: RetrievalResult, rules: RuleResult | None = None) -> set[str]:
    return {c.id for c in build_context(ret, rules)}


def rules_block(rules: RuleResult, decision: DecisionCode, team: str | None) -> str:
    return (f"decision_code={decision.value}; route_team={team}; risk={rules.risk.value}; missing_fields={rules.missing_fields}; "
            f"invalid_fields={list(rules.invalid_fields.values())}; conflicts={rules.conflicts}; notes={rules.notes}; "
            f"risk_reasons={rules.risk_reasons}")


def choose_tier(rules: RuleResult, cls: Classification, ctx: list[CtxItem], explain: bool = False) -> str:
    """CONTRACTS §6: strong only when needed."""
    policies = [c for c in ctx if c.type == PageType.POLICY]
    precedents = [c for c in ctx if c.type == PageType.PRECEDENT]
    strong = (bool(rules.conflicts) or rules.risk in (Risk.HIGH, Risk.CRITICAL) or (len(policies) >= 2 and len(precedents) >= 1)
              or cls.request_type == "unknown" or explain)
    return "strong" if strong else "light"


# ------------------------------------------------------------------ code-built requester questions
def build_questions(rules: RuleResult, ret: RetrievalResult) -> list[str]:
    meaning = {p.meta.get("field"): p.body for p in ret.fields}
    out: list[str] = []
    for f in rules.required_fields:
        hint = FORMAT_HINT.get(f, "see the form")
        if f in rules.invalid_fields:
            out.append(f"{rules.invalid_fields[f]}. Please re-send the {label(f)} (format: {hint}).")
        elif f in rules.missing_fields:
            what = f" - {meaning[f]}" if meaning.get(f) else ""
            out.append(f"Please provide the {label(f)}{what} (format: {hint}).")
    return out


# ------------------------------------------------------------------ deterministic wording
def _sentences(text: str, n: int) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if p.strip()]
    return parts[:n]


def not_enough_evidence_text(team: str | None) -> str:
    where = team or "the right team"
    return (f"1. I don't have enough approved guidance to answer this.\n"
            f"2. It has been passed to {where} so a person can review it.")


def howto_text(wf) -> tuple[str, list[str]]:
    """The workflow's steps as a numbered list plus its required fields, built by CODE from the page meta: no model may alter a step."""
    steps = [str(s).strip() for s in wf.meta.get("steps", []) if str(s).strip()]
    lines = [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    needed = [label(f) for f in wf.meta.get("required_fields", [])]
    if needed:
        lines.append("You will need: " + ", ".join(needed) + ".")
    return "\n".join(lines), [f"See {wf.id} v{wf.version} for the full workflow."]


def refusal_text(rules: RuleResult) -> str:
    team = rules.route_team or "the right team"
    codes = set(rules.reason_codes)
    if ReasonCode.CLINICAL in codes:
        return f"1. I can't give medical advice.\n2. This question has been routed to Clinical Review ({team})."
    if ReasonCode.ACCESS_DENIED in codes or ReasonCode.SENSITIVE in codes:
        return f"1. I can't help with that request.\n2. It has been routed to {team} for review."
    if ReasonCode.ACCOUNT_SPECIFIC in codes:
        return f"1. I can't share details of a specific record here.\n2. The request has been routed to {team}."
    return f"1. This request needs a person to handle it.\n2. It has been routed to {team}."


def _summary(cls: Classification, decision: DecisionCode, rules: RuleResult, team: str | None) -> str:
    parts = [f"Request type: {cls.request_type}. Decision: {decision.value} (risk {rules.risk.value})."]
    if rules.risk_reasons:
        parts.append("Risk: " + "; ".join(rules.risk_reasons) + ".")
    if rules.missing_fields or rules.invalid_fields:
        bits = [label(f) for f in rules.missing_fields] + [label(f) + " (invalid)" for f in rules.invalid_fields]
        parts.append("Needs: " + ", ".join(bits) + ".")
    if rules.conflicts:
        parts.append("Conflict: " + "; ".join(rules.conflicts) + ".")
    for n in rules.notes:
        if not n.startswith("llm_"):
            parts.append(n[0].upper() + n[1:] + ("" if n.endswith(".") else "."))
    parts.append(f"Recommendation: review and route to {team or 'the right team'}.")
    return " ".join(parts)


def deterministic_wording(decision: DecisionCode, cls: Classification, ret: RetrievalResult, rules: RuleResult,
                          ctx: list[CtxItem], questions: list[str]) -> tuple[str, list[str], str]:
    team = rules.route_team
    where = team or "the right team"
    policies = [c for c in ctx if c.type == PageType.POLICY]
    steps: list[str] = []
    if decision == DecisionCode.ANSWER_FROM_POLICY and ret.howto_workflow is not None:
        answer, steps = howto_text(ret.howto_workflow)
    elif decision == DecisionCode.ANSWER_FROM_POLICY and policies:
        top = policies[0]
        lines = _sentences(top.body, 2)
        answer = "\n".join(f"{i}. {s}" for i, s in enumerate(lines, 1)) or f"1. See {top.id} for the guidance."
        steps = [f"See {top.id} v{top.version} for the full guidance."]
    elif decision == DecisionCode.REQUEST_MISSING_INFO:
        answer = "1. A few details are still needed before this can move forward.\n2. Please send all of them in one reply."
        steps = ["Reply with every requested item in a single message."]
    elif decision == DecisionCode.ESCALATE_SENIOR:
        why = "; ".join(rules.risk_reasons) or f"risk is {rules.risk.value}"
        answer = f"1. This request is high risk ({why}).\n2. It has been prepared for {where} and needs senior review."
        steps = [f"{where} reviews before anything is changed."]
    elif decision == DecisionCode.REFUSE_AND_ROUTE:
        answer = refusal_text(rules)
        steps = [f"{where} will follow up."]
    elif decision == DecisionCode.NOT_ENOUGH_EVIDENCE:
        answer = not_enough_evidence_text(team)
        steps = [f"{where} reviews the request."]
    else:
        answer = f"1. Your request has been prepared for {where}.\n2. A specialist will review it before anything is changed."
        wf_steps = (ret.workflow.meta.get("steps") if ret.workflow else None) or []
        steps = [str(s) for s in wf_steps[:3]] or [f"{where} reviews the request."]
    return answer, steps, _summary(cls, decision, rules, team)


# ------------------------------------------------------------------ LLM output guard
_LIST_MARK = re.compile(r"(?m)^\s*\d+[.)]\s+")
_NUM = re.compile(r"\d[\d,./-]*\d|\d")
_DONE_CLAIM = re.compile(
    r"(?i)\b(?:been|was|were|is now|are now|already)\s+(?:approved|actioned|completed|processed|closed|sent|updated|changed|reset)\b|"
    r"\bapproved on\b")


def numbers_in(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUM.finditer(_LIST_MARK.sub("", text))}


def llm_text_problem(texts: list[str], allowed_source: str) -> str | None:
    """None when the LLM wording is safe to use, else a short machine-readable reason (never the offending text)."""
    allowed = numbers_in(allowed_source)
    joined = "\n".join(texts)
    if numbers_in(joined) - allowed:
        return "unsupported_number"
    if detect_pii(joined):
        return "pii"
    if _MEDICAL_ADVICE.search(joined):
        return "medical_advice"
    if CLINICAL.search(joined) and not CLINICAL.search(allowed_source):
        return "clinical_content"          # drug names, doses, "stop taking": an ops answer never introduces these
    if _DONE_CLAIM.search(joined):
        return "claims_done_or_approved"
    return None


def _as_lines(value) -> list[str]:
    """Small models often return a list where a string is asked for (or the reverse). Accept both shapes; nothing else."""
    if isinstance(value, str):
        return [ln.strip() for ln in value.splitlines() if ln.strip()]
    if isinstance(value, list):
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]
    return []


def _as_text(value) -> str | None:
    lines = _as_lines(value)
    return "\n".join(lines) if lines else None


_ID = re.compile(r"[A-Za-z]+-[A-Za-z0-9]+")


def _llm_cited_ids(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        m = _ID.match(str(item).strip())
        if m:
            out.append(m.group(0))
    return out


def assemble_citations(ctx: list[CtxItem], ret: RetrievalResult, decision: DecisionCode, team: str | None,
                       llm_ids: list[str]) -> list[Citation]:
    """Code-chosen support (policies, workflow, matching precedents) + LLM-cited ids that were in the context.
    Version, type and title come from the context pages (which came from the Brain), never from the LLM."""
    by_id = {c.id: c for c in ctx}
    policies = [c for c in ctx if c.type == PageType.POLICY]
    linked = {s.page.id for s in ret.policies if s.linked}
    # what actually supports the answer: the workflow-linked policies, or (when none is linked) the single best search hit
    if ret.howto_workflow is not None:                  # how-to: the workflow and every policy it links to (the answer is that workflow's steps)
        chosen = [ret.howto_workflow.id] + ([p.id for p in ret.howto_policies] or [c.id for c in policies[:1]])   # no linked policy: the best relevant one
        return [Citation(page_id=c.id, version=c.version, page_type=c.type, title=c.title) for cid in dict.fromkeys(chosen)
                if (c := by_id.get(cid)) is not None]
    chosen: list[str] = [c.id for c in (([c for c in policies if c.id in linked]) or policies[:1])]
    chosen += [c.id for c in ctx if c.type == PageType.WORKFLOW]
    matched = [s.precedent.id for s in similar_precedents(ret)
               if s.precedent.decision_code == decision and s.precedent.route_team == team and s.precedent.id in by_id]
    chosen += matched[:MAX_CONTEXT_PRECEDENTS]
    chosen += [i for i in llm_ids if i in by_id]
    seen, out = set(), []
    for cid in chosen:
        if cid in seen:
            continue
        seen.add(cid)
        c = by_id[cid]
        out.append(Citation(page_id=c.id, version=c.version, page_type=c.type, title=c.title))
    return out


# ------------------------------------------------------------------ entry points
def refusal_proposal(rules: RuleResult, cls: Classification | None = None) -> Proposal:
    """Used when the input guard refuses a request: no retrieval, no LLM."""
    cls = cls or Classification(request_type="unknown")
    answer, steps, summary = deterministic_wording(DecisionCode.REFUSE_AND_ROUTE, cls, RetrievalResult(), rules, [], [])
    return Proposal(decision_code=DecisionCode.REFUSE_AND_ROUTE, route_team=rules.route_team, answer_text=answer,
                    next_steps=steps, summary_for_reviewer=summary, model_used=DETERMINISTIC)


def propose(masked_text: str, cls: Classification, ret: RetrievalResult, rules: RuleResult, llm: LLM,
            explain: bool = False) -> Proposal:
    decision = decide_code(rules, ret, cls)
    team = rules.route_team
    ctx = build_context(ret, rules)
    questions = build_questions(rules, ret)
    answer, steps, summary = deterministic_wording(decision, cls, ret, rules, ctx, questions)
    llm_ids: list[str] = []
    model = DETERMINISTIC

    if ret.howto_workflow is not None and decision == DecisionCode.ANSWER_FROM_POLICY:
        summary = f"How-to question answered from {ret.howto_workflow.id} v{ret.howto_workflow.version}: its steps, listed by code."
    elif decision != DecisionCode.REFUSE_AND_ROUTE:         # refusals are never worded by a model; neither are how-to steps
        tier = choose_tier(rules, cls, ctx, explain)
        user = (f"REQUEST (masked):\n{masked_text}\n\nCLASSIFICATION:\n"
                f"{json.dumps({'request_type': cls.request_type, 'extracted_fields': cls.extracted_fields, 'urgency': cls.urgency, 'sentiment': cls.sentiment})}\n\n"
                f"RULES (fixed, do not change):\n{rules_block(rules, decision, team)}\n\n"
                "CONTEXT PAGES:\n" + "\n\n".join(f"{c.header}\n{c.body}" for c in ctx))
        try:
            wanted = tier
            raw, tier = complete_json_tiered(llm, PROPOSER_SYSTEM, user, tier)
            if tier != wanted and "llm_tier_downgrade" not in rules.notes:
                rules.notes.append("llm_tier_downgrade")
            if not isinstance(raw, dict):
                raise ValueError("proposer did not return a JSON object")
        except Exception:
            if "llm_fallback" not in rules.notes:
                rules.notes.append("llm_fallback")
        else:
            l_answer = _as_text(raw.get("answer_text"))
            l_steps = [s.strip()[:240] for s in _as_lines(raw.get("next_steps"))][:6]
            l_summary = _as_text(raw.get("summary_for_reviewer"))
            if not (l_answer and l_summary):
                rules.notes += ["llm_output_rejected", "llm_reject_reason: malformed"]
            else:
                source = "\n".join([masked_text, rules_block(rules, decision, team), *questions,
                                    *[f"{c.header}\n{c.body}" for c in ctx]])
                problem = llm_text_problem([l_answer, *l_steps, l_summary], source)
                if problem:
                    rules.notes += ["llm_output_rejected", f"llm_reject_reason: {problem}"]
                else:
                    answer, steps, summary = l_answer.strip()[:1500], l_steps or steps, l_summary.strip()[:900]
                    llm_ids = _llm_cited_ids(raw.get("citations"))
                    model = model_name(llm, tier)

    if decision == DecisionCode.REQUEST_MISSING_INFO and questions:
        answer = answer.rstrip() + "\n\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))

    return Proposal(
        decision_code=decision, route_team=team, answer_text=answer, next_steps=steps, questions_for_requester=questions,
        summary_for_reviewer=summary, citations=assemble_citations(ctx, ret, decision, team, llm_ids), model_used=model)
