import os

# Must be set before caregrid.config is imported anywhere.
os.environ["CAREGRID_NO_DOTENV"] = "1"      # ignore the developer's .env (keys, models, thresholds)
os.environ["LLM_PROVIDER"] = "mock"
os.environ["CAREGRID_TODAY"] = "2026-10-08"
