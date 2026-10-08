"""Deterministic rules engine (CONTRACTS §4, §6-8): fields, risk, tier, conflicts, notes, decision code.

Everything here is code: the LLM never decides numbers, risk, approvals or routing.
"""
from __future__ import annotations

import csv
import re
from datetime import date
from itertools import combinations

from caregrid import config
from caregrid.knowledge.brain import Brain
from caregrid.models import (
    HARD_OVERRIDES, ActionTier, Classification, DecisionCode, GuardResult, PageType, ReasonCode, RetrievalResult, Risk,
    Role, RuleResult,
)
from caregrid.reasoning.confidence import relevant_policies, similar_precedents
from caregrid.reasoning.guards import LEGAL_WORDS

RISK_ORDER = [Risk.LOW, Risk.MEDIUM, Risk.HIGH, Risk.CRITICAL]
APPROVER_BY_RISK = {Risk.LOW: Role.TEAM_SPECIALIST, Risk.MEDIUM: Role.OPS_MANAGER,
                    Risk.HIGH: Role.SENIOR_REVIEWER, Risk.CRITICAL: Role.SENIOR_REVIEWER}
RECORD_CHANGES = {"provider_address_change", "provider_name_change"}
NEVER_AUTO_REASON = {"prior_auth_status": ReasonCode.ACCOUNT_SPECIFIC, "claim_status_inquiry": ReasonCode.ACCOUNT_SPECIFIC,
                     "complaint_grievance": ReasonCode.SENSITIVE}
FIELD_LABEL = {"npi": "NPI", "provider_npi": "NPI", "member_id": "member ID", "auth_id": "authorization ID", "claim_id": "claim ID",
               "effective_date": "effective date", "user_email": "user email", "equipment_code": "equipment code",
               "estimated_cost_inr": "estimated cost", "new_address": "new address", "old_name": "old name",
               "new_name": "new name", "supporting_document": "supporting document",
               "prescription_on_file": "prescription on file"}
NOTE_LABEL = {"supporting_document": "document"}   # human wording inside stale-precedent notes
FIELD_ALIAS = {"provider_npi": "npi", "npi": "provider_npi"}


def label(field: str) -> str:
    return FIELD_LABEL.get(field, field.replace("_", " "))


