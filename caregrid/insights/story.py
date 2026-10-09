"""The case as a story: the problem, what was checked, the decision and the next steps, all built by CODE from the stored case, the Second Brain
and the viewer's own role. Read-only: nothing here decides, scores or routes. The API passes every string through check_output for the viewer
(amounts are hidden from roles that may not see billing), and masked tokens are never quoted (a request mentions "the provider" or "the member").
"""
from __future__ import annotations

import re

from caregrid.insights.explain import ROLE_LABEL, _team_name
from caregrid.knowledge.brain import Brain
from caregrid.models import Case, DecisionCode, PageType, Role, State, User
from caregrid.rbac import can_approve, can_view
from caregrid.reasoning.rules import label
from caregrid.workflow.decisions import ACTION_DESCRIPTIONS, DECIDABLE

ASKED = {
    "general_policy_question": "asked a policy question", "provider_address_change": "asked to change a provider's billing address",
    "provider_name_change": "asked to update a provider's legal name", "portal_access_reset": "asked to reset a provider's portal login",
    "prior_auth_status": "asked for the status of a prior authorization", "dme_equipment_request": "asked to order equipment for a member",
    "claim_status_inquiry": "asked for the status of a claim", "complaint_grievance": "raised a complaint", "unknown": "sent a request that could not be classified",
}
PROBLEM_LINE = {
    "general_policy_question": "Policy question", "provider_address_change": "Change a provider's billing address", "provider_name_change": "Update a provider's name",
    "portal_access_reset": "Reset a provider's portal login", "prior_auth_status": "Prior authorization status", "dme_equipment_request": "Order equipment",
    "claim_status_inquiry": "Claim status", "complaint_grievance": "Complaint", "unknown": "Unclassified request",
}
FINISHED = {State.ANSWERED, State.APPROVED, State.ACTIONED, State.NOTIFIED, State.CLOSED, State.REJECTED}
_THRESHOLD = re.compile(r"cost\s+(\S+)\s+above\s+(\S+)\s+threshold", re.I)
_ITEM = re.compile(r"\(([A-Za-z][A-Za-z ]{2,40})\)")


def _role(case: Case) -> str:
    return ROLE_LABEL.get(case.approver_role, "person") if case.approver_role else "person"


def _item(case: Case) -> str:
    """'an oxygen concentrator (E1390)' from the masked request (a parenthesised item name) and the extracted equipment code."""
    f = case.classification.extracted_fields if case.classification else {}
    m = _ITEM.search(case.masked_text or "")
    name = m.group(1).strip().lower() if m else ""
    code = f.get("equipment_code", "")
    if not name and not code:
        return ""
    article = "an" if name[:1] in "aeiou" else "a"
    return (f"{article} {name}" if name else "equipment") + (f" ({code})" if code else "")


def problem_line(case: Case) -> str:
    """One plain line for the Cases list: what is being asked."""
    rtype = case.classification.request_type if case.classification else "unknown"
    base = PROBLEM_LINE.get(rtype, "Request")
    if rtype == "dme_equipment_request" and (it := _item(case)):
        return f"Order {it}"
    return base


def problem(case: Case) -> str:
    """1-2 plain sentences: who asked what, and what makes it matter."""
    cls, rules, prop = case.classification, case.rules, case.proposal
    rtype = cls.request_type if cls else "unknown"
    asked = ASKED.get(rtype, "sent a request")
    if rtype == "dme_equipment_request" and (it := _item(case)):
        asked = f"asked to order {it} for a member"
    first = f"{case.requester.name} {asked}."
    second = ""
    thr = next((m for r in (rules.risk_reasons if rules else []) if (m := _THRESHOLD.search(r))), None)
    reasons = {r.value for r in case.reason_codes}
    if thr:
        second = f"Cost is {thr.group(1)}, above the {thr.group(2)} limit."
    elif "CLINICAL" in reasons:
        second = "It is a medical question, which this system never answers."
    elif cls and cls.model_used == "guard":
        second = "It was stopped by the safety check before any record was read."
    elif "POLICY_CONFLICT" in reasons:
        second = "Two approved policies disagree about the rule that applies."
    elif rules and (rules.missing_fields or rules.invalid_fields):
        second = "Some required details are missing or wrong."
    elif "SENSITIVE" in reasons:
        second = "It is sensitive, so a person must handle it."
    elif "ACCOUNT_SPECIFIC" in reasons:
        second = "It is about a specific account, so identity must be verified by a person."
    elif prop and prop.decision_code == DecisionCode.NOT_ENOUGH_EVIDENCE:
        second = "No approved policy covers it."
    return f"{first} {second}".strip()


