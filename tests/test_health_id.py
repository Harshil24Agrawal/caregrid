"""Item 5: CareGrid Health ID (synthetic, ABHA-style): Verhoeff checksum, guard, masking, patient link, RBAC-filtered record, reveal, audit."""
import json
import random
import re

import pytest

from caregrid import config, health_id
from caregrid.ingest.leakscan import detect_pii, leak_scan
from caregrid.models import Role, User
from caregrid.reasoning.guards import check_input
from caregrid.workflow import patients
from tests.test_api import H, client, post_request  # noqa: F401

ASHA = User(id="U1", name="Asha", role=Role.OPS_EMPLOYEE)


def members():
    return json.loads((config.DATA_DIR / "profiles.json").read_text(encoding="utf-8"))["members"]


def with_wrong_digit(hid):
    return hid[:-1] + str((int(hid[-1]) + 1) % 10)


# ------------------------------------------------------------------ the number itself
def test_verhoeff_known_vectors_and_roundtrip():
    assert health_id.verhoeff_digit("236") == 3 and health_id.verhoeff_valid("2363") and not health_id.verhoeff_valid("2364")
    rng = random.Random(5)
    for _ in range(200):
        payload = "".join(str(rng.randrange(10)) for _ in range(11))
        assert health_id.verhoeff_valid(payload + str(health_id.verhoeff_digit(payload)))


def test_generated_ids_are_well_formed_valid_unique_and_seeded():
    ids = [health_id.generate(random.Random(i)) for i in range(50)]
    assert all(re.fullmatch(r"CG-\d{4}-\d{4}-\d{4}", i) and health_id.is_valid(i) for i in ids) and len(set(ids)) == 50
    assert health_id.generate(random.Random(9)) == health_id.generate(random.Random(9))
    for m in members():
        assert health_id.is_valid(m["health_id"])
    assert len({m["health_id"] for m in members()}) == len(members())


def test_the_id_is_not_derived_from_any_personal_attribute():
    for m in members():
        digits = m["health_id"].replace("CG-", "").replace("-", "")
        for attr in (m["member_id"], m["phone"], m["dob"]):
            attr_digits = re.sub(r"\D", "", attr)
            assert len(attr_digits) < 6 or (attr_digits not in digits and digits[:11] not in attr_digits)
    assert list(health_id.generate.__annotations__) == ["rng", "return"]            # the only input is the random generator


def test_masked_display_shows_the_last_four_digits_only():
    m = members()[0]["health_id"]
    assert health_id.masked(m) == "CG-XXXX-XXXX-" + m[-4:] and m[3:7] not in health_id.masked(m)


@pytest.mark.parametrize("variant", ["{id}", "{id_lower}", "{id_spaced}", "{id_plain}"])
def test_every_typing_of_a_valid_id_is_recognised_and_masked(variant):
    m = members()[0]
    hid = m["health_id"]
    typed = variant.format(id=hid, id_lower=hid.lower(), id_spaced=hid.replace("-", " "), id_plain=hid.replace("-", ""))
    g = check_input(f"Patient {typed} needs the form.", ASHA)
    assert g.validated_fields["health_id"] == "valid" and "[HEALTH_ID]" in g.masked_text and "HEALTH_ID" in g.pii_types_found
    assert g.patient_key == m["profile_key"] and not re.search(r"\d{4}", g.masked_text)


def test_guard_flags_a_wrong_checksum_and_a_wrong_shape_without_linking():
    hid = members()[0]["health_id"]
    g = check_input(f"Patient {with_wrong_digit(hid)} please.", ASHA)
    assert g.validated_fields["health_id"] == "invalid: checksum" and g.patient_key is None and "[HEALTH_ID]" in g.masked_text
    g = check_input("Patient CG-1234-56 please.", ASHA)
    assert g.validated_fields["health_id"] == "invalid: format" and g.patient_key is None and "[HEALTH_ID]" in g.masked_text


def test_a_valid_but_unknown_id_says_nothing_and_links_nothing():
    payload = "12345678901"
    unknown = health_id.format_id(payload + str(health_id.verhoeff_digit(payload)))
    assert health_id.is_valid(unknown) and health_id.member_by_id(unknown, config.DATA_DIR) is None
    g = check_input(f"Patient {unknown} please.", ASHA)
    assert g.validated_fields["health_id"] == "valid" and g.patient_key is None


def test_leak_scan_treats_a_health_id_as_an_identifier(tmp_path):
    hid = members()[0]["health_id"]
    assert "HEALTH_ID" in detect_pii(f"seen {hid} in a page")
    (tmp_path / "policy").mkdir()
    (tmp_path / "policy" / "KA-99@v1.md").write_text(f"---\nid: KA-99\n---\nthe patient {hid}\n", encoding="utf-8")
    assert any("Health ID" in f.message for f in leak_scan(tmp_path))
    assert "HEALTH_ID" not in detect_pii("Patient CG-XXXX-XXXX-3588 (masked)")


