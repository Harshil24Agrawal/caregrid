# web/ - the CareGrid web UI

Static pages (own CSS in `styles.css`, vanilla JS) served by the API process and wired to the REAL backend. No data and no business logic in the
browser: `api.js` only calls `/api/*`; `shell.js` draws the header and small presentation helpers. Nothing is loaded from the network (system
fonts, no icon font, vis-network vendored in `web/vendor/`), so it works offline.

```bash
python -m caregrid.cli reset      # clean demo state
python -m caregrid.cli serve      # http://127.0.0.1:8000/   (API docs: /api/docs)
python scripts/web_screenshots.py # full-page screenshots as Asha, Rahul and Meera -> docs/screenshots/
```

Design: navy header, white cards on light grey, one blue for actions. Colour carries meaning only: green = ok / approved / auto,
amber = needs review / medium, red = critical / conflict / refused, grey = info / stale. Reason codes appear in plain words with the code
in a tooltip ("Policies disagree" = POLICY_CONFLICT).

Header on every page: provider pill, PHI masked count (masked tokens such as [PERSON_1], [NPI], [PHONE] in the cases you can see; computed by the API, values never stored), demo login
switcher (the acting user is sent as the `X-CareGrid-User` header; NOT real authentication) and, for ops managers and senior reviewers in demo mode only, Reset demo (with confirmation).
Nav: Dashboard · New request · Cases · Patients · Knowledge · Audit (+ Demo in demo mode). The old `approval.html` and `comms.html` redirect (to the case's Decide panel and to
Audit > Messages).

| Page | The one question it answers |
|---|---|
| Dashboard | What needs me right now? A role-aware banner (requester: my requests; approvers: only what they can approve; knowledge owner: conflicts, gaps and policy updates; auditor: blocked or denied events), 4 KPIs, trust ladder, my queue, needs-attention cards; the pipeline below. (Evaluation and gap radar were removed from the page; `/api/scorecard` and `/api/metrics` still serve them.) |
| New request | What happens to my request? One result card with a headline per state. |
| Cases | What is going on with this case? List, or one case: why a human, risk, confidence, sources, next step, Decide panel, assistant, evidence / graph / audit / messages tabs. |
| Patients | Who is this patient, and what happened to their requests? Masked Health ID, plan, consent, a role-filtered timeline; senior reviewers may reveal name, phone and date of birth for one linked case with a reason (audited); auditors see the access log. |
| Knowledge | What does the Second Brain say, and what is wrong with it? Pages with versions (compare), needs attention (lint) and policy updates (PRs); the last two are for knowledge admins only. |
| Audit | What happened, and who did it? Log with plain-word events; Messages tab with "simulated" tags. |

## Click paths (users: Asha ops employee, Vikram Enrollment specialist, Neha ops manager, Rahul senior reviewer, Meera knowledge owner, Arjun auditor, Kiran IT specialist)

| | Path |
|---|---|
| **S1** | Asha -> New request -> chip "S1 · Policy question" -> Submit: "Answered automatically", cites KA-02, confidence High. The box is cleared. |
| **S2** | Asha -> chip "S2 · Missing details" -> Submit: "Need 3 more details" (numbered), masked chips, sources. |
| **S3** | Asha -> chip "S3 · Policies disagree" -> Submit: "Sent to IT Service Desk for review". Switch to Kiran -> Open case: red "Policies disagree" (KA-31 vs KA-32), "Capped at Medium". |
| **S4** | Asha -> type the insulin question: "Refused: medical question -> Clinical Review". Type the "Ignore previous instructions..." text: "Refused: access denied -> Compliance & Privacy". |
| **S5** | Rahul -> Cases -> CASE-1024 -> assistant "Why is this case flagged?" (cites KA-40 and INV-1024) -> Decide: More options -> tick Email + WhatsApp, enter a contact e-mail -> Submit decision (state path, precedent, trust). Audit > Messages lists both (simulated). Switch to Asha -> same case: amount hidden, evidence locked, "What is the amount?" answers ACCESS RESTRICTED, Decide explains why she can't. |
| **S6** | Asha submits the name-change request (confidence 60). Vikram -> case -> Decide -> Submit. Asha submits the second name change -> 75, citing the new precedent in Sources. Dashboard -> trust ladder shows the streak. |
| **S7** | Asha submits S3. Kiran -> case -> Decide -> More options -> "Propose a change to a policy", KA-32, "Retire this policy" -> Submit. Meera -> Knowledge -> Policy updates -> Approve. Needs attention is now clear; Asha submits S3 again: no "Policies disagree". |
| **S8** | Rahul (sample IDs are offered to ops managers and senior reviewers in demo mode) -> New request -> chip "S8 · Health ID" -> Submit: answered, "Patient CG-XXXX-XXXX-nnnn · linked"; click it: the patient timeline lists the case (an ops employee only sees the masked ID that was typed, never whether it matched). Chip "S8 · Wrong ID": "Need 1 more detail" asking to re-check the Health ID, not linked. Patients -> open -> Reveal personal details (a real reason of 3+ words, 5 per hour); Arjun -> Patients: access log. |
| **How-to** | Asha -> New request -> "How do I change a provider's billing address?": numbered steps from WF-03, cites WF-03 and KA-12. Every case page shows Summary, "How this is handled" (steps done / now / next) and "Why this decision" (each claim and its source; Open scrolls the Knowledge page to the section). |

