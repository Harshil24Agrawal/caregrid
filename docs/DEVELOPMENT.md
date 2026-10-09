# CareGrid development notes

Source-of-truth documents, in order: `CLAUDE.md` (rules), `CONTRACTS.md` (exact models and contracts), `BUILD_PLAN.md` (phases and the decisions log), `DATA_SPEC.md`, `PROMPTS.md`, `solution.md`.

```bash
python -m caregrid.cli data      # generate synthetic data
python -m caregrid.cli brain     # anonymize + compile second_brain/ + leak scan
python -m caregrid.cli lint      # lint report
python -m caregrid.cli reset     # wipe sqlite + recompile brain + seed the demo (clean demo state)
python -m caregrid.cli demo      # the acceptance scenarios, headless
python -m caregrid.cli eval [--file eval/heldout.csv]   # evaluation scorecard
python -m caregrid.cli check     # the checklist: reset repeatability, tests, leak scan, citations, audit, state rules, demo, API smoke, data audit
python -m caregrid.cli alerts    # SLA sweep (SNS alerts for cases waiting too long)
python -m caregrid.cli serve     # web UI (web/) + HTTP API on http://127.0.0.1:8000
streamlit run app_min/Home.py    # the minimal Streamlit fallback UI
pytest -q
```

UI notes and click paths: `web/README.md`. Deployment: `DEPLOY.md`. Screenshots: `python scripts/web_screenshots.py`.
