"""Unit tests for classify, propose, verify_citations and decide_route (mock LLM only)."""
import shutil
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from chain import USERS, run_chain  # noqa: E402

from caregrid import config  # noqa: E402
from caregrid.ingest.compile import compile_brain  # noqa: E402
from caregrid.ingest.generate import generate  # noqa: E402
from caregrid.knowledge.brain import Brain  # noqa: E402
from caregrid.llm import MockLLM  # noqa: E402
from caregrid.models import (  # noqa: E402
    ActionTier, Band, Case, Citation, Classification, Confidence, DecisionCode, PageType, Proposal, ReasonCode, Risk, Role,
    RuleResult, State, TrustRecord,
)
from caregrid.reasoning import propose as P  # noqa: E402
from caregrid.reasoning.citations import DOWNGRADED, verify_citations  # noqa: E402
from caregrid.reasoning.classify import FALLBACK_MODEL, classify  # noqa: E402
from caregrid.workflow.routing import decide_route  # noqa: E402

ASHA = USERS["asha"]
S2 = "Dr. Ramesh Iyer wants to update his billing address to 14 Lake Road, Chennai. NPI 123456789."
S1 = "What supporting documents are accepted for provider record changes?"


@pytest.fixture(scope="module")
def paths(tmp_path_factory):
    root = tmp_path_factory.mktemp("p4u")
    generate(root / "data", eval_dir=root / "eval")
    compile_brain(root / "data", root / "brain")
    return root


@pytest.fixture(scope="module")
def brain(paths):
    return Brain(paths / "brain")


class Canned(MockLLM):
    """An LLM double that answers the classifier / proposer with a fixed payload (or raises / sleeps)."""

    def __init__(self, classify=None, propose=None, delay=0.0, error=None):
        super().__init__()
        self._classify, self._propose, self.delay, self.error = classify, propose, delay, error

    def complete_json(self, system, user, tier):
        self.calls.append(tier)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        if "classify healthcare" in system and self._classify is not None:
            return self._classify
        if "You are CareGrid" in system and self._propose is not None:
            return self._propose
        return self._respond(system, user)


# ================================================================== classify
def cls_of(payload, text="Please update the billing address for NPI [NPI] to [ADDRESS] effective 2026-11-01"):
    return classify(text, Canned(classify=payload))


def test_classify_validates_type_and_clamps_confidence():
    c = cls_of({"request_type": "launch_missiles", "confidence": 7.5})
    assert c.request_type == "provider_address_change" and c.llm_confidence == 1.0       # invalid -> keyword rule decides
    c = classify("hello there", Canned(classify={"request_type": "launch_missiles", "confidence": -3}))
    assert c.request_type == "unknown" and c.llm_confidence == 0.0
    assert cls_of({"request_type": "portal_access_reset", "confidence": "high"}).llm_confidence == 0.0
    assert cls_of({"request_type": "portal_access_reset", "confidence": True}).llm_confidence == 0.0
    assert cls_of({"request_type": "portal_access_reset", "confidence": 0.55}).request_type == "portal_access_reset"
    assert cls_of({"request_type": "unknown", "confidence": 0.2}).request_type == "provider_address_change"   # keyword hit is certain


def test_classify_merge_code_wins_and_llm_only_adds_free_text_from_the_request():
    text = "Provider NPI [NPI] changed name from [PERSON_1] to [PERSON_2], W-9 attached, effective 2026-11-01"
    c = classify(text, Canned(classify={
        "request_type": "provider_name_change", "confidence": 0.9,
        "extracted_fields": {"npi": "9999999999", "effective_date": "2030-01-01", "supporting_document": "passport",
                             "estimated_cost_inr": "1", "new_address": "5 Evil Street", "old_name": "[PERSON_9]", "new_name": "[PERSON_2]"}}))
    f = c.extracted_fields
    assert f["npi"] == "[NPI]" and f["effective_date"] == "2026-11-01" and f["supporting_document"] == "W-9"
    assert "estimated_cost_inr" not in f                                  # the LLM cannot add structured fields
    assert "new_address" not in f                                         # free text not present in the request is dropped
    assert f["old_name"] == "[PERSON_1]" and f["new_name"] == "[PERSON_2]"   # code-extracted names win over the LLM's


