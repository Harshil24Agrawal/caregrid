import os

# Must be set before caregrid.config is imported anywhere.
os.environ["CAREGRID_NO_DOTENV"] = "1"      # ignore the developer's .env (keys, models, thresholds)
os.environ["LLM_PROVIDER"] = "mock"
for _k in ("LLM_TIMEOUT_S", "LLM_TIMEOUT_LIGHT_S", "LLM_TIMEOUT_STRONG_S"):   # timeout tests must not depend on the caller's shell
    os.environ.pop(_k, None)
os.environ["CAREGRID_TODAY"] = "2026-10-08"


import pytest  # noqa: E402

# Unit tests written before the requester-confirmation step decide cases straight after the pipeline. They keep the old routing; every API,
# browser, demo and forwarding test runs with the real behaviour.
LEGACY_ROUTING = {"test_prs", "test_relevance", "test_round2", "test_phase4", "test_phase4_units", "test_phase5", "test_openai_compat", "test_app_min"}


@pytest.fixture(autouse=True)
def _routing_mode(request, monkeypatch):
    from caregrid import config

    monkeypatch.setattr(config, "REQUIRE_CONFIRMATION", request.module.__name__.split(".")[-1] not in LEGACY_ROUTING)
