"""`cli eval` / caregrid.scorecard (mock LLM, no network)."""
import csv
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from caregrid import config, scorecard as sc  # noqa: E402
from caregrid.cli import main  # noqa: E402
from caregrid.ingest.compile import compile_brain  # noqa: E402
from caregrid.ingest.generate import generate  # noqa: E402
from caregrid.knowledge.brain import Brain  # noqa: E402
from caregrid.llm import MockLLM  # noqa: E402
from caregrid.models import Citation, PageType, Proposal, DecisionCode, Case, User, Role  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("score")
    generate(root / "data", eval_dir=root / "eval")
    compile_brain(root / "data", root / "brain")
    return root


@pytest.fixture()
def cfg(world, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", world / "data")
    monkeypatch.setattr(config, "BRAIN_DIR", world / "brain")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "must-not-exist.sqlite")
    out = tmp_path / "eval"
    out.mkdir()
    (out / "requests_eval.csv").write_bytes((world / "eval" / "requests_eval.csv").read_bytes())
    monkeypatch.setattr(config, "EVAL_DIR", out)
    return out


def tree_hash(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(f.relative_to(path).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()


# ================================================================== the full mock run
def test_full_run_on_the_mock_scores_every_metric(cfg, world):
    before = tree_hash(world / "brain")
    card = sc.run_eval("mock")
    m = card["metrics"]
    assert card["rows"] == 63 and card["provider"] == "mock" and card["models"] == {"light": "mock-light", "strong": "mock-strong"}
    for key in ("request_type_accuracy", "routing_first_time_right", "missing_field_recall", "citation_validity", "correct_abstention_rate",
                "safety_pass_rate"):
        assert m[key]["value"] == 100.0, key
        assert m[key]["num"] == m[key]["den"] > 0
    assert (m["request_type_accuracy"]["den"], m["missing_field_recall"]["den"], m["safety_pass_rate"]["den"]) == (63, 24, 23)
    assert m["correct_abstention_rate"]["den"] == 5                                             # EV-07 + EV-60..63
    assert m["false_abstention_rate"] == {"value": 0.0, "num": 0, "den": 7}
    assert set(m["safety_pass_rate"]["by_category"]) == {"clinical", "injection", "access", "sensitive", "account_specific"}
    assert m["stale_precedent_catches"] == {"caught": 10, "stale_retrieved": 10, "stale_cited": 0}
    t = m["llm_tier_share"]
    assert t["no_llm_pct"] + t["light_only_pct"] + t["light_and_strong_pct"] == pytest.approx(100.0, abs=0.2) and sum(t["counts"].values()) == 63
    assert m["errors"] == 0 and m["llm_fallbacks"] == 0 and m["pii_leaks_in_store"] == 0
    assert 0 < m["latency_ms"]["avg"] <= m["latency_ms"]["p95"] <= m["latency_ms"]["max"]
    assert tree_hash(world / "brain") == before                                                  # the eval never changes the real brain
    assert not (cfg.parent / "must-not-exist.sqlite").exists()                                   # ... nor creates a database


def test_per_row_results_are_complete_and_hold_no_request_text(cfg):
    card = sc.run_eval("mock", limit=12)
    assert card["rows"] == 12 and len(card["results"]) == 12
    need = {"id", "type_ok", "route_ok", "decision", "score", "band", "citations", "citations_valid", "safety_row", "tiers", "latency_ms", "error"}
    assert all(need <= set(r) for r in card["results"])
    blob = json.dumps(card)
    for raw in ("Ramesh", "Lake Road", "123456789", "Priya", "M12345678", "Kapoor"):
        assert raw not in blob
    assert [r["id"] for r in card["results"]][:3] == ["EV-01", "EV-02", "EV-03"]


# ================================================================== outputs
def test_cli_eval_prints_a_table_and_writes_the_scorecard_files(cfg, capsys):
    assert main(["eval"]) == 0
    out = capsys.readouterr().out
    for line in ("Request-type accuracy", "Routing first-time-right", "Missing-field recall", "Citation validity", "Correct abstention rate",
                 "Safety pass rate", "Stale-precedent catches", "LLM use: none / light only / light + strong", "Latency per request"):
        assert line in out
    assert "100.0%  (63/63)" in out
    js = json.loads((cfg / "scorecard.json").read_text(encoding="utf-8"))
    assert js["rows"] == 63 and js["metrics"]["safety_pass_rate"]["num"] == 23 and len(js["results"]) == 63
    assert js == json.loads((cfg / "scorecard.mock.json").read_text(encoding="utf-8"))
    md = (cfg / "scorecard.md").read_text(encoding="utf-8")
    assert md.startswith("# CareGrid evaluation scorecard") and "| Request-type accuracy | 100.0%  (63/63) |" in md
    assert "regression gate" in md and "Rows needing attention: none." in md
    assert (cfg / "scorecard.mock.md").read_text(encoding="utf-8") == md


def test_partial_runs_never_overwrite_the_scorecard_the_ui_reads(cfg, capsys):
    assert main(["eval"]) == 0
    full = (cfg / "scorecard.json").read_bytes()
    assert main(["eval", "--limit", "5"]) == 0
    assert (cfg / "scorecard.json").read_bytes() == full
    part = json.loads((cfg / "scorecard.mock.partial.json").read_text(encoding="utf-8"))
    assert part["rows"] == 5 and part["limit"] == 5 and (cfg / "scorecard.mock.partial.md").exists()
    assert "5/5" in capsys.readouterr().out


def test_missing_prerequisites_are_reported(cfg, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "BRAIN_DIR", tmp_path / "nope")
    assert main(["eval"]) == 1 and "reset" in capsys.readouterr().out


# ================================================================== provider handling
class Recording(MockLLM):
    def __init__(self, sleep=0.0):
        super().__init__()
        self.paced, self.sleep = [], sleep

    def pace(self, calls=2):
        self.paced.append(calls)
        time.sleep(self.sleep)


def test_env_provider_is_paced_before_every_row_and_pacing_is_not_counted_as_latency(cfg, monkeypatch, capsys):
    from caregrid import llm as llm_mod

    rec = Recording(sleep=0.25)
    monkeypatch.setattr(llm_mod, "get_llm", lambda: rec)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    t0 = time.perf_counter()
    assert main(["eval", "--provider", "env", "--limit", "4"]) == 0
    wall = time.perf_counter() - t0
    assert rec.paced == [2, 2, 2, 2] and wall >= 1.0
    card = json.loads((cfg / "scorecard.env.partial.json").read_text(encoding="utf-8"))
    assert card["provider"] == "env" and card["llm_provider"] == "openai_compat"
    assert card["metrics"]["latency_ms"]["avg"] < 200                                  # the 0.25 s pacing waits are not in the latency
    out = capsys.readouterr().out
    assert "pacing to LLM_MAX_RPM" in out and "provider env -> openai_compat" in out


def test_mock_provider_ignores_the_env_configuration(cfg, monkeypatch):
    from caregrid import llm as llm_mod

    monkeypatch.setattr(llm_mod, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("must not be built")))
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    assert sc.run_eval("mock", limit=3)["models"]["light"] == "mock-light"


def test_a_failing_row_is_recorded_not_fatal(cfg, monkeypatch, capsys):
    real = sc.run

    def flaky(text, *a, **k):
        if "cafeteria" in text:
            raise RuntimeError("boom")
        return real(text, *a, **k)

    monkeypatch.setattr(sc, "run", flaky)
    card = sc.run_eval("mock")
    bad = [r for r in card["results"] if r["error"]]
    assert [r["id"] for r in bad] == ["EV-60"] and "RuntimeError" in bad[0]["error"]
    assert card["metrics"]["errors"] == 1 and card["metrics"]["request_type_accuracy"]["num"] == 62
    assert card["metrics"]["correct_abstention_rate"]["num"] == 4
    assert main(["eval"]) == 1                                          # the CLI exits 1 when any row errored


def test_wrong_expectations_lower_the_scores(cfg, world, tmp_path):
    rows = list(csv.DictReader(open(world / "eval" / "requests_eval.csv", encoding="utf-8", newline="")))
    rows[0]["expected_type"] = "provider_address_change"                   # EV-01
    rows[7]["expected_team"] = "TEAM-IT"
    rows[40]["must_refuse"] = "false"
    rows[40]["expected_reasons"] = ""                                      # drop the row from the safety set
    bad = tmp_path / "bad.csv"
    with open(bad, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    m = sc.run_eval("mock", csv_path=bad)["metrics"]
    assert m["request_type_accuracy"]["num"] == 62 and m["routing_first_time_right"]["num"] <= 62
    assert m["safety_pass_rate"]["den"] == 22


# ================================================================== the pieces
def test_aggregate_handles_empty_and_zero_denominators():
    m = sc.aggregate([])
    assert m["request_type_accuracy"]["value"] is None and m["latency_ms"]["avg"] is None
    assert sc.table_rows({"metrics": {**m, "pii_leaks_in_store": 0}})[0][1] == "n/a"


def test_aggregate_arithmetic():
    base = dict(type_ok=True, route_ok=True, expected_missing=[], missing_found=0, citations=2, citations_valid=2, stale_cited=0, expects_abstain=False,
                abstained_ok=False, expects_auto=False, abstained=False, safety_row=False, safety_category=None, safety_ok=False, stale_seen=False,
                stale_caught=False, tiers=[], llm_fallback=False, llm_output_rejected=False, latency_ms=10.0, error=None)
    rows = [
        {**base, "id": "a", "expected_missing": ["x", "y"], "missing_found": 1, "tiers": ["light"], "latency_ms": 10.0},
        {**base, "id": "b", "type_ok": False, "route_ok": False, "citations_valid": 1, "tiers": ["light", "strong"], "latency_ms": 30.0, "expects_auto": True,
         "abstained": True},
        {**base, "id": "c", "safety_row": True, "safety_category": "clinical", "safety_ok": True, "expects_abstain": True, "abstained_ok": True,
         "stale_seen": True, "stale_caught": True, "latency_ms": 20.0},
        {**base, "id": "d", "safety_row": True, "safety_category": "clinical", "safety_ok": False, "expects_abstain": True, "llm_fallback": True,
         "stale_seen": True, "latency_ms": 100.0},
    ]
    m = sc.aggregate(rows)
    assert m["request_type_accuracy"] == {"value": 75.0, "num": 3, "den": 4}
    assert m["routing_first_time_right"]["num"] == 3
    assert m["missing_field_recall"] == {"value": 50.0, "num": 1, "den": 2}
    assert m["citation_validity"] == {"value": 87.5, "num": 7, "den": 8}
    assert m["correct_abstention_rate"] == {"value": 50.0, "num": 1, "den": 2}
    assert m["false_abstention_rate"] == {"value": 100.0, "num": 1, "den": 1}
    assert m["safety_pass_rate"]["value"] == 50.0 and m["safety_pass_rate"]["by_category"] == {"clinical": {"value": 50.0, "num": 1, "den": 2}}
    assert m["stale_precedent_catches"] == {"caught": 1, "stale_retrieved": 2, "stale_cited": 0}
    assert m["llm_tier_share"]["counts"] == {"no_llm": 2, "light_only": 1, "light_and_strong": 1}
    assert m["llm_tier_share"]["strong_share_of_llm_requests_pct"] == 50.0
    assert m["latency_ms"] == {"avg": 40.0, "p50": 25.0, "p95": 100.0, "max": 100.0}
    assert m["llm_fallbacks"] == 1


def test_citation_validity_rejects_unknown_stale_wrong_type_and_old_versions(world):
    brain = Brain(world / "brain")
    cites = [Citation(page_id="KA-02", version=1, page_type=PageType.POLICY, title="x"),          # valid
             Citation(page_id="KA-12", version=2, page_type=PageType.POLICY, title="x"),          # old version
             Citation(page_id="KA-60", version=1, page_type=PageType.POLICY, title="x"),          # draft
             Citation(page_id="KA-999", version=1, page_type=PageType.POLICY, title="x"),         # unknown
             Citation(page_id="KA-02", version=1, page_type=PageType.WORKFLOW, title="x"),        # wrong type
             Citation(page_id="P-88", version=None, page_type=PageType.PRECEDENT, title="x"),     # stale precedent
             Citation(page_id="P-91", version=None, page_type=PageType.PRECEDENT, title="x")]     # active precedent
    case = Case(id="REQ-1", created_at=datetime(2026, 10, 8), requester=User(id="U1", name="A", role=Role.OPS_EMPLOYEE), masked_text="m",
                proposal=Proposal(decision_code=DecisionCode.ROUTE_TO_TEAM, answer_text="1. x", summary_for_reviewer="s", citations=cites))
    assert sc.citation_checks(case, brain) == (2, 7, 1)


def test_safety_category_mapping():
    assert sc.safety_category({"CLINICAL", "HIGH_RISK"}) == "clinical"
    assert sc.safety_category({"ACCESS_DENIED", "SENSITIVE"}) == "injection"
    assert sc.safety_category({"ACCESS_DENIED", "ACCOUNT_SPECIFIC"}) == "access"
    assert sc.safety_category({"SENSITIVE", "HIGH_RISK"}) == "sensitive"
    assert sc.safety_category({"ACCOUNT_SPECIFIC"}) == "account_specific"


def test_the_committed_baseline_scorecard_matches_a_fresh_mock_run_apart_from_timing(cfg, world):
    card = sc.run_eval("mock")
    root = Path(__file__).resolve().parent.parent / "eval" / "scorecard.mock.json"       # the committed MOCK baseline
    if not root.exists():
        pytest.skip("no committed scorecard yet")
    committed = json.loads(root.read_text(encoding="utf-8"))

    def strip(c):
        c = json.loads(json.dumps(c))
        for k in ("started_at", "wall_seconds"):
            c.pop(k, None)
        c["metrics"].pop("latency_ms", None)
        for r in c["results"]:
            r.pop("latency_ms", None)
        return c

    assert strip(card) == strip(committed)
