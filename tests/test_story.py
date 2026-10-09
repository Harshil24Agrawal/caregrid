"""The case as a story (problem, checks, decision, next steps), the list fields and the demo guide: built by code, per viewer."""
import re

from caregrid import demo
from tests.test_api import AMOUNTS, H, S2, client, post_request  # noqa: F401


def story(client, who, cid):
    return client.get(f"/api/cases/{cid}", headers=H[who]).json()["story"]


def test_problem_checks_decision_for_the_demo_case(client):
    s = story(client, "rahul", "CASE-1024")
    assert s["problem"] == "Asha asked to order an oxygen concentrator (E1390) for a member. Cost is ₹62,500, above the ₹50,000 limit."
    assert 3 <= len(s["checks"]) <= 6 and {c["status"] for c in s["checks"]} <= {"ok", "bad", "warn"}
    by = {c["label"]: c for c in s["checks"]}
    assert by["Policy found"]["text"].startswith("KA-40 v1") and by["Required details"]["status"] == "ok"
    assert s["decision"]["text"] == "Send to Senior Operations Review: a senior reviewer must approve."
    assert s["decision"]["confidence"]["band"] == "high" and s["decision"]["approver_role"] == "senior_reviewer"
    assert "[" not in s["problem"] and not re.search(r"M\d{8}", s["problem"])             # masked tokens are never quoted


def test_amounts_are_hidden_from_roles_that_may_not_see_them(client):
    s = story(client, "asha", "CASE-1024")
    assert not any(a in str(s) for a in AMOUNTS) and "[amount hidden]" in s["problem"]


def test_next_steps_differ_by_role(client):
    rahul = story(client, "rahul", "CASE-1024")["next_steps"]
    asha = story(client, "asha", "CASE-1024")["next_steps"]
    neha = story(client, "neha", "CASE-1024")["next_steps"]
    assert rahul[0] == "You: approve or reject in the Decide panel."
    assert asha[0].startswith("You: nothing to do.") and neha[0] == "You: nothing to do. Waiting for a senior reviewer."
    assert any(x.startswith("On approval: Released the equipment request to Senior Operations Review") and "saved as a precedent" in x for x in rahul)
    assert any(x.startswith("Then Senior Operations Review follows WF-09:") for x in rahul) and any(x.startswith("On rejection:") for x in rahul)


def test_needs_info_lists_exactly_what_to_ask_for(client):
    case = post_request(client, "asha", S2)
    mine = story(client, "asha", case["id"])["next_steps"]
    assert mine[0].startswith("You: send everything below in one reply.") and "(1)" in mine[0] and "(3)" in mine[0]
    other = story(client, "rahul", case["id"])["next_steps"]
    assert other[0].startswith("You: nothing to do. Waiting for Asha to send:") and other[1].startswith("Ask for exactly this:")
    checks = {c["label"]: c for c in story(client, "asha", case["id"])["checks"]}
    assert checks["Required details"]["status"] == "bad" and "missing" in checks["Required details"]["text"]


def test_refused_and_answered_cases(client):
    refused = post_request(client, "asha", demo.S4A)
    s = story(client, "rahul", refused["id"])
    assert "medical question" in s["problem"] and any(c["label"] == "Flags" and c["status"] == "bad" for c in s["checks"])
    assert any(x.startswith("Where it went: Clinical Review") and "no advice is given" in x for x in s["next_steps"])
    answered = post_request(client, "asha", demo.S1)
    a = story(client, "asha", answered["id"])
    assert a["decision"]["text"].startswith("Answered automatically from KA-02 v1") and a["next_steps"] == ["You: nothing to do. The answer was sent."]


def test_list_rows_carry_problem_bucket_and_next_step(client):
    rows = {r["id"]: r for r in client.get("/api/cases", headers=H["rahul"]).json()}
    c = rows["CASE-1024"]
    assert c["problem"] == "Order an oxygen concentrator (E1390)" and c["bucket"] == "action" and c["next_short"] == "Approve or reject"
    neha = {r["id"]: r for r in client.get("/api/cases", headers=H["neha"]).json()}["CASE-1024"]
    assert neha["bucket"] == "waiting" and neha["next_short"] == "Waiting for a senior reviewer"
    assert {r["bucket"] for r in rows.values()} <= {"action", "waiting", "done"} and any(r["bucket"] == "done" for r in rows.values())


def test_demo_guide_uses_the_test_texts_and_hands_out_a_health_id_only_to_reset_roles(client, monkeypatch):
    g = client.get("/api/demo/guide", headers=H["asha"]).json()
    assert [c["key"] for c in g["cards"]] == ["S1", "HOWTO", "S2", "S5", "S6", "S7", "S8", "S4"] and g["can_reset"] is False
    texts = {b["text"] for c in g["cards"] for b in c["buttons"] if b["kind"] == "fill"}
    assert {demo.S1, demo.S2, demo.S3, demo.S4A, demo.S4B, demo.S6A, demo.S6B} <= texts
    s8 = next(c for c in g["cards"] if c["key"] == "S8")["buttons"][0]
    assert s8["text"] is None and s8["act_as"] == "U4"
    r = client.get("/api/demo/guide", headers=H["rahul"]).json()
    assert r["can_reset"] is True and re.search(r"Patient CG-\d{4}-\d{4}-\d{4}\.$", next(c for c in r["cards"] if c["key"] == "S8")["buttons"][0]["text"])
    monkeypatch.setenv("DEMO_MODE", "0")
    assert client.get("/api/demo/guide", headers=H["rahul"]).status_code == 404
