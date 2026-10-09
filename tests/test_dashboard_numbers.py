"""Dashboard numbers are the lists they link to, for every role: tile = len(view), every counted case is openable, no misleading zero in the seed."""
from caregrid import api
from caregrid.insights.dashboard import case_views
from caregrid.rbac import can_approve, can_view, visible_cases
from caregrid.seed import load_users
from caregrid import config
from tests.test_api import H, client  # noqa: F401

USERS = {"asha": "U1", "vikram": "U2", "neha": "U3", "rahul": "U4", "meera": "U5", "arjun": "U6", "kiran": "U7"}


def test_every_case_tile_equals_the_length_of_the_list_it_links_to(client):
    for who in USERS:
        d = client.get("/api/dashboard", headers=H[who]).json()
        for t in d["tiles"]:
            if t["href"].startswith("case.html?view="):
                view = t["href"].split("=")[1]
                rows = client.get(f"/api/cases?view={view}", headers=H[who]).json()
                assert len(rows) == t["count"], (who, t["key"])
                for r in rows:                                                       # nothing counted that the viewer cannot open
                    assert client.get(f"/api/cases/{r['id']}", headers=H[who]).status_code == 200, (who, r["id"])
        b = d["banner"]
        if b["href"].startswith("case.html?view="):
            assert len(client.get(f"/api/cases?view={b['href'].split('=')[1]}", headers=H[who]).json()) == b["count"], who
        assert d["queue"]["count"] == len(client.get("/api/cases?view=queue", headers=H[who]).json()), who
        assert d["visible"] == len(client.get("/api/cases", headers=H[who]).json()), who


def test_numbers_match_a_recomputation_from_the_raw_cases(client):
    store, users = api.get_store(), load_users(config.DATA_DIR)
    for who, uid in USERS.items():
        user = users[uid]
        raw = [c for c in store.list_cases() if can_view(user, c, "summary")]
        d = {t["key"]: t["count"] for t in client.get("/api/dashboard", headers=H[who]).json()["tiles"]}
        if "waiting_for_you" in d:
            assert d["waiting_for_you"] == sum(c.state.value in ("in_review", "escalated") and can_approve(user, c) for c in raw), who
            assert d["high_risk"] == sum(c.state.value in ("proposed", "needs_info", "in_review", "escalated") and c.rules is not None and c.rules.risk.value in ("high", "critical") for c in raw)
        if "needs_action" in d:
            assert d["with_reviewers"] == sum(c.state.value in ("in_review", "escalated") for c in raw)
            assert d["answered_auto"] == sum(c.routing == "auto" for c in raw)
        assert {c.id for c in case_views(user, visible_cases(user, store))["queue"]} <= {c.id for c in raw}


def test_the_seed_gives_every_role_something_to_show(client):
    for who in ("asha", "vikram", "neha", "rahul", "meera", "arjun", "kiran"):
        tiles = {t["key"]: t["count"] for t in client.get("/api/dashboard", headers=H[who]).json()["tiles"]}
        assert any(v > 0 for v in tiles.values()), who
    for who, keys in (("asha", ("needs_action", "with_reviewers", "answered_auto", "done")), ("rahul", ("waiting_for_you", "forwarded_today", "high_risk", "overdue")),
                      ("meera", ("prs", "conflicts", "gaps")), ("arjun", ("blocked", "reveals", "denials"))):
        tiles = {t["key"]: t["count"] for t in client.get("/api/dashboard", headers=H[who]).json()["tiles"]}
        assert all(tiles[k] > 0 for k in keys), (who, tiles)
    states = {c["state"] for c in client.get("/api/cases", headers=H["rahul"]).json()}
    assert {"answered", "needs_info", "proposed", "in_review", "escalated", "actioned", "notified", "rejected", "closed"} <= states


def test_trust_ladder_comes_from_the_trust_records_and_matches_the_seeded_decisions(client):
    m = client.get("/api/metrics", headers=H["rahul"]).json()
    rows = {t["request_type"]: t for t in m["trust"]}
    store = api.get_store()
    for rtype, rec in rows.items():
        t = store.get_trust(rtype)
        assert (rec["total_reviews"], rec["agreements"], rec["consecutive_agreements"]) == (t.total_reviews, t.agreements, t.consecutive_agreements)
    assert rows["provider_address_change"]["total_reviews"] == 3 and rows["provider_address_change"]["agreements"] == 2
    assert rows["provider_name_change"]["total_reviews"] == 0