# ------------------------------------------------------------------ through the API
def stored_blob():
    from caregrid import api

    store = api.get_store()
    return " ".join(c.model_dump_json() for c in store.list_cases()) + " ".join(e.model_dump_json() for e in store.list_audit())


def test_s8_valid_id_links_the_case_and_nothing_stores_the_id(client):
    hid = members()[0]["health_id"]
    case = post_request(client, "asha", f"What supporting documents are accepted for provider record changes? Patient {hid}.")
    assert case["patient"] == {"ref": None, "masked_id": health_id.masked(hid)} and "[HEALTH_ID]" in case["masked_text"]      # Asha: what she typed, masked
    assert "HEALTH_ID" in case["pii_types"] and hid not in str(case)
    blob = stored_blob()
    assert hid not in blob and hid.replace("-", "") not in blob
    rahul = client.get("/api/patients/PRF-2001", headers=H["rahul"]).json()
    assert case["id"] in {t["case_id"] for t in rahul["timeline"]}
    assert case["id"] in {t["case_id"] for t in client.post("/api/patients/lookup", headers=H["asha"], json={"health_id": hid}).json()["timeline"]}


def test_s8_wrong_checksum_asks_to_recheck_and_is_not_linked(client):
    hid = members()[0]["health_id"]
    bad = with_wrong_digit(hid)
    case = post_request(client, "asha", f"What supporting documents are accepted for provider record changes? Patient {bad}.")
    assert case["state"] == "needs_info" and case["missing"]["invalid"] == ["health_id"] and case["patient"]["ref"] is None
    q = " ".join(case["proposal"]["questions_for_requester"])
    assert "Health ID" in q and "check digit" in q and bad not in q
    assert case["id"] not in {t["case_id"] for t in client.get("/api/patients/PRF-2001", headers=H["rahul"]).json()["timeline"]}
    cid = case["id"]
    prov = next(e for e in client.get(f"/api/cases/{cid}", headers=H["rahul"]).json()["provenance"] if "Health ID" in e["claim"])
    assert prov["source_id"] == "FIELD-HEALTH-ID" and prov["location"] == "second_brain/field/FIELD-HEALTH-ID.md"


def test_checksum_422_and_one_404_for_unknown_and_unauthorized(client):
    hid = members()[0]["health_id"]
    assert client.post("/api/patients/lookup", headers=H["rahul"], json={"health_id": with_wrong_digit(hid)}).status_code == 422
    unknown_valid = health_id.format_id("99999999999" + str(health_id.verhoeff_digit("99999999999")))
    a = client.post("/api/patients/lookup", headers=H["rahul"], json={"health_id": unknown_valid})
    b = client.post("/api/patients/lookup", headers=H["kiran"], json={"health_id": hid})             # exists, but Kiran has no case on this patient
    c = client.get("/api/patients/PRF-2999", headers=H["rahul"])
    d = client.post("/api/patients/lookup", headers=H["meera"], json={"health_id": hid})             # knowledge owner: never
    assert {a.status_code, b.status_code, c.status_code, d.status_code} == {404} and a.json() == b.json() == c.json() == d.json()


def test_record_never_carries_personal_details_for_any_role(client):
    m = members()[0]
    for who in ("asha", "neha", "rahul", "arjun"):
        r = client.get("/api/patients/PRF-2001", headers=H[who])
        assert r.status_code == 200
        for secret in (m["name"], m["phone"], m["dob"], m["email"], m["health_id"], m["member_id"]):
            assert secret not in r.text, (who, secret)
        assert r.json()["personal"]["hidden"] is True and r.json()["masked_id"].startswith("CG-XXXX-XXXX-")


def test_auditor_sees_the_access_log_and_no_timeline(client):
    client.get("/api/patients/PRF-2001", headers=H["rahul"])
    r = client.get("/api/patients/PRF-2001", headers=H["arjun"]).json()
    assert r["timeline"] == [] and r["access_log"] and {e["event"] for e in r["access_log"]} == {"record_viewed"}
    assert any(e["actor_id"] == "U4" for e in r["access_log"])
    assert client.get("/api/patients/PRF-2001", headers=H["rahul"]).json()["access_log"] is None


def test_reveal_needs_a_senior_reviewer_a_linked_case_and_a_reason(client):
    m = members()[0]
    path = "/api/patients/PRF-2001/reveal"
    body = {"case_id": "CASE-1024", "reason": "Verify identity before approving the equipment request now."}
    assert client.post(path, headers=H["neha"], json=body).status_code == 403
    assert client.post(path, headers=H["asha"], json=body).status_code == 403
    assert client.post(path, headers=H["rahul"], json={**body, "reason": "short"}).status_code == 422
    assert client.post(path, headers=H["rahul"], json={**body, "case_id": "REQ-0001"}).status_code == 422
    r = client.post(path, headers=H["rahul"], json=body)
    assert r.status_code == 200 and r.json()["name"] == m["name"] and r.json()["stored"] is False
    log = client.get("/api/patients/PRF-2001", headers=H["arjun"]).json()["access_log"]
    assert any(e["event"] == "record_revealed" and e["case_id"] == "CASE-1024" for e in log)
    blob = stored_blob()
    assert m["name"] not in blob and m["phone"] not in blob and m["dob"] not in blob


