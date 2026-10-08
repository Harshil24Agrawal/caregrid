# Audit of the static HTML frontend (`CareGrid_pages`)

Design input only. Nothing was copied from the sibling `caregrid` folder (old repo copy with `.git` / `.venv` / sqlite) and `app_min/` is untouched.
Location note: the export was not at `caregrid_fe_tmp\CareGrid_pages`; the same files (`Dashboard.html`, `Page_1..6.html`, `caregrid_store.js`,
`caregrid_data.js/.json`) were found at `Downloads\CareGrid_pages\CareGrid_pages` and read from there, read-only. `docs/UI_SPEC.md` was also not
in the repo; the spec found at `Downloads\CareGrid_UI_Spec.md` is now committed as `docs/UI_SPEC.md`.

## What the frontend is

Seven static pages that share a sidebar/header, a glass-card Tailwind theme, and one script, `caregrid_store.js`. That script keeps a
**fake backend in the browser**: a `CareGridStore` class seeded from `caregrid_data.js` (a 250 KB snapshot: 21 cases, 93 audit events, 8 trust
rows, a brain dump), persisted to `localStorage`, with `addCase`, `decide`, `addAudit`, `recalculateMetrics`. Most page content is hard-coded
HTML that the script only partly overwrites. There is no network call anywhere; no API exists.

## Cross-cutting fakes

| Item | What it is | Verdict |
|---|---|---|
| Intake "classification" | `hydrateIntakePage`: `text.includes('document')` -> `general_policy_query`, `'portal'` -> `portal_access_reset`, else `clinical_triage`; fixed scores 100 / 90 / 30 / 75; fixed conflict string `KA-31 vs KA-32` | **business logic in JS, invented types** (`general_policy_query`, `clinical_triage`, `portal_lockout`, `policy_inquiry_docs` are not in `REQUEST_TYPES`) |
| Masking in the browser | two regexes (9-10 digit numbers, e-mail) and the result is stored as `masked_text` | the backend masker is the only one that may mask |
| Decisions | `decide()` flips state to approved -> actioned -> notified, creates a random `P-xxxx` precedent, bumps trust, pushes comms `COMM-xxxxx` | fake approval, fake precedent, fake trust; ignores RBAC and risk |
| Data store | `caregrid_data.js` / `.json` snapshot + `localStorage` | delete; all data comes from the API |
| Users / avatars | 7 users copied from the spec (correct) with `images.unsplash.com` and `lh3.googleusercontent.com` avatars | replace with initials |
| Reset demo | navigates to `Dashboard.html` (and clears localStorage) | must call the real reset |
| `alert()` | decision success / error (`handleDecision`) | replace with toasts |
| Static header | clock `14:32:10 UTC`, `CLUSTER: US-EAST-CLINICAL-A`, "Pipeline LLM: mock" hard-coded, model label "DeepSeek R1" | remove / read from `/api/config` |
| CDNs | `cdn.tailwindcss.com` (unpinned play CDN), Google Fonts (Inter, JetBrains Mono, Material Symbols) | Tailwind and vis-network are vendored; the fonts still need the network (cosmetic only) |
| "Cryptographic" claims | Merkle root, SHA-256 per event, "Verify Proofs", "Export JSONL", Twilio / DKIM / TLS 1.3 / SLA percentages | the backend has none of this: **removed, not faked** |

## Per page

