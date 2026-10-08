import os

# Must be set before caregrid.config is imported anywhere.
os.environ["CAREGRID_NO_DOTENV"] = "1"      # ignore the developer's .env (keys, models, thresholds)
os.environ["LLM_PROVIDER"] = "mock"
for _k in ("LLM_TIMEOUT_S", "LLM_TIMEOUT_LIGHT_S", "LLM_TIMEOUT_STRONG_S"):   # timeout tests must not depend on the caller's shell
    os.environ.pop(_k, None)
os.environ["CAREGRID_TODAY"] = "2026-10-08"