def checks(case: Case, brain: Brain, viewer: User) -> list[dict]:
    """3-6 checklist rows {status: ok|bad|warn, label, text}."""
    rules, prop = case.rules, case.proposal
    full = can_view(viewer, case, "full")
    reasons = {r.value for r in case.reason_codes}
    blocked = bool(case.classification and case.classification.model_used == "guard")
    rows: list[dict] = []
    if rules and rules.required_fields:
        bad = [label(f) for f in rules.missing_fields]
        wrong = [label(f) for f in rules.invalid_fields]
        have = [label(f) for f in rules.required_fields if f not in rules.missing_fields and f not in rules.invalid_fields]
        if bad or wrong:
            rows.append({"status": "bad", "label": "Required details",
                         "text": "; ".join(x for x in (("missing: " + ", ".join(bad)) if bad else "", ("not valid: " + ", ".join(wrong)) if wrong else "") if x)})
        else:
            rows.append({"status": "ok", "label": "Required details", "text": "all present: " + ", ".join(have)})
    elif not blocked:
        rows.append({"status": "ok", "label": "Required details", "text": "none needed for this kind of request"})
    pols = [c for c in (prop.citations if prop else []) if c.page_type == PageType.POLICY]
    if blocked:
        rows.append({"status": "bad", "label": "Safety check", "text": "stopped before any record or policy was read"})
    elif pols:
        rows.append({"status": "ok", "label": "Policy found", "text": ", ".join(f"{c.page_id} v{c.version}" for c in pols[:3])})
    else:
        rows.append({"status": "warn", "label": "Policy found", "text": "no approved policy covers this"})
    precs = [c for c in (prop.citations if prop else []) if c.page_type == PageType.PRECEDENT]
    if not blocked:
        rows.append({"status": "ok" if precs else "warn", "label": "Past cases",
                     "text": (f"{len(precs)} similar approved case{'s' if len(precs) != 1 else ''} agree" if precs else "no similar approved case yet")})
    if rules and rules.conflicts and full:
        rows.append({"status": "bad", "label": "Conflicts", "text": rules.conflicts[0]})
    elif "POLICY_CONFLICT" in reasons:
        rows.append({"status": "bad", "label": "Conflicts", "text": "approved policies disagree"})
    elif not blocked:
        rows.append({"status": "ok", "label": "Conflicts", "text": "no policy conflicts"})
    stale = next((n for n in (rules.notes if rules else []) if " is stale (" in n), None)
    if stale and full:
        rows.append({"status": "warn", "label": "Stale past case", "text": stale.split(" and ")[0].replace(" is stale", " relies on an old policy version") + "; not used"})
    flags = [t for k, t in (("CLINICAL", "medical question"), ("SENSITIVE", "sensitive"), ("ACCOUNT_SPECIFIC", "about a specific account"),
                            ("ACCESS_DENIED", "asks for data beyond the role")) if k in reasons]
    if flags:
        rows.append({"status": "bad" if "CLINICAL" in reasons or "ACCESS_DENIED" in reasons else "warn", "label": "Flags", "text": ", ".join(flags)})
    elif not blocked:
        rows.append({"status": "ok", "label": "Flags", "text": "no medical, legal or personal-data flags"})
    return rows[:6]


