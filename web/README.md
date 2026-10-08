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
Nav: Dashboard · New request · Cases · Patients · Knowledge · Audit. The old `approval.html` and `comms.html` redirect (to the case's Decide panel and to
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
| **S8** | Asha -> New request -> chip "S8 · Health ID" -> Submit: answered, "Patient CG-XXXX-XXXX-nnnn · linked"; click it: the patient timeline lists the case. Chip "S8 · Wrong ID": "Need 1 more detail" asking her to re-check the Health ID, not linked. Rahul -> Patients -> open -> Reveal personal details (reason required); Arjun -> Patients: access log. |
| **How-to** | Asha -> New request -> "How do I change a provider's billing address?": numbered steps from WF-03, cites WF-03 and KA-12. Every case page shows Summary, "How this is handled" (steps done / now / next) and "Why this decision" (each claim and its source; Open scrolls the Knowledge page to the section). |

## Tests

`tests/test_api.py` (API, RBAC, no raw text stored, S1-S7), `tests/test_web_e2e.py` (the click paths above in real Chrome via Playwright, plus
"no request leaves 127.0.0.1"; skipped when Playwright or Chrome is missing: `python -m pip install playwright`), and the API smoke step of
`python -m caregrid.cli check`.
