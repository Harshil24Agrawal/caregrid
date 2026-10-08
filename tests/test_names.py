import pytest

from caregrid.ingest.anonymize import Gazetteer, anonymize, build_gazetteer
from caregrid.ingest.compile import compile_brain
from caregrid.ingest.generate import generate
from caregrid.ingest.names import AMBIGUOUS, STOP_STATIC, first_names
from caregrid.ingest.pagefmt import read_page_file
from caregrid.reasoning.guards import check_input, check_output
from caregrid.models import Role, User

ASHA = User(id="U1", name="Asha", role=Role.OPS_EMPLOYEE, team="TEAM-OPS-TRIAGE")


def mask(text, gaz=None):
    return anonymize(text, gaz or Gazetteer())[0]


def test_first_name_pool_is_faker_plus_supplement():
    pool = first_names()
    assert len(pool) > 1000 and {"anita", "sara", "priya", "vikram", "kiran", "asha", "rahul"} <= pool


@pytest.mark.parametrize("text,want", [
    ("Anita Rao wants to change her address", "[PERSON_1] wants to change her address"),
    ("Sara Khan called", "[PERSON_1] called"),
    ("assign to Vikram", "assign to [PERSON_1]"),
    ("Vikram's queue is full", "[PERSON_1]'s queue is full"),
    ("Neha Sharma and Rahul Verma", "[PERSON_1] and [PERSON_2]"),
    ("Please contact Ramesh Iyer about the W-9", "Please contact [PERSON_1] about the W-9"),
    ("Mark Johnson emailed", "[PERSON_1] emailed"),
    ("Kiran approved it", "[PERSON_1] approved it"),
])
def test_cueless_names_are_masked(text, want):
    assert mask(text) == want


@pytest.mark.parametrize("text", [
    "Provider Enrollment", "Senior Operations Review", "Claims Operations", "Lake Road", "Clinical Review", "Utilization Management",
    "IT Service Desk", "Compliance & Privacy", "Operations Triage", "Provider Enrollment handles name changes",
    "Grace period applies", "Please ask Mark", "relocating to Austin", "Monday Tuesday", "Retroactive dates need Enrollment Manager approval",
])
def test_business_terms_and_ambiguous_words_are_unchanged(text):
    assert mask(text) == text


def test_same_person_gets_same_token_and_first_name_reuses_full_name_token():
    out = mask("Sara Khan called. Later Sara said hi. Vikram's queue; Sara Khan again.")
    assert out == "[PERSON_1] called. Later [PERSON_1] said hi. [PERSON_2]'s queue; [PERSON_1] again."


def test_standalone_name_needs_a_cue():
    assert mask("Sara") == "Sara" and mask("Anita is on leave") == "[PERSON_1] is on leave"
    assert mask("Hello Anita") == "Hello Anita"          # no verb/possessive/cue word: left alone (documented limitation)


def test_pii_types_report_person():
    assert anonymize("Sara Khan called", Gazetteer()) == ("[PERSON_1] called", ["PERSON"])


def test_does_not_touch_masked_tokens_ids_or_dates():
    s = "[PERSON_1] NPI [NPI] CASE-1024 KA-12 INV-1024 E1390 2026-11-01 W-9 \u20b962,500"
    assert mask(s) == s


def test_check_input_masks_free_text_names():
    g = check_input("Anita Rao wants to change her address. Assign to Vikram.", ASHA)
    assert "Anita" not in g.masked_text and "Rao" not in g.masked_text and "Vikram" not in g.masked_text
    assert "PERSON" in g.pii_types_found


def test_check_output_keeps_staff_first_names():
    text = "Routed to expert review (Kiran) and Vikram will follow up."
    assert check_output(text, ASHA) == (True, text, [])


def test_authored_pages_are_unchanged_by_masking(tmp_path):
    generate(tmp_path / "data", eval_dir=tmp_path / "eval")
    compile_brain(tmp_path / "data", tmp_path / "brain")
    gaz = build_gazetteer(tmp_path / "data")
    assert {"provider", "enrollment", "operations", "triage"} <= gaz.stop_terms | STOP_STATIC
    checked = 0
    for sub in ("policy", "workflow", "team", "field", "runbook", "regulatory"):
        for f in (tmp_path / "brain" / sub).glob("*.md"):
            body = read_page_file(f)[1]
            assert anonymize(body, gaz)[0] == body, f.name
            checked += 1
    assert checked == 21 + 8 + 8 + 15 + 4 + 2


def test_precedent_summaries_have_no_known_names_after_compile(tmp_path):
    import json

    generate(tmp_path / "data", eval_dir=tmp_path / "eval")
    compile_brain(tmp_path / "data", tmp_path / "brain")
    prof = json.loads((tmp_path / "data" / "profiles.json").read_text(encoding="utf-8"))
    names = [p["name"] for p in prof["providers"] + prof["members"]]
    # even with an EMPTY gazetteer the cue-less pass must catch the generated names in raw historical text
    import csv
    for row in csv.DictReader(open(tmp_path / "data" / "historical_cases.csv", encoding="utf-8", newline="")):
        masked = anonymize(row["raw_text"], Gazetteer())[0]
        for n in names:
            first, last = n.split()[0], n.split()[-1]
            if n in row["raw_text"]:
                assert n not in masked, (row["id"], n)
