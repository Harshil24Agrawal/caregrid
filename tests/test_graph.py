"""Case context graph: nodes/edges, evidence gated by RBAC, no amounts or free text."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_phase5 import LLM, env, world, data_dir  # noqa: E402,F401

from caregrid.insights.graph import case_graph  # noqa: E402
from caregrid.seed import DEMO_CASE_ID, seed_demo_case  # noqa: E402

S3 = "A clinic staff member is locked out of the provider portal, email staff@clinic.example, provider NPI 1234567890. Can we reset it?"


def demo(env):
    seed_demo_case(env.store, env.brain, LLM, env.data)


def test_case_1024_graph_for_a_senior_reviewer(env):
    demo(env)
    g = case_graph(DEMO_CASE_ID, env.rahul, env.store, env.brain)
    kinds = {n.id: n.kind for n in g.nodes}
    assert kinds[DEMO_CASE_ID] == "case" and kinds["KA-40"] == "policy" and kinds["INV-1024"] == "invoice"
    assert {"L-552": "log", "J-184": "jira", "RB-07": "runbook"}.items() <= kinds.items() and "profile" in kinds.values()
    assert "TEAM-SENIOR-OPS" in kinds and all(e.source in kinds and e.target in kinds for e in g.edges)
    assert "62,500" not in g.model_dump_json() and "62500" not in g.model_dump_json()


def test_rbac_hides_evidence_and_blocks_others(env):
    demo(env)
    kinds = {n.kind for n in case_graph(DEMO_CASE_ID, env.meera, env.store, env.brain).nodes}
    assert not kinds & {"invoice", "log", "jira", "profile"} and "policy" in kinds     # knowledge owner: summary level only
    with pytest.raises(PermissionError):
        case_graph(DEMO_CASE_ID, env.kiran, env.store, env.brain)                    # TEAM-IT is not this case's team
    with pytest.raises(KeyError):
        case_graph("NOPE", env.rahul, env.store, env.brain)


def test_conflict_edge_and_precedents(env):
    c = env.run(S3)
    g = case_graph(c.id, env.neha, env.store, env.brain)
    assert any(e.relation == "conflicts_with" and {e.source, e.target} == {"KA-31", "KA-32"} for e in g.edges)
    assert any(n.kind == "precedent" for n in g.nodes)
    assert g.neighbours(c.id) and g.node("KA-31").kind == "policy"
