"""P4.1: absolute relevance, linked-but-irrelevant, topic fact, and abstention (behavioural, mock LLM)."""
import csv
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from chain import USERS, run_chain  # noqa: E402

from caregrid import config  # noqa: E402
from caregrid.ingest.compile import compile_brain  # noqa: E402
from caregrid.ingest.generate import generate  # noqa: E402
from caregrid.knowledge.brain import Brain  # noqa: E402
from caregrid.knowledge.retrieve import _search_scores, derive_case_facts, retrieve  # noqa: E402
from caregrid.llm import MockLLM  # noqa: E402
from caregrid.models import (  # noqa: E402
    Band, Classification, DecisionCode, PageStatus, PageType, ReasonCode, State, TrustRecord,
)
from caregrid.reasoning.confidence import policy_component, relevant_policies  # noqa: E402
from caregrid.reasoning.pipeline import run  # noqa: E402
from caregrid.seed import load_users, seed_trust  # noqa: E402
from caregrid.store import SQLiteStore  # noqa: E402

LLM = MockLLM()
ASHA = USERS["asha"]
MIN = config.POLICY_MIN_SCORE

OFF_TOPIC = [
    "What's on the cafeteria menu?", "How do I file my income tax?", "What is the policy on telehealth provider credentialing?",
    "Can you explain our parking rules?", "What is the process to onboard a new telehealth practice?",
    "What's the weather today?", "Who won the cricket match last night?", "Recommend a good restaurant near the office",
    "How do I reset my home wifi router?", "What is the dress code for the holiday party?",
]
ON_TOPIC = [  # question -> the article(s) that may be the top policy hit
    ("What supporting documents are accepted for provider record changes?", {"KA-02"}),
    ("How do I submit a new operations request?", {"KA-01"}),
    ("What is the escalation path for urgent requests?", {"KA-04"}),
    ("Where can I find the forms for provider updates?", {"KA-01"}),
    ("What turnaround should I expect on an operations request?", {"KA-01"}),
    ("Which documents does enrollment accept as proof?", {"KA-02"}),
    ("Which documents can a provider send as proof for a record update?", {"KA-02", "KA-05"}),     # KA-05 is the generic sibling
    ("What is the policy on record retention?", {"KA-11"}),
    ("What are the business hours for operations?", {"KA-03"}),
]


@pytest.fixture(scope="module")
def paths(tmp_path_factory):
    root = tmp_path_factory.mktemp("rel")
    generate(root / "data", eval_dir=root / "eval")
    compile_brain(root / "data", root / "brain")
    return root


@pytest.fixture(scope="module")
def brain(paths):
    return Brain(paths / "brain")


def all_relevance(brain, text):
    cands = brain.current_pages(PageType.POLICY, PageType.REGULATORY, PageType.RUNBOOK)
    scores = _search_scores(cands, text, LLM)
    return {p.id: scores[p.id] for p in cands if p.type == PageType.POLICY}


def general(brain, text, user=ASHA):
    return run_chain(text, user, brain, LLM)


# ================================================================== 1. absolute relevance
@pytest.mark.parametrize("text", OFF_TOPIC)
def test_an_unrelated_best_hit_stays_below_the_minimum(brain, text):
    rel = all_relevance(brain, text)
    best = max(rel, key=rel.get)
    assert rel[best] < MIN, (text, best, rel[best])


@pytest.mark.parametrize("text,pages", ON_TOPIC)
def test_a_covering_article_is_found_above_the_minimum(brain, text, pages):
    rel = all_relevance(brain, text)
    best = max(rel, key=rel.get)
    assert best in pages and rel[best] >= MIN, (text, best, round(rel[best], 3))
    assert all(rel[p] >= MIN for p in pages if p == min(pages))              # the expected article itself is above the bar


def test_workflow_specific_requests_find_their_policy(brain):
    cases = {
        "Dr. Ramesh Iyer wants to update his billing address to 14 Lake Road, Chennai. NPI 123456789.": {"KA-12"},
        "A clinic staff member is locked out of the provider portal, email staff@clinic.example, provider NPI 1234567890. Can we reset it?":
            {"KA-31"},                                                       # KA-32 (self-service) is below the bar
        "Equipment E1390 (oxygen concentrator) requested for member M12345678, estimated cost ₹62,500, prescription on file.": {"KA-40"},
        "Provider NPI 1234567890 legally changed name from Priya Nair to Priya Menon, W-9 attached.": {"KA-05"},
        "What is the status of claim CLM-12345678?": {"KA-45"},
    }
    for text, expect in cases.items():
        c = general(brain, text)
        got = {s.page.id for s in relevant_policies(c.ret)}
        assert expect <= got, (text, got)


