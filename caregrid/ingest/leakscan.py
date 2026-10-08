"""Scan every file in second_brain/ for PII. Findings use LintFinding(code="PII_LEAK"); values are never echoed."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from caregrid import config
from caregrid.ingest.anonymize import DOB, EMAIL, MEMBER_ID, PHONE_IN, PHONE_US, build_gazetteer
from caregrid.models import LintFinding

# versioned page filenames such as KA-12@v3.md look like emails; they are not
VERSIONED_FILE = re.compile(r"@v\d+\.md")
DIGITS_9_10 = re.compile(r"(?<![\w.-])\d{9,10}(?![\w])")


def team_mailboxes(data_dir: Path) -> set[str]:
    """Exact contact_email values from teams.csv: the only emails allowed in the brain."""
    path = Path(data_dir) / "teams.csv"
    if not path.exists():
        return set()
    with open(path, encoding="utf-8", newline="") as f:
        return {r["contact_email"].lower() for r in csv.DictReader(f) if r.get("contact_email")}


def leak_scan(brain_dir: Path, data_dir: Path | None = None) -> list[LintFinding]:
    brain_dir = Path(brain_dir)
    data_dir = Path(data_dir) if data_dir is not None else config.DATA_DIR
    gaz = build_gazetteer(data_dir)
    allowed_emails = team_mailboxes(data_dir)
    literals = {"name": list(gaz.names), "address": gaz.addresses, "phone": gaz.phones, "email": gaz.emails}
    findings: list[LintFinding] = []

    for path in sorted(p for p in brain_dir.rglob("*") if p.is_file()):
        text = VERSIONED_FILE.sub("", path.read_text(encoding="utf-8"))
        rel = path.relative_to(brain_dir).as_posix()
        hits: set[str] = set()

        for m in EMAIL.finditer(text):
            if m.group(0).lower() not in allowed_emails:
                hits.add("email address")
        if MEMBER_ID.search(text):
            hits.add("member ID")
        if DIGITS_9_10.search(text):
            hits.add("9-10 digit number (NPI?)")
        if PHONE_IN.search(text) or PHONE_US.search(text):
            hits.add("phone number")
        if DOB.search(text):
            hits.add("labelled date of birth")
        low = text.lower()
        for kind, values in literals.items():
            for v in values:
                if v and v.lower() in low:
                    hits.add(f"known {kind} from source data")
                    break
        for h in sorted(hits):
            findings.append(LintFinding(severity="error", code="PII_LEAK", page_ids=[rel], message=f"{rel}: {h}"))
    return findings
