"""Context-aware case assistant (PROMPTS section 3).

Order of work, which is the safety argument: (1) the CASE CONTEXT is built ONLY from sections the viewer may see, BEFORE any model call;
(2) a question that asks for something the role cannot see gets the ACCESS RESTRICTED line without calling a model; (3) clinical questions are
refused; (4) otherwise the light tier answers (strong for why / explain), and the reply is accepted only if it adds no number, date, PII,
medical advice or "done" claim that is not in the context and cites only ids that are in the context; otherwise a deterministic answer built
from the same context is returned; (5) every reply passes check_output for the viewer.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field

from caregrid import config
from caregrid.knowledge.brain import Brain
from caregrid.llm import LLM, call_with_timeout, model_name, timeout_for
from caregrid.models import Case, User
from caregrid.rbac import can_approve, can_view, visible_cases
from caregrid.reasoning.guards import CLINICAL, check_output
from caregrid.reasoning.propose import llm_text_problem
from caregrid.store import Store

RESTRICTED = "ACCESS RESTRICTED — you don't have permission to view this information. Please contact the authorized team."
CLINICAL_REFUSAL = "I can't give medical advice. This case can be routed to Clinical Review."
NO_EVIDENCE = "I don't have enough evidence to answer that."
CHIPS = ["Why is this case flagged?", "Which policy applies?", "Show related cases", "Explain the recommendation", "What should I do next?",
         "Prepare for approval"]

ASSISTANT_SYSTEM = """You are the CareGrid assistant inside the application. The user is {user_name} ({role}).
You are looking at case {case_id}. Answer ONLY from the CASE CONTEXT and CONTEXT PAGES below.
Cite page ids and case evidence ids in square brackets, e.g. [KA-40] [INV-1024].
If the user asks for information their role cannot see, reply exactly:
"{restricted}"
If asked for medical advice, refuse and say the case can be routed to Clinical Review.
If the context doesn't contain the answer, say "I don't have enough evidence to answer that" and name what is missing.
Never invent policies, approvals, amounts or past cases."""

_AMOUNT_Q = re.compile(r"(?i)\b(?:amount|cost|price|how much|invoice|billing|bill|balance|₹|rupee|rs\.?|inr|payment)\b")
_PROFILE_Q = re.compile(r"(?i)\b(?:profile|npi|phone|e-?mail|address of|date of birth|dob|home address|contact details)\b")
_LOGS_Q = re.compile(r"(?i)\b(?:logs?|jira|ticket|runbook|system error)\b")
_WHY = re.compile(r"(?i)\b(?:why|flag\w*|risk\w*|high risk|reason)\b")
_POLICY = re.compile(r"(?i)\b(?:polic\w*|rule|article|which (?:ka|page)|applies|apply)\b")
_RELATED = re.compile(r"(?i)\b(?:related|similar|other cases|precedents?|past cases?)\b")
_EXPLAIN = re.compile(r"(?i)\b(?:explain|recommend\w*|confidence|score|why .*(?:route|decid))\b")
_NEXT = re.compile(r"(?i)\b(?:next|what should i do|what now|todo)\b")
_PREPARE = re.compile(r"(?i)\b(?:prepare|approval|approve|handoff|checklist)\b")
_CITED = re.compile(r"\[([A-Za-z]+-[A-Za-z0-9]+)\]")


@dataclass
class Reply:
    text: str
    citations: list[str] = field(default_factory=list)
    restricted: bool = False
    refused: bool = False
    model_used: str = "deterministic"
    tier: str | None = None
    chips: list[str] = field(default_factory=lambda: list(CHIPS))


@dataclass
class Context:
    text: str                          # what a model may see
    ids: set[str]                      # every id that may be cited
    facts: dict                        # structured pieces for the deterministic answer


def _billing_row(ref: str) -> dict | None:
    path = config.DATA_DIR / "billing.csv"
    if not path.exists():
        return None
    with open(path, encoding="utf-8", newline="") as f:
        return next((r for r in csv.DictReader(f) if r["invoice_id"] == ref), None)


def build_context(case: Case, user: User, brain: Brain, store: Store) -> Context:
    """CASE CONTEXT, role-filtered. Nothing the viewer cannot see enters this text."""
    full = can_view(user, case, "full")
    billing = can_view(user, case, "billing")
    logs = can_view(user, case, "logs")
    profile = can_view(user, case, "profile")
    clean = lambda t: check_output(t or "", user)[1]   # noqa: E731
    rules, prop, conf = case.rules, case.proposal, case.confidence
    ids: set[str] = {case.id}
    lines = [f"case {case.id}: type {case.classification.request_type if case.classification else 'unknown'}, state {case.state.value}, "
             f"team {case.assigned_team}, routing {case.routing}",
             f"request (masked): {clean(case.masked_text)}"]
    facts: dict = {"case": case, "full": full, "billing": billing, "cites": [], "invoices": [], "reasons": [r.value for r in case.reason_codes]}
    if rules:
        lines.append(f"risk {rules.risk.value}")
        facts["risk"] = rules.risk.value
        if full:
            risk_reasons = [clean(r) for r in rules.risk_reasons]
            lines += [f"risk reason: {r}" for r in risk_reasons]
            lines += [f"conflict: {clean(c)}" for c in rules.conflicts] + [f"missing field: {f}" for f in rules.missing_fields]
            facts.update(risk_reasons=risk_reasons, conflicts=[clean(c) for c in rules.conflicts], missing=list(rules.missing_fields),
                         invalid={k: clean(v) for k, v in rules.invalid_fields.items()}, notes=[clean(n) for n in rules.notes])
    if conf:
        lines.append(f"confidence {conf.score} ({conf.band.value})")
        facts["confidence"] = (conf.score, conf.band.value)
        if full:
            lines.append(f"confidence parts: {conf.breakdown}")
            facts["breakdown"] = dict(conf.breakdown)
    if case.reason_codes:
        lines.append("reason codes: " + ", ".join(facts["reasons"]))
    pages = []
    if prop:
        lines.append(f"proposal: {prop.decision_code.value} -> {prop.route_team or case.assigned_team}")
        facts.update(decision=prop.decision_code.value, team=prop.route_team or case.assigned_team,
                     next_steps=[clean(s) for s in prop.next_steps])
        if full:
            lines.append(f"reviewer summary: {clean(prop.summary_for_reviewer)}")
            facts["summary"] = clean(prop.summary_for_reviewer)
        for c in prop.citations:
            ids.add(c.page_id)
            facts["cites"].append((c.page_id, c.version, c.page_type.value, clean(c.title)))
            page = brain.get(c.page_id, c.version) if c.version else brain.get(c.page_id)
            if page is not None:
                pages.append(f"[{page.id}{f' v{page.version}' if c.version else ''}] {clean(page.title)}: {clean(page.body)[:500]}")
    for key, section_ok in (("profile", profile), ("invoice", billing), ("logs", logs), ("jira", logs), ("runbook", logs)):
        refs = case.related.get(key, [])
        if refs and section_ok:
            ids.update(refs)
            lines.append(f"related {key}: {', '.join(refs)}")
            if key == "invoice":
                facts["invoices"] = list(refs)
                for ref in refs:
                    row = _billing_row(ref)
                    if row and billing:
                        lines.append(f"invoice {ref}: status {row['status']}, due {row['due_date']}")
        elif refs:
            lines.append(f"related {key}: restricted for {user.role.value}")
    text = "CASE CONTEXT (role-filtered):\n" + "\n".join(lines) + "\n\nCONTEXT PAGES:\n" + ("\n".join(pages) or "(none)")
    return Context(text=text, ids=ids, facts=facts)


def _restricted_for(question: str, case: Case, user: User) -> bool:
    if _AMOUNT_Q.search(question) and not can_view(user, case, "billing"):
        return True
    if _PROFILE_Q.search(question) and not can_view(user, case, "profile"):
        return True
    return bool(_LOGS_Q.search(question) and not can_view(user, case, "logs"))


def _cite(ids: list[str]) -> str:
    return " ".join(f"[{i}]" for i in dict.fromkeys(ids))


def deterministic_answer(question: str, ctx: Context, case: Case, user: User, store: Store) -> tuple[str, list[str]]:
    """Built from the structured context only: no model, no invented facts."""
    f = ctx.facts
    cites = [c[0] for c in f["cites"]]
    inv = f["invoices"] if f["billing"] else []
    if _PREPARE.search(question):
        parts = ["Handoff checklist:"]
        parts.append(f"Proposed: {f.get('decision', 'n/a')} -> {f.get('team')}.")
        if f.get("missing") or f.get("invalid"):
            parts.append("Still missing: " + ", ".join(list(f.get("missing", [])) + list(f.get("invalid", {}))) + ".")
        elif f["full"]:
            parts.append("All required fields are present.")
        if f.get("conflicts"):
            parts.append("Conflicts to resolve: " + "; ".join(f["conflicts"]) + ".")
        parts.append("You may approve this case." if can_approve(user, case) else "You cannot approve this case with your role.")
        return " ".join(parts) + " " + _cite(cites + inv), cites + inv
    if _RELATED.search(question):
        others = [c for c in visible_cases(user, store) if c.id != case.id and c.classification and case.classification
                  and c.classification.request_type == case.classification.request_type][:5]
        precs = [c[0] for c in f["cites"] if c[2] == "precedent"]
        bits = []
        if others:
            bits.append("Other visible cases of this type: " + ", ".join(f"{c.id} ({c.state.value})" for c in others) + ".")
        if precs:
            bits.append("Precedents considered: " + _cite(precs) + ".")
        return (" ".join(bits) or NO_EVIDENCE + " No related cases are visible to you."), precs
    if _NEXT.search(question):
        steps = f.get("next_steps") or []
        text = "Next steps: " + "; ".join(steps) + "." if steps else NO_EVIDENCE + " The proposal lists no next steps."
        return text + " " + _cite(cites), cites
    if _EXPLAIN.search(question) and not _WHY.search(question):
        bits = [f"The recommendation is {f.get('decision', 'n/a')} -> {f.get('team')}."]
        if f.get("confidence"):
            bits.append(f"Confidence {f['confidence'][0]} ({f['confidence'][1]}).")
        if f.get("breakdown"):
            bits.append("Parts: " + ", ".join(f"{k} {v}" for k, v in f["breakdown"].items()) + ".")
        if f.get("summary"):
            bits.append(f["summary"])
        return " ".join(bits) + " " + _cite(cites), cites
    if _POLICY.search(question) and not _WHY.search(question):
        pol = [c for c in f["cites"] if c[2] == "policy"]
        if not pol:
            return NO_EVIDENCE + " No policy was cited for this case.", []
        return "Policies cited: " + "; ".join(f"[{c[0]}{f' v{c[1]}' if c[1] else ''}] {c[3]}" for c in pol) + ".", [c[0] for c in pol]
    # default and "why": reasons, risk reasons, conflicts
    bits = []
    if f.get("risk"):
        bits.append(f"Risk is {f['risk'].upper()}.")
    if f["reasons"]:
        bits.append("Reason codes: " + ", ".join(f["reasons"]) + ".")
    for r in f.get("risk_reasons", []):
        bits.append(r.rstrip(".") + ".")
    for c in f.get("conflicts", []):
        bits.append("Conflict: " + c + ".")
    if not f["full"]:
        bits.append("Detailed reasoning is limited for your role.")
    if not bits:
        return NO_EVIDENCE, []
    return " ".join(bits) + " " + _cite(cites + inv), cites + inv


def ask(case: Case, question: str, user: User, brain: Brain, store: Store, llm: LLM) -> Reply:
    question = (question or "").strip()[:500]
    if not can_view(user, case, "summary"):
        raise PermissionError("no access to this case")
    if not question:
        return Reply(NO_EVIDENCE + " Ask a question about this case.")
    if CLINICAL.search(question):
        return Reply(CLINICAL_REFUSAL, refused=True)
    if _restricted_for(question, case, user):
        return Reply(RESTRICTED, restricted=True)
    ctx = build_context(case, user, brain, store)
    text, cites = deterministic_answer(question, ctx, case, user, store)
    model, tier = "deterministic", None
    wants_strong = bool(_WHY.search(question) or _EXPLAIN.search(question))
    if config.LLM_PROVIDER != "mock":
        tier = "strong" if wants_strong else "light"
        system = ASSISTANT_SYSTEM.format(user_name=user.name, role=user.role.value, case_id=case.id, restricted=RESTRICTED)
        try:
            raw = call_with_timeout(lambda: llm.complete_text(system, f"{ctx.text}\n\nQUESTION:\n{question}", tier), timeout_for(tier))
            raw = (raw or "").strip()
            cited = set(_CITED.findall(raw))
            if raw and not raw.startswith("{") and cited <= ctx.ids and llm_text_problem([raw], ctx.text + "\n" + question) is None:
                text, cites, model = raw, sorted(cited), model_name(llm, tier)
        except Exception:
            pass                                           # the deterministic answer above stands
    cleaned = check_output(text, user)[1]
    return Reply(cleaned, citations=[c for c in dict.fromkeys(cites) if c in ctx.ids], model_used=model, tier=tier)
