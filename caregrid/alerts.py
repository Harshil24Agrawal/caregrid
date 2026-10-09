"""Alerts to on-call people through AWS SNS (a first-party signal that something needs a person NOW).

Triggers: critical (risk CRITICAL: clinical, legal complaint), security (injection / access-denied attempt), approval (a HIGH-risk case forwarded
to Senior Ops), sla (a case waiting in review longer than ALERT_SLA_HOURS). One alert per case per trigger (deduplicated through the audit log).

The message is the minimum necessary: case id, request type, risk, what is needed and a link. NO personal data, NO amounts, NO Health ID, nothing
from the request text. Environment only: ALERT_SNS_TOPIC_ARN, AWS_REGION, AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (read by boto3), APP_URL,
ALERT_SLA_HOURS. Without a topic or credentials the alert is "simulated" (audited, never an error); a failure never blocks the case, and the
call has a 5-second timeout. Audit: alert_sent | alert_simulated | alert_failed, with the trigger, the target and the topic NAME only.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from caregrid.models import Case, Risk, State, User
from caregrid.workflow.audit import log

TYPE_NAME = {
    "general_policy_question": "Policy question", "provider_address_change": "Address change", "provider_name_change": "Name change",
    "portal_access_reset": "Portal reset", "prior_auth_status": "Prior auth status", "dme_equipment_request": "DME request",
    "claim_status_inquiry": "Claim status", "complaint_grievance": "Complaint", "unknown": "Request",
}
TRIGGERS = {
    "critical": ("needs senior or clinical review", "Senior / Clinical on-call"),
    "security": ("blocked attempt, compliance review", "Compliance on-call"),
    "approval": ("needs senior approval", "Senior Ops on-call"),
    "sla": ("waiting too long for review", "Senior Ops on-call"),
}
ALERT_EVENTS = ("alert_sent", "alert_simulated", "alert_failed")
TIMEOUT_S = 5
SUPPRESSED = False        # seeding old history must never page anyone
SYSTEM = User(id="system", name="CareGrid", role="senior_reviewer")           # audit actor label only


def _topic() -> str:
    return os.environ.get("ALERT_SNS_TOPIC_ARN", "").strip()


def topic_name(arn: str) -> str:
    return arn.rsplit(":", 1)[-1] if arn else "none"


def sla_hours() -> float:
    try:
        return float(os.environ.get("ALERT_SLA_HOURS", "24"))
    except ValueError:
        return 24.0


IST = timezone(timedelta(hours=5, minutes=30))
FULL_TYPE = {
    "general_policy_question": "Policy question", "provider_address_change": "Provider address change", "provider_name_change": "Provider name change",
    "portal_access_reset": "Portal access reset", "prior_auth_status": "Prior authorization status", "dme_equipment_request": "DME equipment request",
    "claim_status_inquiry": "Claim status inquiry", "complaint_grievance": "Complaint", "unknown": "Unclassified request",
}
TEAM_FALLBACK = {"TEAM-SENIOR-OPS": "Senior Operations Review", "TEAM-ENROLL": "Provider Enrollment", "TEAM-IT": "IT Service Desk", "TEAM-CLAIMS": "Claims",
                 "TEAM-UM": "Utilization Management", "TEAM-COMPLIANCE": "Compliance & Privacy", "TEAM-CLINICAL": "Clinical Review", "TEAM-OPS-TRIAGE": "Operations Triage"}
ROLE_NAME = {"ops_employee": "Ops employee", "team_specialist": "Team specialist", "ops_manager": "Ops manager", "senior_reviewer": "Senior reviewer",
             "knowledge_owner": "Knowledge owner", "auditor": "Auditor"}
LABEL_WIDTH = 12
FOOTER = ("This alert contains no patient data. Details are visible only inside CareGrid to authorised roles.\n"
          "You receive this because you are subscribed to CareGrid critical alerts.")


def ist(when: datetime) -> str:
    """'09 Oct 2026, 10:12 IST' (a naive time is the server's local time)."""
    return when.astimezone(IST).strftime("%d %b %Y, %H:%M") + " IST"


def _team(team_id: str | None) -> str:
    try:
        from caregrid import config
        from caregrid.knowledge.brain import Brain

        page = Brain(config.BRAIN_DIR).team(team_id) if team_id else None
        if page is not None:
            return page.title
    except Exception:
        pass
    return TEAM_FALLBACK.get(team_id or "", team_id or "the right team")


def _hours_waiting(case: Case, now: datetime | None = None) -> int:
    entered = next((ts for st, ts in reversed(case.state_history) if st == case.state), case.created_at)
    return int(((now or datetime.now()) - entered).total_seconds() // 3600)


def a_(kind: str) -> str:
    """'a DME equipment request', 'an unclassified request', 'a provider address change' (acronyms keep their capitals)."""
    phrase = kind if kind[:2].isupper() else kind[:1].lower() + kind[1:]
    return ("an " if phrase[:1].lower() in "aeiou" else "a ") + phrase


def layout(subject: str, what: str, fields: list[tuple[str, str]], link: str | None) -> tuple[str, str]:
    """The one fixed plain-text layout every alert uses (SNS e-mail is plain text: nothing is wrapped, labels are aligned with spaces)."""
    lines = ["CareGrid alert \u2014 action needed", "=" * 30, "", "WHAT HAPPENED", what, ""]
    lines += [f"{label:<{LABEL_WIDTH}}{value}" for label, value in fields]
    if link:
        lines += ["", "OPEN THE CASE", link]
    lines += ["", "-" * 30, FOOTER]
    return subject[:99], "\n".join(lines)


def build(case: Case, trigger: str, now: datetime | None = None) -> tuple[str, str]:
    """(subject, message): the same layout for every trigger, only the fields change. No personal data, amounts, Health ID or request text.
    The subject is plain ASCII (SNS requires it) and under 100 characters."""
    rules, prop = case.rules, case.proposal
    rtype = case.classification.request_type if case.classification else "unknown"
    risk = (rules.risk.value if rules else "unknown").upper()
    kind = FULL_TYPE.get(rtype, "Request")
    reasons = {r.value for r in case.reason_codes}
    threshold = any("threshold" in r.lower() for r in (rules.risk_reasons if rules else []))
    policy = next((f"{c.page_id} v{c.version}" for c in (prop.citations if prop else []) if c.page_type.value == "policy"), None)
    role = (case.approver_role.value if case.approver_role else "senior_reviewer")
    needs = f"{ROLE_NAME.get(role, 'Senior reviewer')} approval"
    hours = _hours_waiting(case, now)
    lead = a_(kind)[:1].upper() + a_(kind)[1:]                                   # "A DME equipment request", "A complaint"
    if trigger == "security":
        severity, action = "SECURITY", "Blocked access attempt"
        what, why, needs = ("A request that tried to override the rules or read protected data was blocked and routed to Compliance.",
                            "Prompt-injection or access-denied pattern detected", "Compliance & Privacy review")
    elif trigger == "critical":
        clinical = "CLINICAL" in reasons
        severity, action = "CRITICAL", "Clinical review needed" if clinical else "Senior review needed"
        what = ("A medical question was routed to clinical review; it is never answered automatically." if clinical
                else f"{lead} is critical risk and needs senior review.")
        why = "Medical question (clinical safety rule)" if clinical else ("Legal wording in a sensitive complaint" if "SENSITIVE" in reasons else "Critical risk")
        needs = "Clinical review" if clinical else "Senior reviewer approval"
    elif trigger == "sla":
        severity, action = "OVERDUE", f"Waiting {hours} h for review"
        what = f"{lead} has been waiting {hours} hours for review."
        why = f"In review longer than the {int(sla_hours())} h target"
    else:                                                                        # approval
        severity, action = ("CRITICAL" if risk == "CRITICAL" else "HIGH RISK"), "Senior approval needed"
        what = f"{lead} is " + ("above the cost limit" if threshold else f"{risk.lower()} risk") + " and needs senior approval."
        why = (f"Cost above the limit in policy {policy}" if threshold and policy else ("Cost above the limit" if threshold else f"{risk.title()} risk"))
    when = case.forwarded_at if trigger == "approval" and case.forwarded_at else case.created_at
    sender = ROLE_NAME.get(case.requester.role.value, "Requester") + (" (forwarded)" if case.forwarded_by and trigger == "approval" else "")
    base = os.environ.get("APP_URL", "").strip().rstrip("/")
    return layout(f"[CareGrid] {severity} | {case.id} | {action}", what,
                  [("CASE", case.id), ("TYPE", kind), ("RISK", risk), ("TEAM", _team(case.assigned_team)), ("NEEDS", needs), ("WHY", why),
                   ("SENT BY", f"{sender} \u00b7 {ist(when)}")],
                  f"{base}/case.html?case={case.id}" if base else None)


def test_message() -> tuple[str, str]:
    """The sample `cli alerts test` sends: the exact layout with clearly fake fields."""
    base = os.environ.get("APP_URL", "").strip().rstrip("/")
    return layout("[CareGrid] TEST | Alert formatting check", "This is a test of the alert format. No case is involved.",
                  [("CASE", "CASE-0000"), ("TYPE", "DME equipment request"), ("RISK", "HIGH"), ("TEAM", "Senior Operations Review"),
                   ("NEEDS", "Senior reviewer approval"), ("WHY", "Cost above the limit in policy KA-40 v1"),
                   ("SENT BY", f"Ops employee (forwarded) \u00b7 {ist(datetime.now())}")],
                  f"{base}/case.html?case=CASE-0000" if base else None)



def _client():
    """The SNS client (tests replace this). None when there are no credentials to use."""
    import boto3
    from botocore.config import Config

    _clean_env()
    if boto3.Session().get_credentials() is None:
        return None
    return boto3.client("sns", region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
                        config=Config(connect_timeout=TIMEOUT_S, read_timeout=TIMEOUT_S, retries={"max_attempts": 0}))


def _clean_env() -> None:
    """An EMPTY AWS_PROFILE (some shells export it) makes botocore raise ProfileNotFound: treat it as unset."""
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        if name in os.environ and not os.environ[name].strip():
            del os.environ[name]


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""


def missing() -> list[str]:
    """Names of the settings that stop a real alert from being sent (empty = SNS is enabled). Never a value."""
    out = []
    if not _topic():
        out.append("ALERT_SNS_TOPIC_ARN")
    if not _region():
        out.append("AWS_REGION")
    try:
        import boto3

        _clean_env()
        if boto3.Session().get_credentials() is None:
            out.append("AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY")
    except ImportError:
        out.append("boto3")
    except Exception:                                                              # a broken profile / config: no usable credentials
        out.append("AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY")
    return out


def status_line() -> str:
    """One line for the startup log: whether alerts are real or simulated. Names and the topic NAME only, never a secret."""
    gaps = missing()
    return f"alerts: simulated (missing {', '.join(gaps)})" if gaps else f"alerts: SNS enabled (topic {topic_name(_topic())}, region {_region()})"


def error_info(e: Exception) -> dict:
    """The exception type and the AWS error code (if there is one). Never the message, which could echo a request."""
    code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
    return {"error": type(e).__name__, **({"aws_code": code} if code else {})}


class suppressed:
    """`with alerts.suppressed():` nothing inside publishes or records an alert (seeding, reset, demo, eval and check runs)."""

    def __enter__(self):
        global SUPPRESSED
        self.was, SUPPRESSED = SUPPRESSED, True

    def __exit__(self, *exc):
        global SUPPRESSED
        SUPPRESSED = self.was
        return False


def send_test(store=None) -> tuple[str, str]:
    """Publish ONE test alert with the current configuration. Returns (status, line) where status is sent | simulated | failed and the line is
    exactly what `cli alerts test` prints: the MessageId, which setting is missing, or the error type and AWS error code (no secrets)."""
    gaps = missing()
    subject, message = test_message()
    details = {"trigger": "test", "target": "test alert", "topic": topic_name(_topic())}
    if gaps:
        if store is not None:
            log(store, "alert_simulated", SYSTEM, None, **details)
        return "simulated", f"simulated (missing {', '.join(gaps)})"
    try:
        client = _client()
        if client is None:
            raise RuntimeError("no client")
        resp = client.publish(TopicArn=_topic(), Subject=subject, Message=message) or {}
    except Exception as e:
        info = error_info(e)
        if store is not None:
            log(store, "alert_failed", SYSTEM, None, **details, **info)
        return "failed", "failed (" + info["error"] + (f", AWS error code {info['aws_code']}" if info.get("aws_code") else "") + ")"
    if store is not None:
        log(store, "alert_sent", SYSTEM, None, **details)
    return "sent", f"sent (MessageId {resp.get('MessageId', 'unknown')})"


def already_sent(store, case_id: str, trigger: str) -> bool:
    return any(e.event in ALERT_EVENTS and e.details.get("trigger") == trigger for e in store.list_audit(case_id))


def notify(case: Case, trigger: str, store) -> str:
    """'sent' | 'simulated' | 'failed' | 'duplicate'. Never raises."""
    try:
        if SUPPRESSED:
            return "suppressed"
        if trigger not in TRIGGERS:
            return "failed"
        if already_sent(store, case.id, trigger):
            return "duplicate"
        subject, message = build(case, trigger)
        arn, target = _topic(), TRIGGERS[trigger][1]
        details = {"trigger": trigger, "target": target, "topic": topic_name(arn)}
        client = None
        if arn:
            try:
                client = _client()
            except Exception:                                                     # no boto3 / bad config: simulate
                client = None
        if client is None:
            log(store, "alert_simulated", SYSTEM, case.id, **details)
            return "simulated"
        try:
            client.publish(TopicArn=arn, Subject=subject, Message=message)
        except Exception as e:                                                    # network, permission, throttling: the case carries on
            log(store, "alert_failed", SYSTEM, case.id, **error_info(e), **details)
            return "failed"
        log(store, "alert_sent", SYSTEM, case.id, **details)
        return "sent"
    except Exception:                                                             # an alert must never break the case
        return "failed"


def sweep(store, now: datetime | None = None) -> list[str]:
    """SLA breaches: every case waiting in review longer than ALERT_SLA_HOURS gets one `sla` alert. Returns the case ids alerted."""
    now = now or datetime.now()
    limit = timedelta(hours=sla_hours())
    done = []
    for c in store.list_cases():
        if c.state not in (State.IN_REVIEW, State.ESCALATED) or not c.state_history:
            continue
        entered = next((ts for st, ts in reversed(c.state_history) if st == c.state), c.created_at)
        if now - entered > limit and notify(c, "sla", store) in ("sent", "simulated", "failed"):
            done.append(c.id)
    return done


def after_routing(case: Case, store) -> None:
    """Called when the pipeline has routed a case: critical risk and blocked / access-denied attempts alert at once."""
    if case.rules and case.rules.risk == Risk.CRITICAL:
        notify(case, "critical", store)
    if case.classification and case.classification.model_used == "guard" and any(r.value == "ACCESS_DENIED" for r in case.reason_codes):
        notify(case, "security", store)


def after_forward(case: Case, store) -> None:
    """A HIGH-risk (or worse) case forwarded to Senior Ops needs an approval now."""
    if case.rules and case.rules.risk in (Risk.HIGH, Risk.CRITICAL) and case.assigned_team == "TEAM-SENIOR-OPS":
        notify(case, "approval", store)