def test_reveal_reason_is_masked_before_it_is_audited(client):
    body = {"case_id": "CASE-1024", "reason": "Caller gave phone 9876543210 and email x.y@clinic.example to confirm."}
    assert client.post("/api/patients/PRF-2001/reveal", headers=H["rahul"], json=body).status_code == 200
    blob = stored_blob()
    assert "9876543210" not in blob and "x.y@clinic" not in blob


def test_comms_never_carry_the_health_id(client):
    hid = members()[0]["health_id"]
    case = post_request(client, "asha", f"Provider NPI 1234567890 legally changed name from Priya Nair to Priya Menon, W-9 attached. Patient {hid}.")
    assert case["state"] == "in_review"
    r = client.post(f"/api/cases/{case['id']}/decision", headers=H["vikram"],
                    json={"action": "approve", "channels": ["email", "whatsapp"], "contact_email": "desk@clinic.example", "contact_phone": "+91 98100 12345"})
    assert r.status_code == 200, r.text
    comms = client.get("/api/comms", headers=H["rahul"]).json()
    mine = [c for c in comms if c["case_id"] == case["id"]]
    assert len(mine) == 2
    text = json.dumps(mine)
    assert hid not in text and hid.replace("-", "") not in text and "HEALTH_ID" not in text and not re.search(r"CG-\d", text)


def test_case_detail_links_to_the_patient_only_for_viewers_who_may_open_it(client):
    assert client.get("/api/cases/CASE-1024", headers=H["rahul"]).json()["patient"]["ref"] == "PRF-2001"
    assert client.get("/api/cases/CASE-1024", headers=H["asha"]).json()["patient"] is None          # nothing was typed in that seeded case
    assert client.get("/api/cases/CASE-1024", headers=H["meera"]).json()["patient"] is None


def test_demo_samples_are_a_valid_and_a_wrong_id_in_demo_mode_only(client, monkeypatch):
    for who in ("rahul", "neha"):
        r = client.get("/api/demo/samples", headers=H[who]).json()
        assert health_id.is_valid(r["health_id_valid"]) and not health_id.is_valid(r["health_id_wrong_checksum"])
    for who in ("asha", "vikram", "meera", "arjun", "kiran"):
        assert client.get("/api/demo/samples", headers=H[who]).status_code == 404, who
    monkeypatch.setenv("DEMO_MODE", "0")
    assert client.get("/api/demo/samples", headers=H["rahul"]).status_code == 404


# ------------------------------------------------------------------ the role rules, without the HTTP layer
def test_team_specialist_sees_own_team_and_other_teams_only_with_consent(client):
    from caregrid import api

    store = api.get_store()
    claims = User(id="U-claims", name="C", role=Role.TEAM_SPECIALIST, team="TEAM-CLAIMS")
    compliance = User(id="U-comp", name="K", role=Role.TEAM_SPECIALIST, team="TEAM-COMPLIANCE")
    triage = User(id="U-tri", name="T", role=Role.TEAM_SPECIALIST, team="TEAM-UM")
    d = config.DATA_DIR
    own = patients.record("PRF-2002", claims, store, d, audit=False)                  # PRF-2002: no cross-team consent
    assert own and {t["team"] for t in own["timeline"]} == {"TEAM-CLAIMS"}
    shared = patients.record("PRF-2001", claims, store, d, audit=False)               # PRF-2001 consented: every team's cases
    assert shared and {t["team"] for t in shared["timeline"]} >= {"TEAM-CLAIMS", "TEAM-SENIOR-OPS", "TEAM-COMPLIANCE"}
    assert patients.record("PRF-2002", compliance, store, d, audit=False) is None     # no case of theirs on this patient
    assert patients.record("PRF-2001", triage, store, d, audit=False) is None
    assert patients.record("PRF-2003", compliance, store, d, audit=False)["timeline"]


def test_ops_employee_sees_only_the_cases_they_requested(client):
    from caregrid import api

    store = api.get_store()
    other = User(id="U9", name="Other", role=Role.OPS_EMPLOYEE)
    assert patients.record("PRF-2001", other, store, config.DATA_DIR, audit=False) is None
    mine = patients.record("PRF-2001", ASHA, store, config.DATA_DIR, audit=False)
    assert mine and all(store.get_case(t["case_id"]).requester.id == "U1" for t in mine["timeline"])


def test_seeded_cases_give_three_patients_non_empty_timelines(client):
    rows = {r["ref"]: r["cases"] for r in client.get("/api/patients", headers=H["rahul"]).json()}
    assert {"PRF-2001", "PRF-2002", "PRF-2003"} <= set(rows) and all(rows[k] >= 2 for k in ("PRF-2001", "PRF-2002", "PRF-2003"))
