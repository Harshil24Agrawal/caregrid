"""The deterministic keyword classifier must generalise beyond the rows it was tuned on (held-out ruling 1)."""
import pytest

from caregrid.reasoning.extract import keyword_type


@pytest.mark.parametrize("text", [
    "Dr. Meena has a new surname since her wedding, NPI 1234567890, please update.",
    "Her maiden name was Shah, she now practises as Mehta; records need fixing.",
    "Practitioner got married last month and the name needs updating after marriage.",
    "The provider's name was updated at the registry, please align our records.",
    "He changed surname by deed poll, NPI 1098765437.",
    "Name has been corrected on her licence, ours is outdated.",
])
def test_name_change_phrasings(text):
    assert keyword_type(text) == ("provider_name_change", True)


@pytest.mark.parametrize("text", [
    "Where do we stand on the authorisation for the member's MRI, ref PA-2031-00555?",
    "Need the decision on the authorization request submitted Monday.",
    "Update on the authorisation please, patient called twice.",
    "pre-auth for a knee scan: any outcome yet?",
    "PA-2026-12345 - is it through?",
    "What's the status of the prior-auth the clinic sent?",
])
def test_prior_auth_phrasings(text):
    assert keyword_type(text) == ("prior_auth_status", True)


@pytest.mark.parametrize("text", [
    "Any update on claim CLM-55512345?",
    "Has the claim been paid yet? Provider keeps asking.",
    "What is the status of my claim?",
    "The claim for last week is still pending, why?",
    "claim number 8812 was denied, can you check?",
    "Progress on the claim submitted in September?",
])
def test_claim_status_needs_status_intent(text):
    assert keyword_type(text) == ("claim_status_inquiry", True)


@pytest.mark.parametrize("text", [
    "What is the holiday calendar for the claims team this Diwali season?",
    "Who leads the claims team and where do they sit?",
    "How do I join the claims process training session?",
    "Which forms does the claims department use for onboarding?",
    "Claims team lunch on Friday - where is it?",
])
def test_the_word_claims_alone_is_not_a_claim_status_request(text):
    assert keyword_type(text)[0] != "claim_status_inquiry"


def test_address_and_others_unchanged():
    assert keyword_type("Please change the billing address for NPI 1098765433 to 55 Lake Road") == ("provider_address_change", True)
    assert keyword_type("Provider legally changed name from [PERSON_1] to [PERSON_2]") == ("provider_name_change", True)
    assert keyword_type("This claim denial is unacceptable, I will call my lawyer")[0] == "complaint_grievance"
