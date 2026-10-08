import shutil
from datetime import date, datetime

import pytest

from caregrid import config
from caregrid.cli import main
from caregrid.ingest.compile import compile_brain
from caregrid.ingest.generate import generate
from caregrid.knowledge.brain import Brain
from caregrid.knowledge.lint import lint
from caregrid.knowledge.retrieve import derive_case_facts, facts_match, retrieve, tokenize
from caregrid.llm import MockLLM
from caregrid.models import (
    Case, Classification, DecisionCode, Page, PageStatus, PageType, Precedent, ReasonCode, Risk, Role, User,
)
from caregrid.store import SQLiteStore

ASHA = User(id="U1", name="Asha", role=Role.OPS_EMPLOYEE, team="TEAM-OPS-TRIAGE")

ADDRESS_TEXT = "[PROVIDER_1] wants to update his billing address to [ADDRESS] effective 2026-11-01. NPI [NPI]. W-9 attached."
S1_TEXT = "What supporting documents are accepted for provider record changes?"


@pytest.fixture(scope="module")
def paths(tmp_path_factory):
    root = tmp_path_factory.mktemp("p2")
    generate(root / "data", eval_dir=root / "eval")
    compile_brain(root / "data", root / "brain")
    return root


@pytest.fixture(scope="module")
def brain(paths):
    return Brain(paths / "brain")


@pytest.fixture()
def brain_copy(paths, tmp_path):
    shutil.copytree(paths / "brain", tmp_path / "brain")
    return Brain(tmp_path / "brain")


def cls(rt, **fields):
    return Classification(request_type=rt, llm_confidence=0.9, rules_type=rt, extracted_fields=fields)


ADDRESS_FIELDS = {"npi": "[NPI]", "new_address": "[ADDRESS]", "effective_date": "2026-11-01", "supporting_document": "W-9"}


def policy_ids(res):
    return {s.page.key: s for s in res.policies}


# ------------------------------------------------------------------ Brain
def test_brain_get_current_and_explicit_versions(brain):
    assert brain.get("KA-12").version == 3
    assert brain.get("KA-12", 2).status == PageStatus.EXPIRED
    assert brain.get("KA-60") is None and brain.get("KA-60", 1).status == PageStatus.DRAFT
    assert brain.get("KA-15") is None
    assert brain.get("NOPE") is None
    assert brain.get("P-88") is None and brain.get_precedent("P-88").status == PageStatus.STALE
    view = brain.get("P-91")
    assert view.type == PageType.PRECEDENT and view.meta["decision_code"] == "request_missing_info"


def test_current_policies_exclude_draft_and_expired(brain):
    keys = {p.key for p in brain.current_policies()}
    assert "KA-12@v3" in keys and "KA-12@v2" not in keys and "KA-60@v1" not in keys and "KA-15@v1" not in keys
    assert all(p.status == PageStatus.APPROVED for p in brain.current_policies())


def test_workflow_team_precedent_queries(brain):
    assert brain.workflow_for("provider_address_change").id == "WF-03"
    assert brain.workflow_for("unknown") is None
    assert brain.team("TEAM-IT").meta["contact_email"] == "servicedesk@caregrid.example"
    assert brain.team("WF-03") is None
    assert [p.id for p in brain.precedents("provider_address_change", PageStatus.STALE)] == ["P-88"]
    assert brain.precedents("provider_name_change") == []


def test_reload_picks_up_external_changes(brain_copy):
    assert brain_copy.get("KA-99") is None
    src = brain_copy.dir / "policy" / "KA-12@v3.md"
    (brain_copy.dir / "policy" / "KA-99@v1.md").write_text(src.read_text(encoding="utf-8").replace("id: KA-12", "id: KA-99"), encoding="utf-8")
    assert brain_copy.get("KA-99") is None
    brain_copy.reload()
    assert brain_copy.get("KA-99").version == 3