def decision(case: Case, brain: Brain) -> dict:
    prop, conf = case.proposal, case.confidence
    team = _team_name(brain, case.assigned_team)
    role = _role(case)
    refused = bool(prop and prop.decision_code == DecisionCode.REFUSE_AND_ROUTE)
    pol = next((f"{c.page_id} v{c.version}" for c in (prop.citations if prop else []) if c.page_type == PageType.POLICY), None)
    if case.state == State.ANSWERED:
        text = f"Answered automatically{' from ' + pol if pol else ''}. No person is needed."
    elif case.state == State.NEEDS_INFO:
        n = len(prop.questions_for_requester) if prop else 0
        text = f"Waiting for the requester to send {n} more detail{'s' if n != 1 else ''}. Nothing goes to {team} until then."
    elif refused:
        text = f"Refused here and sent to {team}. A {role} must handle it."
    elif case.state in FINISHED:
        text = f"A person has decided: the case is {case.state.value}."
    else:
        text = f"Send to {team}: a {role} must approve."
    return {"text": text, "team": team, "approver_role": case.approver_role.value if case.approver_role else None,
            "confidence": {"score": conf.score, "band": conf.band.value} if conf else None}


def next_steps(case: Case, brain: Brain, viewer: User) -> list[str]:
    """Numbered, concrete steps: the viewer's own action first, then what happens after."""
    prop, rules = case.proposal, case.rules
    rtype = case.classification.request_type if case.classification else "unknown"
    team, role = _team_name(brain, case.assigned_team), _role(case)
    mine = case.requester.id == viewer.id
    refused = bool(prop and prop.decision_code == DecisionCode.REFUSE_AND_ROUTE)
    out: list[str] = []
    wf = next((brain.get(c.page_id) for c in (prop.citations if prop else []) if c.page_type == PageType.WORKFLOW), None) or brain.workflow_for(rtype)
    if case.state == State.NEEDS_INFO:
        asks = [q.split(" (format")[0] for q in (prop.questions_for_requester if prop else [])]
        names = [label(f) for f in (list(rules.missing_fields) + list(rules.invalid_fields) if rules else [])]
        if mine:
            out.append("You: send everything below in one reply." + "".join(f" ({i}) {a}" for i, a in enumerate(asks, 1)))
        else:
            out.append(f"You: nothing to do. Waiting for {case.requester.name} to send: {', '.join(names) or 'the missing details'}.")
            if asks:
                out.append("Ask for exactly this: " + " ".join(f"({i}) {a}" for i, a in enumerate(asks, 1)))
        out.append(f"Then CareGrid re-checks the details and routes the case to {team}.")
    elif case.state in DECIDABLE:
        if can_approve(viewer, case):
            out.append("You: approve or reject in the Decide panel.")
        elif mine:
            out.append(f"You: nothing to do. You can't approve your own request; a {role} decides.")
        else:
            out.append(f"You: nothing to do. Waiting for a {role}.")
        if refused:
            out.append(f"Where it went: {team}. {'It is a medical question, so no advice is given.' if any(r.value == 'CLINICAL' for r in case.reason_codes) else 'The rules say a person must handle this kind of request.'}")
        else:
            did = ACTION_DESCRIPTIONS.get(rtype, "Released the request to {team}.").format(team=team)
            out.append(f"On approval: {did} The requester is notified on the channels the approver picks, and the decision is saved as a precedent.")
            if wf is not None and wf.meta.get("steps"):
                out.append(f"Then {team} follows {wf.id}: " + " ".join(f"({i}) {str(s).rstrip('.')}." for i, s in enumerate(wf.meta["steps"][:3], 1)))
        out.append("On rejection: the case is closed with the reviewer's note and nothing is changed.")
    elif case.state == State.ANSWERED:
        out.append("You: nothing to do. The answer was sent." if mine else "You: nothing to do. The requester has the answer.")
    elif case.state in FINISHED:
        out.append(f"You: nothing to do. This case is {case.state.value}.")
    else:
        out.append(f"You: nothing to do. The case is {case.state.value.replace('_', ' ')}.")
    return out


def bucket(case: Case, viewer: User) -> str:
    """action (needs this viewer) | waiting | done."""
    if case.state in FINISHED:
        return "done"
    if case.state in DECIDABLE and can_approve(viewer, case):
        return "action"
    if case.state == State.NEEDS_INFO and case.requester.id == viewer.id:
        return "action"
    return "waiting"


