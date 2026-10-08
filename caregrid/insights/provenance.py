"""Where every claim on a case comes from ("Why this decision"): for each claim the page, version, section and file that backs it.

Built by CODE from the stored case and the Second Brain, never by a model. A source is listed only when it is VERIFIED: the page exists, is
the approved current version (an ACTIVE precedent), and the viewer may see that part of the case. Anything the viewer may not see comes back as
{"restricted": true} with no source; a claim whose source cannot be verified is not given a source at all.

Entry: {claim, source_kind (policy|workflow|precedent|routing_rule|field_definition|threshold|guard), source_id, version, section, section_heading,
title, location, restricted}. `location` is a repo-relative file path, e.g. second_brain/policy/KA-12@v3.md or
second_brain/config/routing_rules.csv#RR-01. `section` is the anchor the Knowledge page scrolls to.
"""
from __future__ import annotations

import csv
import re
from typing import Callable

from caregrid.ingest.pagefmt import precedent_relpath
from caregrid.insights.explain import SOURCE_ROOT, TYPE_LABEL, _team_name, page_location
from caregrid.knowledge.brain import Brain
from caregrid.knowledge.retrieve import field_page_id
from caregrid.models import Case, DecisionCode, Page, PageStatus, PageType, User
from caregrid.rbac import can_view
from caregrid.reasoning.confidence import MAX
from caregrid.reasoning.rules import label

GUARD_LOCATION = "caregrid/reasoning/guards.py"
ROUTING_FILE = f"{SOURCE_ROOT}/config/routing_rules.csv"
_MONEY = re.compile(r"[^.!?\n]*(?:₹\s?[\d,]+|threshold|limit)[^.!?\n]*[.!?]?", re.I)
_RR_FOR = {"blocked": "RR-11", "clinical": "RR-09", "unknown": "RR-10"}


# ------------------------------------------------------------------ sections of a page (also served by /api/pages/{id})
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


def page_sections(page: Page) -> list[dict]:
    """[{slug, heading, text}] the Knowledge page can scroll to: markdown headings, plus the sections a reader looks for in each page type."""
    out: list[dict] = []
    for m in re.finditer(r"(?m)^#{1,4}\s+(.+?)\s*$", page.body):
        out.append({"slug": _slug(m.group(1)), "heading": m.group(1), "text": ""})
    meta = page.meta or {}
    if page.type == PageType.POLICY:
        out.append({"slug": "policy-text", "heading": "Policy text", "text": " ".join(page.body.split())})
        hit = _MONEY.search(page.body)
        if hit:
            out.append({"slug": "threshold", "heading": "Threshold", "text": " ".join(hit.group(0).split())})
        if meta.get("rule_key"):
            out.append({"slug": "rule", "heading": "Rule", "text": f"{meta['rule_key']} = {meta.get('rule_value')}"})
    elif page.type == PageType.WORKFLOW:
        out.append({"slug": "request-type", "heading": "Request type", "text": str(meta.get("request_type") or ", ".join(page.request_types))})
        out.append({"slug": "required-fields", "heading": "Required fields", "text": ", ".join(label(f) for f in meta.get("required_fields", []))})
        out.append({"slug": "steps", "heading": "Steps", "text": " ".join(f"{i}. {s}" for i, s in enumerate(meta.get("steps", []), 1))})
        out.append({"slug": "risk", "heading": "Risk", "text": f"base risk {meta.get('risk', 'low')}; action tier {meta.get('action_tier', 'read')}"})
        if meta.get("thresholds"):
            out.append({"slug": "thresholds", "heading": "Thresholds", "text": ", ".join(f"{k} {v}" for k, v in meta["thresholds"].items())})
        out.append({"slug": "routing", "heading": "Routing", "text": f"team {meta.get('team')}; rule {meta.get('routing_rule')}; approver {meta.get('approver_role')}"})
    elif page.type == PageType.FIELD:
        out.append({"slug": "definition", "heading": "Definition", "text": " ".join(page.body.split())})
    else:
        out.append({"slug": "overview", "heading": "Overview", "text": " ".join(page.body.split())[:300]})
    seen, unique = set(), []
    for s in out:
        if s["slug"] not in seen:
            seen.add(s["slug"])
            unique.append(s)
    return unique


def _heading(page: Page, slug: str) -> str:
    return next((s["heading"] for s in page_sections(page) if s["slug"] == slug), "Page")