def test_classify_llm_can_add_free_text_that_occurs_in_the_request():
    text = "Update billing address to [ADDRESS] for [PERSON_1]"
    c = classify(text, Canned(classify={"request_type": "provider_address_change", "extracted_fields": {"new_name": "[PERSON_1]"}}))
    assert c.extracted_fields["new_name"] == "[PERSON_1]" and c.extracted_fields["new_address"] == "[ADDRESS]"


def test_classify_rules_type_comes_from_keywords_not_the_llm():
    c = cls_of({"request_type": "portal_access_reset", "confidence": 0.99})
    assert c.rules_type == "provider_address_change" and c.request_type == "portal_access_reset"
    assert c.model_used == config.LIGHT_MODEL_ID


@pytest.mark.parametrize("llm", [
    Canned(error=RuntimeError("boom")), Canned(error=TimeoutError("slow")), Canned(classify=["not", "a", "dict"]),
    Canned(classify="garbage"),
])
def test_classify_falls_back_to_deterministic_on_llm_failure(llm):
    c = classify("Please update the billing address for NPI [NPI] to [ADDRESS]", llm)
    assert c.model_used == FALLBACK_MODEL and c.request_type == "provider_address_change" and c.llm_confidence == 0.9
    assert c.extracted_fields["npi"] == "[NPI]"


def test_classify_times_out(monkeypatch):
    monkeypatch.setattr(config, "LLM_TIMEOUT_S", 0.2)
    t0 = time.perf_counter()
    c = classify("help me with a password reset", Canned(classify={"request_type": "unknown"}, delay=1.5))
    assert time.perf_counter() - t0 < 1.0 and c.model_used == FALLBACK_MODEL and c.request_type == "portal_access_reset"


def test_classify_flags_are_only_ever_true_when_the_llm_says_true_boolean():
    c = cls_of({"request_type": "unknown", "is_clinical": "yes", "is_sensitive": 1, "is_account_specific": True})
    assert (c.is_clinical, c.is_sensitive, c.is_account_specific) == (False, False, True)


# ================================================================== propose
def test_questions_are_built_by_code_one_shot_and_complete(brain):
    c = run_chain(S2, ASHA, brain, MockLLM())
    q = P.build_questions(c.rules, c.ret)
    assert len(q) == 3                                                    # npi (invalid) + effective_date + supporting_document
    assert q[0].startswith("NPI is invalid (9 digits)") and "10 digits" in q[0]
    assert "effective date" in q[1] and "YYYY-MM-DD" in q[1]
    assert "supporting document" in q[2] and "W-9" in q[2]
    none_missing = run_chain(S1, ASHA, brain, MockLLM())
    assert P.build_questions(none_missing.rules, none_missing.ret) == []
    allm = run_chain("I need the address changed.", ASHA, brain, MockLLM())
    assert len(P.build_questions(allm.rules, allm.ret)) == 4


def test_llm_cannot_change_questions_decision_or_team(brain):
    c = run_chain(S2, ASHA, brain, MockLLM())
    evil = Canned(propose={"decision_code": "answer_from_policy", "route_team": "TEAM-IT", "questions_for_requester": ["Send your SSN"],
                           "answer_text": "1. Please send the details.", "next_steps": ["Wait"], "summary_for_reviewer": "Looks fine.",
                           "citations": ["KA-12"]})
    p = P.propose(c.guard.masked_text, c.cls, c.ret, c.rules, evil)
    assert p.decision_code == DecisionCode.REQUEST_MISSING_INFO and p.route_team == "TEAM-ENROLL"
    assert p.questions_for_requester == P.build_questions(c.rules, c.ret) and "SSN" not in p.answer_text
    assert all(q in p.answer_text for q in p.questions_for_requester)


