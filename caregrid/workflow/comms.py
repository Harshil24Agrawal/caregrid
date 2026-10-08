"""Requester communications: templated, minimum necessary, simulated for now (COMMS_EMAIL=simulated|sns; sns arrives after midnight).

A message carries the case id, the outcome and the next step. Billing details (invoice id, amount, status, due date) are added ONLY when
the case has a related invoice, and they are read from billing.csv, never from model text. The reviewer's official contacts are appended
("For queries: ...") and are the only PII allowed in the record: they are listed in `Communication.official_contacts` (an explicit,
per-record allowlist for the store's PII safety net, logged by count).
"""
from __future__ import annotations

import csv
import re
import uuid
from datetime import datetime

from caregrid import config
from caregrid.models import Case, Channel, Communication, ReviewAction, ReviewDecision, User
from caregrid.reasoning.guards import _AMOUNT, check_output
from caregrid.reasoning.rules import inr
from caregrid.store import Store
from caregrid.workflow.audit import log

_EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+$")
_PHONE_OK = re.compile(r"^\+?[\d\s().-]{7,24}$")
_STORAGE_VIEWER = User(id="system", name="System", role="senior_reviewer")      # amounts are allowed in the stored message


def validate_contacts(d: ReviewDecision) -> None:
    """Reviewer-provided contacts must look like an email / a phone number: free text cannot ride along as a 'contact'."""
    if d.contact_email is not None and not _EMAIL_OK.match(d.contact_email.strip()):
        raise ValueError("contact_email is not a valid email address")
    if d.contact_phone is not None and (not _PHONE_OK.match(d.contact_phone.strip()) or sum(ch.isdigit() for ch in d.contact_phone) < 7):
        raise ValueError("contact_phone is not a valid phone number")


def billing_lines(case: Case) -> list[str]:
    """Invoice details for the invoices related to the case, from billing.csv only."""
    ids = set(case.related.get("invoice", []))
    path = config.DATA_DIR / "billing.csv"
    if not ids or not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["invoice_id"] in ids]
    return [f"Invoice {r['invoice_id']}: amount ₹{inr(int(r['amount_inr']))}, status {r['status']}, due {r['due_date']}" for r in rows]


def _clean(text: str) -> str:
    """PII/medical check, then drop any rupee amount: an amount reaches the requester ONLY through billing.csv, never through
    model or reviewer text."""
    return _AMOUNT.sub("[amount omitted]", check_output(text, _STORAGE_VIEWER)[1])


def compose_message(case: Case, d: ReviewDecision) -> str:
    outcome = "approved" if d.action == ReviewAction.APPROVE else "approved with changes"
    if d.edited_answer and d.action == ReviewAction.EDIT_APPROVE:
        next_step = _clean(d.edited_answer.strip().splitlines()[0])[:240]
    elif case.proposal and case.proposal.next_steps:
        next_step = _clean(case.proposal.next_steps[0])[:240]
    else:
        next_step = "A specialist will follow up."
    lines = [f"CareGrid update for {case.id}.", f"Outcome: {outcome}.", f"Next step: {next_step}"]
    lines += billing_lines(case)
    contacts = [c.strip() for c in (d.contact_email, d.contact_phone) if c and c.strip()]
    if contacts:
        lines.append("For queries: " + " · ".join(contacts))
    return "\n".join(lines)


def send_communications(case: Case, d: ReviewDecision, store: Store) -> list[Communication]:
    validate_contacts(d)
    message = compose_message(case, d)
    contacts = [c.strip() for c in (d.contact_email, d.contact_phone) if c and c.strip()]
    if config.COMMS_EMAIL == "sns":
        log(store, "comms_note", d.reviewer, case.id, note="COMMS_EMAIL=sns is not implemented yet: email is simulated")
    sent: list[Communication] = []
    for channel in dict.fromkeys(d.channels):                    # de-duplicated, order kept
        recipient = {Channel.EMAIL: d.contact_email, Channel.WHATSAPP: d.contact_phone, Channel.SMS: d.contact_phone,
                     Channel.PORTAL: case.requester.id}[channel]
        recipient = recipient.strip() if recipient else None
        comm = Communication(
            id="COM-" + uuid.uuid4().hex[:10], case_id=case.id, channel=channel, recipient=recipient or "(none)",
            message=message if recipient else "No recipient was provided for this channel; nothing was sent.",
            status="simulated" if recipient else "failed", ts=datetime.now(), official_contacts=contacts if recipient else [])
        store.save_comm(comm)
        log(store, "communication_sent", d.reviewer, case.id, comm=comm.id, channel=channel.value, status=comm.status,
            simulated=comm.status == "simulated", has_billing_details=bool(case.related.get("invoice")))
        sent.append(comm)
    return sent
