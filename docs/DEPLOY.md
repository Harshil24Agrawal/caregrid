# Deploying CareGrid

CareGrid is one process: FastAPI serves the API and the static `web/` UI. All configuration is environment variables; nothing is read from a file in the image and no secret is logged.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | 8000 | port the server listens on (Render sets it) |
| `LLM_PROVIDER` | `mock` | `mock` (offline, deterministic), `openai_compat` (Gemini through its OpenAI-compatible endpoint), `bedrock`, `anthropic` |
| `OPENAI_COMPAT_API_KEY`, `OPENAI_COMPAT_BASE_URL`, `LIGHT_MODEL_ID`, `STRONG_MODEL_ID` | - | needed for `openai_compat`; set the key as a SECRET |
| `DEMO_MODE` | `1` in the image | shows the Demo guide and allows the reset button for ops managers / senior reviewers |
| `DATA_DIR`, `DB_PATH`, `BRAIN_DIR` | `/data/...` in the image | where the database, generated synthetic data and the compiled Second Brain live (mount a persistent disk at `/data`) |
| `APP_ACCESS_CODE` | unset | if set, a passcode screen gates the whole UI and API (cookie, constant-time compare, 5 tries a minute). Set it on any public URL |
| `APP_URL` | unset | public base URL, used for the link inside alerts |
| `ALERT_SNS_TOPIC_ARN`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | unset | alerts through AWS SNS. Without a topic or credentials alerts are "simulated" (audited, never an error) |
| `ALERT_SLA_HOURS` | 24 | a case waiting longer than this in review triggers an SLA alert (checked when the dashboard loads, or `python -m caregrid.cli alerts`) |

Health check: `GET /api/health` (open, no data). On first start with an empty `/data`, the app seeds the demo state by itself (about 20 seconds).

## Run locally with Docker

```bash
docker build -t caregrid .
docker run --rm -p 8000:8000 -v caregrid-data:/data -e APP_ACCESS_CODE=letmein caregrid
# open http://127.0.0.1:8000  (health: /api/health)
```
Use the real model by adding `-e LLM_PROVIDER=openai_compat -e OPENAI_COMPAT_API_KEY=... -e OPENAI_COMPAT_BASE_URL=... -e LIGHT_MODEL_ID=... -e STRONG_MODEL_ID=...`.

## Render (Docker web service, free tier)

1. Push the repository to GitHub.
2. Render dashboard -> New -> Web Service -> connect the repository. Environment: Docker (it finds the `Dockerfile`). Instance type: Free.
3. Health Check Path: `/api/health`.
4. Environment variables (Environment tab):
   - `APP_ACCESS_CODE` = a passcode for the demo (mark as secret)
   - `APP_URL` = the service URL Render shows (for example `https://caregrid.onrender.com`)
   - `LLM_PROVIDER` = `openai_compat`, `OPENAI_COMPAT_API_KEY` = your Gemini key (secret), `OPENAI_COMPAT_BASE_URL`, `LIGHT_MODEL_ID`, `STRONG_MODEL_ID`; or leave `LLM_PROVIDER=mock` for a fully offline demo
   - alerts (optional): `ALERT_SNS_TOPIC_ARN`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (secrets)
   - `DEMO_MODE` = `1`
5. Deploy. The first start seeds the demo data; open the URL, enter the passcode.

Free-tier notes: the free service sleeps after inactivity (the first request takes about a minute to wake it) and has NO persistent disk, so `/data` is recreated (and the demo re-seeded) on every restart or redeploy. That is fine for a demo: use "Reset demo" to return to the clean state. A paid instance with a disk mounted at `/data` keeps the database across restarts (SQLite needs a persistent disk; without one the data resets).

Other hosts (for example Hugging Face Spaces, Docker SDK): set `app_port: 8000` in the Space README, add the same variables as Space secrets, and the same image runs unchanged.

## Alerts: configure, test, read the log

Render (and any host) reads exactly these names from the environment; set the first one and the last three as secrets/variables:

| Name | Example |
|---|---|
| `ALERT_SNS_TOPIC_ARN` | `arn:aws:sns:us-east-1:<account>:caregrid-critical-alerts` |
| `AWS_REGION` | `us-east-1` |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | an IAM user allowed `sns:Publish` on that topic (secrets) |
| `APP_URL` | the public URL (used for the link inside an alert) |

- **Startup log** (one line, never a secret): `alerts: SNS enabled (topic caregrid-critical-alerts, region us-east-1)` or `alerts: simulated (missing <names>)`.
- **Test it**: `python -m caregrid.cli alerts test` (Render shell or locally) prints exactly one line: `sent (MessageId ...)`, `simulated (missing ...)` or `failed (<error type>, AWS error code <code>)`. In the UI: Audit page -> "Send test alert" (demo mode, ops manager or senior reviewer).
- **Reset and seeding never alert**, and leave no alert dedup entries. CASE-1024 is seeded already forwarded; to see a fresh approval alert, submit a new high-cost equipment request (for example the CASE-1024 text from the Demo guide) and forward it as Asha. One alert is sent per case per trigger.
- SLA alerts are checked when the dashboard loads (at most once a minute) and by `python -m caregrid.cli alerts`; the seeded cases that have been in review over `ALERT_SLA_HOURS` will alert once on the first dashboard load of a fresh deployment.
- The test suite, `cli demo`, `cli eval` and `cli check` never publish.