def next_short(case: Case, viewer: User) -> str:
    b = bucket(case, viewer)
    role = _role(case)
    if b == "done":
        return "Done"
    if case.state == State.NEEDS_INFO:
        return "Send the missing details" if case.requester.id == viewer.id else "Waiting for the requester"
    return "Approve or reject" if b == "action" else f"Waiting for a {role}"


def story(case: Case, brain: Brain, viewer: User) -> dict:
    return {"problem": problem(case), "checks": checks(case, brain, viewer), "decision": decision(case, brain),
            "next_steps": next_steps(case, brain, viewer)}


# ------------------------------------------------------------------ the timeline: the story of the case, from the audit log, newest last
CHANNEL = {"email": "Email", "whatsapp": "WhatsApp", "sms": "SMS", "portal": "Portal message"}
ACTION_PHRASE = {"approve": "approved it", "edit_approve": "edited and approved it", "reject": "rejected it", "escalate": "escalated it",
                 "ask_requester": "asked the requester for more information"}


def timeline(case: Case, events: list, names: dict[str, str], brain: Brain) -> list[dict]:
    """[{ts, text}] human-readable, oldest first. Names come from the user list; the ids and amounts never appear."""
    from datetime import datetime

    who = lambda e: names.get(e.actor_id, "CareGrid")                                        # noqa: E731
    out: list[dict] = []
    for e in sorted(events, key=lambda e: e.ts):
        d, text = e.details, None
        if e.event == "request_received":
            text = f"{who(e)} submitted the request"
        elif e.event == "citations_verified":
            kept = d.get("kept", [])
            pol = [k.replace("@v", " v") for k in kept if k[:3] in ("KA-", "REG")]
            prec = [k for k in kept if k.startswith("P-")]
            text = "CareGrid checked it (" + ", ".join(x for x in (("policy " + ", ".join(pol)) if pol else "", (f"{len(prec)} past case{'s' if len(prec) != 1 else ''}") if prec else "") if x) + ")" \
                if pol or prec else "CareGrid checked it (no approved policy found)"
        elif e.event == "details_added":
            text = f"{who(e)} added the missing details ({', '.join(label(f) for f in d.get('fields', []))})"
        elif e.event == "forwarded":
            text = f"{who(e)} sent it to {_team_name(brain, d.get('team'))}" + (" with a note" if d.get("note") else "")
        elif e.event == "withdrawn":
            text = f"{who(e)} withdrew the request"
        elif e.event == "guard_blocked":
            text = "The safety check stopped the request"
        elif e.event == "routed":
            st, team = d.get("state"), _team_name(brain, d.get("team"))
            if d.get("routing") == "auto":
                text = None
            elif st == "needs_info":
                text = "CareGrid needs more details from the requester"
            elif st == "proposed":
                text = f"CareGrid suggests sending it to {team}"
            elif st == "in_review":
                text = f"Sent automatically for safety to {team}"
        elif e.event == "auto_with_audit":
            text = "Answered automatically"
        elif e.event == "review_submitted":
            text = f"{who(e)} {ACTION_PHRASE.get(d.get('action'), 'decided')}"
        elif e.event == "communication_sent":
            text = f"{CHANNEL.get(d.get('channel'), 'Message')} sent" + (" (simulated)" if d.get("status") == "simulated" else "")
        elif e.event == "precedent_saved":
            text = f"Saved as precedent {d.get('precedent')}"
        elif e.event in ("alert_sent", "alert_simulated", "alert_failed"):
            where = d.get("target", "the on-call team")
            text = {"alert_sent": f"Alert emailed to {where}", "alert_simulated": f"Alert to {where} simulated (no topic configured)",
                    "alert_failed": f"Alert to {where} could not be sent"}[e.event]
        if text:
            day = "" if e.ts.date() == datetime.now().date() else e.ts.strftime("%d %b ")
            out.append({"ts": e.ts.isoformat(), "time": day + e.ts.strftime("%H:%M"), "text": text})
    return out
