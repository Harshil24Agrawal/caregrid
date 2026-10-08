"""Knowledge PR flow: draft guard, structured changes only from the human, knowledge_owner-only decisions (mock LLM)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_phase5 import LLM, NAME_A, ReviewAction, env, world, data_dir  # noqa: E402,F401  (fixtures)

from caregrid.models import PageStatus, ReviewDecision  # noqa: E402
from caregrid.workflow.prs import PRStateError, clean_meta, decide_pr, draft_pr, target_policy  # noqa: E402


class Liar:
    """Strong tier that invents numbers / approvals / rewrites everything."""
    def __init__(self, body_fn):
        self.fn = body_fn
    def complete_json(self, system, user, tier):
        import re
        cur = re.search(r"CURRENT ARTICLE \[[^\]]*\]:\n(.*?)\n\nCASE SUMMARY", user, re.S).group(1)
        return {"proposed_body": self.fn(cur), "reason": "x"}
    def model_name(self, tier):
        return "liar"


def case_and_policy(env):
    case = env.run(NAME_A)
    return env.store.get_case(case.id), target_policy(case)


def decision(env, case, **kw):
    return ReviewDecision(case_id=case.id, reviewer=env.vikram, action=ReviewAction.APPROVE, channels=[], **kw)


def test_draft_appends_note_and_diff(env):
    case, pid = case_and_policy(env)
    pr = draft_pr(case, decision(env, case, note="Bank letters on letterhead are accepted."), env.brain, LLM)
    assert pr.target_page_id == pid and pr.status == "open" and pr.base_version == env.brain.get(pid).version
    assert "Bank letters on letterhead are accepted." in pr.proposed_body and pr.diff.startswith("---") and "+Reviewer clarification" in pr.diff


@pytest.mark.parametrize("evil", [
    lambda cur: cur + "\nThe limit is now 9999 days.",
    lambda cur: cur + "\nThis was approved on 2026-01-01.",
    lambda cur: cur + "\nDose: take 50 mg daily.",
    lambda cur: "short",
    lambda cur: cur + "\n" + "x" * 5000,
])
def test_guard_rejects_bad_drafts_and_falls_back(env, evil):
    case, _ = case_and_policy(env)
    cur = env.brain.get(target_policy(case)).body
    pr = draft_pr(case, decision(env, case, note="Clarify wording."), env.brain, Liar(evil))
    assert pr.proposed_body == cur + "\n\nReviewer clarification: Clarify wording." and "guard" in pr.reason


def test_llm_cannot_set_structured_changes(env):
    case, pid = case_and_policy(env)
    pr = draft_pr(case, decision(env, case, note="Clarify."), env.brain, LLM)
    assert pr.meta_changes == {}
    pr = draft_pr(case, decision(env, case, note="x", meta_changes={"rule_key": "max_days", "rule_value": "30", "evil": 1}), env.brain, LLM)
    assert pr.meta_changes == {"rule_key": "max_days", "rule_value": "30"}
    assert clean_meta({"rule_key": "only"}) == {} and clean_meta({"retire": "yes"}) == {}


def test_no_note_no_meta_no_pr(env):
    case, _ = case_and_policy(env)
    assert draft_pr(case, decision(env, case), env.brain, LLM) is None
    env.decide(env.run(NAME_A), env.vikram, propose_pr=True)
    assert env.audit(None) is not None and env.store.list_prs() == []


def test_decide_pr_only_knowledge_owner(env):
    case, _ = case_and_policy(env)
    env.decide(case, env.vikram, propose_pr=True, note="Clarify.")
    pr = env.store.list_prs("open")[0]
    for u in (env.asha, env.vikram, env.neha, env.rahul, env.arjun):
        with pytest.raises(PermissionError):
            decide_pr(pr.id, True, u, env.store, env.brain)
    assert env.store.list_prs("open")[0].status == "open"


def test_approve_publishes_new_version_and_stales_precedents(env):
    case, pid = case_and_policy(env)
    env.decide(case, env.vikram, propose_pr=True, note="Clarify the list.")
    old_v = env.brain.get(pid).version
    prec = env.brain.precedents(status=PageStatus.ACTIVE)
    pr = env.store.list_prs("open")[0]
    done = decide_pr(pr.id, True, env.meera, env.store, env.brain)
    assert done.status == "approved" and done.decided_by == env.meera.id
    assert env.brain.get(pid).version == old_v + 1 and "Clarify the list." in env.brain.get(pid).body
    assert env.brain.get(pid, old_v).status == PageStatus.EXPIRED
    assert all(p.status == PageStatus.STALE for p in env.brain.precedents() if p.policy_id == pid and p.id in {x.id for x in prec})
    assert "published v" in (env.dir / "log.md").read_text(encoding="utf-8") and pid in (env.dir / "index.md").read_text(encoding="utf-8")
    ev = env.store.list_audit(None)
    assert any(e.event == "pr_decided" and e.details["decision"] == "approved" for e in ev)
    with pytest.raises(PRStateError):
        decide_pr(pr.id, False, env.meera, env.store, env.brain)


def test_reject_leaves_page_untouched(env):
    case, pid = case_and_policy(env)
    env.decide(case, env.vikram, propose_pr=True, note="Clarify.")
    keys = {p.key for p in env.brain.all_pages()}
    pr = env.store.list_prs("open")[0]
    done = decide_pr(pr.id, False, env.meera, env.store, env.brain)
    assert done.status == "rejected" and done.decided_by == env.meera.id and {p.key for p in env.brain.all_pages()} == keys
    assert any(e.event == "pr_decided" and e.details["decision"] == "rejected" for e in env.store.list_audit(None))


def test_outdated_pr_cannot_be_approved(env):
    case, pid = case_and_policy(env)
    env.decide(case, env.vikram, propose_pr=True, note="Clarify.")
    pr = env.store.list_prs("open")[0]
    env.brain.write_page(env.brain.get(pid).model_copy(update={"body": env.brain.get(pid).body + " Extra."}))
    with pytest.raises(PRStateError):
        decide_pr(pr.id, True, env.meera, env.store, env.brain)


def test_structured_rule_and_retire(env):
    case, pid = case_and_policy(env)
    env.decide(case, env.vikram, propose_pr=True, note="Raise it.", meta_changes={"rule_key": "max_days", "rule_value": "45"})
    decide_pr(env.store.list_prs("open")[0].id, True, env.meera, env.store, env.brain)
    assert env.brain.get(pid).meta["max_days"] == "45"
    case2 = env.run(NAME_A)
    env.decide(case2, env.vikram, propose_pr=True, note="Obsolete.", meta_changes={"retire": True})
    decide_pr(env.store.list_prs("open")[0].id, True, env.meera, env.store, env.brain)
    assert env.brain.get(pid) is None and all(p.status != PageStatus.ACTIVE for p in env.brain.precedents() if p.policy_id == pid)
