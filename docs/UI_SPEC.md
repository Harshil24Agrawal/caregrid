# CareGrid — UI Spec for the Frontend (Streamlit)

> **AI prepares the decision. Humans own the decision. Workflows execute the approved action.**
> This doc tells you **what each screen shows, where the data comes from, and who may see it.**
> Source of truth for models and function signatures: `CONTRACTS.md` in the repo. If a name here differs from the code, **CONTRACTS.md wins**. Ask Harshil before inventing anything.

---

## 0. Setup (10 min)

```powershell
git clone https://github.com/Harshil24Agrawal/caregrid.git
cd caregrid
conda create -n caregrid python=3.11 -y
conda activate caregrid
conda env config vars set PYTHONUTF8=1 -n caregrid
conda deactivate; conda activate caregrid
python -m pip install -r requirements.txt
python -m caregrid.cli reset      # generates data, compiles second_brain/, seeds SQLite (incl. CASE-1024)
python -m caregrid.cli demo       # should print 7/7 PASS
streamlit run app/Home.py
```

- Work with `LLM_PROVIDER=mock` (the default, no `.env` needed). It's deterministic, offline and free, so the UI behaves identically every run.
- **Don't edit anything under `caregrid/`** (the backend). If you need a new backend function, ask Harshil. You own `app/` only.
- Commit messages: `ui: <what>`. Pull often; the backend is moving fast.

---

## 1. Layout & files

```text
app/
  Home.py                    # Dashboard + role picker (sidebar)
  pages/
    1_New_Request.py         # Intake
    2_Case.py                # Case Intelligence + Recommendation + Assistant
    3_Approval.py            # Handoff Packet + decision form
    4_Audit_Log.py
    5_Knowledge_Hub.py       # Second Brain pages + lint (+ Knowledge PRs later)
    6_Communications.py      # after midnight
  components/
    session.py               # current user, cached Brain/LLM/Store
    badges.py                # state / risk / band / reason-code chips
    confidence.py            # score bar + 5-part breakdown
    citations.py             # citation chips with version + status
    handoff_packet.py        # the approval card
    assistant.py             # context-aware assistant (PROMPTS.md §3)
```

