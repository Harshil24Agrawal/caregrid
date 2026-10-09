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


def _first_sentence(text: str, limit: int = 220) -> str:
    one = " ".join((text or "").split())
    cut = re.split(r"(?<=[.!?])\s", one, maxsplit=1)[0]
    return cut if len(cut) <= limit else cut[: limit - 1].rstrip() + "\u2026"


def _intent(question: str) -> str:
    """The closest chip for a question: prepare | related | next | explain | policy | why."""
    q = question or ""
    if _PREPARE.search(q):
        return "prepare"
    if _RELATED.search(q):
        return "related"
    if _NEXT.search(q):
        return "next"
    if _EXPLAIN.search(q) and not _WHY.search(q):
        return "explain"
    if _POLICY.search(q) and not _WHY.search(q):
        return "policy"
    return "why"


_COMPONENT = {"policy": "policy evidence", "precedent": "similar past cases", "fields": "required details", "clarity": "request clarity",
              "no_conflict": "no policy conflict"}


def deterministic_answer(question: str, ctx: Context, case: Case, user: User, store: Store, brain: Brain | None = None) -> tuple[str, list[str]]:
    """Built from the case story and the role-filtered context only: no model, no invented facts. Every answer is a few plain lines that cite
    the pages they rest on (the API adds the 'Sources' line). The same text answers the suggested chips and any free-text question when the model
    is the mock or unavailable (the closest chip wins)."""
    from caregrid.insights.explain import REASON_PLAIN, _team_name
    from caregrid.insights.story import story as build_story

    f = ctx.facts
    full = f["full"]
    brain = brain or Brain(config.BRAIN_DIR)
    st = build_story(case, brain, user)
    prop, rules = case.proposal, case.rules
    pols = [c for c in (prop.citations if prop else []) if c.page_type.value == "policy"]
    wfs = [c for c in (prop.citations if prop else []) if c.page_type.value == "workflow"]
    cites = [c.page_id for c in (prop.citations if prop else [])]
    inv = f["invoices"] if f["billing"] else []
    team = _team_name(brain, case.assigned_team)
    role = (case.approver_role.value if case.approver_role else "person").replace("_", " ")
    kind = _intent(question)
    ref = lambda c: f"[{c.page_id}{f' v{c.version}' if c.version else ''}]"      # noqa: E731

    def rule_line(c) -> str:
        page = brain.get(c.page_id, c.version) if c.version else brain.get(c.page_id)
        return f"{ref(c)} {c.title}: {_first_sentence(page.body) if page else 'see the page'}"

    if kind == "prepare":
        flagged = [r["label"] + " (" + r["text"] + ")" for r in st["checks"] if r["status"] != "ok"]
        lines = [f"Approval brief for {case.id}:",
                 f"1. Request: {st['problem']}",
                 "2. Checks: " + (f"{sum(r['status'] == 'ok' for r in st['checks'])} of {len(st['checks'])} passed; to look at: " + "; ".join(flagged) if flagged else "all passed") + ".",
                 f"3. Recommendation: {st['decision']['text']}" + (f" Confidence {st['decision']['confidence']['score']} ({st['decision']['confidence']['band']})." if st["decision"]["confidence"] else ""),
                 "4. " + ("You may approve this case." if can_approve(user, case) else "You cannot approve this case with your role: " + (f"a {role} decides.")),
                 f"5. After approval: {(st['next_steps'][1] if len(st['next_steps']) > 1 else 'nothing further.').removeprefix('On approval: ')}"]
        if f.get("conflicts"):
            lines.append("6. Conflicts to resolve first: " + "; ".join(f["conflicts"]) + ".")
        return "\n".join(lines), cites + inv
    if kind == "related":
        precs = [p for p in (prop.citations if prop else []) if p.page_type.value == "precedent"]
        others = [c for c in visible_cases(user, store) if c.id != case.id and c.classification and case.classification
                  and c.classification.request_type == case.classification.request_type][:5]
        lines = []
        if others:
            lines.append("Other cases of this type you can see:")
            lines += [f"- {c.id} ({c.state.value.replace('_', ' ')}): {_first_sentence(c.masked_text, 90)}" for c in others]
        if precs:
            lines.append("Past decisions this case was compared with:")
            for c in precs[:4]:
                pr = brain.get_precedent(c.page_id)
                lines.append(f"- [{c.page_id}] " + (f"{pr.decision_code.value.replace('_', ' ')} to {pr.route_team or 'the team'}; outcome: {_first_sentence(pr.outcome, 110)}" if pr else "past decision"))
        return ("\n".join(lines) if lines else NO_EVIDENCE + " No related cases or past decisions are visible to you."), [c.page_id for c in precs]
    if kind == "next":
        return "What to do next:\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(st["next_steps"], 1)), cites
    if kind == "explain":
        d = st["decision"]
        lines = [f"The recommendation: {d['text']}"]
        if d["confidence"]:
            lines.append(f"Confidence is {d['confidence']['score']} ({d['confidence']['band']}).")
        if f.get("breakdown"):
            lines.append("It comes from " + ", ".join(f"{_COMPONENT.get(k, k)} {v}/{ {'policy': 30, 'precedent': 25, 'fields': 20, 'clarity': 15, 'no_conflict': 10}.get(k, 0) }" for k, v in f["breakdown"].items()) + ".")
            if rules and rules.conflicts:
                lines.append("A policy conflict caps the confidence at Medium.")
        else:
            lines.append("The breakdown of the score is limited for your role.")
        if full and f.get("summary") and prop and not prop.model_used.startswith(("mock", "deterministic", "seed")):
            lines.append(f["summary"])
        return " ".join(lines[:2]) + ("\n" + " ".join(lines[2:]) if len(lines) > 2 else ""), cites
    if kind == "policy":
        if not pols:
            return NO_EVIDENCE + " No approved policy was cited for this case.", []
        return "Policies that apply:\n" + "\n".join("- " + rule_line(c) for c in pols), [c.page_id for c in pols]
    # why is it flagged
    lines = ["This case is flagged because:"]
    plain = [REASON_PLAIN[r] for r in f["reasons"] if r in REASON_PLAIN and not (r == "HIGH_RISK" and f.get("risk"))]
    lines += [f"- {x[:1].upper() + x[1:]}." for x in plain] or ["- It needs a person to decide."]
    if f.get("risk"):
        lines.append(f"- Risk is {f['risk'].upper()}" + (": " + "; ".join(r.rstrip(".") for r in f.get("risk_reasons", [])) if full and f.get("risk_reasons") else "") + ".")
    for c in f.get("conflicts", []):
        lines.append("- Policy conflict: " + c + ".")
    lines.append(f"It goes to {team}; a {role} must approve.")
    if inv:
        lines.append("Evidence on file: " + ", ".join(inv) + ".")
    for c in pols[:2]:
        lines.append(rule_line(c))
    wf = brain.get(wfs[0].page_id) if wfs else None
    if wf is not None and wf.meta.get("steps"):
        lines.append(f"[{wf.id}] {wf.title}: {str(wf.meta['steps'][0]).rstrip('.')}.")
    if not full:
        lines.append("More detail is limited for your role.")
    return "\n".join(lines), cites + inv


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
    text, cites = deterministic_answer(question, ctx, case, user, store, brain)
    model, tier = "deterministic", None
    ctx.text += "\n\nCASE STORY (facts built by code for this viewer; answer from these and cite their ids):\n" + text
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