def test_write_page_bumps_version_expires_old_and_stales_precedents(brain_copy):
    old = brain_copy.get("KA-12")
    assert {p.id for p in brain_copy.precedents("provider_address_change", PageStatus.ACTIVE)} >= {"P-91", "P-92", "P-93"}
    brain_copy.write_page(old.model_copy(update={"body": old.body + " Documents must be dated within 6 months."}))

    assert brain_copy.get("KA-12").version == 4 and "6 months" in brain_copy.get("KA-12").body
    assert brain_copy.get("KA-12", 3).status == PageStatus.EXPIRED
    stale = {p.id for p in brain_copy.precedents(status=PageStatus.STALE)}
    assert {"P-88", "P-91", "P-92", "P-93"} <= stale
    assert brain_copy.precedents("provider_address_change", PageStatus.ACTIVE) == []
    # on disk too: a fresh Brain agrees, files are side by side, log + index updated
    fresh = Brain(brain_copy.dir)
    assert fresh.get("KA-12").version == 4 and fresh.get("KA-12", 3).status == PageStatus.EXPIRED
    assert (brain_copy.dir / "policy" / "KA-12@v3.md").exists() and (brain_copy.dir / "policy" / "KA-12@v4.md").exists()
    log = (brain_copy.dir / "log.md").read_text(encoding="utf-8")
    assert "write_page | KA-12 | published v4" in log and "mark_stale | KA-12" in log
    index = (brain_copy.dir / "index.md").read_text(encoding="utf-8")
    assert "KA-12@v4.md" in index and "approved" in index
    # retrieval now links v4
    res = retrieve(fresh, cls("provider_address_change", **ADDRESS_FIELDS), ADDRESS_TEXT, MockLLM())
    assert "KA-12@v4" in policy_ids(res) and "KA-12@v3" not in policy_ids(res)
    assert res.precedents_active == [] and {s.precedent.id for s in res.precedents_stale} >= {"P-91", "P-92", "P-93"}


def test_write_page_non_policy_replaces_in_place(brain_copy):
    team = brain_copy.get("TEAM-IT")
    brain_copy.write_page(team.model_copy(update={"title": "IT Service Desk (renamed)"}))
    assert brain_copy.get("TEAM-IT").version == 2 and brain_copy.get("TEAM-IT").title.endswith("(renamed)")
    assert Brain(brain_copy.dir).get("TEAM-IT").version == 2


def test_write_precedent_and_index(brain_copy):
    prec = Precedent(id="P-abc123", request_type="provider_name_change", facts={"category": "record_update"},
                     summary="[PROVIDER_1] changed name, W-9 attached.", decision_code=DecisionCode.ROUTE_TO_TEAM,
                     route_team="TEAM-ENROLL", approver_role=Role.TEAM_SPECIALIST, risk=Risk.LOW, date=date(2026, 10, 8),
                     outcome="Approved.")
    brain_copy.write_precedent(prec)
    assert brain_copy.get("P-abc123").status == PageStatus.ACTIVE
    assert Brain(brain_copy.dir).get_precedent("P-abc123") == prec
    assert "P-abc123" in (brain_copy.dir / "index.md").read_text(encoding="utf-8")
    assert "write_precedent | P-abc123" in (brain_copy.dir / "log.md").read_text(encoding="utf-8")


def test_mark_stale_for_policy_is_idempotent(brain_copy):
    assert brain_copy.mark_stale_for_policy("KA-12", 3) == []
    assert sorted(brain_copy.mark_stale_for_policy("KA-02", 2)) == [f"P-{i}" for i in (10, 11, 12, 13, 14, 18, 19)]
    assert brain_copy.mark_stale_for_policy("KA-02", 2) == []


# ------------------------------------------------------------------ retrieval
def test_address_change_retrieval(brain):
    res = retrieve(brain, cls("provider_address_change", **ADDRESS_FIELDS), ADDRESS_TEXT, MockLLM())
    pol = policy_ids(res)
    assert pol["KA-12@v3"].linked is True and "KA-12@v2" not in pol
    assert res.workflow.id == "WF-03" and res.team.id == "TEAM-ENROLL"
    assert {s.precedent.id for s in res.precedents_active} >= {"P-91", "P-92", "P-93"}
    assert [s.precedent.id for s in res.precedents_stale] == ["P-88"]
    assert all(s.precedent.status == PageStatus.ACTIVE for s in res.precedents_active)
    assert {p.meta["field"] for p in res.fields} == set(ADDRESS_FIELDS)
    assert res.case_facts == {"category": "record_update", "missing": "none", "risk": "low", "team": "TEAM-ENROLL"}