def test_the_scale_is_absolute_not_relative_to_the_best_page(brain):
    """With corpus-max normalisation a corpus of ONE unrelated page scored 1.0 on bm25. It must stay low now."""
    only_unrelated = [brain.get("KA-10")]                                    # "Training and onboarding"
    assert _search_scores(only_unrelated, "What is the process to onboard a new telehealth practice?", LLM)["KA-10"] < MIN
    assert _search_scores(only_unrelated, "How do I file my income tax?", LLM)["KA-10"] < MIN
    both = [brain.get("KA-10"), brain.get("KA-02")]
    again = _search_scores(both, "What is the process to onboard a new telehealth practice?", LLM)
    assert again["KA-10"] < MIN and again["KA-02"] < MIN


def test_unseen_query_terms_lower_relevance_and_mask_tokens_do_not_count(brain):
    page = [brain.get("KA-02")]
    base = _search_scores(page, "supporting documents accepted", LLM)["KA-02"]
    noisy = _search_scores(page, "supporting documents accepted zebra quantum pineapple", LLM)["KA-02"]
    assert noisy < base
    masked = _search_scores(page, "supporting documents accepted [PERSON_1] [NPI] [MEMBER_ID]", LLM)["KA-02"]
    assert masked == pytest.approx(base)


def test_relevance_is_clipped_to_unit_interval_and_empty_inputs_are_safe(brain):
    rel = _search_scores(brain.current_pages(PageType.POLICY), "supporting documents accepted for provider record changes " * 3, LLM)
    assert all(0.0 <= v <= 1.0 for v in rel.values())
    assert _search_scores(brain.current_pages(PageType.POLICY), "", LLM) is not None
    assert _search_scores([], "anything", LLM) == {}
    assert all(v < MIN for v in _search_scores(brain.current_pages(PageType.POLICY), "the of and", LLM).values())


# ================================================================== 2. linked + relevant
def test_a_linked_but_irrelevant_policy_earns_nothing(brain):
    c = general(brain, "What is the process to onboard a new telehealth practice?")
    linked = [s for s in c.ret.policies if s.linked]
    assert {s.page.id for s in linked} == {"KA-01", "KA-02"}                 # WF-01 still links them ...
    assert all(s.relevance < MIN for s in linked)                            # ... but they are irrelevant here
    assert relevant_policies(c.ret) == [] and policy_component(c.ret) == 0 and c.conf.breakdown["policy"] == 0


def test_a_linked_and_relevant_policy_earns_30_and_the_boost_only_affects_ranking(brain):
    c = general(brain, "What supporting documents are accepted for provider record changes?")
    ka02 = next(s for s in c.ret.policies if s.page.id == "KA-02")
    assert ka02.linked and ka02.relevance >= MIN and ka02.score == pytest.approx(ka02.relevance + 0.3, abs=1e-3)
    assert c.conf.breakdown["policy"] == 30
    ka01 = next(s for s in c.ret.policies if s.page.id == "KA-01")
    assert ka01.linked and ka01.relevance < MIN                              # the other linked article does not count
    assert [s.page.id for s in relevant_policies(c.ret)] == ["KA-02"] or "KA-02" in [s.page.id for s in relevant_policies(c.ret)]
    assert "KA-01" not in {s.page.id for s in relevant_policies(c.ret)}


def test_both_sides_of_a_conflict_are_in_the_context_and_cited_even_if_one_is_below_the_bar(brain, paths):
    store = SQLiteStore(":memory:")
    case = run("A clinic staff member is locked out of the provider portal, email staff@clinic.example, provider NPI 1234567890. "
               "Can we reset it?", ASHA, store, brain, LLM)
    cited = {c.page_id for c in case.proposal.citations}
    assert {"KA-31", "KA-32"} <= cited and "KA-15" not in cited
    assert {"KA-31", "KA-32"} <= {c.page_id for c in case.citations_considered}
    assert case.rules.conflicts and case.confidence.breakdown["policy"] == 30 and case.confidence.band == Band.MEDIUM