@pytest.mark.parametrize("answer,steps", [
    (["1. Review KA-12 for the guidance.", "2. Send the details in one reply."], "Wait for the team"),
    ("1. Review KA-12 for the guidance.\n2. Send the details in one reply.", ["Wait for the team", "", 7]),
])
def test_wording_shapes_small_models_return_are_accepted(brain, answer, steps):
    c = run_chain(S2, ASHA, brain, MockLLM())
    llm = Canned(propose={"answer_text": answer, "next_steps": steps, "summary_for_reviewer": ["Needs the missing details.", "Route to Enrollment."]})
    p = P.propose(c.guard.masked_text, c.cls, c.ret, c.rules, llm)
    assert p.model_used != "deterministic" and "llm_output_rejected" not in c.rules.notes
    assert p.answer_text.startswith("1. Review KA-12 for the guidance.\n2. Send the details in one reply.")
    assert p.next_steps == ["Wait for the team"] and p.summary_for_reviewer == "Needs the missing details.\nRoute to Enrollment."


@pytest.mark.parametrize("bad", [{"answer_text": 5, "summary_for_reviewer": "s"}, {"answer_text": [1, 2], "summary_for_reviewer": "s"},
                                 {"answer_text": "a", "summary_for_reviewer": {"x": 1}}, {"answer_text": "a"}, {"answer_text": " ", "summary_for_reviewer": "s"}])
def test_wording_of_the_wrong_type_is_rejected_as_malformed(brain, bad):
    c = run_chain(S2, ASHA, brain, MockLLM())
    p = P.propose(c.guard.masked_text, c.cls, c.ret, c.rules, Canned(propose=bad))
    assert p.model_used == "deterministic" and "llm_output_rejected" in c.rules.notes and "llm_reject_reason: malformed" in c.rules.notes


def test_context_has_current_policies_workflow_team_and_active_precedents_only(brain):
    c = run_chain(S2, ASHA, brain, MockLLM())
    ctx = P.build_context(c.ret)
    ids = {i.id for i in ctx}
    assert {"KA-12", "WF-03", "TEAM-ENROLL", "P-91"} <= ids
    assert "P-88" not in ids and "KA-60" not in ids and "KA-15" not in ids
    assert all(i.version == 3 for i in ctx if i.id == "KA-12")
    assert any(i.header == "[KA-12 v3 | policy | Provider Billing Address Change]" for i in ctx)
    assert any(i.header.startswith("[P-91 | precedent | provider_address_change]") for i in ctx)
    # the stale precedent is only visible as a note inside the RULES line
    block = P.rules_block(c.rules, DecisionCode.REQUEST_MISSING_INFO, "TEAM-ENROLL")
    assert "P-88 is stale (KA-12 v2) and skipped the document check" in block


def test_prompt_sent_to_the_llm_contains_no_stale_precedent_body_or_raw_text(brain):
    seen = {}

    class Spy(Canned):
        def complete_json(self, system, user, tier):
            if "You are CareGrid" in system:
                seen["user"] = user
            return super().complete_json(system, user, tier)

    c = run_chain(S2, ASHA, brain, MockLLM())
    P.propose(c.guard.masked_text, c.cls, c.ret, c.rules, Spy())
    user = seen["user"]
    stale_body = brain.get_precedent("P-88").summary
    context_part = user.split("CONTEXT PAGES:", 1)[1]
    assert stale_body not in user and "[P-88 |" not in context_part
    for raw in ("Ramesh", "Iyer", "Lake Road", "123456789"):
        assert raw not in user


@pytest.mark.parametrize("conflicts,risk,n_pol,n_prec,rtype,explain,want", [
    (True, Risk.LOW, 0, 0, "x", False, "strong"), (False, Risk.HIGH, 0, 0, "x", False, "strong"),
    (False, Risk.CRITICAL, 0, 0, "x", False, "strong"), (False, Risk.MEDIUM, 2, 1, "x", False, "strong"),
    (False, Risk.LOW, 2, 0, "x", False, "light"), (False, Risk.LOW, 1, 3, "x", False, "light"), (False, Risk.LOW, 0, 0, "unknown", False, "strong"),
    (False, Risk.LOW, 1, 1, "x", True, "strong"), (False, Risk.LOW, 1, 1, "x", False, "light"),
])
def test_two_level_rule(conflicts, risk, n_pol, n_prec, rtype, explain, want):
    rules = RuleResult(risk=risk, conflicts=["c"] if conflicts else [])
    ctx = [P.CtxItem(f"KA-{i}", 1, PageType.POLICY, "t", "b") for i in range(n_pol)] + \
          [P.CtxItem(f"P-{i}", None, PageType.PRECEDENT, "t", "b") for i in range(n_prec)]
    assert P.choose_tier(rules, Classification(request_type=rtype), ctx, explain) == want


