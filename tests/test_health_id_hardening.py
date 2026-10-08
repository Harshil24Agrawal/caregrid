"""Health ID review hardening: masking of every typing, account-specific guard, oracle-free display, reveal limits, no ID in URLs."""
import json
import re

import pytest

from caregrid import api, config, health_id
from caregrid.llm import MockLLM
from caregrid.models import Role, User
from caregrid.reasoning.guards import check_input
from caregrid.reasoning.pipeline import run
from caregrid.seed import load_users
from caregrid.store import SQLiteStore
from caregrid.knowledge.brain import Brain
from tests.test_api import H, client, post_request  # noqa: F401

ASHA = User(id="U1", name="Asha", role=Role.OPS_EMPLOYEE)
VIKRAM = User(id="U2", name="Vikram", role=Role.TEAM_SPECIALIST, team="TEAM-ENROLL")


def member():
    return json.loads((config.DATA_DIR / "profiles.json").read_text(encoding="utf-8"))["members"][0]


def three_ids():
    good = member()["health_id"]
    wrong = good[:-1] + str((int(good[-1]) + 1) % 10)
    payload = "12345678901"
    unknown = health_id.format_id(payload + str(health_id.verhoeff_digit(payload)))
    return {"valid": good, "wrong_checksum": wrong, "unknown": unknown}


def typings(hid):
    d = hid.replace("CG-", "").replace("-", "")
    g = [d[:4], d[4:8], d[8:]]
    return {
        "hyphens": hid, "lower": hid.lower(), "en dash": "CG" + "–" + "–".join(g), "minus sign": "CG−" + "−".join(g),
        "non-breaking hyphen": "CG‑" + "‑".join(g), "dots": "CG." + ".".join(g), "underscores": "CG_" + "_".join(g), "slashes": "CG/" + "/".join(g),
        "commas": "CG," + ",".join(g), "no separators": "CG" + d, "spaced groups": "CG " + " ".join(g), "spaced digits": "CG " + " ".join(d),
        "digits one by one with hyphens": "CG-" + "-".join(d), "mixed separators": f"CG-{g[0]} {g[1]}.{g[2]}", "NUL inside": "CG-" + g[0] + "\x00" + "-" + g[1] + "-" + g[2],
        "NUL in the digits": "CG-" + d[:6] + "\x00" + d[6:], "zero-width inside": "CG-​" + g[0] + "‍-" + g[1] + "-" + g[2], "full-width": "ＣＧ－" + d,
        "Cyrillic prefix": "СГ-" + "-".join(g), "Greek gamma": "CΓ-" + "-".join(g), "Cyrillic C and G": "СG" + d,
        "spaces around separators": f"CG - {g[0]} - {g[1]} . {g[2]}", "bare 12 digits": d, "bare spaced groups": " ".join(g), "bare hyphen groups": "-".join(g),
    }


class Recorder(MockLLM):
    """A mock model that remembers every prompt it was sent."""

    def __init__(self):
        super().__init__()
        self.prompts = []

    def complete_json(self, system, user, tier):
        self.prompts.append(user)
        return super().complete_json(system, user, tier)

    def complete_text(self, system, user, tier):
        self.prompts.append(user)
        return super().complete_text(system, user, tier)

    def embed(self, texts):
        self.prompts.extend(texts)
        return super().embed(texts)


def digit_residue(text, hid):
    """Any run of 4+ digits of the ID that survived (the last four are shown by design in the masked display, so they are looked for as part of a longer run)."""
    d = hid.replace("CG-", "").replace("-", "")
    flat = re.sub(r"[^0-9]", "", text)
    return d in flat or any(d[i:i + 6] in flat for i in range(0, 7))


