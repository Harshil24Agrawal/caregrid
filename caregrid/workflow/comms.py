"""Requester communications: templated, minimum necessary, simulated for now (COMMS_EMAIL=simulated|sns; sns arrives after midnight).

A message carries the case id, the outcome and the next step. Billing details (invoice id, amount, status, due date) are added ONLY when
the case has a related invoice, and they are read from billing.csv, never from model text. Raw contact details (e-mail, phone) exist only in
memory while the message is sent: the stored recipient is the masked placeholder ([EMAIL] / [PHONE]), the stored text says
"For queries: [EMAIL] · [PHONE]", and a short salted hash of the raw recipient is kept for dedup only.
"""
from __future__ import annotations

import csv
import hashlib
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


def contact_hash(value: str) -> str:
    """Short salted hash used to recognise the same recipient twice without keeping the recipient."""
    return hashlib.sha256((config.COMMS_HASH_SALT + "|" + value.strip().lower()).encode("utf-8")).hexdigest()[:12]


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
    placeholders = [mark for mark, value in (("[EMAIL]", d.contact_email), ("[PHONE]", d.contact_phone)) if value and value.strip()]
    if placeholders:
        lines.append("For queries: " + " · ".join(placeholders))      # the raw values are never written down
    return "\n".join(lines)


def send_communications(case: Case, d: ReviewDecision, store: Store) -> list[Communication]:
    validate_contacts(d)
    message = compose_message(case, d)
    if config.COMMS_EMAIL == "sns":
        log(store, "comms_note", d.reviewer, case.id, note="COMMS_EMAIL=sns is not implemented yet: email is simulated")
    sent: list[Communication] = []
    for channel in dict.fromkeys(d.channels):                    # de-duplicated, order kept
        raw = {Channel.EMAIL: d.contact_email, Channel.WHATSAPP: d.contact_phone, Channel.SMS: d.contact_phone,
               Channel.PORTAL: case.requester.id}[channel]
        raw = raw.strip() if raw else None                       # in memory only, for the (simulated) send
        shown = {Channel.EMAIL: "[EMAIL]", Channel.WHATSAPP: "[PHONE]", Channel.SMS: "[PHONE]", Channel.PORTAL: case.requester.id}[channel]
        digest = contact_hash(raw) if raw else None
        if digest and any(c.channel == channel and c.recipient_hash == digest for c in store.list_comms(case.id)):
            continue                                              # same recipient on the same channel for this case: already sent
        comm = Communication(
            id="COM-" + uuid.uuid4().hex[:10], case_id=case.id, channel=channel, recipient=shown if raw else "(none)",
            message=message if raw else "No recipient was provided for this channel; nothing was sent.",
            status="simulated" if raw else "failed", ts=datetime.now(), recipient_hash=digest)
        store.save_comm(comm)
        log(store, "communication_sent", d.reviewer, case.id, comm=comm.id, channel=channel.value, status=comm.status,
            simulated=comm.status == "simulated", has_billing_details=bool(case.related.get("invoice")))
        sent.append(comm)
    return sent