def test_search_only_relevant_policy_still_earns_15(brain):
    c = general(brain, "Provider NPI 1234567890 legally changed name from Priya Nair to Priya Menon, W-9 attached.")
    assert c.conf.breakdown["policy"] == 15 and not any(s.linked for s in c.ret.policies)


def test_linked_policy_irrelevant_to_a_workflow_request_earns_nothing(brain):
    c = general(brain, "My cat is called Whiskers and I want a new address for him")           # keyword 'address' -> address workflow
    assert c.cls.request_type == "provider_address_change"
    ka12 = next((s for s in c.ret.policies if s.page.id == "KA-12"), None)
    assert ka12 is None or ka12.linked is True
    if ka12 is not None and ka12.relevance < MIN:
        assert c.conf.breakdown["policy"] == 0


# ================================================================== 3. topic fact
def test_case_facts_gain_a_topic_only_for_general_questions(brain):
    s1 = general(brain, "What supporting documents are accepted for provider record changes?")
    assert s1.ret.case_facts == {"category": "policy_info", "missing": "none", "risk": "low", "team": "TEAM-OPS-TRIAGE", "topic": "KA-02"}
    gap = general(brain, "What is the process to onboard a new telehealth practice?")
    assert gap.ret.case_facts["topic"] == "none"
    other = general(brain, "A clinic staff member is locked out of the provider portal, email staff@clinic.example, provider NPI 1234567890.")
    assert "topic" not in other.ret.case_facts
    wf = brain.workflow_for("general_policy_question")
    cls = Classification(request_type="general_policy_question")
    assert derive_case_facts(wf, cls)["category"] == "policy_info" and "topic" not in derive_case_facts(wf, cls)   # topic is opt-in
    assert derive_case_facts(wf, cls, "KA-09")["topic"] == "KA-09"


def test_precedent_facts_carry_a_topic(brain):
    general_precs = brain.precedents("general_policy_question")
    answers = [p for p in general_precs if p.decision_code == DecisionCode.ANSWER_FROM_POLICY]
    gaps = [p for p in general_precs if p.decision_code == DecisionCode.NOT_ENOUGH_EVIDENCE]
    assert len(answers) == 12 and len(gaps) == 8
    assert all(p.facts["topic"] == p.policy_id for p in answers)
    assert all(p.facts["topic"] == "none" and p.policy_id is None for p in gaps)
    assert sum(p.facts["topic"] == "KA-02" and p.status == PageStatus.ACTIVE for p in answers) >= 3
    assert all("topic" not in p.facts for p in brain.precedents() if p.request_type != "general_policy_question")


def test_topic_changes_precedent_similarity(brain):
    c = general(brain, "What supporting documents are accepted for provider record changes?")
    sims = {s.precedent.id: s.similarity for s in c.ret.precedents_active}
    same = [v for k, v in sims.items() if brain.get_precedent(k).facts["topic"] == "KA-02"]
    other = [v for k, v in sims.items() if brain.get_precedent(k).facts["topic"] != "KA-02"]
    assert min(same) > 0.6 and max(same) > max(other)
    gap_sims = [v for k, v in sims.items() if brain.get_precedent(k).facts["topic"] == "none"]
    assert all(v < MIN for v in gap_sims)                                    # telehealth gap precedents never match S1


def test_gap_precedents_match_a_gap_question_and_answers_do_not(brain):
    c = general(brain, "What is the process to onboard a new telehealth practice?")
    sims = [(s.precedent.decision_code, s.similarity) for s in c.ret.precedents_active]
    gap = [v for d, v in sims if d == DecisionCode.NOT_ENOUGH_EVIDENCE]
    assert max(gap) >= config.PRECEDENT_MIN_SIM                              # the telehealth gap precedents do match
    matching = sum(v >= config.PRECEDENT_MIN_SIM for v in gap)
    # answer_from_policy precedents may be lexically similar, but their decision differs, so they earn no points
    assert c.conf.breakdown["precedent"] == {0: 0, 1: 15, 2: 20}.get(matching, 25)
    assert c.conf.breakdown["precedent"] < 25