# ------------------------------------------------------------------ entries
def _page_source(kind: str, page: Page, slug: str) -> dict:
    return {"source_kind": kind, "source_id": page.id, "version": page.version, "section": slug, "section_heading": _heading(page, slug),
            "title": page.title, "location": page_location(page), "restricted": False}


def _verified(brain: Brain, page: Page | None) -> Page | None:
    """The page if it exists and is not a draft. A page the case CITED may have been superseded since: it is still the source (marked as
    superseded), never replaced by the current version or another page."""
    return page if page is not None and page.status != PageStatus.DRAFT else None


def resolve(brain: Brain, case: Case | None, page_id: str) -> Page | None:
    """The page at the version the case cited (any status), else the current approved page."""
    version = None
    if case is not None:
        version = next((c.version for c in (case.proposal.citations if case.proposal else []) if c.page_id == page_id and c.version), None) \
            or next((c.version for c in case.citations_considered if c.page_id == page_id and c.version), None)
    return brain.get(page_id, version) if version else brain.get(page_id)


def _supersession(brain: Brain, src: dict) -> dict:
    """superseded_by = the current version number when the cited version is no longer current; retired = no approved version is left."""
    out = {"superseded_by": None, "retired": False}
    if src.get("version") and src.get("source_kind") != "routing_rule" and src.get("source_id"):
        current = brain.get(src["source_id"])
        if current is None:
            out["retired"] = True
        elif current.version != src["version"]:
            out["superseded_by"] = current.version
    return out


def _routing_row(brain: Brain, rr_id: str) -> dict | None:
    path = brain.dir / "config" / "routing_rules.csv"
    if not rr_id or not path.exists():
        return None
    with open(path, encoding="utf-8", newline="") as f:
        return next((r for r in csv.DictReader(f) if r["id"] == rr_id), None)


def _routing_source(brain: Brain, rr_id: str) -> dict | None:
    row = _routing_row(brain, rr_id)
    if row is None:
        return None
    return {"source_kind": "routing_rule", "source_id": row["id"], "version": None, "section": row["id"].lower(), "section_heading": f"Rule {row['id']}",
            "title": row["condition"], "location": f"{ROUTING_FILE}#{row['id']}", "restricted": False}


def _guard_source(heading: str) -> dict:
    return {"source_kind": "guard", "source_id": "GUARD", "version": None, "section": None, "section_heading": heading,
            "title": "Input and output guards", "location": GUARD_LOCATION, "restricted": False}


def _precedent_source(brain: Brain, pid: str) -> dict | None:
    prec = brain.get_precedent(pid)
    if prec is None or prec.status != PageStatus.ACTIVE:
        return None
    return {"source_kind": "precedent", "source_id": prec.id, "version": None, "section": "case", "section_heading": "Past decision",
            "title": f"{prec.request_type}: {prec.decision_code.value}", "location": f"{SOURCE_ROOT}/{precedent_relpath(prec)}", "restricted": False}


def format_source(src: dict) -> str:
    """'KA-40 v1 · DME equipment requests · § Threshold · second_brain/policy/KA-40@v1.md' (the same line the UI and the assistant show)."""
    if src.get("restricted") or not src.get("source_id"):
        return "restricted"
    ident = src["source_id"] + (f" v{src['version']}" if src.get("version") else "")
    if src.get("superseded_by"):
        ident += f" (superseded by v{src['superseded_by']})"
    elif src.get("retired"):
        ident += " (retired)"
    parts = [ident, src.get("title") or "", f"§ {src['section_heading']}" if src.get("section_heading") else "", src.get("location") or ""]
    return " · ".join(p for p in parts if p)


def source_for_citation(brain: Brain, page_id: str, case: Case | None = None) -> dict | None:
    src = _source_for_citation(brain, page_id, case)
    return None if src is None else {**src, **_supersession(brain, src)}


def _source_for_citation(brain: Brain, page_id: str, case: Case | None = None) -> dict | None:
    """The source entry for a page the assistant cited (policy -> threshold section when the case's risk comes from it)."""
    prec = _precedent_source(brain, page_id) if brain.get_precedent(page_id) else None
    if prec is not None:
        return prec
    page = _verified(brain, resolve(brain, case, page_id))
    if page is None:
        return None
    if page.type == PageType.WORKFLOW:
        return _page_source("workflow", page, "steps")
    if page.type == PageType.POLICY:
        threshold = any("threshold" in r.lower() for r in (case.rules.risk_reasons if case and case.rules else []))
        sections = {s["slug"] for s in page_sections(page)}
        return _page_source("threshold" if threshold and "threshold" in sections else "policy", page, "threshold" if threshold and "threshold" in sections else "policy-text")
    if page.type == PageType.FIELD:
        return _page_source("field_definition", page, "definition")
    return _page_source("policy", page, page_sections(page)[0]["slug"])


