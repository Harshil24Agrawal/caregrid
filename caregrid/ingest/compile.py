"""Compile synthetic data into the Second Brain: markdown pages with YAML frontmatter + index.md + log.md.

profiles.json and billing.csv are NEVER read here. Routing rules are folded into workflow meta and also copied to
second_brain/config/routing_rules.csv for rules.py (decisions logged in BUILD_PLAN.md).
"""
from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from caregrid.ingest.anonymize import Gazetteer, anonymize, build_gazetteer
from caregrid.ingest.pagefmt import (
    log_line, page_relpath, page_text, precedent_relpath, precedent_text, precedent_title, render_index, write_text,
)
from caregrid.models import Page, PageStatus, PageType, Precedent

PAGE_DIRS = {t: t.value for t in PageType}

REG_HIPAA = """HIPAA protects individually identifiable health information (PHI). Operations staff apply the **minimum necessary**
rule: use or disclose only what a task needs. Under the Safe Harbor method, 18 identifier types must be removed before data
is treated as de-identified, including names, geographic detail smaller than a state, dates tied to a person, telephone
numbers, email addresses, record numbers, account numbers, and any other unique identifying code.

CareGrid masks these identifiers before any text is stored or sent to a model."""

REG_DPDP = """India's Digital Personal Data Protection Act, 2023 governs processing of digital personal data. Key duties:
**consent** that is free, specific and informed; **purpose limitation** (use data only for the stated purpose);
**data minimisation** (collect only what is necessary); security safeguards; and respect for **data principal rights**
(access, correction, erasure and grievance redress).

CareGrid keeps personal data out of the Second Brain and restricts it by role."""


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _field_page_id(field: str) -> str:
    return "FIELD-" + field.upper().replace("_", "-")


def _write_page(brain_dir: Path, page: Page) -> dict:
    relpath = page_relpath(page)
    write_text(brain_dir / relpath, page_text(page))
    return {"id": page.id, "type": page.type.value, "status": page.status.value, "title": page.title, "path": relpath}


