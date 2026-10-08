# CLAUDE.md — CareGrid (read this first, every session)

## What we are building

**CareGrid** — a healthcare operations **Second Brain** + agentic reasoning + human-governed workflow, for the
Acentra Health Hackathon final round (Problem 1: AI-Powered Healthcare Operations Assistant).

> AI prepares the decision. Humans own the decision. Workflows execute the approved action.

## Source-of-truth documents (in this order)

| File | Use it for |
|---|---|
| `CLAUDE.md` | Rules for working in this repo (this file) |
| `BUILD_PLAN.md` | **What to build now** — phases, order, acceptance scenarios, midnight checklist |
| `CONTRACTS.md` | **Exact** data models, enums, module function signatures, scoring formulas. Do not deviate without updating it |
| `DATA_SPEC.md` | Synthetic data: files, columns, IDs, planted test conditions |
| `PROMPTS.md` | LLM prompts and JSON output formats |
| `solution.md` | The *why*: problem, users, innovations, guardrails, AWS design, pitch |

If documents disagree: `CONTRACTS.md` > `BUILD_PLAN.md` > `DATA_SPEC.md` > `PROMPTS.md` > `solution.md`.
If something is missing or ambiguous, choose the simplest option that keeps the demo working, and note it under
"Decisions log" at the bottom of `BUILD_PLAN.md`.

## Non-negotiable rules (guardrails — never break these)

1. **The LLM never produces numbers, amounts, dates, IDs, approvals or confidence.** Those come from data and rules.
2. **Cite-or-abstain.** Every answer cites Second Brain page IDs; citations are verified in code (exists, approved/active, current version). If verification fails → downgrade, never show an uncited claim.
3. **Hard overrides always go to a human:** `CLINICAL`, `ACCOUNT_SPECIFIC`, `SENSITIVE`, `IRREVERSIBLE_ACTION`, any `WRITE` tier action, `ACCESS_DENIED`.
4. **No medical advice.** Clinical questions are refused and routed to Clinical Review.
5. **RBAC before retrieval.** Never load or show data the current user's role cannot see.
6. **PHI/PII is masked before anything is stored in the Second Brain or sent to the LLM.** Raw request text is never persisted.
7. **Only human-approved decisions become precedents.** Never learn from raw AI output.
8. **Write-tier actions (approve, close, change record, change billing, send communication) require an authorized human.**
9. Everything important is written to the audit log.

## Tech stack

- Python 3.11 (conda env `caregrid`), Streamlit UI, SQLite (local), markdown pages with YAML frontmatter.
- Retrieval: `rank-bm25` + embeddings (cosine via numpy) + link traversal + structured filters.
- LLM: provider switch in `caregrid/config.py` → `mock` (deterministic, for tests/offline) | `bedrock` | `anthropic`.
  Two tiers: `light` (classification/extraction) and `strong` (proposal/explanation/PR drafts).
- Anonymization: regex masker always on; Microsoft Presidio if installed.
- Cloud (after midnight): AWS Lambda, DynamoDB, Step Functions, SNS, S3, Bedrock — **free tier + credits only**.
  Build **local-first** behind interfaces (`Store`, `LLM`) so the cloud swap is an adapter, not a rewrite.

## Repo layout

```text
caregrid/
  config.py            # env-driven settings (LLM_PROVIDER, model ids, paths, thresholds)
  models.py            # ALL pydantic models + enums from CONTRACTS.md
  llm.py               # LLM interface + mock/bedrock/anthropic providers + embeddings
  store.py             # Store interface + SQLiteStore
  rbac.py              # can_view / can_approve
  ingest/              # generate.py, anonymize.py, compile.py, leakscan.py
  knowledge/           # brain.py (page store), retrieve.py, lint.py
  reasoning/           # guards.py, classify.py, rules.py, propose.py, citations.py, confidence.py, pipeline.py
  workflow/            # routing.py, trust.py, decisions.py, precedents.py, prs.py, comms.py, audit.py
  insights/            # metrics.py (dashboard, gap radar, queue aging, trust overview)
  cli.py               # python -m caregrid.cli <command>
app/                   # Streamlit: Home.py + pages/ + components/assistant.py
data/synthetic/        # generated CSV/JSON (raw, contains fake PII on purpose)
second_brain/          # compiled pages: policy/ workflow/ team/ field/ precedent/ regulatory/ runbook/ + index.md + log.md
tests/                 # pytest, LLM_PROVIDER=mock
eval/                  # requests_eval.csv + run_eval.py
```

## Commands

```bash
conda create -n caregrid python=3.11 -y && conda activate caregrid
pip install -r requirements.txt
python -m caregrid.cli data      # generate synthetic data
python -m caregrid.cli brain     # anonymize + compile second_brain/ + leak scan
python -m caregrid.cli lint      # lint report
python -m caregrid.cli reset     # wipe sqlite + recompile brain (clean demo state)
python -m caregrid.cli demo      # run the 6 acceptance scenarios headless
python -m caregrid.cli eval      # run evaluation scorecard
pytest -q                       # tests (mock LLM)
streamlit run app/Home.py
```

## How to work

- Follow `BUILD_PLAN.md` phase by phase. Finish a phase's "done when" checks before starting the next.
- Small modules, type hints, pydantic models from `models.py` only — no ad-hoc dicts crossing module boundaries.
- Every module gets a small pytest using `LLM_PROVIDER=mock`. Tests must pass without network.
- Prefer deterministic code; use the LLM only where `CONTRACTS.md` says.
- Keep logging light. Never log raw (unmasked) text.
- When a phase is done: update the checklist in `BUILD_PLAN.md`, commit with message `phase N: <summary>`.
- Don't add features outside the current phase unless the acceptance scenarios need them.