# ================================================================== 4. abstention
def test_general_question_without_a_relevant_policy_abstains_even_with_similar_answer_precedents(brain):
    c = general(brain, "What is the process to onboard a new telehealth practice?")
    assert c.proposal.decision_code == DecisionCode.NOT_ENOUGH_EVIDENCE
    ret = c.ret
    ret_no_policy = ret.model_copy(update={"policies": []})
    from caregrid.reasoning.rules import decide_code
    cls = Classification(request_type="general_policy_question")
    assert decide_code(c.rules, ret_no_policy, cls) == DecisionCode.NOT_ENOUGH_EVIDENCE
    # a different type with no policy but with precedents keeps the old behaviour
    other = Classification(request_type="provider_name_change")
    assert decide_code(c.rules, ret_no_policy, other) != DecisionCode.REFUSE_AND_ROUTE


def test_abstention_is_never_auto_whatever_the_band_and_trust(brain, paths):
    store = SQLiteStore(":memory:")
    for rtype in ("general_policy_question", "provider_name_change"):
        store.save_trust(TrustRecord(request_type=rtype, level=2, total_reviews=99, agreements=99, consecutive_agreements=99,
                                     updated_at=datetime.now()))
    for text in OFF_TOPIC:
        case = run(text, ASHA, store, brain, LLM)
        assert case.proposal.decision_code == DecisionCode.NOT_ENOUGH_EVIDENCE, text
        assert case.routing == "human" and case.state == State.IN_REVIEW, text
        assert {ReasonCode.POLICY_GAP, ReasonCode.LOW_CONFIDENCE} <= set(case.reason_codes), text
        assert case.assigned_team == "TEAM-OPS-TRIAGE", text
        assert not any(c.page_type == PageType.POLICY for c in case.proposal.citations), text
        assert "enough approved guidance" in case.proposal.answer_text and not case.proposal.questions_for_requester


def test_abstention_adds_policy_gap_even_when_the_band_is_not_low(brain, paths):
    store = SQLiteStore(":memory:")
    seed_trust(store, paths / "data")
    case = run("What is the process to onboard a new telehealth practice?", ASHA, store, brain, LLM)
    assert case.confidence.band != Band.LOW                                  # 60: the gap precedents still match
    assert ReasonCode.POLICY_GAP in case.reason_codes and case.routing == "human"


def test_the_four_eval_rows_added_in_p41(paths, brain):
    rows = [r for r in csv.DictReader(open(paths / "eval" / "requests_eval.csv", encoding="utf-8", newline=""))
            if r["id"] in {"EV-60", "EV-61", "EV-62", "EV-63"}]
    assert [r["text"] for r in rows] == ["What's on the cafeteria menu?", "How do I file my income tax?",
                                         "What is the policy on telehealth provider credentialing?", "Can you explain our parking rules?"]
    store = SQLiteStore(":memory:")
    seed_trust(store, paths / "data")
    users = load_users(paths / "data")
    for r in rows:
        case = run(r["text"], users[r["requester_id"]], store, brain, LLM)
        assert (case.proposal.decision_code, case.routing, case.assigned_team) == (DecisionCode.NOT_ENOUGH_EVIDENCE, "human", "TEAM-OPS-TRIAGE")
        assert ReasonCode.POLICY_GAP in case.reason_codes
        assert (r["expected_route"], r["expected_team"], r["must_refuse"]) == ("human", "TEAM-OPS-TRIAGE", "false")


def test_s1_is_still_high_auto_answered_and_cites_ka02(paths, brain):
    store = SQLiteStore(":memory:")
    seed_trust(store, paths / "data")
    case = run("What supporting documents are accepted for provider record changes?", ASHA, store, brain, LLM)
    assert case.confidence.band == Band.HIGH and case.routing == "auto" and case.state == State.ANSWERED
    assert any(c.page_id == "KA-02" and c.version == 1 for c in case.proposal.citations)
    assert "KA-01" not in {c.page_id for c in case.proposal.citations if c.page_type == PageType.POLICY}   # irrelevant linked article


def test_retrieve_returns_unrelated_linked_policies_but_flags_their_relevance(brain):
    ret = retrieve(brain, Classification(request_type="general_policy_question"), "How do I file my income tax?", LLM)
    assert {s.page.id for s in ret.policies if s.linked} == {"KA-01", "KA-02"}
    assert all(s.relevance < MIN for s in ret.policies) and ret.case_facts["topic"] == "none"
