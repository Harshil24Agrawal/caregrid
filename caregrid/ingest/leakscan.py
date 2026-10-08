"""PII leak detection: one detector (`detect_pii`) used by the brain scan, the store safety net and the CLI.

`detect_pii` runs the anonymizer's own detectors (so masking and detection can never drift apart) with the cue-less
name pass off, and ignores the exact team mailboxes from teams.csv. Findings use LintFinding(code="PII_LEAK");
values are never echoed.
"""
from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path

from caregrid import config
from caregrid.ingest.anonymize import EMAIL, Gazetteer, anonymize, build_gazetteer
from caregrid.ingest.normalize import normalize_text
from caregrid.models import LintFinding

# versioned page filenames such as KA-12@v3.md look like emails; they are not
VERSIONED_FILE = re.compile(r"@v\d+\.md")
_ORG_MAILBOX = "ORGMAILBOX"

MESSAGES = {
    "EMAIL": "email address", "MEMBER_ID": "member ID", "NPI": "NPI number", "PHONE": "phone number",
    "ID": "long ID number", "CARD": "card number", "DATE_OF_BIRTH": "date of birth", "ADDRESS": "address",
    "PERSON": "person name",
}


def team_mailboxes(data_dir: Path) -> set[str]:
    """Exact contact_email values from teams.csv: the only emails allowed in the brain."""
    path = Path(data_dir) / "teams.csv"
    if not path.exists():
        return set()
    with open(path, encoding="utf-8", newline="") as f:
        return {r["contact_email"].lower() for r in csv.DictReader(f) if r.get("contact_email")}


@lru_cache(maxsize=8)
def _cached_mailboxes(data_dir: str) -> frozenset[str]:
    return frozenset(team_mailboxes(Path(data_dir)))


def detect_pii(text: str, gazetteer: Gazetteer | None = None, allowed_emails: set[str] | frozenset[str] | None = None) -> list[str]:
    """PII types present in `text` (types only, sorted). Team mailboxes are not PII."""
    allowed = _cached_mailboxes(str(config.DATA_DIR)) if allowed_emails is None else allowed_emails
    s = normalize_text(text)
    if allowed:
        s = EMAIL.sub(lambda m: _ORG_MAILBOX if m.group(0).lower() in allowed else m.group(0), s)
    return anonymize(s, gazetteer, cueless=False)[1]


def _findings(label: str, types: list[str]) -> list[LintFinding]:
    return [LintFinding(severity="error", code="PII_LEAK", page_ids=[label], message=f"{label}: {MESSAGES.get(t, t)}") for t in types]


def leak_scan(brain_dir: Path, data_dir: Path | None = None) -> list[LintFinding]:
    brain_dir = Path(brain_dir)
    data_dir = Path(data_dir) if data_dir is not None else config.DATA_DIR
    gaz = build_gazetteer(data_dir)
    allowed = team_mailboxes(data_dir)
    findings: list[LintFinding] = []
    for path in sorted(p for p in brain_dir.rglob("*") if p.is_file()):
        text = VERSIONED_FILE.sub("", path.read_text(encoding="utf-8"))
        findings += _findings(path.relative_to(brain_dir).as_posix(), detect_pii(text, gaz, allowed))
    return findings


def leak_scan_store(store, data_dir: Path | None = None) -> list[LintFinding]:
    """Scan persisted cases, audit rows and communications. Nothing is exempt: `related` holds surrogate PRF-xxxx keys."""
    data_dir = Path(data_dir) if data_dir is not None else config.DATA_DIR
    gaz = build_gazetteer(data_dir)
    allowed = team_mailboxes(data_dir)
    findings: list[LintFinding] = []

    def scan(label: str, obj: dict) -> None:
        findings.extend(_findings(label, detect_pii(json.dumps(obj, ensure_ascii=False, default=str), gaz, allowed)))

    for c in store.list_cases():
        scan(f"case:{c.id}", json.loads(c.model_dump_json()))
    for e in store.list_audit():
        scan(f"audit:{e.id}", json.loads(e.model_dump_json()))
    for m in store.list_comms():
        scan(f"comm:{m.id}", json.loads(m.model_dump_json()))        # no allowlist: communications hold no raw contacts
    return findings
