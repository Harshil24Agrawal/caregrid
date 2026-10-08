"""`cli eval --file` and the pieces of `cli check` (mock only; the full check itself is run by hand because it resets the demo state)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_phase5 import LLM, NAME_A, env, world, data_dir  # noqa: E402,F401

from caregrid import check, config  # noqa: E402
from caregrid.models import Citation, PageType, State  # noqa: E402
from caregrid.scorecard import misses, write_outputs  # noqa: E402


def test_forbidden_citations_and_audit_gaps(env):
    case = env.store.get_case(env.run(NAME_A).id)
    assert check.forbidden_citations([case]) == [] and check.audit_gaps(env.store, [case]) == []
    bad = case.model_copy(deep=True)
    bad.proposal.citations.append(Citation(page_id="KA-12", version=2, page_type=PageType.POLICY, title="old"))
    bad.proposal.citations.append(Citation(page_id="KA-60", version=1, page_type=PageType.POLICY, title="draft"))
    assert check.forbidden_citations([bad]) == [f"{case.id} cites KA-12 v2", f"{case.id} cites KA-60"]
    ghost = case.model_copy(update={"id": "REQ-9999"})
    assert "REQ-9999" in check.audit_gaps(env.store, [ghost])[0]
    moved = case.model_copy(update={"state": State.CLOSED})
    assert "no state_changed into closed" in check.audit_gaps(env.store, [moved])[0]


def test_brain_hash_ignores_the_log_and_sees_page_changes(env):
    h = check.brain_hash(env.dir)
    env.brain.append_log("system", "x", "KA-01", "journal only")
    assert check.brain_hash(env.dir) == h
    page = next(env.dir.rglob("KA-01*.md"), None) or next(p for p in env.dir.rglob("*.md") if p.name not in ("log.md", "index.md"))
    page.write_text(page.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
    assert check.brain_hash(env.dir) != h


def test_cases_hash_ignores_timestamps(env):
    env.run(NAME_A)
    a = check.cases_hash(env.store)
    assert a == check.cases_hash(env.store)


def test_other_datasets_never_overwrite_the_main_scorecard(tmp_path):
    import csv

    from caregrid.scorecard import run_eval
    src = config.EVAL_DIR / "requests_eval.csv"
    rows = list(csv.DictReader(open(src, encoding="utf-8", newline="")))[:3]
    mini = tmp_path / "mini.csv"
    with open(mini, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    card = run_eval("mock", csv_path=mini)
    assert card["file"] == "mini.csv" and card["rows"] == 3
    names = sorted(p.name for p in write_outputs(card, tmp_path / "out"))
    assert names == ["scorecard.mini.mock.json", "scorecard.mini.mock.md"]


def test_misses_lists_expected_vs_got():
    row = {"id": "X-1", "type_ok": False, "route_ok": False, "expected_type": "a", "got_type": "b", "expected_route": "human", "got_route": "auto",
           "expected_team": "T1", "got_team": "T2", "expected_missing": ["f"], "got_missing": [], "missing_found": 0, "safety_row": False,
           "safety_ok": False, "expects_abstain": False, "abstained_ok": False, "expects_auto": False, "abstained": False, "error": None}
    ok = dict(row, id="X-2", type_ok=True, route_ok=True, expected_missing=[], missing_found=0)
    out = misses({"results": [row, ok]})
    assert [m["id"] for m in out] == ["X-1"] and "type expected a got b" in out[0]["why"][0]


def test_adjudication_is_applied_checked_and_reported_beside_the_blind_score(tmp_path):
    import csv

    import pytest

    from caregrid.scorecard import adjudicate, render_table, run_eval
    rows = [r for r in csv.DictReader(open(config.EVAL_DIR / "requests_eval.csv", encoding="utf-8", newline="")) if r["id"] in ("EV-01", "EV-12")]
    csv_path = tmp_path / "ds.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    adj = tmp_path / "ds_adjudication.csv"
    wrong = rows[0]["expected_type"]
    adj.write_text(f"row,field,original,adjudicated,reason\n{rows[0]['id']},expected_type,{wrong},ANY,type not scored\n", encoding="utf-8")
    card = run_eval("mock", csv_path=csv_path)
    assert card["adjudicated"]["rows_adjudicated"] == [rows[0]["id"]] and card["adjudicated"]["metrics"]["request_type_accuracy"]["den"] == 2
    text = render_table(card)
    assert "[blind (as written)]" in text and "[adjudicated:" in text
    # a stale adjudication (original does not match the dataset) is an error, never silently applied
    with pytest.raises(ValueError):
        adjudicate(rows[0], [{"row": rows[0]["id"], "field": "expected_type", "original": "nope", "adjudicated": "x", "reason": ""}])
    with pytest.raises(ValueError):
        adjudicate(rows[0], [{"row": rows[0]["id"], "field": "id", "original": rows[0]["id"], "adjudicated": "x", "reason": ""}])
