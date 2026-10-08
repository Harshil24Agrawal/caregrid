"""Item 3: 'How this is handled' (workflow steps with progress) and how-to answers built from workflow steps by code."""
import csv
from pathlib import Path

import pytest

from caregrid import api
from caregrid.reasoning.extract import is_howto
from tests.test_api import H, S2, client, decide, post_request  # noqa: F401

HOWTO = "How do I change a provider's billing address?"


def steps_of(wf_id):
    return [str(s) for s in api.get_brain().get(wf_id).meta["steps"]]


def test_live_guidance_for_a_case_missing_details(client):
    case = post_request(client, "asha", S2)
    g = case["guidance"]
    assert g["mode"] == "live" and g["cite"] == "WF-03 v1" and g["workflow"]["location"] == "second_brain/workflow/WF-03.md"
    assert [s["text"] for s in g["steps"]] == steps_of("WF-03")
    assert [s["status"] for s in g["steps"]][0] == "current" and {s["status"] for s in g["steps"][1:]} == {"next"}
    by = {f["field"]: f["status"] for f in g["fields"]}
    assert by["new_address"] == "ok" and by["npi"] in ("invalid", "missing")


def test_guidance_progresses_with_the_case_state(client):
    g = client.get("/api/cases/CASE-1024", headers=H["rahul"]).json()["guidance"]
    n = len(g["steps"])
    assert [s["status"] for s in g["steps"]] == ["done"] * (n - 1) + ["current"]
    assert decide(client, "rahul", "CASE-1024").status_code == 200
    g = client.get("/api/cases/CASE-1024", headers=H["rahul"]).json()["guidance"]
    assert {s["status"] for s in g["steps"]} == {"done"}


def test_guidance_is_visible_to_a_restricted_viewer_but_holds_no_case_data(client):
    g = client.get("/api/cases/CASE-1024", headers=H["asha"]).json()["guidance"]
    assert g and all(set(f) == {"field", "label", "status"} for f in g["fields"])


def test_howto_answer_is_the_workflow_steps_built_by_code(client):
    case = post_request(client, "asha", HOWTO)
    assert case["request_type"] == "general_policy_question" and case["state"] == "answered"
    prop = case["proposal"]
    assert prop["decision_code"] == "answer_from_policy"
    ids = [c["page_id"] for c in prop["citations"]]
    assert ids[0] == "WF-03" and "KA-12" in ids
    lines = prop["answer_text"].splitlines()
    assert lines[: len(steps_of("WF-03"))] == [f"{i}. {s}" for i, s in enumerate(steps_of("WF-03"), 1)]
    assert lines[-1].startswith("You will need:") and "new address" in lines[-1]
    assert case["guidance"]["mode"] == "info" and {s["status"] for s in case["guidance"]["steps"]} == {"info"}


def test_howto_does_not_let_a_model_reword_the_steps(client, monkeypatch):
    from caregrid.reasoning import propose as P

    calls = []
    real = P.complete_json_tiered
    monkeypatch.setattr(P, "complete_json_tiered", lambda *a, **k: calls.append(a) or real(*a, **k))
    case = post_request(client, "asha", HOWTO)
    assert not calls and case["proposal"]["answer_text"].startswith("1. " + steps_of("WF-03")[0])


@pytest.mark.parametrize("text", [
    "How do I reset my portal password?",                                  # the linked policies disagree: the normal pipeline handles it
    "How do I change the address for NPI 1234567890?",                     # concrete data: a real request
    "How do I change the address to 14 Lake Road?",
    "What are the steps to file a complaint?",                             # sensitive
    "Please change the address of Dr. Rao",                                # not a how-to
])
def test_questions_that_are_not_clean_howtos_keep_their_own_flow(client, text):
    case = post_request(client, "asha", text)
    assert case["guidance"] is None or case["guidance"]["mode"] != "info"


def test_is_howto_needs_a_process_question_without_data():
    assert is_howto("How do I change a provider's billing address?") and is_howto("What are the steps to update a provider name?")
    assert not is_howto("How do I change the address for [NPI]?") and not is_howto("how do I update 2026-11-01 address")
    assert not is_howto("Change my address") and not is_howto("")


def test_eval_has_three_howto_rows_with_decision_and_citation():
    rows = [r for r in csv.DictReader(open(Path(__file__).resolve().parent.parent / "eval" / "requests_eval.csv", encoding="utf-8"))
            if r.get("expected_decision")]
    assert len(rows) == 3 and all(r["expected_decision"] == "answer_from_policy" and r["expected_cite"].startswith("WF-") for r in rows)
