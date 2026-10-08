from datetime import date, datetime

import numpy as np
import pytest

from caregrid import config
from caregrid.cli import main
from caregrid.llm import MockLLM, get_llm, parse_json_tolerant
from caregrid.models import (
    AuditEvent, Case, Channel, Classification, Communication, DecisionCode, KnowledgePR, Page, PageStatus,
    PageType, Precedent, Risk, Role, State, TrustRecord, User,
)
from caregrid.store import SQLiteStore

ASHA = User(id="U1", name="Asha", role=Role.OPS_EMPLOYEE, team="TEAM-OPS-TRIAGE")


def make_case(case_id: str = "REQ-0001", state: State = State.NEW) -> Case:
    return Case(id=case_id, created_at=datetime(2026, 10, 8, 12, 0), requester=ASHA, masked_text="[NPI] moved",
                state=state, classification=Classification(request_type="unknown"))


# ---- config / models
def test_config_defaults():
    assert config.LLM_PROVIDER == "mock"
    assert config.POLICY_MIN_SCORE == 0.35 and config.PRECEDENT_MIN_SIM == 0.6
    assert config.TRUST_L1_STREAK == 10


def test_page_key_and_precedent_date_field():
    page = Page(id="KA-12", type=PageType.POLICY, title="t", version=3, status=PageStatus.APPROVED, body="b")
    assert page.key == "KA-12@v3"
    p = Precedent(id="P-91", request_type="provider_address_change", summary="s", decision_code=DecisionCode.ROUTE_TO_TEAM,
                  approver_role=Role.TEAM_SPECIALIST, risk=Risk.LOW, date=date(2026, 9, 1), outcome="ok")
    assert Precedent.model_validate_json(p.model_dump_json()) == p


# ---- llm
def test_get_llm_mock_and_unknown_provider(monkeypatch):
    assert isinstance(get_llm(), MockLLM)
    monkeypatch.setattr(config, "LLM_PROVIDER", "nope")
    with pytest.raises(ValueError):
        get_llm()


def test_mock_classify_is_deterministic_and_tracks_tiers():
    llm = MockLLM()
    sys_prompt = "You classify healthcare operations requests. You do not answer them."
    a = llm.complete_json(sys_prompt, "REQUEST:\nPlease update the billing address", "light")
    b = llm.complete_json(sys_prompt, "REQUEST:\nPlease update the billing address", "light")
    assert a == b and a["request_type"] == "provider_address_change" and a["confidence"] == 0.9
    clinical = llm.complete_json(sys_prompt, "REQUEST:\nShould this patient double her insulin dose?", "light")
    assert clinical["is_clinical"] is True
    unknown = llm.complete_json(sys_prompt, "REQUEST:\nhello there", "strong")
    assert unknown["request_type"] == "unknown" and unknown["confidence"] == 0.3
    assert llm.calls == ["light", "light", "light", "strong"]


def test_mock_embed_shape_and_normalised():
    vecs = MockLLM().embed(["address change", "address change", "totally different words"])
    assert len(vecs[0]) == 512
    assert np.linalg.norm(vecs[0]) == pytest.approx(1.0)
    assert vecs[0] == vecs[1]
    assert float(np.dot(vecs[0], vecs[2])) < 0.5


def test_parse_json_tolerant():
    assert parse_json_tolerant('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_tolerant('Sure! {"a": 1} hope that helps') == {"a": 1}
    for bad in ("no json", "[1,2]", "{broken"):
        with pytest.raises(ValueError):
            parse_json_tolerant(bad)


# ---- store
def test_store_cases_roundtrip_and_ids(tmp_path):
    s = SQLiteStore(tmp_path / "t.sqlite")
    assert s.next_case_id() == "REQ-0001"
    s.save_case(make_case("REQ-0001"))
    s.save_case(make_case("CASE-1024", State.IN_REVIEW))
    assert s.next_case_id() == "REQ-0002"
    assert s.get_case("CASE-1024").state == State.IN_REVIEW
    assert s.get_case("missing") is None
    assert [c.id for c in s.list_cases(state=State.IN_REVIEW)] == ["CASE-1024"]
    c = s.get_case("REQ-0001")
    c.state = State.CLOSED
    s.save_case(c)
    assert len(s.list_cases()) == 2 and s.get_case("REQ-0001").state == State.CLOSED


def test_store_audit_trust_pr_comm(tmp_path):
    s = SQLiteStore(tmp_path / "t.sqlite")
    now = datetime(2026, 10, 8, 12, 0)
    for i, case_id in enumerate(["REQ-0001", "REQ-0001", None]):
        s.append_audit(AuditEvent(id=f"A{i}", ts=now, case_id=case_id, actor_id="U1", actor_role="ops_employee", event="e"))
    assert [e.id for e in s.list_audit("REQ-0001")] == ["A0", "A1"] and len(s.list_audit()) == 3

    fresh = s.get_trust("provider_name_change")
    assert fresh.level == 0 and fresh.total_reviews == 0
    s.save_trust(TrustRecord(request_type="general_policy_question", level=1, updated_at=now))
    assert s.get_trust("general_policy_question").level == 1

    pr = KnowledgePR(id="PR-1", target_page_id="KA-12", base_version=3, proposed_body="b", diff="d", reason="r",
                     author_id="U2", created_at=now)
    s.save_pr(pr)
    assert s.list_prs("open") == [pr] and s.list_prs("approved") == []

    comm = Communication(id="C1", case_id="REQ-0001", channel=Channel.EMAIL, recipient="[EMAIL]", message="m",
                         status="simulated", ts=now)
    s.save_comm(comm)
    assert s.list_comms("REQ-0001") == [comm] and s.list_comms("other") == []


def test_store_utf8_roundtrip_and_wipe(tmp_path):
    s = SQLiteStore(tmp_path / "t.sqlite")
    c = make_case()
    c.masked_text = "cost ₹62,500 above ₹50,000"
    s.save_case(c)
    assert s.get_case(c.id).masked_text == "cost ₹62,500 above ₹50,000"
    s.wipe()
    assert s.list_cases() == []


def test_store_in_memory():
    s = SQLiteStore(":memory:")
    s.save_case(make_case())
    assert s.get_case("REQ-0001") is not None


# ---- cli
def test_cli_llmcheck_mock(capsys):
    assert main(["llmcheck"]) == 0
    out = capsys.readouterr().out
    assert "OK   light" in out and "OK   strong" in out and "OK   embed" in out


def test_cli_llmcheck_fails_gracefully(monkeypatch, capsys):
    monkeypatch.setattr(config, "LLM_PROVIDER", "nope")
    assert main(["llmcheck"]) == 1
    assert "FAIL init" in capsys.readouterr().out


def test_cli_stub(capsys):
    assert main(["eval"]) == 0
    assert "not implemented" in capsys.readouterr().out