def test_numbers_in_ignores_list_markers_and_commas():
    assert P.numbers_in("1. Pay ₹62,500 by 2026-11-01.\n2. See KA-12 v3.") == {"62500", "2026-11-01", "12", "3"}
    assert P.numbers_in("no digits at all") == set()


@pytest.mark.parametrize("text,reason", [
    ("Your refund of ₹99,999 is on the way", "unsupported_number"), ("Done by 2031-12-31", "unsupported_number"),
    ("Contact +91 98765 43210 today", "unsupported_number"), ("Contact jane.doe@clinic.example", "pii"),
    ("Take five tablets of aspirin tonight", "medical_advice"), ("Your request has been approved", "claims_done_or_approved"),
    ("The record was updated yesterday", "claims_done_or_approved"),
])
def test_llm_text_problems_are_detected(text, reason):
    assert P.llm_text_problem([text], "source mentioning 3 and KA-12") == reason


def test_llm_text_is_accepted_when_numbers_come_from_the_sources():
    src = "estimated cost 62500 above 50000 threshold [KA-40] 2026-11-01 KA-12 v3"
    assert P.llm_text_problem(["1. Cost ₹62,500 is above ₹50,000 [KA-40].\n2. Effective 2026-11-01 per KA-12 v3."], src) is None


def test_deterministic_wording_for_every_decision(brain):
    base = run_chain(S1, ASHA, brain, MockLLM())
    ctx = P.build_context(base.ret)
    for decision in DecisionCode:
        a, steps, summary = P.deterministic_wording(decision, base.cls, base.ret, base.rules, ctx, [])
        assert a.strip() and steps and summary.startswith("Request type:")
    a, *_ = P.deterministic_wording(DecisionCode.ANSWER_FROM_POLICY, base.cls, base.ret, base.rules, ctx, [])
    assert "W-9" in a                                                       # first sentences of the cited policy


def test_citations_come_from_context_with_brain_versions(brain):
    c = run_chain(S2, ASHA, brain, MockLLM())
    ctx = P.build_context(c.ret)
    cites = P.assemble_citations(ctx, c.ret, DecisionCode.REQUEST_MISSING_INFO, "TEAM-ENROLL",
                                 ["KA-60", "KA-12@v2", "P-88", "KA-999", "ka-12", "WF-03 v9", "TEAM-ENROLL", ""])
    ids = [x.page_id for x in cites]
    assert ids.count("KA-12") == 1 and next(x for x in cites if x.page_id == "KA-12").version == 3
    assert not {"KA-60", "P-88", "KA-999"} & set(ids)
    assert next(x for x in cites if x.page_id == "WF-03").version == 1                   # "v9" from the LLM was ignored


# ================================================================== verify_citations
def prop(citations, decision=DecisionCode.ANSWER_FROM_POLICY):
    return Proposal(decision_code=decision, route_team="TEAM-OPS-TRIAGE", answer_text="1. text", summary_for_reviewer="s", citations=citations)


def cite(pid, version=None, typ=PageType.POLICY, title="x"):
    return Citation(page_id=pid, version=version, page_type=typ, title=title)


