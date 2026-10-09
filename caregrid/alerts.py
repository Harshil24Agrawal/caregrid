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
from datetime import datetime, timedelta

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


def build(case: Case, trigger: str) -> tuple[str, str]:
    """(subject, message). Subject at most 100 characters; no PII, amounts or Health ID."""
    rtype = case.classification.request_type if case.classification else "unknown"
    risk = (case.rules.risk.value if case.rules else "unknown").upper()
    need = TRIGGERS[trigger][0]
    if trigger == "sla":
        need = f"waiting over {int(sla_hours())}h for review"
    url = f"{os.environ.get('APP_URL', '').rstrip('/')}/case.html?case={case.id}"
    subject = f"CareGrid alert: {case.id} {risk} risk"[:100]
    return subject, f"CareGrid alert · {case.id} · {TYPE_NAME.get(rtype, 'Request')} · {risk} risk · {need} · {url}"


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
    subject, message = "CareGrid test alert", "CareGrid test alert · configuration check · no case, no personal data"
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