## Tests

`tests/test_api.py` (API, RBAC, no raw text stored, S1-S7), `tests/test_web_e2e.py` (the click paths above in real Chrome via Playwright, plus
"no request leaves 127.0.0.1"; skipped when Playwright or Chrome is missing: `python -m pip install playwright`), and the API smoke step of
`python -m caregrid.cli check`.

## The case page is a story (demo-v5)

Top to bottom, all built by the server from the stored case and the Second Brain (`insights/story.py`, read-only; amounts go through `check_output` for the viewer):
1. header: id, type, state, risk, age;
2. **The problem**: one or two plain sentences ("Asha asked to order an oxygen concentrator (E1390) for a member. Cost is ₹62,500, above the ₹50,000 limit.");
3. **What CareGrid checked**: 3-6 rows with ✓ / ✗ / ⚠ (required details, policy found, past cases, conflicts, stale warnings, medical / sensitive flags);
4. **Decision**: one sentence, the team, who must approve, the confidence pill;
5. **Next steps**: numbered, different for each role (your own action first, then what happens after; a needs-info case lists exactly what to ask for; a refused case says where it went and why).
The Decide panel and the Assistant stay on the right. **Why this decision** (each claim and its source) and **Details** (How this is handled, Evidence, Graph, Audit timeline, Messages) are collapsed. Sections 2-5 fit one 1440x900 screen (browser-tested for CASE-1024, a needs-info case, a refused case and an answered case).

**Cases list:** Case · Problem · Status · With · Next step · Age, with the filters All / Needs my action / Waiting / Done.

**Demo guide** (nav item "Demo", shown only when `DEMO_MODE=1`; `GET /api/demo/guide`): one card per scenario in demo order (S1, how-to, S2, CASE-1024 as Rahul then as Asha, S6, S7, S8, S4). Each button switches the demo login and either fills New request with the exact test text (not submitted) or opens the case / page. The S8 text holds a synthetic Health ID and is given only to an ops manager or senior reviewer, so that card's button switches to Rahul first. "Reset demo" at the top is shown to the allowed roles only.

## The flow since demo-v6

1. A request that a person must decide, with no safety override, stops at "Ready to send to <team>": the requester opens the case and either **sends it** (optional masked note) or **withdraws** it. Safety cases (medical, injection, sensitive, account-specific) go to review at once, shown as "Sent automatically for safety". Auto-answered cases are unchanged.
2. A case that needs details shows the requester an **Add missing details** form with exactly the missing / invalid fields (a drop-down for the supporting document). The values are guarded and masked like a new request, the case is re-checked, and only what is still missing is asked again.
3. Reviewers see **Forwarded by Asha · time** and her note on the case; the **Timeline** card tells the story from the audit log (last three entries, expandable), including alerts ("Alert emailed to Senior Ops on-call", or "simulated").
4. Click paths that changed: **S2** Asha submits, opens the case, fills the form (NPI 1234567890, date, document), sends it. **S3 / S6 / S7** Asha opens her case and clicks "Send to ..." before the reviewer can decide (Cases -> filter "Needs my action"). **S5** CASE-1024 is already forwarded by Asha ("Vendor quote attached...").
5. Deploy: see `docs/DEPLOY.md`.
