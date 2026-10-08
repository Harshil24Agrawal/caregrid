"""Headless smoke + click-path tests for app_min (Streamlit AppTest) on a throw-away world: temp data, brain, database."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from caregrid import config  # noqa: E402
from caregrid.admin import reset_demo  # noqa: E402
from caregrid.store import SQLiteStore  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app_min"
PAGES = {"home": "Home.py", "new": "pages/1_New_Request.py", "case": "pages/2_Case.py", "knowledge": "pages/3_Knowledge.py"}
USERS = {"asha": "U1", "vikram": "U2", "neha": "U3", "rahul": "U4", "meera": "U5", "arjun": "U6", "kiran": "U7"}

S1 = "What supporting documents are accepted for provider record changes?"
S2 = "Dr. Ramesh Iyer wants to update his billing address to 14 Lake Road, Chennai. NPI 123456789."
S3 = "A clinic staff member is locked out of the provider portal, email staff@clinic.example, provider NPI 1234567890. Can we reset it?"
S4A = "Should this patient double her insulin dose?"
S6A = "Provider NPI 1234567890 legally changed name from Priya Nair to Priya Menon, W-9 attached."


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("appmin")
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "DATA_DIR", root / "data")
    mp.setattr(config, "BRAIN_DIR", root / "brain")
    mp.setattr(config, "EVAL_DIR", root / "eval")
    mp.setattr(config, "DB_PATH", root / "db.sqlite")
    mp.setattr(config, "LLM_PROVIDER", "mock")
    import streamlit as st

    st.cache_resource.clear()
    reset_demo()
    yield root
    st.cache_resource.clear()
    mp.undo()


def open_page(page: str, user: str, **state) -> AppTest:
    at = AppTest.from_file(str(APP / PAGES[page]), default_timeout=90)
    at.session_state["uid"] = USERS[user]
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def clean(at: AppTest) -> AppTest:
    assert not at.exception, [e.value for e in at.exception]
    return at


def submit(user: str, text: str) -> AppTest:
    at = open_page("new", user)
    at.text_area(key="req_text").set_value(text)
    next(b for b in at.button if b.label == "Submit").click()
    return clean(at.run())


def text_of(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [m.value for m in at.success] + [m.value for m in at.info] + [m.value for m in at.warning] \
        + [m.value for m in at.error] + [m.value for m in at.caption]
    return "\n".join(str(p) for p in parts)


# ------------------------------------------------------------------ every page loads for every role
@pytest.mark.parametrize("user", list(USERS))
@pytest.mark.parametrize("page", list(PAGES))
def test_every_page_loads_for_every_role(world, page, user):
    clean(open_page(page, user))


# ------------------------------------------------------------------ click paths for the scenarios
def test_s1_trusted_answer(world):
    at = submit("asha", S1)
    assert "Answered automatically" in text_of(at) and "KA-02" in text_of(at)
    assert at.text_area(key="req_text").value == ""                                   # input cleared after submit


def test_s2_one_numbered_message_and_masked_chips(world):
    at = submit("asha", S2)
    body = text_of(at)
    assert "ONE message" in body and "1." in body and "2." in body and "3." in body
    assert "Ramesh" not in body and "Lake Road" not in body                           # raw values never shown
    assert any("PERSON" in str(m.value) or "ADDRESS" in str(m.value).upper() for m in at.markdown)   # masked-type chips


def test_s3_conflict_goes_to_review_with_team_and_reasons(world):
    at = submit("asha", S3)
    body = text_of(at)
    assert "human review" in body and "TEAM-IT" in body and "POLICY_CONFLICT" in body


def test_s4_clinical_is_refused_and_routed(world):
    at = submit("asha", S4A)
    body = text_of(at)
    assert "cannot be answered" in body and "TEAM-CLINICAL" in body and "CLINICAL" in body


def test_case_page_shows_evidence_locks_and_graph_by_role(world):
    rahul = clean(open_page("case", "rahul", last_case_id="CASE-1024"))
    body = text_of(rahul)
    assert "INV-1024" in body and "L-552" in body and "restricted" not in body
    meera = clean(open_page("case", "meera", last_case_id="CASE-1024"))
    assert "🔒 restricted" in text_of(meera) and "INV-1024" not in text_of(meera)
    asha = clean(open_page("case", "asha", last_case_id="CASE-1024"))                 # her own request: summary yes, evidence no
    assert "🔒 restricted" in text_of(asha) and "INV-1024" not in text_of(asha) and "L-552" not in text_of(asha)
    kiran = clean(open_page("case", "kiran", last_case_id="CASE-1024"))               # TEAM-IT: this is a TEAM-SENIOR-OPS case
    assert "not visible" in text_of(kiran) and "INV-1024" not in text_of(kiran)


def test_s5_approval_buttons_and_state_path(world):
    asha = clean(open_page("case", "asha", last_case_id="CASE-1024"))
    assert all(b.disabled for b in asha.button if b.label == "Submit decision")      # requester / wrong role: disabled, reason shown
    assert "🔒" in text_of(asha)
    vik = clean(open_page("case", "vikram", last_case_id="CASE-1024"))
    assert "not visible" in text_of(vik)                                              # a TEAM-SENIOR-OPS case is not in his queue
    rahul = open_page("case", "rahul", last_case_id="CASE-1024")
    rahul.checkbox(key="prec_CASE-1024").set_value(True)
    rahul.multiselect(key="ch_CASE-1024").set_value(["email"])
    rahul.text_input(key="em_CASE-1024").set_value("dme.desk@clinic-supplies.example")
    btn = next(b for b in rahul.button if b.label == "Submit decision")
    assert not btn.disabled
    btn.click()
    clean(rahul.run())
    body = text_of(rahul)
    assert "Decision recorded" in body and "notified" in body and "Precedent:" in body and "Trust:" in body
    assert SQLiteStore().get_case("CASE-1024").state.value == "notified"


def test_s6_name_change_then_similar_request_compounds(world):
    at = submit("asha", S6A)
    case_id = at.session_state["last_case_id"]
    vik = open_page("case", "vikram", last_case_id=case_id)
    btn = next(b for b in vik.button if b.label == "Submit decision")
    assert not btn.disabled
    btn.click()
    clean(vik.run())
    assert "P-" in text_of(vik) or "Precedent:" in text_of(vik)


def test_s7_pr_loop_through_the_ui(world):
    at = submit("asha", S3)
    case_id = at.session_state["last_case_id"]
    kiran = open_page("case", "kiran", last_case_id=case_id)
    kiran.checkbox(key=f"pr_{case_id}").set_value(True)
    kiran.run()
    kiran.selectbox(key=f"tgt_{case_id}").set_value("KA-32")
    kiran.checkbox(key=f"ret_{case_id}").set_value(True)
    kiran.text_area(key=f"note_{case_id}").set_value("KA-32 is superseded by KA-31; retire it.")
    kiran.run()
    next(b for b in kiran.button if b.label == "Submit decision").click()
    clean(kiran.run())
    assert "Knowledge PR opened" in text_of(kiran)
    kiran_k = clean(open_page("knowledge", "kiran"))
    assert all(b.disabled for b in kiran_k.button if b.label in ("Approve", "Reject")) and any(b.label == "Approve" for b in kiran_k.button)
    meera = clean(open_page("knowledge", "meera"))
    approve = next(b for b in meera.button if b.label == "Approve")
    assert not approve.disabled
    approve.click()
    clean(meera.run())
    from caregrid.knowledge.brain import Brain

    assert Brain(config.BRAIN_DIR).get("KA-32") is None