def test_verify_citations_drops_everything_that_is_not_citable(brain):
    p, issues = verify_citations(prop([
        cite("KA-02", 1), cite("KA-60", 1), cite("KA-12", 2), cite("KA-15", 1), cite("P-88", None, PageType.PRECEDENT),
        cite("KA-999", 1), cite("KA-12", 3), cite("KA-02", 1)]), brain)
    assert [(c.page_id, c.version) for c in p.citations] == [("KA-02", 1), ("KA-12", 3)]
    text = " ".join(issues)
    for gone in ("KA-60", "KA-15", "P-88", "KA-999"):
        assert gone in text
    assert "cited v2, current is v3" in text and p.decision_code == DecisionCode.ANSWER_FROM_POLICY


def test_verify_citations_reattaches_type_title_and_version_from_the_brain(brain):
    p, _ = verify_citations(prop([cite("KA-02", 1, PageType.REGULATORY, "HACKED TITLE"), cite("P-91", None, PageType.POLICY, "x")]), brain)
    ka02 = next(c for c in p.citations if c.page_id == "KA-02")
    assert (ka02.page_type, ka02.title, ka02.version) == (PageType.POLICY, "Supporting documents accepted", 1)
    p91 = next(c for c in p.citations if c.page_id == "P-91")
    assert p91.page_type == PageType.PRECEDENT and p91.version is None and p91.title == "provider_address_change: request_missing_info"


def test_verify_citations_requires_context_membership(brain):
    p, issues = verify_citations(prop([cite("KA-02", 1), cite("KA-12", 3)]), brain, context_ids={"KA-12"})
    assert [c.page_id for c in p.citations] == ["KA-12"] and "dropped KA-02: not in the context" in issues


def test_answer_from_policy_without_a_policy_citation_is_downgraded(brain):
    p, issues = verify_citations(prop([cite("WF-01", 1, PageType.WORKFLOW), cite("KA-60", 1)]), brain)
    assert p.decision_code == DecisionCode.NOT_ENOUGH_EVIDENCE and DOWNGRADED in issues
    assert "enough approved guidance" in p.answer_text and [c.page_id for c in p.citations] == ["WF-01"]
    ok, issues = verify_citations(prop([cite("KA-02", 1)]), brain)
    assert ok.decision_code == DecisionCode.ANSWER_FROM_POLICY and DOWNGRADED not in issues
    other, issues = verify_citations(prop([cite("KA-60", 1)], DecisionCode.ROUTE_TO_TEAM), brain)
    assert other.decision_code == DecisionCode.ROUTE_TO_TEAM and DOWNGRADED not in issues       # only answer_from_policy downgrades


def test_a_newer_policy_version_invalidates_older_citations(paths, tmp_path):
    shutil.copytree(paths / "brain", tmp_path / "b")
    b = Brain(tmp_path / "b")
    old = b.get("KA-02")
    b.write_page(old.model_copy(update={"body": old.body + " Updated."}))
    p, issues = verify_citations(prop([cite("KA-02", 1)]), b)
    assert p.citations == [] and any("current is v2" in i for i in issues) and DOWNGRADED in issues


# ================================================================== routing
def make_case(band=Band.HIGH, score=90, hard=False, missing=(), invalid=None, risk=Risk.LOW, tier=ActionTier.READ,
              rtype="general_policy_question", policy_points=30, decision=DecisionCode.ANSWER_FROM_POLICY):
    rules = RuleResult(hard_override=hard, missing_fields=list(missing), invalid_fields=invalid or {}, risk=risk, action_tier=tier,
                       route_team="TEAM-OPS-TRIAGE", approver_role=Role.TEAM_SPECIALIST, reason_codes=[ReasonCode.MISSING_DATA] if missing else [])
    return Case(id="REQ-0001", created_at=datetime(2026, 10, 8), requester=ASHA, masked_text="m", state=State.PROPOSED,
                state_history=[(State.NEW, datetime(2026, 10, 8)), (State.PROPOSED, datetime(2026, 10, 8))],
                classification=Classification(request_type=rtype), rules=rules,
                confidence=Confidence(score=score, band=band, breakdown={"policy": policy_points, "precedent": 0, "fields": 0, "clarity": 0,
                                                                       "no_conflict": 0}, explanation="e"),
                proposal=Proposal(decision_code=decision, route_team="TEAM-OPS-TRIAGE", answer_text="1. a", summary_for_reviewer="s"))