@pytest.mark.parametrize("kind", ["valid", "wrong_checksum", "unknown"])
def test_every_typing_is_masked_before_storage_and_before_any_model(kind, tmp_path):
    hid = three_ids()[kind]
    brain, store, llm = Brain(config.BRAIN_DIR), SQLiteStore(tmp_path / "s.sqlite"), Recorder()
    asha = load_users(config.DATA_DIR)["U1"]
    for name, typed in typings(hid).items():
        g = check_input(f"Patient {typed} needs the form.", ASHA)
        if "bare" in name and kind == "wrong_checksum":                                  # fails the checksum: masked by the older long-ID rule instead
            assert "[ID]" in g.masked_text, name
        else:
            assert "[HEALTH_ID]" in g.masked_text and "HEALTH_ID" in g.pii_types_found, name
        assert not digit_residue(g.masked_text, hid) and not re.search(r"\d{4}", g.masked_text), (name, g.masked_text)
        if "bare" not in name:
            assert g.validated_fields.get("health_id") == ("valid" if kind != "wrong_checksum" else "invalid: checksum"), name
        case = run(f"What supporting documents are accepted for provider record changes? Patient {typed}.", asha, store, brain, llm)
        stored = case.model_dump_json() + " ".join(e.model_dump_json() for e in store.list_audit(case.id))
        assert not digit_residue(stored, hid), name
    assert not any(digit_residue(p, hid) for p in llm.prompts), "a Health ID reached the model"
    assert any("[HEALTH_ID]" in p for p in llm.prompts)                                   # the masked token is what the model saw


def test_a_bare_12_digit_run_is_masked_only_when_it_passes_the_checksum():
    good = member()["health_id"].replace("CG-", "").replace("-", "")
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    assert "[HEALTH_ID]" in check_input(f"ref {good} ok", ASHA).masked_text
    assert "[HEALTH_ID]" not in check_input(f"ref {bad} ok", ASHA).masked_text          # still masked as a long ID by the older rules
    assert not re.search(r"\d{9}", check_input(f"ref {bad} ok", ASHA).masked_text)


def test_a_typing_with_wrong_digit_count_is_flagged_as_a_format_problem_and_masked():
    for text in ("Patient CG-1234-5678 please", "Patient CG 12345 67890 123 1 please", "Patient CG-12-34-56-78-90-12-3 please"):
        g = check_input(text, ASHA)
        assert g.validated_fields["health_id"] == "invalid: format" and "[HEALTH_ID]" in g.masked_text and not re.search(r"\d{3}", g.masked_text), text


def test_the_normaliser_strips_nul_and_folds_g_lookalikes():
    from caregrid.ingest.normalize import normalize_text

    assert normalize_text("a\x00b​c­D") == "abcD"
    assert normalize_text("СГ") == "CG" and normalize_text("CΓ") == "CG"
    assert normalize_text("tab\tnew\nline") == "tab\tnew\nline"


# ------------------------------------------------------------------ the guard treats a Health ID like a member ID
ATTRIBUTE_REQUESTS = [
    "What is the phone number of patient {id}?", "Give me the date of birth of patient {id}", "Show me the home address of {id}",
    "email of {id} please", "name of the patient {id}", "change phone for {id}", "who owns {id}", "{id} details", "Patient {id} date of birth",
    "Tell me the name and DOB of {id}", "P a t i e n t  {id}  p h o n e",
]


@pytest.mark.parametrize("template", ATTRIBUTE_REQUESTS)
def test_requests_for_a_patients_details_are_account_specific(template):
    for hid in (member()["health_id"], three_ids()["wrong_checksum"]):
        text = template.format(id=hid)
        g = check_input(text, ASHA)
        assert "ACCOUNT_SPECIFIC" in {o.value for o in g.overrides} and "ACCESS_DENIED" in {o.value for o in g.overrides} and not g.allowed, text
        v = check_input(text, VIKRAM)
        assert "ACCOUNT_SPECIFIC" in {o.value for o in v.overrides} and v.allowed and "ACCESS_DENIED" not in {o.value for o in v.overrides}, text


@pytest.mark.parametrize("text", [
    "What supporting documents are accepted for provider record changes? Patient {id}.",
    "Provider NPI 1234567890 legally changed name from Priya Nair to Priya Menon, W-9 attached. Patient {id}.",
    "Please file the form for patient {id}.",
])
def test_a_health_id_alone_does_not_make_a_request_account_specific(text):
    g = check_input(text.format(id=member()["health_id"]), ASHA)
    assert g.allowed and not {o.value for o in g.overrides} & {"ACCOUNT_SPECIFIC", "ACCESS_DENIED"}