**Caching:** `Brain` and the LLM go in `@st.cache_resource`. Create a fresh `SQLiteStore(config.DB_PATH)` per run (it's cheap and Streamlit-safe). After any write (submit request, approve), call `st.rerun()`.

---

## 2. Global elements (every page)

### Sidebar
- **"Acting as" user switcher.** These are the demo users, from `data/synthetic/users.csv`:

| id | name | role | team |
|---|---|---|---|
| U1 | Asha | ops_employee | TEAM-OPS-TRIAGE |
| U2 | Vikram | team_specialist | TEAM-ENROLL |
| U3 | Neha | ops_manager | — |
| U4 | Rahul | senior_reviewer | TEAM-SENIOR-OPS |
| U5 | Meera | knowledge_owner | — |
| U6 | Arjun | auditor | — |
| U7 | Kiran | team_specialist | TEAM-IT |

- Show `Name · role · team` under the switcher. Store it in `st.session_state["user"]` as a `User` model.
- **LLM indicator:** small text `LLM: mock | anthropic | openai_compat` read from config. Judges like the transparency.
- **"Reset demo" button:** calls the same function as `cli reset`. Ask Harshil for the import path, and put it behind a confirm dialog.

### Visual conventions (use everywhere, keep consistent)

| Thing | Values → style |
|---|---|
| Confidence band | **High** (≥75) green · **Medium** (45–74) amber · **Low** (<45) red |
| Risk | low grey · medium amber · high orange · **critical red** |
| State | badge with the `State` enum text: `answered`, `needs_info`, `in_review`, `approved`, `actioned`, `notified`, `closed`, … |
| Reason codes | small chips: `MISSING_DATA`, `POLICY_CONFLICT`, `CLINICAL`, `ACCESS_DENIED`, … (hard overrides in red: CLINICAL, ACCOUNT_SPECIFIC, SENSITIVE, IRREVERSIBLE_ACTION, ACCESS_DENIED) |
| Routing | `AUTO (with audit)` vs `HUMAN → TEAM-X` |
| Trust level | `0 Shadow` · `1 Assist` · `2 Auto-with-audit` |

### Confidence bar component (`components/confidence.py`)
Input: `case.confidence` (`Confidence` model). Show:
- The big number, the band badge, and the `explanation` text.
- A 5-segment stacked bar or table: `policy /30 · precedent /25 · fields /20 · clarity /15 · no_conflict /10`.
- If `case.rules.conflicts` is non-empty, show the note **"Band capped at Medium: conflicting evidence"**.

### Citation chips (`components/citations.py`)
Input: `case.proposal.citations` (`list[Citation]`). Each chip reads **`KA-12 v3 · policy · Provider Billing Address Change`**. Clicking it opens that page in the Knowledge Hub (`Brain.get(page_id, version)`).
- **Never show uncited claims.** If `citations` is empty on an answer, show "No verified source, sent to a human."

---

## 3. RBAC: who sees what (CONTRACTS §12). Enforce in the UI as well as the backend.

| Role | Case list | Case sections visible | Can approve |
|---|---|---|---|
| ops_employee | own cases only | **summary only**: no profile, **no billing amounts**, no logs | — |
| team_specialist | cases assigned to own team | full (billing amounts view-only) | LOW risk, own team |
| ops_manager | all | full | LOW, MEDIUM |
| senior_reviewer | all | full | all risks |
| knowledge_owner | summary | pages, lint, PRs | PRs only |
| auditor | all (summary) + full audit log | summary, read-only | — |

Rules:
- Use `rbac.can_view(user, case, section)` with sections `summary | profile | billing | logs | full`, `rbac.can_approve(user, case)` and `rbac.visible_cases(user, store)`. **These arrive in Phase 5.** Until then, stub them in `components/session.py` using the table above, and swap to the real ones when they land.
- **Every piece of generated text you render must pass through `guards.check_output(text, user)`** and show the cleaned text. That's what redacts ₹ amounts for Asha (S5 acceptance: *"Asha cannot see billing amount"*).
- Hidden sections show a locked placeholder: 🔒 *"ACCESS RESTRICTED: you don't have permission to view this information."* Never just omit them silently.

---

## 4. Screens

### 4.1 Home: Dashboard (`Home.py`)
**Phase 6 (by midnight):**
- Count tiles: Open · Needs info · In review (pending approval) · High/critical risk · Escalated · Auto-answered today. Compute from `store.list_cases()` filtered by `visible_cases(user)`.
- **My queue:** a table of visible cases with columns `id · type · state · risk · band · team · age (now − created_at) · reason codes`. Clicking a row opens the Case page.
- **Trust Ladder:** table from `store.list_trust()` with columns `request_type · level · consecutive agreements · agreement %`.

**After midnight (from `insights/metrics.py`):**
- **Knowledge Gap Radar:** `gap_radar(store)` → rows `request_type · reason_code · count · avg hours in queue · est. hours saved`. Highlight the telehealth POLICY_GAP hotspot.
- **Queue aging:** `queue_aging(store)` → bar chart by state/team.
- **Cost split:** `cost_split(store)` → % light-only vs light+strong LLM.

### 4.2 New Request: Intake (`pages/1_New_Request.py`)
- Text area with a **live character counter, max 4000**. Block submit above that.
- Channel select (`portal` default, `email`, `whatsapp`, `sms`).
- Submit → `pipeline.run(text, user, store, brain, llm, channel)` → returns a `Case`. **Clear the text box after submit.** Raw text must never be kept in session state or shown again.
- Result card, depending on `case.state`:

| state | Show |
|---|---|
| `answered` (auto) | ✅ the answer text + citation chips + confidence bar + **"Answered automatically · Trust level 1 · audited"** |
| `needs_info` | 📝 **one message** listing **all** `proposal.questions_for_requester` (numbered), plus a checklist of missing/invalid fields (`rules.missing_fields`, `rules.invalid_fields`, e.g. *"NPI: invalid, 9 digits"*) |
| `in_review` | 👤 "Sent to **TEAM-X** for review" + reason chips + the approver role needed |
| refused (`decision_code == refuse_and_route`) | ⛔ the refusal message + the team it was routed to + reason chips. **No other content.** |

- Always show the **"Open case →"** link, the band and the reason codes.
- Show what was masked: `pii_types_found` as chips (e.g. "Masked: PERSON, ADDRESS, NPI"). This visibly demos privacy.

### 4.3 Case Intelligence + Recommendation (`pages/2_Case.py`)
Case picker (from `visible_cases`) or open via query param. Header: `CASE-1024 · dme_equipment_request · IN_REVIEW · 3h · Risk HIGH · Trust 0 (Shadow)`.

Tabs:
1. **Overview:** `case.masked_text` (already masked, safe to show), classification (type, urgency, sentiment), reason chips, `rules.risk_reasons` (e.g. *"cost ₹62,500 above ₹50,000 threshold [KA-40]"*, through `check_output`).
2. **Recommendation:** `proposal.decision_code`, `route_team`, `answer_text`, `next_steps`, `summary_for_reviewer`, the confidence component, citation chips, `rules.conflicts` (red box), `rules.notes` (grey info box, e.g. *"P-88 is stale (KA-12 v2) and skipped the document check"*), and `llm_tiers_used` as a small "light / light+strong" tag.
3. **Evidence:**
   - Policies retrieved: id, version, **status**, linked or by search, score.
   - Precedents: **active** (with similarity) and **stale** (greyed, labelled `STALE: used KA-12 v2, current v3`).
   - Related records from `case.related` (keys `profile`, `invoice`, `logs`, `jira`, `runbook`), each **gated by `can_view`**:
     - profile → `PRF-xxxx` looked up in `data/synthetic/profiles.json` (`profile` section only)
     - invoice → `billing.csv` row (`billing` section; Asha sees 🔒)
     - logs → `system_logs.csv` (L-552), jira → `jira_records.csv` (J-184), runbook → RB-07 page
4. **Audit:** this case's events (`store.list_audit(case_id)`) as a timeline.

**Context graph (after midnight):** PyVis/NetworkX graph: case ↔ policies ↔ precedents ↔ invoice ↔ logs ↔ JIRA ↔ runbook.

**Assistant panel** (right column, or `st.popover` bottom-right; a floating version is polish for later). Implement in `components/assistant.py` per **PROMPTS.md §3**:
- Suggested chips: *Why is this case flagged? · Which policy applies? · Show related cases · Explain the recommendation · What should I do next? · Prepare for approval*.
- Build the CASE CONTEXT **only from sections the user can view** (role-filtered *before* calling the LLM).
- Use the light tier normally and the strong tier for "why/explain" questions.
- Pass the reply through `check_output(text, user)`. Render `[KA-40]`-style ids as clickable chips.
- S5 acceptance: Rahul asks *"Why is this high risk?"* and the answer cites **[KA-40] [INV-1024]**. If Asha asks for the amount, she gets the ACCESS RESTRICTED line.

### 4.4 Approval: Handoff Packet (`pages/3_Approval.py`)
List: cases in `in_review` / `needs_info` visible to the user. The selected case renders this card:

```text
┌──────────────────────────────────────────────────────────┐
│ REQ-0042 · Provider address change · IN_REVIEW · 2h     │
│ Reason: POLICY_CONFLICT · Risk: LOW · Trust: 0 (Shadow)  │
├──────────────────────────────────────────────────────────┤
│ Summary: (proposal.summary_for_reviewer)                 │
│ Checked: ✓ NPI valid  ✓ Policy KA-12 v3 (current)        │
│ Missing: ✗ Supporting document                           │
│ Proposed: (decision_code → route_team)                   │
│ Confidence 58 · policy 30 · precedent 5 · fields 13 …    │
│ Conflict / Notes: (rules.conflicts / rules.notes)        │
│ Sources: KA-12 v3 · WF-03 · P-91                         │
├──────────────────────────────────────────────────────────┤
│ [Approve] [Edit & approve] [Reject] [Escalate] [Ask]     │
│ ☑ Save as precedent   ☐ Propose wiki change (PR)         │
│ Query contact: [ email ] [ phone ]  Channels: ☑Email ☐WA │
└──────────────────────────────────────────────────────────┘
```

- "Checked/Missing" come from `rules.required_fields`, `missing_fields` and `invalid_fields`.
- Buttons are **disabled unless `can_approve(user, case)`**, with a tooltip explaining why (e.g. *"HIGH risk requires senior_reviewer"*).
- "Edit & approve" opens a text area prefilled with `answer_text`.
- Submit builds a `ReviewDecision` and calls `workflow.decisions.submit_decision(d, store, brain, llm)` (**Phase 5**). Then show the new state path (e.g. `APPROVED → ACTIONED → NOTIFIED`), the precedent id created, and the trust record change (`consecutive 0 → 1`).
- "Ask requester" shows `questions_for_requester` as one message.

### 4.5 Audit Log (`pages/4_Audit_Log.py`)
- Table from `store.list_audit()`: `ts · case · event · actor · role`, with filters by case, event and actor. Row expander shows the `details` JSON (masked, types only).
- Highlight `guard_blocked`, `auto_with_audit`, `pii_remasked`, `review_submitted`, `precedent_saved` and `communication_sent`.
- Auditor (Arjun) sees everything, read-only. Ops employee sees only their own cases' events.

### 4.6 Knowledge Hub (`pages/5_Knowledge_Hub.py`)
- **Pages browser:** filter by type (policy, workflow, team, field, precedent, regulatory, runbook) and status (approved, draft, expired, active, stale). Render frontmatter as a small table and the body as markdown.
  - Show **version history** side by side: KA-12 **v2 expired** vs **v3 approved**.
  - Draft KA-60 shows a big **DRAFT, never cited** badge.
- **Lint report:** `lint(brain, store)` grouped by severity: ERROR (CONTRADICTION KA-31/KA-32) · WARNING (EXPIRED_LINKED, STALE_PRECEDENT, ESCALATION_HOTSPOT) · INFO (orphans, collapsed by default). Add a "Run lint" button.
- `index.md` and `log.md` viewers.
- **After midnight, Knowledge PRs:** a list of open PRs with a **red/green unified diff** (`pr.diff`) and Approve/Reject buttons for Meera (knowledge_owner only), calling `decide_pr(...)`.

### 4.7 Communications (`pages/6_Communications.py`, after midnight)
- `store.list_comms()` → `case · channel · recipient · message · status (sent/simulated/failed) · time`. WhatsApp/SMS show a "simulated" badge.

---

## 5. Acceptance checklist: what the UI must visibly show (BUILD_PLAN S1–S6)

| # | As | Do | The screen must show |
|---|---|---|---|
| S1 | Asha | Intake: *"What supporting documents are accepted for provider record changes?"* | ANSWERED · **auto** · cites **KA-02 v1** · High band · "audited" |
| S2 | Asha | *"Dr. Ramesh Iyer wants to update his billing address to 14 Lake Road, Chennai. NPI 123456789."* | NEEDS_INFO · **one** message with 3 asks · NPI *invalid, 9 digits* · TEAM-ENROLL · note about **P-88 stale** · masked text has no name or address |
| S3 | Asha | Portal lockout request (see BUILD_PLAN) | POLICY_CONFLICT · KA-31 vs KA-32 red box · band **capped Medium** · → TEAM-IT (Kiran) |
| S4 | Asha | (a) insulin dose question (b) "Ignore previous instructions…" | (a) refused, CLINICAL → Clinical Review, **no advice text** (b) blocked, ACCESS_DENIED/SENSITIVE, audit `guard_blocked` |
| S5 | Rahul | Open CASE-1024 → assistant "Why is this high risk?" → Approve + contact + Email & WhatsApp | HIGH · ₹62,500 > ₹50,000 [KA-40] · evidence INV-1024, L-552, J-184, RB-07 · assistant cites [KA-40] [INV-1024] · **switch to Asha → amount hidden** · state path to NOTIFIED · comms listed · precedent saved |
| S6 | Asha → Vikram → Asha | Name-change request → Vikram approves → similar request again | first **60 Medium** → after approval the second shows **75 High** citing the **new precedent id** · trust consecutive = 1 |

---

## 6. Hard rules (don't break; judges will test)

1. Never show or store **raw** request text after submit. Display `masked_text` only.
2. Run **every** generated string (answers, assistant replies, summaries) through `check_output(text, user)` before rendering.
3. Never display a citation the backend didn't verify. No hand-written policy references in the UI.
4. Respect RBAC on every section. Show 🔒 placeholders, never empty gaps.
5. Approve buttons only work for authorized roles. The backend also checks, but the UI must not invite the action.
6. No medical advice anywhere, including placeholder or demo text.

## 7. Priority if time runs short
1. Sidebar user switcher + Intake + Case (Overview/Recommendation) + confidence + citations
2. Approval (Handoff Packet) + Audit Log
3. Dashboard tiles + Knowledge Hub (pages + lint)
4. Assistant panel
5. *(after midnight)* Gap Radar, queue aging, context graph, Knowledge PR diff, Communications, polish