def test_portal_reset_retrieval_returns_conflict_pair_not_expired(brain):
    res = retrieve(brain, cls("portal_access_reset", user_email="[EMAIL]", provider_npi="[NPI]"),
                   "A clinic staff member is locked out of the provider portal, email [EMAIL], provider NPI [NPI]. Can we reset it?", MockLLM())
    pol = policy_ids(res)
    assert pol["KA-31@v1"].linked and pol["KA-32@v1"].linked
    assert not any(s.page.id == "KA-15" for s in res.policies)
    assert res.case_facts["missing"] == "none"


def test_name_change_retrieval_has_search_only_policy_and_no_precedents(brain):
    res = retrieve(brain, cls("provider_name_change", npi="[NPI]", old_name="x", new_name="y", supporting_document="W-9"),
                   "Provider NPI [NPI] legally changed name from [PERSON_1] to [PERSON_2], W-9 attached.", MockLLM())
    pol = policy_ids(res)
    assert pol["KA-05@v1"].linked is False
    assert not any(s.linked for s in res.policies)  # WF-05 links no policy
    assert res.precedents_active == [] and res.precedents_stale == []


def test_s1_retrieval(brain):
    res = retrieve(brain, cls("general_policy_question"), S1_TEXT, MockLLM())
    assert "KA-02@v1" in policy_ids(res) and res.policies[0].page.id == "KA-02"
    good = [s for s in res.precedents_active
            if s.precedent.decision_code == DecisionCode.ANSWER_FROM_POLICY and s.precedent.route_team == "TEAM-OPS-TRIAGE"
            and s.similarity >= config.PRECEDENT_MIN_SIM]
    assert len(good) >= 3
    assert not any(s.precedent.decision_code == DecisionCode.NOT_ENOUGH_EVIDENCE for s in res.precedents_active[:10]
                   if s.similarity >= config.PRECEDENT_MIN_SIM)
    print("S1 top-3 precedent similarities:", [(s.precedent.id, s.similarity) for s in res.precedents_active[:3]])


def test_unknown_type_has_no_workflow_or_precedents(brain):
    res = retrieve(brain, cls("unknown"), "help", MockLLM())
    assert res.workflow is None and res.precedents_active == [] and res.case_facts["team"] == "TEAM-OPS-TRIAGE"


@pytest.mark.parametrize("rt", ["general_policy_question", "provider_address_change", "provider_name_change", "portal_access_reset",
                                "prior_auth_status", "dme_equipment_request", "claim_status_inquiry", "complaint_grievance", "unknown"])
def test_no_retrieval_result_contains_draft_or_expired(brain, rt):
    text = "telehealth onboarding old portal password policy provider billing address change documents W-9 password reset claim"
    res = retrieve(brain, cls(rt), text, MockLLM())
    pages = [s.page for s in res.policies] + res.regulatory + res.fields + [p for p in (res.workflow, res.team) if p]
    assert all(p.status == PageStatus.APPROVED for p in pages)
    keys = {p.key for p in pages}
    assert not keys & {"KA-60@v1", "KA-15@v1", "KA-12@v2"}
    assert all(s.precedent.status == PageStatus.ACTIVE for s in res.precedents_active)
    assert all(s.precedent.status == PageStatus.STALE for s in res.precedents_stale)


def test_derive_case_facts_aliases_and_missing(brain):
    wf = brain.workflow_for("portal_access_reset")
    assert derive_case_facts(wf, cls("portal_access_reset", user_email="a", npi="b"))["missing"] == "none"
    assert derive_case_facts(wf, cls("portal_access_reset", user_email="a"))["missing"] == "provider_npi"
    wf = brain.workflow_for("provider_address_change")
    assert derive_case_facts(wf, cls("provider_address_change", npi="x"))["missing"] == "effective_date,new_address,supporting_document"
    assert derive_case_facts(wf, cls("provider_address_change", npi=""))["missing"].startswith("effective_date,new_address,npi")
    assert facts_match({"a": "1", "b": "2"}, {"a": "1", "b": "x"}) == 0.5