def inr(n: int) -> str:
    """Indian digit grouping: 62500 -> '62,500', 1250000 -> '12,50,000'."""
    s = str(abs(int(n)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join([*groups, tail])


def _routing_rule(brain: Brain, condition: str) -> dict | None:
    path = brain.dir / "config" / "routing_rules.csv"
    if not path.exists():
        return None
    with open(path, encoding="utf-8", newline="") as f:
        return next((r for r in csv.DictReader(f) if r["condition"] == condition), None)


def _max_risk(a: Risk, b: Risk) -> Risk:
    return a if RISK_ORDER.index(a) >= RISK_ORDER.index(b) else b


def _add(lst: list, item) -> None:
    if item not in lst:
        lst.append(item)


def apply_rules(cls: Classification, ret: RetrievalResult, brain: Brain, guard: GuardResult) -> RuleResult:
    wf = ret.workflow
    meta = wf.meta if wf else {}
    res = RuleResult()
    reasons: list[ReasonCode] = []

    # ---- fields -----------------------------------------------------------------
    required = list(meta.get("required_fields", []))
    res.required_fields = required
    have = {k for k, v in cls.extracted_fields.items() if v}
    have |= {FIELD_ALIAS[k] for k in have if k in FIELD_ALIAS}
    regexes = {p.meta.get("field"): p.meta.get("regex") for p in ret.fields}
    for f in required:
        gv = guard.validated_fields.get("npi" if f == "provider_npi" else f)
        value = cls.extracted_fields.get(f, "")
        if gv and gv.startswith("invalid"):
            res.invalid_fields[f] = f"{label(f)} is invalid ({gv.removeprefix('invalid: ')})"
        elif f in have and value and not value.startswith("[") and regexes.get(f) and not re.fullmatch(regexes[f], value):
            res.invalid_fields[f] = f"{label(f)} is invalid (unexpected format)"
        elif f not in have:
            res.missing_fields.append(f)
    if res.missing_fields or res.invalid_fields:
        _add(reasons, ReasonCode.MISSING_DATA)

    # ---- risk (only ever escalates) -------------------------------------------------
    risk = Risk(meta.get("risk", "low"))
    reasons_txt: list[str] = []
    policy_ref = (meta.get("policy_ids") or [""])[0]
    cost = cls.extracted_fields.get("estimated_cost_inr", "")
    for key, limit in (meta.get("thresholds") or {}).items():
        if key == "estimated_cost_inr" and cost.isdigit() and int(cost) > int(limit):
            risk = _max_risk(risk, Risk.HIGH)
            ref = f" [{policy_ref}]" if policy_ref else ""
            reasons_txt.append(f"cost ₹{inr(int(cost))} above ₹{inr(int(limit))} threshold{ref}")
    eff = cls.extracted_fields.get("effective_date", "")
    if cls.request_type in RECORD_CHANGES and eff:
        try:
            if date.fromisoformat(eff) < config.TODAY:
                risk = _max_risk(risk, Risk.MEDIUM)
                reasons_txt.append("retroactive change (effective date is in the past)")
        except ValueError:
            pass
    sensitive = cls.is_sensitive or ReasonCode.SENSITIVE in guard.overrides
    if sensitive and LEGAL_WORDS.search(guard.masked_text):
        risk = _max_risk(risk, Risk.CRITICAL)
        reasons_txt.append("legal wording in a sensitive complaint")
    clinical = cls.is_clinical or ReasonCode.CLINICAL in guard.overrides
    if clinical:
        risk = _max_risk(risk, Risk.CRITICAL)
        reasons_txt.append("clinical question needs Clinical Review")
    res.risk, res.risk_reasons = risk, reasons_txt
    if RISK_ORDER.index(risk) >= RISK_ORDER.index(Risk.HIGH):
        _add(reasons, ReasonCode.HIGH_RISK)

    # ---- hard overrides -----------------------------------------------------------
    for code in guard.overrides:
        _add(reasons, code)
    if clinical:
        _add(reasons, ReasonCode.CLINICAL)
    if cls.is_account_specific:
        _add(reasons, ReasonCode.ACCOUNT_SPECIFIC)
    if sensitive:
        _add(reasons, ReasonCode.SENSITIVE)
    if meta.get("never_auto") and cls.request_type in NEVER_AUTO_REASON:
        _add(reasons, NEVER_AUTO_REASON[cls.request_type])

    res.action_tier = ActionTier(meta.get("action_tier", "read"))
    if res.action_tier == ActionTier.WRITE:
        res.notes.append("write-tier action requires an authorized human")

    # ---- team ------------------------------------------------------------------------
    team = meta.get("team")
    if not guard.allowed:                    # refused by the input guard (injection, ACCESS_DENIED, too long): RR-11
        rule = _routing_rule(brain, "blocked=true")
        team = rule["team"] if rule else "TEAM-COMPLIANCE"
    elif clinical:
        rule = _routing_rule(brain, "clinical=true")
        team = rule["team"] if rule else "TEAM-CLINICAL"
    elif cls.request_type == "unknown" or wf is None:
        rule = _routing_rule(brain, "request_type=unknown")
        team = rule["team"] if rule else "TEAM-OPS-TRIAGE"
        if not any(c in HARD_OVERRIDES for c in reasons):
            _add(reasons, ReasonCode.UNCLEAR_INTENT)
        elif ReasonCode.SENSITIVE in reasons:
            team = "TEAM-COMPLIANCE"
    res.route_team = team

    # ---- conflicts & notes ----------------------------------------------------------------
    # from ALL approved current policies for this request type, not just the retrieved top-k: a contradicting
    # policy must be caught even when search ranks it below the cut-off
    pols = [p for p in brain.current_policies() if cls.request_type in p.request_types and p.meta.get("rule_key")]
    for a, b in combinations(sorted(pols, key=lambda p: p.id), 2):
        if a.meta["rule_key"] == b.meta["rule_key"] and a.meta.get("rule_value") != b.meta.get("rule_value"):
            res.conflicts.append(f"{a.id} ({a.meta.get('rule_value')}) contradicts {b.id} ({b.meta.get('rule_value')}) "
                                 f"on {a.meta['rule_key']}")
            _add(reasons, ReasonCode.POLICY_CONFLICT)

    def skipped(prec) -> list[str]:
        if prec.decision_code not in (DecisionCode.ROUTE_TO_TEAM, DecisionCode.ANSWER_FROM_POLICY):
            return []
        provided = set(prec.fields_provided) | {FIELD_ALIAS[f] for f in prec.fields_provided if f in FIELD_ALIAS}
        return [f for f in required if f not in provided]

    for sp in ret.precedents_active:
        if sp.similarity >= config.PRECEDENT_MIN_SIM and (gap := skipped(sp.precedent)):
            res.conflicts.append(f"{sp.precedent.id} (active precedent) skipped the {', '.join(label(f) for f in gap)} "
                                 f"that the current workflow requires")
            _add(reasons, ReasonCode.POLICY_CONFLICT)
    for sp in ret.precedents_stale:
        p = sp.precedent
        if gap := skipped(p):
            res.notes.append(f"{p.id} is stale ({p.policy_id} v{p.policy_version}) and skipped the "
                             f"{', '.join(NOTE_LABEL.get(f, label(f)) for f in gap)} check")

    # ---- finish ----------------------------------------------------------------------
    res.reason_codes = reasons
    res.approver_role = APPROVER_BY_RISK[res.risk]
    # a request refused by the input guard (incl. "input too long") always goes to a human, whatever its reason code
    res.hard_override = (any(c in HARD_OVERRIDES for c in reasons) or res.action_tier == ActionTier.WRITE
                         or not guard.allowed)
    return res


def decide_code(rules: RuleResult, ret: RetrievalResult, cls: Classification) -> DecisionCode:
    """Chosen by code BEFORE the proposer runs (PROMPTS §2)."""
    if any(c in HARD_OVERRIDES for c in rules.reason_codes):
        return DecisionCode.REFUSE_AND_ROUTE
    if rules.missing_fields or rules.invalid_fields:
        return DecisionCode.REQUEST_MISSING_INFO
    if rules.risk in (Risk.HIGH, Risk.CRITICAL):
        return DecisionCode.ESCALATE_SENIOR
    has_policy = bool(relevant_policies(ret))
    if not has_policy and not similar_precedents(ret):
        return DecisionCode.NOT_ENOUGH_EVIDENCE
    if cls.request_type == "general_policy_question" and has_policy:
        return DecisionCode.ANSWER_FROM_POLICY
    return DecisionCode.ROUTE_TO_TEAM
