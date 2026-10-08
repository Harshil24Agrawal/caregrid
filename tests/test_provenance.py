"""Item 4: 'Why this decision' - every claim with the verified, current, viewer-visible source behind it."""
from pathlib import Path

from caregrid import api, config
from tests.test_api import AMOUNTS, H, S2, S3, client, post_request  # noqa: F401

ALLOWED_KINDS = {"policy", "workflow", "precedent", "routing_rule", "field_definition", "threshold", "guard", None}


def prov(client, who, cid):
    return client.get(f"/api/cases/{cid}", headers=H[who]).json()["provenance"]


def find(entries, text):
    return next(e for e in entries if text in e["claim"])


def test_every_claim_of_the_demo_case_has_its_source(client):
    p = prov(client, "rahul", "CASE-1024")
    assert {e["source_kind"] for e in p} <= ALLOWED_KINDS
    req = find(p, "Request type")
    assert (req["source_kind"], req["source_id"], req["version"], req["section"]) == ("workflow", "WF-09", 1, "request-type")
    assert req["location"] == "second_brain/workflow/WF-09.md"
    route = find(p, "Routed to")
    assert (route["source_kind"], route["source_id"], route["location"]) == ("routing_rule", "RR-07", "second_brain/config/routing_rules.csv#RR-07")
    risk = find(p, "Risk is HIGH")
    assert (risk["source_kind"], risk["source_id"], risk["version"], risk["section_heading"]) == ("threshold", "KA-40", 1, "Threshold")
    assert risk["location"] == "second_brain/policy/KA-40@v1.md" and "62,500" in risk["claim"]
    assert find(p, "senior reviewer decides")["source_id"] == "KA-40"                      # a threshold moved the decision up
    parts = [e for e in p if e["claim"].split(":")[0] in ("Policy evidence", "Similar past decisions", "Required details", "Request clarity", "No conflicting policies")]
    assert len(parts) == 5 and all("/" in e["claim"] for e in parts)
    assert find(p, "Similar past decisions")["source_kind"] == "precedent"
    assert any(e["claim"].startswith("Next step") for e in p)


def test_locations_are_real_files_and_never_mangled_by_the_output_guard(client):
    for e in prov(client, "rahul", "CASE-1024") + prov(client, "neha", "CASE-1024"):
        if not e["location"]:
            continue
        assert "[EMAIL]" not in e["location"] and e["location"].startswith("second_brain/") or e["location"].startswith("caregrid/")
        if e["location"].startswith("second_brain/"):
            assert (Path(config.BRAIN_DIR) / e["location"].removeprefix("second_brain/").split("#")[0]).exists(), e["location"]


def test_sources_are_verified_current_and_approved(client):
    brain = api.get_brain()
    for who, cid in (("rahul", "CASE-1024"), ("asha", "CASE-1024")):
        for e in prov(client, who, cid):
            if e["source_kind"] in ("policy", "workflow", "threshold", "field_definition") and e["source_id"]:
                page = brain.get(e["source_id"])
                assert page is not None and page.version == e["version"] and page.status.value == "approved", e
            if e["source_kind"] == "precedent":
                assert brain.get(e["source_id"]) is not None            # ACTIVE only


def test_missing_and_invalid_fields_point_at_their_definitions(client):
    case = post_request(client, "asha", S2)
    p = case["provenance"]
    npi = next(e for e in p if "NPI" in e["claim"] and ("missing" in e["claim"] or "invalid" in e["claim"]))
    assert npi["source_kind"] == "field_definition" and npi["source_id"].startswith("FIELD-") and npi["location"].startswith("second_brain/field/")


def test_a_conflict_cites_both_policies(client):
    cid = post_request(client, "asha", S3)["id"]
    p = prov(client, "kiran", cid)
    ids = {e["source_id"] for e in p if "(conflict)" in e["claim"]}
    assert ids == {"KA-31", "KA-32"}
    assert all(e["section"] == "rule" for e in p if "(conflict)" in e["claim"])


def test_restricted_viewers_get_restricted_entries_not_the_breakdown(client):
    p = prov(client, "asha", "CASE-1024")
    parts = [e for e in p if e["restricted"]]
    assert len(parts) == 5 and all(e["source_id"] is None and e["location"] is None for e in parts)
    blob = str(p)
    assert not any(a in blob for a in AMOUNTS)
    assert find(p, "Risk is HIGH")["claim"] == "Risk is HIGH"                              # no threshold figure for her


def test_knowledge_page_exposes_sections_for_deep_links(client):
    page = client.get("/api/pages/KA-40", headers=H["rahul"]).json()
    slugs = {s["slug"]: s for s in page["sections"]}
    assert "threshold" in slugs and "50,000" in slugs["threshold"]["text"] and "policy-text" in slugs
    wf = client.get("/api/pages/WF-09", headers=H["asha"]).json()
    assert {"request-type", "required-fields", "steps", "risk", "routing"} <= {s["slug"] for s in wf["sections"]}


def test_assistant_reply_ends_with_a_sources_line(client):
    r = client.post("/api/cases/CASE-1024/assistant", headers=H["rahul"], json={"question": "Why is this case flagged?"}).json()
    last = r["text"].splitlines()[-1]
    assert last.startswith("Sources: ") and "KA-40 v1 · DME equipment requests · § Threshold · second_brain/policy/KA-40@v1.md" in last
    assert [s["source_id"] for s in r["sources"]] and "[EMAIL]" not in r["text"]
    denied = client.post("/api/cases/CASE-1024/assistant", headers=H["asha"], json={"question": "What is the amount?"}).json()
    assert denied["restricted"] and "Sources:" not in denied["text"] and denied["sources"] == []


def test_a_source_that_is_no_longer_current_is_not_offered(client):
    case = post_request(client, "asha", "What supporting documents are accepted for provider record changes?")
    cited = [c["page_id"] for c in case["proposal"]["citations"] if c["page_type"] == "policy"]
    assert cited and any(e["source_id"] in cited for e in case["provenance"])
    with api._lock:
        api.get_brain().retire_page(cited[0])                                          # now expired: no longer the approved current version
    again = client.get(f"/api/cases/{case['id']}", headers=H["asha"]).json()["provenance"]
    assert not any(e["source_id"] == cited[0] and e["source_kind"] in ("policy", "threshold") for e in again)
