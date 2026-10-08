# web/ - the CareGrid web UI

Static pages (Tailwind, vanilla JS) served by the API process and wired to the REAL backend. No data and no business logic in the browser:
`api.js` only calls `/api/*`; `shell.js` draws the sidebar and small presentation helpers. The design comes from the teammate's HTML export
(glass cards, tokens, sidebar); everything fake in it (see `docs/UI_AUDIT.md`) was removed.

```bash
python -m caregrid.cli reset      # clean demo state
python -m caregrid.cli serve      # http://127.0.0.1:8000/   (API docs: /api/docs)
```

Every page: user switcher (sidebar, "Acting as"), LLM provider indicator, Reset demo (with confirmation). The acting user is sent as the header
`X-CareGrid-User` (demo authentication, NOT real login); the server enforces RBAC on every endpoint and runs every text field through
`check_output(text, viewer)`.

Offline note: Tailwind (3.4.17) and vis-network are vendored in `web/vendor/`. Inter, JetBrains Mono and the Material Symbols icons load from
Google Fonts; without a network the page still works with fallback fonts and icon names as text.

## Click paths (users: Asha U1 ops, Vikram U2 ENROLL, Neha U3 manager, Rahul U4 senior, Meera U5 knowledge owner, Arjun U6 auditor, Kiran U7 IT)

| | Path |
|---|---|
| **S1** | Asha -> New Request -> chip "S1 policy question" -> Submit. Green "Answered automatically", cites KA-02, band HIGH, "Trust level 1 - audited". The box is cleared. |
| **S2** | Asha -> chip "S2 missing info" -> Submit. ONE numbered message with 3 asks, purple chips of what was masked, team TEAM-ENROLL. As Vikram, Open case -> Recommendation tab: the notes include the stale P-88 / KA-12 v2 warning. |
| **S3** | Asha -> chip "S3 conflict" -> Submit: "Sent for human review -> TEAM-IT" with POLICY_CONFLICT. Switch to Kiran -> Case -> Recommendation: red conflict KA-31 vs KA-32, "Band capped at Medium". |
| **S4** | Asha -> chip "S4a clinical" (refused, TEAM-CLINICAL, no advice) and chip "S4b injection" (refused, ACCESS_DENIED + SENSITIVE, TEAM-COMPLIANCE). Audit Log (Arjun) shows `guard_blocked`. |
| **S5** | Rahul -> Case -> CASE-1024 -> assistant chip "Why is this case flagged?" (cites [KA-40] [INV-1024]) -> Open approval -> tick Email + WhatsApp, enter a contact e-mail -> Submit decision: state path ... approved -> actioned -> notified, precedent id, trust change. Communications lists both (simulated). Switch to Asha -> Case CASE-1024: amount hidden, Evidence rows locked, the assistant answers "ACCESS RESTRICTED" to "What is the amount?", Approval is disabled with the reason. |
| **S6** | Asha submits chip "S6 name change" (confidence 60 Medium). Vikram -> Approval -> Submit decision (precedent saved). Asha submits "Provider NPI 1098765437 legally changed name from Arun Pillai to Arun Menon, bank letter attached." -> 75 HIGH citing the new precedent. Dashboard -> Trust ladder shows the streak. |
| **S7** | Asha submits the S3 chip. Kiran -> Approval -> tick "Propose a Knowledge PR", policy KA-32, tick "Retire this policy", add a note -> Submit ("Knowledge PR opened"). Meera -> Knowledge Hub -> Knowledge PRs -> read the change -> Approve -> Lint report: the KA-31/KA-32 CONTRADICTION is gone. Asha submits S3 again: no POLICY_CONFLICT. |

## Tests

`tests/test_api.py` (API, RBAC, no raw text stored, S1-S7), `tests/test_web_e2e.py` (the click paths above in real Chrome via Playwright; skipped
when Playwright or Chrome is missing: `python -m pip install playwright`, it uses the installed Chrome), and the API smoke step of
`python -m caregrid.cli check`.