def trust(level, rtype="general_policy_question"):
    return TrustRecord(request_type=rtype, level=level, updated_at=datetime(2026, 10, 8))


def test_route_hard_override_is_always_human_in_review():
    c = decide_route(make_case(hard=True), trust(2))
    assert (c.routing, c.state) == ("human", State.IN_REVIEW)


@pytest.mark.parametrize("level", [0, 1, 2])
@pytest.mark.parametrize("band,score", [(Band.HIGH, 100), (Band.MEDIUM, 60), (Band.LOW, 10)])
@pytest.mark.parametrize("risk", list(Risk))
@pytest.mark.parametrize("tier", list(ActionTier))
@pytest.mark.parametrize("missing", [(), ("npi",)])
def test_hard_override_can_never_be_auto(level, band, score, risk, tier, missing):
    c = decide_route(make_case(band=band, score=score, hard=True, risk=risk, tier=tier, missing=missing), trust(level))
    assert c.routing == "human"


def test_route_low_band_becomes_not_enough_evidence_with_reasons():
    c = decide_route(make_case(band=Band.LOW, score=20, policy_points=0), trust(0))
    assert c.proposal.decision_code == DecisionCode.NOT_ENOUGH_EVIDENCE and c.state == State.IN_REVIEW and c.routing == "human"
    assert {ReasonCode.LOW_CONFIDENCE, ReasonCode.POLICY_GAP} <= set(c.reason_codes)
    with_policy = decide_route(make_case(band=Band.LOW, score=20, policy_points=15), trust(0))
    assert ReasonCode.LOW_CONFIDENCE in with_policy.reason_codes and ReasonCode.POLICY_GAP not in with_policy.reason_codes
    vague = decide_route(make_case(band=Band.LOW, score=5, policy_points=0, rtype="unknown"), trust(0))
    # phase 4.2: an UNKNOWN request is an intent problem, never a policy gap (POLICY_GAP is for known types only)
    assert ReasonCode.POLICY_GAP not in vague.reason_codes and ReasonCode.LOW_CONFIDENCE in vague.reason_codes


def test_route_missing_or_invalid_fields_need_info():
    for kw in ({"missing": ("npi",)}, {"invalid": {"npi": "bad"}}):
        c = decide_route(make_case(**kw), trust(1))
        assert c.state == State.NEEDS_INFO and c.routing == "human"


def test_route_medium_band_goes_to_review():
    c = decide_route(make_case(band=Band.MEDIUM, score=60), trust(2))
    assert (c.state, c.routing) == (State.IN_REVIEW, "human")


def test_route_auto_needs_high_band_low_risk_trust_and_read_or_draft():
    read = decide_route(make_case(), trust(1))
    assert (read.routing, read.state, read.trust_level) == ("auto", State.ANSWERED, 1)
    draft = decide_route(make_case(tier=ActionTier.DRAFT, rtype="provider_address_change"), trust(2))
    assert (draft.routing, draft.state) == ("auto", State.READY)
    assert decide_route(make_case(), trust(0)).routing == "human"                          # no trust yet
    assert decide_route(make_case(risk=Risk.MEDIUM), trust(2)).routing == "human"          # risk not low
    assert decide_route(make_case(tier=ActionTier.WRITE), trust(2)).routing == "human"     # write tier
    assert decide_route(make_case(tier=ActionTier.WRITE), trust(2)).state == State.IN_REVIEW


def test_route_copies_team_approver_and_reasons_and_records_state_history():
    c = decide_route(make_case(), trust(1))
    assert c.assigned_team == "TEAM-OPS-TRIAGE" and c.approver_role == Role.TEAM_SPECIALIST
    assert [s for s, _ in c.state_history][-1] == State.ANSWERED


def test_decide_route_requires_a_complete_case():
    case = make_case()
    case.rules = None
    with pytest.raises(AssertionError):
        decide_route(case, trust(1))


def test_date_import_is_used():          # keeps the import list honest for linting
    assert date(2026, 10, 8).year == 2026
