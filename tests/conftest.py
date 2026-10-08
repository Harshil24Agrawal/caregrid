import os

# Must be set before caregrid.config is imported anywhere.
os.environ["LLM_PROVIDER"] = "mock"
os.environ["CAREGRID_TODAY"] = "2026-10-08"