| Page | What it shows | Fake or invented | Missing vs `docs/UI_SPEC.md` / S1-S7 |
|---|---|---|---|
| **Dashboard** | tiles (42 open, 8 needs info, 14 in review, 5 high, 128 auto), queue table (CASE-1024, REQ-0042, REQ-0089, REQ-0105, REQ-0112), trust ladder ("48 / 50 consecutive"), gap / aging radar, "compute tier 82% / 18%" | every number and row is hard-coded; ids and types invented; "Telehealth hotspot, 18 cases, PR-08" is not in the data; "50 consecutive agreements" contradicts `TRUST_L1_STREAK` | counts from `visible_cases`; real queue with age / risk / band / reason codes; real trust table; real `gap_radar`, `queue_aging`, `cost_split`; scorecard |
| **Page 1 Intake** | textarea pre-filled with a different S2 text (street and phone number), static "194 / 4,000", scenario buttons S1-S4, "Simulate async stream", channel tiles | keyword classifier (above), local case creation, invented "guardrail invariants" (e.g. trust capped at Level 1) | live counter and block above 4000; call `pipeline.run`; spinner; result card per state (answered / needs_info with one numbered message and a missing / invalid checklist / in_review team + reasons / refused); masked-PII chips; Open case link; clear on submit |
| **Page 2 Case** | CASE-1024 dossier: a bariatric wheelchair "Titan-X", clinician and patient names, FIM score, 82/100 risk, 62 = 18+14+15+10+5 confidence, KA-19 v2, WF-08, precedents P-91 (91%) and P-74, PRF-4891 "Medicare Advantage", canned co-pilot answers | **the whole case is invented.** The real CASE-1024 is an oxygen concentrator (E1390), cost ₹62,500, KA-40 threshold. Risk has no 0-100 score in the backend; the confidence parts shown are not the backend's; "Redacted for non-senior" is static text, not driven by role; the graph is hand-placed boxes; the co-pilot has no model | case picker from `visible_cases`; header; real recommendation; 5-part bar with the capped-at-Medium note; citation chips; conflicts / notes; evidence gated by `can_view` with locks; real audit; real graph; assistant (PROMPTS section 3) with chips and role-filtered context |
| **Page 3 Approval** | handoff for the same invented case; queue of CASE-1024 / REQ-0042 / CASE-0994; trust gauge "0 / 50"; "Simulate role" toggle Rahul / Asha | invented clinical invariants ("NPI valid, Registry #1948201", "FIM < 2"); a raw e-mail address in the page; the state path is a picture | Handoff packet from `rules` / `proposal`; actions enabled only when `can_approve`, with the reason; edit text; save precedent; propose PR (note, retire, target page); contact + channels; result state path, precedent id and trust change from the audit |
| **Page 4 Audit** | "1,429 events", guardrail 14, remasked 348, precedents 42, Merkle root, a hash per row, JSON payload `EVT-2025-0514-9921` dated 2025-05-14, 24h / 7d / 30d window | counts, hashes, payload, dates and the "Merkle verified / latency / model" fields are invented | real `store.list_audit` with filters (case, event, actor), highlight of `guard_blocked`, `auto_with_audit`, `pii_remasked`, `review_submitted`, `precedent_saved`, `communication_sent`; auditor sees all, ops employee only own cases |
| **Page 5 Knowledge** | "148 approved / 190 entries", KA-12 v2 vs v3 diff, a KA-31 vs KA-32 "biometric bypass" story, KA-40 v4, KA-19, WF-08/09, PREC-1025, KA-60 "GLP-1 protocol" draft, lint (1 error, 2 warnings, 1 info), PR-08 / PR-09 by "Dr. Vance", freshness 99.4%, index.md | the article **text** is invented (real KA-12 v3 requires a supporting document; real KA-31/32 are about portal reset; real KA-60 is a telehealth onboarding draft); counts invented; the two PRs do not exist (no PR is seeded); "New Knowledge PR" and "Archive WF-09" have no backend | real pages by type / status with version history (KA-12 v2 expired vs v3), DRAFT badge for KA-60, real lint with a Run button, `index.md` and `log.md`, open PRs with a red / green diff and Approve / Reject for the knowledge owner only |
| **Page 6 Comms** | "524 dispatches", per-channel counts, five records (Dr. Patel, Dr. Sarah Jenkins, `r.iyer@...`, a +1 415 phone, ...), WhatsApp preview, "Simulate Provider Dispatch" | all records invented **and they contain raw-looking contact data**; "Twilio sandbox / DKIM / TLS" invented; "Fire simulated dispatch" has no backend (communications only come out of an approved decision) | `store.list_comms()` with channel, recipient as stored, message, status badge ("simulated"), time; no dispatch button |

## Missing everywhere

- A real API, server-side RBAC, and a real session user (the browser trusted `localStorage`).
- A user switcher, LLM indicator and Reset demo that act on the backend.
- Masked-PII chips, the 5-part confidence from `case.confidence`, citation chips from verified citations, lock placeholders driven by `can_view`.
- The acceptance flows S1-S7: none can be done end to end because nothing is computed by the backend.

## What the new `web/` keeps and drops

Keeps: the layout (collapsible sidebar, glass cards, header), the colour and spacing tokens, chip and badge styles, the page set (dashboard,
intake, case, approval, audit, knowledge, comms). Drops: everything in the "fake" column. Anything without backing data is removed.