def test_full_facts_match_alone_reaches_threshold_documented(brain):
    # Decisions log: 0.6 * 1.0 facts match already equals PRECEDENT_MIN_SIM, so wording only ranks above it.
    assert 0.6 * facts_match({"x": "1"}, {"x": "1"}) >= config.PRECEDENT_MIN_SIM


def test_tokenize_stems_variants_together():
    assert tokenize("changed")[0] == tokenize("change")[0] == tokenize("changes")[0]
    assert tokenize("address") == tokenize("addresses") != []


# ------------------------------------------------------------------ lint
def test_lint_planted_findings_exactly(brain):
    findings = lint(brain)
    serious = [f for f in findings if f.severity != "info"]
    assert sorted(f.code for f in serious) == ["CONTRADICTION", "ESCALATION_HOTSPOT", "EXPIRED_LINKED", "STALE_PRECEDENT"]
    by = {f.code: f for f in serious}
    assert by["CONTRADICTION"].page_ids == ["KA-31", "KA-32"] and by["CONTRADICTION"].severity == "error"
    assert by["EXPIRED_LINKED"].page_ids == ["WF-07", "KA-15"]
    assert by["STALE_PRECEDENT"].page_ids == ["P-88"]
    hot = by["ESCALATION_HOTSPOT"]
    assert len(hot.page_ids) == 8 and "telehealth" in hot.message and "KA-60" in hot.message
    assert all(f.code == "ORPHAN" for f in findings if f.severity == "info")
    assert any(f.page_ids == ["KA-04"] for f in findings)


def test_lint_hotspot_includes_store_cases(brain, tmp_path):
    store = SQLiteStore(tmp_path / "s.sqlite")
    for i in range(5):
        store.save_case(Case(id=f"REQ-{i + 1:04d}", created_at=datetime(2026, 10, 8), requester=ASHA,
                             masked_text="Do we have a rental wheelchair vendor policy?", reason_codes=[ReasonCode.POLICY_GAP],
                             classification=Classification(request_type="dme_equipment_request")))
    hot = [f for f in lint(brain, store) if f.code == "ESCALATION_HOTSPOT"]
    assert len(hot) == 2
    assert any("dme_equipment_request" in f.message and "wheelchair" in f.message for f in hot)
    assert len([f for f in lint(brain) if f.code == "ESCALATION_HOTSPOT"]) == 1


def test_lint_missing_team_and_clean_after_fixes(brain_copy):
    wf = brain_copy.get("WF-01")
    brain_copy.write_page(wf.model_copy(update={"meta": {**wf.meta, "team": "TEAM-NOPE"}}))
    msgs = [f for f in lint(brain_copy) if f.code == "MISSING_TEAM"]
    assert len(msgs) == 1 and msgs[0].page_ids == ["WF-01"] and msgs[0].severity == "error"


def test_lint_does_not_modify_brain(brain_copy):
    before = {p.name: p.read_bytes() for p in brain_copy.dir.rglob("*.md")}
    lint(brain_copy)
    assert before == {p.name: p.read_bytes() for p in brain_copy.dir.rglob("*.md")}


def test_cli_lint(paths, monkeypatch, capsys):
    monkeypatch.setattr(config, "BRAIN_DIR", paths / "brain")
    monkeypatch.setattr(config, "DB_PATH", paths / "missing.sqlite")
    assert main(["lint"]) == 0
    out = capsys.readouterr().out
    for needle in ("ERROR (1)", "WARNING (3)", "INFO (", "CONTRADICTION", "EXPIRED_LINKED", "STALE_PRECEDENT", "ESCALATION_HOTSPOT"):
        assert needle in out
    assert not (paths / "missing.sqlite").exists()
