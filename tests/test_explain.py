"""Case summary (item 2): the summary card text and the one-line list column, through check_output for the viewer."""
import re

from tests.test_api import AMOUNTS, H, S1, S2, S3, client, post_request  # noqa: F401  (client is a fixture)


def test_summary_card_has_three_plain_sentences(client):
    for who, text in (("asha", S1), ("asha", S2), ("asha", S3)):
        case = post_request(client, who, text)
        sents = [s for s in re.split(r"(?<=[.!?])\s+", case["summary"]) if s]
        assert 2 <= len(sents) <= 3, case["summary"]
        assert "{" not in case["summary"] and "Request type:" not in case["summary"]


def test_summary_says_what_was_asked_checked_and_next(client):
    s2 = post_request(client, "asha", S2)["summary"]
    assert "address change" in s2 and "details still needed" in s2 and "waiting for the requester" in s2
    s1 = post_request(client, "asha", S1)["summary"]
    assert "KA-" in s1 or "checked" in s1


def test_summary_for_a_restricted_viewer_carries_no_amount(client):
    for who in ("asha", "meera", "arjun", "rahul"):
        body = client.get("/api/cases/CASE-1024", headers=H[who]).json()
        assert body["summary"] and (who == "rahul" or not any(a in body["summary"] for a in AMOUNTS))
        rows = client.get("/api/cases", headers=H[who]).json()
        assert all("summary" in r for r in rows)
        if who != "rahul":
            assert not any(a in r["summary"] for r in rows for a in AMOUNTS)


def test_list_summary_is_one_line_and_bounded(client):
    for who in ("asha", "rahul"):
        for r in client.get("/api/cases", headers=H[who]).json():
            assert "\n" not in r["summary"] and len(r["summary"]) <= 220
    for q in client.get("/api/metrics", headers=H["rahul"]).json()["queue"]:
        assert q["summary"]