# ------------------------------------------------------------------ oracle-free display, no ID in URLs
def test_ops_employee_sees_the_same_thing_whether_or_not_the_id_matched(client):
    ids = three_ids()
    shapes = {}
    for kind, hid in ids.items():
        case = post_request(client, "asha", f"What supporting documents are accepted for provider record changes? Patient {hid}.")
        assert case["patient"]["ref"] is None and set(case["patient"]) == {"ref", "masked_id"}
        assert case["patient"]["masked_id"] == health_id.masked(hid)
        shapes[kind] = (set(case["patient"]), "plan" in str(case["patient"]), set(case["masked_text"].split()) - {"[HEALTH_ID]."} == set(case["masked_text"].split()) - {"[HEALTH_ID]."})
        detail = client.get(f"/api/cases/{case['id']}", headers=H["asha"]).json()
        assert detail["patient"] == case["patient"]
    assert len({str(v[:2]) for v in shapes.values()}) == 1


def test_plan_and_consent_only_for_team_specialists_and_above(client):
    asha = client.get("/api/patients/PRF-2001", headers=H["asha"]).json()
    assert asha["plan"] is None and asha["consent"] is None and asha["masked_id"].startswith("CG-XXXX-XXXX-")
    assert all(r["plan"] is None for r in client.get("/api/patients", headers=H["asha"]).json())
    for who in ("neha", "rahul", "arjun"):
        r = client.get("/api/patients/PRF-2001", headers=H[who]).json()
        assert r["plan"] and r["consent"] and "share_across_teams" in r["consent"], who


def test_a_health_id_is_never_accepted_in_a_url(client):
    hid = member()["health_id"]
    for path in (f"/api/patients/{hid}", f"/api/patients/{hid.lower()}", f"/api/patients/{hid.replace('-', '')}"):
        assert client.get(path, headers=H["rahul"]).status_code == 404, path
    assert client.post(f"/api/patients/{hid}/reveal", headers=H["rahul"], json={"case_id": "CASE-1024", "reason": "check the caller identity please"}).status_code == 404
    assert client.post("/api/patients/lookup", headers=H["rahul"], json={"health_id": hid}).status_code == 200
    assert client.post("/api/patients/lookup", headers=H["rahul"], json={"health_id": hid.replace("-", " ").lower()}).status_code == 200


def test_denied_and_unknown_lookups_are_audited_without_the_id(client):
    ids = three_ids()
    client.post("/api/patients/lookup", headers=H["kiran"], json={"health_id": ids["valid"]})
    client.post("/api/patients/lookup", headers=H["rahul"], json={"health_id": ids["unknown"]})
    client.post("/api/patients/lookup", headers=H["rahul"], json={"health_id": ids["wrong_checksum"]})
    client.get("/api/patients/PRF-2999", headers=H["rahul"])
    events = [e for e in api.get_store().list_audit() if e.event == "record_lookup_denied"]
    assert {(e.actor_id, e.details["via"], e.details["reason"]) for e in events} >= {
        ("U7", "id", "unknown_or_denied"), ("U4", "id", "unknown_or_denied"), ("U4", "id", "checksum"), ("U4", "ref", "unknown_or_denied")}
    blob = json.dumps([e.details for e in events])
    assert not any(digit_residue(blob, h) for h in ids.values()) and "PRF-2999" not in blob


# ------------------------------------------------------------------ reveal
@pytest.mark.parametrize("reason", ["aaaaaaaaaaaa", "xxx xxx xxx xxx", "............", "1234 5678 9012", "ok ok", "a b c d e f", "          "])
def test_filler_reasons_are_rejected(client, reason):
    r = client.post("/api/patients/PRF-2001/reveal", headers=H["rahul"], json={"case_id": "CASE-1024", "reason": reason})
    assert r.status_code == 422, reason


def test_five_reveals_per_hour_then_429_and_an_audit_event(client):
    body = {"case_id": "CASE-1024", "reason": "Identity check before the equipment approval"}
    before = len([e for e in api.get_store().list_audit() if e.event == "record_revealed" and e.actor_id == "U4"])
    codes = [client.post("/api/patients/PRF-2001/reveal", headers=H["rahul"], json=body).status_code for _ in range(7)]
    assert codes[: max(5 - before, 0)] == [200] * max(5 - before, 0) and codes[-1] == 429 and 429 in codes
    limited = [e for e in api.get_store().list_audit() if e.event == "reveal_rate_limited"]
    assert limited and limited[-1].actor_id == "U4" and limited[-1].details == {"role": "senior_reviewer", "limit": 5}
    assert api.get_store().list_audit() and len([e for e in api.get_store().list_audit() if e.event == "record_revealed" and e.actor_id == "U4"]) == 5