def first_policy_for_threshold(brain: Brain, case: Case, wf: Page | None, cited) -> Page | None:
    """The policy that carries a cost threshold: the workflow's first linked policy (the one named in the risk reason), else the first cited one."""
    ids = list(wf.meta.get("policy_ids", [])) if wf else []
    ids += [c.page_id for c in cited]
    return next((p for pid in ids if (p := _verified(brain, resolve(brain, case, pid))) is not None and p.type == PageType.POLICY), None)


# ------------------------------------------------------------------ the provenance of a case
def provenance(case: Case, brain: Brain, viewer: User, clean: Callable[[str], str] = lambda t: t) -> list[dict]:
    """Every claim of the case with its source. `clean` is check_output for this viewer, applied to claim text only (locations are code-built
    paths such as KA-12@v3.md that a text scrubber would mistake for an e-mail address)."""
    cls, rules, prop, conf = case.classification, case.rules, case.proposal, case.confidence
    full = can_view(viewer, case, "full")
    rtype = cls.request_type if cls else "unknown"
    blocked = bool(cls and cls.model_used == "guard")
    out: list[dict] = []

    def add(claim: str, src: dict | None, *, kind: str | None = None, restricted: bool = False) -> None:
        entry = {"claim": clean(claim), "source_kind": None, "source_id": None, "version": None, "section": None, "section_heading": None,
                 "title": None, "location": None, "restricted": restricted, "superseded_by": None, "retired": False}
        if src is not None and not restricted:
            entry.update(src)
            entry.update(_supersession(brain, entry))
            entry["title"] = clean(entry["title"] or "")
            entry["section_heading"] = clean(entry["section_heading"] or "") or None
        elif kind:
            entry["source_kind"] = kind
        out.append(entry)

    cited_wf = next((resolve(brain, case, c.page_id) for c in (prop.citations if prop else []) if c.page_type == PageType.WORKFLOW), None)
    howto = bool(rtype == "general_policy_question" and cited_wf is not None and "general_policy_question" not in cited_wf.request_types)
    if cited_wf is not None and (howto or rtype in cited_wf.request_types):
        wf = _verified(brain, cited_wf)                            # the workflow version the case cited
    else:
        wf = _verified(brain, brain.workflow_for(rtype)) if rtype != "unknown" else None
    policies = [c for c in (prop.citations if prop else []) if c.page_type == PageType.POLICY]
    first_policy = _verified(brain, resolve(brain, case, policies[0].page_id)) if policies else None

    # 1 request type
    label_txt = TYPE_LABEL.get(rtype, rtype).removeprefix("a ").removeprefix("an ")
    if blocked:
        add(f"The request was stopped by the safety check ({', '.join(c.value for c in case.reason_codes) or 'blocked'})", _guard_source("Input guard"))
    elif wf is not None:
        add(f"Request type: {label_txt}", _page_source("workflow", wf, "request-type"))
    else:
        add(f"Request type: {label_txt}", _guard_source("Classifier and keyword rules") if rtype == "unknown" else None, kind="guard")

    # 2 team / route
    rr = None
    reasons = {r.value for r in case.reason_codes}
    if blocked:
        rr = _RR_FOR["blocked"]
    elif "CLINICAL" in reasons:
        rr = _RR_FOR["clinical"]
    elif rtype == "unknown" or (wf is None and not howto):
        rr = _RR_FOR["unknown"]
    elif wf is not None and not howto:
        rr = wf.meta.get("routing_rule")
    elif howto:
        rr = "RR-08"
    team = _team_name(brain, case.assigned_team)
    add(f"Routed to {team} ({'automatically' if case.routing == 'auto' else 'a person decides'})", _routing_source(brain, rr or ""), kind="routing_rule")

    # 3 risk and threshold
    threshold_reason = next((r for r in (rules.risk_reasons if rules else []) if "threshold" in r.lower()), None)
    risk_src: dict | None
    if rules is None:
        risk_src, risk_claim = None, "Risk was not assessed"
    elif "CLINICAL" in reasons or blocked or any("legal wording" in r for r in rules.risk_reasons):
        risk_src, risk_claim = _guard_source("Clinical, sensitive and injection checks"), f"Risk is {rules.risk.value.upper()}"
    elif threshold_reason and first_policy_for_threshold(brain, case, wf, policies) is not None:
        pol = first_policy_for_threshold(brain, case, wf, policies)
        risk_src = _page_source("threshold", pol, "threshold" if any(s["slug"] == "threshold" for s in page_sections(pol)) else "policy-text")
        risk_claim = f"Risk is {rules.risk.value.upper()}" + (f": {threshold_reason}" if full else "")
    elif wf is not None:
        risk_src, risk_claim = _page_source("workflow", wf, "risk"), f"Risk is {rules.risk.value.upper()} (the workflow's base risk)"
    else:
        risk_src, risk_claim = None, f"Risk is {rules.risk.value.upper()}"
    add(risk_claim, risk_src, kind="threshold")

    # 4 each missing / invalid field
    if rules:
        for f in list(rules.missing_fields) + list(rules.invalid_fields):
            page = _verified(brain, brain.get(field_page_id(f)))
            state_txt = "is invalid" if f in rules.invalid_fields else "is missing"
            src = _page_source("field_definition", page, "definition") if page else (_page_source("workflow", wf, "required-fields") if wf else None)
            add(f"The {label(f)} {state_txt}", src, kind="field_definition")

    # 5 approver role
    if case.approver_role:
        row = _routing_row(brain, rr or "")
        role_txt = case.approver_role.value.replace("_", " ")
        if row is not None and row.get("approver_role") == case.approver_role.value and rules and rules.risk.value == row.get("risk", rules.risk.value):
            approver_src = _routing_source(brain, rr or "")
        else:
            approver_src = risk_src                           # a higher risk moved the decision up: the same source as the risk
        add(f"A {role_txt} decides this case", approver_src, kind="routing_rule")

    # 6 each confidence component (the breakdown is part of the full case view)
    comps = ["policy", "precedent", "fields", "clarity", "no_conflict"]
    names = {"policy": "Policy evidence", "precedent": "Similar past decisions", "fields": "Required details", "clarity": "Request clarity",
             "no_conflict": "No conflicting policies"}
    if conf is not None:
        for key in comps:
            if not full:
                add(names[key], None, restricted=True)
                continue
            value = conf.breakdown.get(key, 0)
            claim = f"{names[key]}: {value}/{MAX[key]}"
            src: dict | None = None
            if key == "policy" and value:
                src = _page_source("policy", first_policy, "policy-text") if first_policy else (_page_source("workflow", wf, "steps") if wf else None)
            elif key == "precedent" and value:
                pid = next((c.page_id for c in (prop.citations if prop else []) if c.page_type == PageType.PRECEDENT), None)
                src = _precedent_source(brain, pid) if pid else None
            elif key == "fields":
                src = _page_source("workflow", wf, "required-fields") if wf else None
            elif key == "clarity":
                src = _page_source("workflow", wf, "request-type") if wf else _guard_source("Classifier and keyword rules")
            elif key == "no_conflict":
                ids = list(dict.fromkeys(re.findall(r"\b(?:KA|REG)-\d+\b", " ".join(rules.conflicts)))) if rules and rules.conflicts else []
                if ids:
                    for pid in ids:
                        page = _verified(brain, resolve(brain, case, pid))
                        if page:
                            add(f"{names[key]}: {value}/{MAX[key]} (conflict)", _page_source("policy", page, "rule" if page.meta.get("rule_key") else "policy-text"))
                    continue
                src = _page_source("policy", first_policy, "policy-text") if first_policy else None
            add(claim, src, kind="policy")

    # 7 each answer step
    if prop:
        wf_steps = [str(s) for s in (wf.meta.get("steps", []) if wf else [])]
        if prop.decision_code == DecisionCode.ANSWER_FROM_POLICY and howto and wf is not None:
            for i, s in enumerate(wf_steps, 1):
                add(f"Step {i}: {s}", _page_source("workflow", wf, "steps"))
        elif prop.decision_code == DecisionCode.ANSWER_FROM_POLICY and first_policy is not None:
            add("The answer is taken from the policy text", _page_source("policy", first_policy, "policy-text"))
        for step in prop.next_steps:
            m = re.search(r"\b((?:KA|WF|REG)-\d+)\b", step)
            if step in wf_steps and wf is not None:
                add(f"Next step: {step}", _page_source("workflow", wf, "steps"))
            elif m and _verified(brain, resolve(brain, case, m.group(1))):
                add(f"Next step: {step}", source_for_citation(brain, m.group(1), case))
            else:
                add(f"Next step: {step}", _routing_source(brain, rr or ""), kind="routing_rule")
    return out