def compile_brain(data_dir: Path, brain_dir: Path) -> dict:
    data_dir, brain_dir = Path(data_dir), Path(brain_dir)
    gaz: Gazetteer = build_gazetteer(data_dir)

    # clean only the directories/files this compiler owns
    for sub in [*PAGE_DIRS.values(), "config"]:
        shutil.rmtree(brain_dir / sub, ignore_errors=True)
    brain_dir.mkdir(parents=True, exist_ok=True)

    def clean(text: str) -> str:
        return anonymize(text, gaz)[0]

    index: list[dict] = []
    counts = {t.value: 0 for t in PageType}

    def emit(page: Page) -> None:
        index.append(_write_page(brain_dir, page))
        counts[page.type.value] += 1

    # ---- policies (versioned side by side; status from the CSV)
    articles = _rows(data_dir / "knowledge_articles.csv")
    current: dict[str, int] = {}
    for a in articles:
        if a["status"] == "approved":
            current[a["id"]] = max(current.get(a["id"], 0), int(a["version"]))
    for a in articles:
        meta = {k: a[k] for k in ("rule_key", "rule_value") if a[k]}
        page = Page(
            id=a["id"], type=PageType.POLICY, title=a["title"], version=int(a["version"]), status=PageStatus(a["status"]),
            effective_from=date.fromisoformat(a["effective_from"]) if a["effective_from"] else None,
            owner=a["owner"] or None, request_types=[x for x in a["request_types"].split(";") if x],
            body=clean(a["body"]), meta=meta,
        )
        emit(page)

    # ---- fields
    for f in _rows(data_dir / "field_definitions.csv"):
        page = Page(id=_field_page_id(f["field"]), type=PageType.FIELD, title=f["field"], status=PageStatus.APPROVED,
                    body=clean(f["meaning"]),
                    meta={"field": f["field"], "regex": f["regex"], "pii": f["pii"] == "true"})
        emit(page)

    # ---- teams
    for t in _rows(data_dir / "teams.csv"):
        body = f"{t['name']} handles: {t['handles']}.\n\nDoes not handle: {t['does_not_handle']}."
        page = Page(id=t["id"], type=PageType.TEAM, title=t["name"], status=PageStatus.APPROVED, body=clean(body),
                    meta={"contact_email": t["contact_email"], "handles": t["handles"], "does_not_handle": t["does_not_handle"]})
        emit(page)

    # ---- runbooks
    rb_text = (data_dir / "runbooks.md").read_text(encoding="utf-8")
    runbook_ids: dict[str, str] = {}
    for m in re.finditer(r"^## (RB-\d+) (.+?)\n(.*?)(?=^## |\Z)", rb_text, re.S | re.M):
        runbook_ids[m.group(1)] = m.group(2)
        page = Page(id=m.group(1), type=PageType.RUNBOOK, title=m.group(2), status=PageStatus.APPROVED, body=clean(m.group(3)))
        emit(page)

    # ---- regulatory (authored)
    for rid, title, body in (("REG-HIPAA", "HIPAA - PHI and Safe Harbor identifiers", REG_HIPAA),
                             ("REG-DPDP", "India DPDP Act 2023 - summary", REG_DPDP)):
        emit(Page(id=rid, type=PageType.REGULATORY, title=title, status=PageStatus.APPROVED, body=body))

    # ---- workflows (+ routing rules folded into meta)
    rules = {r["condition"]: r for r in _rows(data_dir / "routing_rules.csv")}
    for wf in json.loads((data_dir / "workflows.json").read_text(encoding="utf-8")):
        rr = rules.get(f"request_type={wf['request_type']}")
        links = [*wf["policy_ids"], wf["team"], *[_field_page_id(x) for x in wf["required_fields"]], *wf.get("runbook_ids", [])]
        meta = {k: wf[k] for k in ("request_type", "required_fields", "steps", "team", "risk", "action_tier",
                                   "policy_ids", "thresholds", "never_auto")}
        meta["runbook_ids"] = wf.get("runbook_ids", [])
        meta["routing_rule"] = rr["id"] if rr else None
        meta["approver_role"] = rr["approver_role"] if rr else "team_specialist"
        body = "\n".join(f"{i}. {s}" for i, s in enumerate(wf["steps"], 1))
        emit(Page(id=wf["id"], type=PageType.WORKFLOW, title=wf["title"], status=PageStatus.APPROVED,
                  request_types=[wf["request_type"]], links=links, body=body, meta=meta))

    # ---- precedents (masked raw_text -> summary; STALE when policy version is no longer current)
    stale = 0
    for h in _rows(data_dir / "historical_cases.csv"):
        pol_id = h["policy_id"] or None
        pol_ver = int(h["policy_version"]) if h["policy_version"] else None
        is_stale = bool(pol_id and pol_ver is not None and pol_ver != current.get(pol_id))
        stale += is_stale
        prec = Precedent(
            id=h["id"], request_type=h["request_type"], facts=json.loads(h["facts_json"]),
            fields_provided=[x for x in h["fields_provided"].split(";") if x], summary=clean(h["raw_text"]),
            decision_code=h["decision_code"], route_team=h["route_team"] or None, policy_id=pol_id, policy_version=pol_ver,
            reason_codes=[x for x in h["reason_codes"].split(";") if x], approver_role=h["approver_role"], risk=h["risk"],
            status=PageStatus.STALE if is_stale else PageStatus.ACTIVE, date=date.fromisoformat(h["date"]),
            outcome=clean(h["outcome"]),
        )
        write_text(brain_dir / precedent_relpath(prec), precedent_text(prec))
        index.append({"id": prec.id, "type": "precedent", "status": prec.status.value,
                      "title": precedent_title(prec), "path": precedent_relpath(prec)})
        counts["precedent"] += 1

    # ---- config copy for rules.py
    shutil.copyfile(data_dir / "routing_rules.csv", _ensure(brain_dir / "config" / "routing_rules.csv"))

    # ---- index.md + log.md
    write_text(brain_dir / "index.md", render_index(index))
    stamp = datetime.now().isoformat(timespec="seconds")
    summary = ", ".join(f"{k}={v}" for k, v in counts.items())
    write_text(brain_dir / "log.md",
               "# Second Brain log\n\n"
               f"- {stamp} | system | compile | * | compiled {sum(counts.values())} pages ({summary}); {stale} stale precedent(s)\n")
    return {**counts, "stale_precedents": stale}


def _ensure(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
