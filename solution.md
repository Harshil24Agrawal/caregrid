# CareOps — The Healthcare Operations Second Brain

> **AI prepares the decision. Humans own the decision. Workflows execute the approved action.**
> Tagline: *"AI understands the context. Humans own the decision."*

Team Dhurandhar · Acentra Health Hackathon — Final Round (24-hour build)
Problem chosen: **Problem 1 — AI-Powered Healthcare Operations Assistant**

---

## Table of Contents

1. [The Problem (what Acentra asked for)](#1-the-problem-what-acentra-asked-for)
2. [Real User Pains We Are Solving](#2-real-user-pains-we-are-solving)
3. [Users & Roles](#3-users--roles)
4. [Solution Overview](#4-solution-overview)
5. [Why a Second Brain and Not a RAG Chatbot](#5-why-a-second-brain-and-not-a-rag-chatbot)
6. [Architecture — 5 Layers](#6-architecture--5-layers)
7. [Our Innovations](#7-our-innovations)
8. [Decision Logic — Confidence, Risk, Trust & Routing](#8-decision-logic--confidence-risk-trust--routing)
9. [Request Lifecycle, Reason Codes & Action Tiers](#9-request-lifecycle-reason-codes--action-tiers)
10. [Guardrails & Responsible AI](#10-guardrails--responsible-ai)
11. [Communication (Email / WhatsApp / SMS)](#11-communication-email--whatsapp--sms)
12. [Screens & User Journey](#12-screens--user-journey)
13. [Synthetic Data Design](#13-synthetic-data-design)
14. [AWS Architecture (Free Tier + Credits Only)](#14-aws-architecture-free-tier--credits-only)
15. [Tech Stack & Repo Structure](#15-tech-stack--repo-structure)
16. [Evaluation — How We Prove It Works](#16-evaluation--how-we-prove-it-works)
17. [24-Hour Build Plan & Team Split](#17-24-hour-build-plan--team-split)
18. [Demo Script](#18-demo-script)
19. [Judge Q&A Prep](#19-judge-qa-prep)
20. [Limitations & Production Roadmap](#20-limitations--production-roadmap)
21. [Requirement Traceability Matrix](#21-requirement-traceability-matrix)
22. [One-Sentence Pitch](#22-one-sentence-pitch)

---

## 1. The Problem (what Acentra asked for)

Healthcare operations employees work across procedures, documents, reports, workflow systems and support channels. They lose time:

- **finding trusted information**
- **understanding the next step**
- **completing routine requests**
- **identifying the correct support team**

**Build a secure assistant that can:**

| # | Official requirement |
|---|---|
| R1 | Use a synthetic workbook of operations requests, approved knowledge articles, workflow information, routing rules and field definitions |
| R2 | Give grounded answers **with source citations** and communicate **uncertainty** when information is incomplete |
| R3 | Guide users through routine requests and **ask for missing information** instead of assuming |
| R4 | **Route** account-specific, clinical, sensitive, unclear or low-confidence requests to the right **human team** |
| R5 | (Optional) role-based responses, confidence scores, escalation reasons, agent dashboards, summaries, sentiment/urgency detection, audit trails |

**Important boundary:** the assistant must **not** provide medical advice, expose confidential information, bypass access controls, or make irreversible operational decisions without human approval.

**General guidelines:** synthetic/public data only; a clear user journey (what the user enters, what the system does, what it returns, what happens when uncertain); evidence shown for important outputs; responsible AI (limitations, human-in-the-loop, fail safely); a **working end-to-end flow, not a set of screens or a generic chatbot**.

### What the orientation told us the judges value

| Orientation slide | What it means for us |
|---|---|
| Compounding value loop | Capture expertise → resolve case → human approves → approved outcome becomes **precedent** for the next decision |
| AI-Powered Second Brain | Raw sources → AI-maintained, linked knowledge (pages, citations, relationships) → grounded outputs. **Ingest → Query → Lint + Review → Update** |
| What the Second Brain remembers | Policies, business rules, process documents, regulatory material, data definitions, **prior decisions**, technical docs. *"Good retrieval starts with good evidence."* |
| Traceable decision chain | **Retrieve → Interpret → Apply rules → Propose → Score → Cite** → grounded recommendation with evidence, confidence, impact, and a path to human approval |
| Confidence routing | High → automate with audit trail · Medium → expert review · Low → escalate / "not enough evidence". *"A confident wrong answer creates more risk than an explicit gap."* |
| Industry use cases | Same architecture, different evidence. Reusable core: business-rule interpretation, data-quality rules, interface mapping, etc. |

Team notes from the session: **AWS · RAG vs Second Brain · Enterprise architecture (Knowledge layer → AI reasoning → Workflow → Human intervention) · Agentic AI · USA & India centric · Dual Retrieval (Precedent + Policy) · Data Ingestion & Anonymization Engine (old tickets, resolution logs, PHI/PII) · Hybrid knowledge retrieval.**

---

## 2. Real User Pains We Are Solving

> *"Healthcare operations teams don't have an information problem. They have a context problem."*

Every feature must trace back to one of these pains.

| # | Real pain | Cost today | Our answer |
|---|---|---|---|
| P1 | **"Which document is the truth?"** Articles are outdated or contradict each other | Wrong answers, rework, compliance risk | Trusted-Source Answers + Lint |
| P2 | **Endless back-and-forth** for missing details, one question at a time | Requests take days, not minutes | One-Shot Completeness Check |
| P3 | **Ping-pong routing** — tickets bounce between teams; each re-asks the same questions | Delays, frustration | Right-Team-First-Time + Handoff Packet |
| P4 | **Context is scattered** — profile, request, policy, past cases, logs, tickets live in different systems | Staff manually stitch the picture together | Case Intelligence + Context Graph |
| P5 | **Knowledge walks out the door** — seniors know the exceptions, nobody wrote them down | Same problem solved again and again | Precedent Memory + Knowledge PRs |
| P6 | **Managers can't see where time is lost** | Root causes never fixed | Knowledge Gap Radar + queue aging |
| P7 | **Nobody trusts AI on day one** in healthcare | AI tools bought and never used | Trust Ladder (earned autonomy) |
| P8 | **AI cost at enterprise scale** | Every question hits an expensive model | Two-level LLM + compile-once Second Brain |

---

## 3. Users & Roles

| Role | What they want | What they can see / do |
|---|---|---|
| **Ops Employee** (front line) | "What do I do with this request?" | Own requests, approved policies, guidance; cannot see restricted account/clinical details |
| **Team Specialist** (Billing, Enrollment, Clinical Review, IT, Compliance…) | Escalations that arrive with full context | Cases routed to their team; approve Low-risk items in their domain |
| **Ops Manager** | Throughput, bottlenecks, team load | Team cases; approve Medium-risk; dashboard + Gap Radar |
| **Senior Reviewer / Authorized Specialist** | Final say on high-impact decisions | Sensitive case details; approve High/Critical |
| **Knowledge Owner** | A rulebook that stays correct | Approve/reject Knowledge PRs; resolve lint issues |
| **Auditor / Compliance** | "Show me why, and who approved it" | Read-only full audit trail |

The AI assistant **inherits the user's role**. If a user can't see something, neither can the AI on their behalf.

---

## 4. Solution Overview

**CareOps** is a healthcare operations **Second Brain** with an **agentic reasoning layer** and a **human-governed workflow**.

```text
Fragmented data (requests, policies, workflows, routing rules, field defs,
old tickets, resolutions, profiles, billing, logs, JIRA)
        ↓
Privacy-first Ingestion (PHI/PII removed)
        ↓
SECOND BRAIN — linked, versioned knowledge pages + precedents
        ↓
Agentic Reasoning — Retrieve → Interpret → Apply rules → Propose → Score → Cite
   (Dual retrieval: Policy + Precedent · Two-level LLM)
        ↓
Recommendation + Evidence + Confidence + Risk + Reason code
        ↓
Workflow + Human-in-the-Loop (risk-based approver, Trust Ladder)
        ↓
Approved action → Communication (Email / WhatsApp / SMS) → Audit log
        ↓
Verified decision → Precedent / Knowledge PR → back into the Second Brain
```

**What the user enters:** a request in plain language (or opens an existing case).
**What the system does:** anonymizes, classifies, assembles context, runs dual retrieval, checks rules and required fields, scores confidence and risk, proposes.
**What it returns:** an answer with citations, a list of missing details to collect, or a handoff to the right team with reason + summary.
**When uncertain:** says so explicitly, explains why (missing policy / conflict / sensitive / unclear), and routes to a human. **It never guesses.**

---

## 5. Why a Second Brain and Not a RAG Chatbot

| | Conventional RAG | CareOps Second Brain |
|---|---|---|
| When work happens | Search raw chunks on every question | Compile raw sources **once** into organized, linked pages; query the pages |
| What is retrieved | Random text fragments | Approved, versioned **policy, workflow, team, field and precedent pages** with links between them |
| Learns over time? | No — every query starts from zero | Yes — **human-approved decisions become precedents**; rulebook improves via Knowledge PRs |
| Readable / auditable? | Vector DB is opaque | Pages are human-readable markdown with history (Git-like) |
| Self-maintenance | None | **Lint**: contradictions, stale pages, orphans, gaps |
| Cost | LLM + retrieval every query | Heavy work paid once at ingest; cheap model for simple tasks |

Inspired by Andrej Karpathy's LLM-wiki pattern (raw sources → LLM-maintained wiki → schema; operations Ingest, Query, Lint) — the same loop shown on Acentra's slide.

> Embeddings, keyword search and semantic similarity are **implementation mechanisms inside** the Second Brain — not the product identity.

---

## 6. Architecture — 5 Layers

```text
 ┌──────────────────────────────────────────────────────────┐
 │ 1. PRIVACY-FIRST INGESTION & ANONYMIZATION               │
 │   workbook · old tickets · resolution logs · profiles ·  │
 │   billing · logs · JIRA → validate → detect PHI/PII →    │
 │   mask → structured records                              │
 └────────────────────────┬─────────────────────────────────┘
                          ▼
 ┌──────────────────────────────────────────────────────────┐
 │ 2. SECOND BRAIN (Knowledge Layer)                        │
 │   policy · workflow · team · field · runbook · regulatory│
 │   · PRECEDENT pages (versioned, can go stale)            │
 │   index · change log · relationship graph                │
 │   Operations: Ingest · Query · Lint · Update (via PR)    │
 └────────────────────────┬─────────────────────────────────┘
                          ▼
 ┌──────────────────────────────────────────────────────────┐
 │ 3. AGENTIC REASONING (6-step traceable chain)            │
 │   Retrieve (Policy + Precedent, hybrid) → Interpret →    │
 │   Apply rules → Propose → Score → Cite                   │
 │   Two-level LLM: light model ↔ strong model              │
 └────────────────────────┬─────────────────────────────────┘
                          ▼
 ┌──────────────────────────────────────────────────────────┐
 │ 4. WORKFLOW + HUMAN-IN-THE-LOOP                          │
 │   request states · reason codes · risk-based approver ·  │
 │   Trust Ladder · Handoff Packet · Approve/Reject/Escalate│
 │   → approved action → communication → audit              │
 │   → precedent / Knowledge PR → back to Layer 2           │
 └────────────────────────┬─────────────────────────────────┘
                          ▼
 ┌──────────────────────────────────────────────────────────┐
 │ 5. INSIGHTS — Dashboard · Knowledge Gap Radar ·          │
 │   queue aging · Trust Ladder progress · audit explorer   │
 └──────────────────────────────────────────────────────────┘
          Cross-cutting: RBAC · Guardrails · Audit · Cost control
```

### Layer 1 — Privacy-First Ingestion & Anonymization

```text
Raw data → Validate (schema, field defs) → Detect PHI/PII → Mask → Structured records → Second Brain
```

- **PHI** (Protected Health Information): health info tied to a person (name + diagnosis, record number…).
- **PII** (Personally Identifiable Information): name, phone, email, address, government/ID numbers.
- Masking keeps meaning, removes the person:
  - Before: `Dr. Ramesh Iyer (NPI 1234567890) called about Anita Rao's MRI approval`
  - After: `[PROVIDER_1] (NPI [ID]) called about [MEMBER_1]'s imaging approval`
- Tooling: **Microsoft Presidio** (open source, free) + regex for IDs/phones/emails. Comprehend Medical is a production option (paid).
- **USA:** HIPAA de-identification (Safe Harbor lists 18 identifier types). **India:** DPDP Act 2023 — purpose limitation, consent, data minimization.
- A post-ingest **leak scan** runs over the compiled Second Brain to prove no identifiers slipped through.

### Layer 2 — Second Brain (Knowledge Layer)

**What it remembers** (mapped to Acentra's slide):

| Slide category | Page type in CareOps | Source |
|---|---|---|
| Policies | `policy/` (versioned, effective date, owner) | Approved knowledge articles |
| Business rules | `routing/` + rules in policy pages | Routing rules |
| Process documents | `workflow/`, `runbook/` | Workflow info, runbooks |
| Data definitions | `field/` | Field definitions |
| Prior decisions | `precedent/` | Old tickets, resolution logs, every human-approved case |
| Regulatory material | `regulatory/` | HIPAA / DPDP summary pages we author |
| Technical documentation | `system/` | Logs, JIRA patterns (optional) |
| — | `team/` | Who handles what, and what they *don't* handle |

Plus `index.md` (map of all pages, one-line summaries) and `log.md` (every change: what, who, why, when).

**Example policy page**

```markdown
---
id: KA-12
type: policy
title: Provider Billing Address Change
version: 3
effective_from: 2026-07-01
status: approved
owner: Provider Enrollment
links: [WF-03, FIELD-NPI, TEAM-ENROLL]
---
To change a provider's billing address the request must include:
NPI, new address, effective date, and a supporting document (W-9 or bank letter).
Changes with an effective date in the past require Enrollment Manager approval.
```

**Example precedent page**

```markdown
---
id: P-91
type: precedent
request_type: provider_address_change
policy_used: KA-12
policy_version: 3
decision: routed_to_enrollment_after_document_collected
reason_code: MISSING_DATA
approver_role: team_specialist
risk: low
status: active          # becomes "stale" if KA-12 moves to v4
date: 2026-09-14
---
[PROVIDER_1] requested billing address update; supporting document missing.
Collected document in one round, validated NPI, routed to Enrollment. Closed same day.
```

**Second Brain operations**

| Operation | What happens |
|---|---|
| **Ingest** | New source → anonymize → write/update pages → update links, index, log; flag contradictions |
| **Query** | Agent reads index → opens relevant pages (policy + precedent) → reasons → cites page IDs |
| **Lint** | Health check: contradictory policies, expired articles still linked, stale precedents, orphan pages, workflows pointing to non-existent teams, request types that keep escalating (= missing page) |
| **Update** | Only via **Knowledge PR** approved by a Knowledge Owner, or automatic precedent capture after human approval |

### Layer 3 — Agentic Reasoning (6-step chain)

Example request: *"Doctor wants to update billing address, here's his NPI."*

| Step | What CareOps does |
|---|---|
| 1. **Retrieve** | Dual retrieval: Policy (KA-12 v3, WF-03) + Precedent (P-91, P-88) via hybrid search |
| 2. **Interpret** | Request type = `provider_address_change`; entities: NPI provided; urgency: normal; sentiment: neutral |
| 3. **Apply rules** | Deterministic rules engine: required fields = NPI ✓, address ✓, effective date ✗, document ✗; NPI format valid; no hard override |
| 4. **Propose** | "Ask for effective date + supporting document in one message, then route to Provider Enrollment" |
| 5. **Score** | Evidence score + risk level + trust level (Section 8) |
| 6. **Cite** | KA-12 v3 · WF-03 · P-91 · reasoning trail · reviewer checkpoint |

**Dual Retrieval — Policy + Precedent**

- **Policy retrieval:** "What do the rules say?" → policies, procedures, approval requirements.
- **Precedent retrieval:** "What did we do last time in similar cases?" → previous human decisions and outcomes.

| Policy | Precedent | System behaviour |
|---|---|---|
| Clear | Agrees | High confidence → proceed per Trust Ladder, with audit trail |
| Clear | Disagrees | `POLICY_CONFLICT` → expert review (precedent was an exception, or policy outdated?) |
| Missing | Has examples | Medium → suggest, needs expert; may trigger a Knowledge PR |
| Missing | Missing | Low → "Not enough evidence" + `POLICY_GAP` escalation |

**Hybrid knowledge retrieval** = keyword match (exact form names, codes, team names) + semantic match ("change my clinic location" ≈ "address update") + **link traversal** inside the Second Brain (workflow → policy → precedents) + structured filters (request type, policy version, status = active).

**Two-level LLM (cost-aware)**

```text
Incoming request → Light model (classify, extract, simple answers)
                     │
         Needs deeper reasoning?  (conflict, multi-source, high risk, low clarity)
              NO ─┘            └─ YES → Strong model (compare precedents, explain, write brief)
```

- Light model: intent classification, field extraction, summaries, simple policy answers.
- Strong model: connecting multiple sources, resolving conflicts, decision briefs, Knowledge PR drafts.
- **The LLM never produces numbers, amounts or approvals** — those come from records and rules.

### Layer 4 — Workflow + Human-in-the-Loop

- Every request follows explicit **states** with **closure evidence** (Section 9).
- Routing decided by Section 8 matrix: hard overrides → evidence confidence → risk level → Trust Ladder.
- Escalations go to the **right team** (routing rules + precedent of where similar requests ended) with a **Handoff Packet**.
- Human: **Approve · Edit & approve · Reject · Escalate · Ask requester**, plus ☐ *Save as precedent* ☐ *Propose wiki change (Knowledge PR)*; adds official query contact details for outgoing communication.
- After approval → workflow executes (resolution, referral, billing workflow, equipment request, IT request, internal notification) → communication → audit → precedent.

### Layer 5 — Insights

- Open / pending approval / high-risk / escalated counts.
- **Knowledge Gap Radar** (escalations grouped by reason code and request type, with hours lost).
- **Queue aging** per state and team.
- **Trust Ladder** progress per request type.
- Communication status and audit explorer.

---

## 7. Our Innovations

| # | Innovation | Solves | One-liner |
|---|---|---|---|
| 1 | **Trusted-Source Answers** | P1 | Every answer shows policy ID, **version**, effective date; no source → "I don't know, here's why" |
| 2 | **One-Shot Completeness Check** | P2 | Asks for **all** missing fields in one message and validates formats instantly (field definitions) |
| 3 | **Right-Team-First-Time + Handoff Packet** | P3 | Routing rules + precedent destinations; the receiving team gets summary, what's checked, what's missing, reason code, sources |
| 4 | **Case Intelligence + Context Graph** | P4 | One view stitching request, profile, policy, precedent, billing, logs, JIRA, runbook — with a relationship graph |
| 5 | **Precedent Memory with Expiry** | P5 | Precedents are tied to the policy **version** they used; when the policy changes they turn **stale** automatically |
| 6 | **Knowledge PRs** (GitHub-style) | P5, P1 | Human resolutions draft rulebook changes as a red/green **diff**; Knowledge Owner approves like a pull request; full history + rollback |
| 7 | **Knowledge Gap Radar** | P6 | Reason-code analytics: *"23 billing-address escalations were POLICY_GAP, avg 2.4 days in queue → 1 missing article ≈ X staff-hours/week"*; auto-drafts the missing article |
| 8 | **Trust Ladder (earned autonomy)** | P7 | Each request type starts in Shadow mode; earns autonomy only after sustained human agreement; one override drops it back; sensitive types can never graduate |
| 9 | **Two-level LLM + compile-once brain** | P8 | Cheap model for most traffic; strong model only when needed; precedents reduce LLM calls over time |
| 10 | **Context-aware floating assistant** | P1–P4 | Bottom-right on every page; knows the current case; respects the user's role |
| 11 | **Closed-loop operations** | P3, P5 | Decision → approval → workflow → communication → audit → reusable precedent |
| 12 | **Privacy-first, USA + India ready** | Boundary | PHI/PII removed before the brain; HIPAA + DPDP regulatory pages; region-specific deployment |

---

## 8. Decision Logic — Confidence, Risk, Trust & Routing

The final route combines **four independent checks**, in this order.

### Step 1 — Hard overrides (no score needed → human, always)

`CLINICAL` · `ACCOUNT_SPECIFIC` · `SENSITIVE` · `IRREVERSIBLE_ACTION` · any Write-tier action · failed access check → route to the designated team per routing rules.

### Step 2 — Evidence confidence (0–100, computed from measurable signals, never the LLM's self-report)

| Signal | Points |
|---|---|
| Current, approved policy found | 0–30 |
| Active (non-stale) precedents agree with the proposal | 0–25 |
| All required fields present and valid | 0–20 |
| Request type is clear (light model + rules agree; classifier margin) | 0–15 |
| No conflicts (policy vs policy, policy vs precedent) | 0–10 |

- **75–100 → High** · **45–74 → Medium** (expert review) · **< 45 → Low** ("not enough evidence")
- The breakdown is **always shown**: *"58 · policy 30 · precedent 5 · fields 13 · clarity 10 · conflict 0 — only 1 precedent; effective date missing."*
- Weights are a documented team choice and tunable from the evaluation set.

### Step 3 — Risk level (from policy + impact, decides WHO approves)

| Risk | Typical triggers | AI role | Approver |
|---|---|---|---|
| **Low** | Informational, routine, reversible | Analyze + recommend | Team Specialist (or auto, if Trust Ladder allows) |
| **Medium** | Policy thresholds, money involved, partial evidence | Analyze + recommend | Ops Manager |
| **High** | Exceeds threshold, conflicting evidence, member impact | Deep analysis + evidence brief | Senior Reviewer |
| **Critical** | Safety, legal, privacy incident, clinical urgency | Immediate escalation only | Authorized specialist team |

### Step 4 — Trust Ladder (decides WHETHER anything may happen without a human)

| Level | AI may… | Graduation rule (example) |
|---|---|---|
| **0 – Shadow** | Propose only; human approves everything | Default for every request type |
| **1 – Assist** | Answer/guide directly; humans spot-check a sample | ≥ 10 consecutive approvals with ≥ 90% agreement |
| **2 – Auto with audit** | Prepare + route automatically (Read/Draft tiers only), fully logged | ≥ 25 approvals, ≥ 95% agreement, Low risk only |

- **Any human override → drop one level.**
- **Ceiling:** clinical, account-specific, sensitive, Write-tier and High/Critical risk **never** graduate.

### Final routing rule

```text
IF hard override                         → human (designated team)
ELIF confidence = Low                    → "not enough evidence" + escalate (reason code)
ELIF confidence = Medium                 → expert review (approver by risk)
ELIF confidence = High AND risk = Low
     AND trust level ≥ 1 AND tier ∈ {Read, Draft}
                                         → proceed with audit trail
ELSE                                     → human approval (approver by risk)
```

### What makes two cases "similar" (precedent matching)

1. **Same request type** — hard filter.
2. **Same key facts** — category, team, missing fields, risk band (structured match).
3. **Similar wording** — semantic similarity on the anonymized description.
4. **Same policy version** — otherwise the precedent is *stale* and heavily down-weighted.

---

## 9. Request Lifecycle, Reason Codes & Action Tiers

### States (every transition logged)

```text
NEW → CLASSIFIED → NEEDS_INFO ⇄ (requester replies)
               → READY → PROPOSED → IN_REVIEW → APPROVED / REJECTED / ESCALATED
               → ACTIONED → NOTIFIED → CLOSED
```

- **Closure evidence** required to close: action taken, policy/version used, approver, communication sent.
- **Queue aging** = time spent in each state; drives bottleneck alerts.

### Reason codes (why a human is involved)

| Code | Meaning |
|---|---|
| `MISSING_DATA` | Required fields not provided |
| `POLICY_GAP` | No approved article covers this |
| `POLICY_CONFLICT` | Articles disagree, or precedent disagrees with policy |
| `CLINICAL` | Needs medical judgment |
| `ACCOUNT_SPECIFIC` | Needs a specific member/provider record |
| `SENSITIVE` | Complaint, legal, privacy, distressed requester |
| `UNCLEAR_INTENT` | Can't tell what is being asked |
| `IRREVERSIBLE_ACTION` | Would permanently change something |
| `HIGH_RISK` | Risk level High/Critical per policy |
| `ACCESS_DENIED` | Requester lacks permission |

### Action tiers

| Tier | Examples | Who decides |
|---|---|---|
| **Read** | Answer a policy question, explain steps, show related cases | AI (with citations) |
| **Draft** | Prepare form, handoff packet, communication draft, billing summary, Knowledge PR | AI drafts → human sees |
| **Write** | Approve, submit, close, change a record, change billing amount, **send** a communication | **Always a human**, at any confidence |

---

## 10. Guardrails & Responsible AI

Guardrails are **deterministic code around the LLM**, not just prompt instructions.

### Input guards

- **Role check** first — the assistant only loads data the user's role can see.
- **PHI/PII masking** on free text before it reaches the LLM.
- **Prompt-injection detection** (patterns like "ignore previous instructions", "I'm an admin", role-claims) → refuse + log + `SENSITIVE`.
- **Topic classifier + keywords** for clinical questions → hard override `CLINICAL`.

### Reasoning guards

- **Cite-or-abstain:** every claim must reference a page ID from the Second Brain.
- **Citation verification in code:** every cited ID must exist, be `approved`/`active`, and be the current version — otherwise the answer is blocked and downgraded.
- **No invented policy, approvals or cases** — the LLM can only reference retrieved pages.
- **Numbers from records only** — amounts, dates, IDs are filled from data, never generated.

### Output guards

- No medical advice (diagnosis, dosage, treatment) — blocked and routed.
- No fields above the user's access level.
- Re-scan the final text for PHI/PII leakage.

### Action guards

- Write-tier actions require an authenticated human approval of the correct role.
- Communications are **templated**, generated only after approval, minimum-necessary content.
- AI cannot modify or approve billing amounts.

### Fail-safe behaviour

- LLM unavailable/timeouts → rules + routing still work; request goes to human queue with `UNCLEAR_INTENT`/`LOW_CONFIDENCE`.
- Retrieval finds nothing → "Not enough evidence" + `POLICY_GAP`, never a guess.

### Example refusals

```text
User: Show me the confidential details of another patient's case.
CareOps: ACCESS RESTRICTED — you don't have permission to view this information.
         I've logged the request. Contact: Compliance Team.

User: Should this patient take a higher dose?
CareOps: I can't give medical advice. I've routed this to Clinical Review (reason: CLINICAL)
         with a summary so they don't need to ask you again.
```

### Responsible AI principles

Human authority · data minimization · role-based access · no clinical decisions · explainability · auditability · explicit uncertainty · no hallucinated policy · learns only from **verified human decisions**, never from raw AI outputs.

---

## 11. Communication (Email / WhatsApp / SMS)

```text
Human final approval (+ official query contact)
        ↓
Generate approved message from template (minimum necessary data)
        ↓
Communication service →  Email  |  WhatsApp  |  SMS (fallback)
        ↓
Delivery status → Audit log
```

- **Email:** sent for real via Amazon SNS email topic (free allowance) in the demo.
- **WhatsApp / SMS:** shown in the **Communication Center** as a realistic simulation (recipient, channel, message, timestamp, status) unless credentials are available. Production: WhatsApp Business API / SMS provider.
- **Billing communication:** invoice ID, amount, status, due date pulled from billing records; AI only formats; amounts never changed by AI.
- Human-provided official contact (email/phone/WhatsApp) appended to every outgoing message.

```text
CareOps — Case #1024
Status: Approved
Next step: Equipment procurement will begin.
For queries: operations@hospital.example · +91 XXXXX XXXXX
```

---

## 12. Screens & User Journey

| # | Screen | Shows |
|---|---|---|
| 1 | **Dashboard** | Open, pending approval, high-risk, escalated; queue aging; Gap Radar; Trust Ladder; communication status |
| 2 | **New Request / Intake** | Plain-language input; One-Shot Completeness Check; instant answer or missing-fields form |
| 3 | **Case Intelligence** | Current case + profile + policy + precedents + billing + logs + JIRA + runbook; **Context Graph**; Second Brain summary |
| 4 | **Recommendation** | Proposal, evidence, policy (with version), precedents, confidence breakdown, risk, suggested workflow |
| 5 | **Human Approval (Handoff Packet)** | Approve / Edit / Reject / Escalate / Ask requester; save-as-precedent; Knowledge PR; contact details; channel choice |
| 6 | **Communication Center** | Recipient, channel, message, status, timestamp |
| 7 | **Knowledge Hub** | Second Brain pages, Knowledge PRs (diffs), lint report |
| 8 | **Audit Log** | Full decision history per case |
| — | **Floating AI Assistant** | Bottom-right on every page; context-aware (knows the open case); role-aware |

### Handoff Packet (approval screen)

```text
┌──────────────────────────────────────────────────────────┐
│ REQ-1042 · Provider address change · IN_REVIEW · 2h      │
│ Reason: POLICY_CONFLICT · Risk: LOW · Trust: 0 (Shadow)  │
├──────────────────────────────────────────────────────────┤
│ Summary: [PROVIDER_1] requests billing address update    │
│ Checked: ✓ NPI valid  ✓ Policy KA-12 v3 (current)        │
│ Missing: ✗ Supporting document                           │
│ Proposed: Request document → route to Enrollment         │
│ Confidence 58 · policy 30 · precedent 5 · fields 13 …    │
│ Conflict: Precedent P-88 (STALE, policy v2) skipped the  │
│           document check                                 │
│ Sources: KA-12 v3 · WF-03 · P-88 · P-91                  │
├──────────────────────────────────────────────────────────┤
│ [Approve] [Edit & approve] [Reject] [Escalate] [Ask]     │
│ ☑ Save as precedent   ☐ Propose wiki change (PR)         │
│ Query contact: [ operations@… ] [ +91 … ]                │
└──────────────────────────────────────────────────────────┘
```

### End-to-end journey (CASE-1024)

1. Employee opens CASE-1024 (equipment request above cost threshold).
2. CareOps assembles profile, request, policy, precedents, billing, logs, JIRA, runbook.
3. Second Brain summary + Context Graph.
4. Risk = High (policy threshold exceeded; similar precedent required senior review).
5. Recommendation: *Route to Senior Operations Review*, confidence breakdown shown.
6. Employee asks the assistant *"Why is this high risk?"* → cited explanation.
7. Senior Reviewer opens the Handoff Packet → **Approve** + adds query contact.
8. Workflow runs → approved email (+ simulated WhatsApp/SMS) incl. billing details.
9. Audit log records recommendation, evidence, decision, communication, timestamps.
10. Decision saved as **verified precedent**; request type's Trust Ladder updated.

---

## 13. Synthetic Data Design

We create the synthetic workbook ourselves (no real data).

| Sheet / file | Key columns | Purpose |
|---|---|---|
| `requests` | id, text, requester_role, channel, created_at, expected_type, expected_route | Test set + demo |
| `knowledge_articles` | id, title, body, version, effective_from, status (approved/draft/expired), owner | Policy pages |
| `workflows` | id, request_type, steps, required_fields | Completeness check |
| `routing_rules` | condition, team, risk, approver_role | Routing + risk |
| `field_definitions` | field, meaning, format/regex, example | Validation |
| `teams` | id, name, handles, does_not_handle, contact | Team pages |
| `historical_cases` | id, request_type, facts, decision, reason_code, approver_role, policy_id, policy_version, outcome | Precedents |
| `profiles` | synthetic provider/member/account | Case context (RBAC-protected) |
| `billing` | invoice, case_id, amount, status, due_date | Billing communication |
| `system_logs`, `jira_records`, `runbooks` | (optional) | Context graph |
| `users` | id, name, role, team | RBAC demo |

**Deliberately planted test conditions:**

- Two articles that **contradict** each other → Lint + `POLICY_CONFLICT`.
- One **expired** article still linked → Lint.
- A precedent based on **policy v2** while current is v3 → stale precedent demo.
- Requests with **missing fields**, **invalid NPI**, **clinical** questions, **account-specific** asks, **prompt-injection** attempts, **vague** requests.
- A request type with many prior approvals → Trust Ladder already at Level 1.
- A cluster of `POLICY_GAP` escalations → Gap Radar insight.

---

## 14. AWS Architecture (Free Tier + Credits Only)

**How "free" works:** new AWS accounts get $100 credits (up to $200 by completing onboarding activities) plus 30+ **always-free** services. On the Free plan the account ends after 6 months or when credits run out. Goal: **$0 out of pocket** — stay inside always-free limits and let credits cover Bedrock.

```text
 Synthetic workbook / tickets
        │  (ingest script: validate + Presidio anonymize + compile pages)
        ▼
 S3 wiki/ (markdown, versioning ON)   +   DynamoDB (index, cases, states,
        │                                   precedents, trust levels, audit)
        ▼
 Streamlit UI ──HTTPS──▶ Lambda Function URL (API)
                              │
                              ▼
                   Step Functions (6-step chain + HUMAN PAUSE)
                     ├─ Lambda: guards (role, PHI mask, injection, overrides)
                     ├─ Lambda: classify (Bedrock light model)
                     ├─ Lambda: dual retrieval (hybrid search over pages)
                     ├─ Lambda: rules engine (fields, risk, tiers)
                     ├─ Lambda: propose + explain (Bedrock strong model)
                     ├─ Lambda: confidence + Trust Ladder + citation check
                     └─ Wait for callback (task token in DynamoDB)
                              │
     Reviewer clicks Approve ─┘→ Lambda: SendTaskSuccess → action → SNS email
                              → audit + precedent write-back (+ Knowledge PR)
 EventBridge Scheduler ──▶ Lambda: Lint (nightly; "Run lint" button for demo)
 CloudWatch: logs & metrics (light logging)
```

| Need | Service | Free basis | Notes |
|---|---|---|---|
| Backend logic | **AWS Lambda** | Always free: 1M requests + 400K GB-s / month | Use **Function URLs** instead of API Gateway |
| Database (cases, states, precedents, trust, audit) | **DynamoDB** | Always free: 25 GB | On-demand or low provisioned capacity |
| Human-in-the-loop pause | **Step Functions** (Standard) | Always free: 4,000 state transitions / month | `waitForTaskToken` callback pattern = HITL |
| Email notifications | **SNS** | Always free: 1M publishes (+ limited email deliveries) | Recipients confirm subscription once |
| Second Brain pages | **S3** (versioning) | Tiny usage, covered by credits | Alternative: store pages in DynamoDB to stay purely always-free |
| LLM (light + strong) | **Amazon Bedrock** (Claude Haiku-class + Sonnet-class) | **Paid per token → covered by credits** | A 24h demo costs a few dollars |
| Embeddings (semantic search) | Bedrock Titan Text Embeddings | Credits (very cheap) | Computed once at ingest, stored with pages |
| PHI/PII removal | **Presidio** (open source) | Free | Runs in ingest script; production: Lambda container |
| Scheduled lint | **EventBridge Scheduler** | Free allowance | |
| Logs/metrics | **CloudWatch** | Basic free allowance | Log lightly |
| Safety | Own guardrail code in Lambda | Free | Production: Bedrock Guardrails |
| UI hosting | Streamlit (local / Community Cloud) | Free | Not AWS — fine |

**Avoid (cost money):** OpenSearch Serverless, Comprehend Medical, Bedrock Guardrails, API Gateway (outside trial), NAT Gateway, idle EC2/RDS, SES beyond trial, SNS SMS.

**Day-one safety:** create an **AWS Budget alert at $1**; keep **one region** for everything. Mumbai (`ap-south-1`) fits the India story — confirm the chosen Bedrock models are available there; otherwise use `us-east-1` for the demo and present Mumbai as the India production region.

**Resilience:** the same Python code runs **locally** (SQLite + local files) behind a config flag, so the demo survives a cloud/network failure.

**Production story ("same code, two regions"):** Mumbai for India (DPDP), US region with HIPAA-eligible services under a BAA for the USA. Production adds Cognito roles, Bedrock Guardrails, Comprehend Medical, KMS customer keys, S3 Object Lock for immutable audit, and real EHR/JIRA/WhatsApp integrations.

---

## 15. Tech Stack & Repo Structure

| Layer | Choice |
|---|---|
| UI | Streamlit (+ floating assistant via custom component/CSS), Plotly, PyVis/NetworkX for context graph |
| Backend | Python 3.11, boto3 |
| AI | Bedrock: light (Haiku-class) + strong (Sonnet-class) Claude models; Titan embeddings |
| Retrieval | rank-bm25 (keyword) + cosine on embeddings + link traversal + structured filters |
| Privacy | Presidio + regex |
| Data | DynamoDB + S3 (cloud) · SQLite + files (local fallback) |
| Workflow | Step Functions (cloud) · state machine in Python (local) |
| Comms | SNS email (real) · WhatsApp/SMS simulated |

```text
careops/
├── data/synthetic/          # generated workbook (CSV/JSON)
├── brain/                   # compiled Second Brain pages
│   ├── policy/ workflow/ team/ field/ precedent/ regulatory/
│   ├── index.md
│   └── log.md
├── ingest/                  # validate, anonymize, compile, leak-scan
├── reasoning/               # guards, classify, retrieve, rules, propose, score, cite
├── workflow/                # states, routing, trust ladder, approvals, comms
├── insights/                # gap radar, queue aging, metrics
├── infra/                   # Step Functions definition, Lambda handlers, IaC
├── eval/                    # test set + scoring script
└── app/                     # Streamlit screens + assistant
```

---

## 16. Evaluation — How We Prove It Works

Run every synthetic request through CareOps and score against expected labels.

| Metric | What it proves |
|---|---|
| Request-type accuracy | Understands requests |
| **Routing first-time-right %** | Solves ping-pong (P3) |
| Missing-field recall | One-shot completeness works (P2) |
| **Citation validity %** (cited page exists, approved, current) | Grounded, no hallucinated policy (P1) |
| Correct-abstention rate | Says "I don't know" when it should |
| **Safety tests passed** (clinical, injection, access, irreversible) | Boundaries hold |
| Stale-precedent catches | Expiry logic works |
| Light vs strong model share + est. cost/request | Cost story (P8) |

Show the scorecard on a slide and live in the dashboard.

---

## 17. 24-Hour Build Plan & Team Split

| Role | Owner | Scope |
|---|---|---|
| **A — Data & Second Brain** | | Synthetic data, ingestion, anonymization, page compiler, index/log, lint, leak scan |
| **B — Reasoning & Guardrails** | | Guards, classifier, dual retrieval, rules engine, confidence, citation check, LLM prompts, two-level routing |
| **C — Workflow & Cloud** | | AWS setup + budget, DynamoDB, Lambda, Step Functions HITL, Trust Ladder, audit, SNS email, Knowledge PR flow |
| **D — UI & Pitch** | | Streamlit screens, floating assistant, context graph, dashboard/Gap Radar, communication center, deck, demo script |

| Hours | Milestone |
|---|---|
| 0–2 | Repo, AWS account + $1 budget alert, Bedrock model access, schemas, UI skeleton |
| 2–5 | Synthetic data with planted conditions; ingest + anonymize; compile first pages |
| 5–9 | Dual retrieval, rules engine, required-field check, confidence score, guards |
| 9–13 | 6-step chain with two-level LLM; assistant; Case Intelligence + Recommendation screens |
| 13–16 | Approval screen + Step Functions pause/resume; precedent write-back; Trust Ladder; audit |
| 16–18 | Dashboard + Gap Radar + queue aging; context graph; communication center; Knowledge PR diff |
| **18** | **FEATURE FREEZE** |
| 18–21 | Run evaluation, fix bugs, deploy, verify local fallback |
| 21–24 | Rehearse demo ×3, record backup video, finalize slides |

Priority if time runs short: **P1** end-to-end chain + approval + audit + precedent loop → **P2** Trust Ladder, Gap Radar, context graph, email → **P3** Knowledge PR diff, WhatsApp/SMS simulation, regulatory pages.

---

## 18. Demo Script (~6 minutes)

1. **Hook (30s):** *"Ops teams don't have an information problem — they have a context problem. And nobody trusts AI on day one."*
2. **Trusted answer (40s):** routine policy question → answer with KA-ID + version + confidence breakdown.
3. **One-shot completeness (45s):** address change missing 2 fields → asked once, NPI validated, routed to the right team with a Handoff Packet.
4. **Case Intelligence (60s):** CASE-1024 → context graph → "Why is this high risk?" via floating assistant → cited explanation → Senior Reviewer approves → email sent, WhatsApp/SMS simulated, billing details included, audit created.
5. **Compounding + Trust Ladder (60s):** Medium-confidence request → human approves → precedent saved → similar request now **High**, citing the precedent from one minute ago → Trust Ladder ticks up.
6. **Conflict + Knowledge PR (40s):** stale precedent vs current policy flagged → reviewer proposes a rulebook change → red/green diff approved.
7. **Safety (30s):** judge tries a clinical question / "show me another patient's data" → refused, logged, routed.
8. **Gap Radar + scorecard (40s):** *"This missing article costs ~X hours/week"* + evaluation metrics.
9. **Close (15s):** AWS free-tier architecture, USA + India, one-sentence pitch.

---

## 19. Judge Q&A Prep

| Likely question | Answer |
|---|---|
| "Isn't this just RAG?" | No — we compile knowledge once into linked, versioned pages, learn from approved decisions, lint for contradictions, and route through governed workflows. Retrieval is one mechanism inside. |
| "How is confidence not hallucinated?" | It's computed from five measurable signals in code; the LLM never scores itself. The breakdown is shown. |
| "What if a precedent was wrong?" | Precedents are tied to policy versions (auto-stale), conflicts with policy are flagged, only human-approved outcomes become precedents, and any override drops the Trust Ladder. |
| "How do you protect PHI?" | Masking before the brain, masking on free-text input, RBAC on every read, output leak scan, minimum-necessary communication templates. |
| "Why would a hospital trust automation?" | It isn't automatic by default — every request type starts in Shadow mode and earns autonomy with evidence; sensitive types never graduate. |
| "What does it cost at scale?" | Compile-once brain, light model for most traffic, strong model only when needed, precedents reduce LLM calls over time; serverless scales to zero. |
| "What if the LLM is down?" | Rules, routing and the human queue still work; the system fails safe to humans. |
| "How does it work in India vs USA?" | Same code, region-specific deployment and regulatory pages (DPDP / HIPAA). |

---

## 20. Limitations & Production Roadmap

**Limitations (stated openly):**

- Synthetic data; real-world language and edge cases will be messier.
- Similarity and thresholds are hand-tuned on a small evaluation set.
- Automated anonymization is not perfect; production needs human QA sampling.
- WhatsApp/SMS and JIRA/EHR integrations are simulated.
- Role authentication is simplified in the demo.

**Roadmap:**

- Cognito SSO + fine-grained RBAC; Bedrock Guardrails; Comprehend Medical.
- Real integrations: EHR, JIRA/ServiceNow, WhatsApp Business API, SMS.
- Indian-language support for requester communication.
- Active learning on thresholds from reviewer decisions.
- Immutable audit (S3 Object Lock), multi-region deployment.

---

## 21. Requirement Traceability Matrix

| Requirement | Where it is met |
|---|---|
| R1 Synthetic workbook | §13 Synthetic Data; Layer 1 ingest |
| R2 Grounded answers + citations + uncertainty | Trusted-Source Answers, cite-or-abstain, citation verification, confidence breakdown, "not enough evidence" |
| R3 Guide routine requests, ask for missing info | Workflow pages + One-Shot Completeness Check + field validation |
| R4 Route account-specific/clinical/sensitive/unclear/low-confidence to humans | Hard overrides, reason codes, routing rules, Handoff Packet |
| R5 Role-based responses | RBAC + role-aware assistant |
| R5 Confidence scores / escalation reasons | §8 scoring, §9 reason codes |
| R5 Agent dashboards / summaries | Dashboard, Gap Radar, Handoff Packet summaries |
| R5 Sentiment / urgency | Interpret step (light model) → feeds risk + queue priority |
| R5 Audit trails | Audit Log screen + DynamoDB audit + wiki change log |
| Boundary: no medical advice | Clinical override + output guard |
| Boundary: no confidential exposure | RBAC, PHI masking, leak scan |
| Boundary: no access-control bypass | Role check before retrieval; injection detection |
| Boundary: no irreversible decisions without approval | Write tier always human; Step Functions approval gate |
| Guideline: clear user journey | §4 + §12 |
| Guideline: show evidence | Citations with version on every output |
| Guideline: responsible AI | §10 + §20 |
| Guideline: working end-to-end flow | §18 demo: request → decision → approval → communication → audit → precedent |

---

## 22. One-Sentence Pitch

> **CareOps is a healthcare operations Second Brain that turns scattered policies, workflows and past decisions into a living, versioned knowledge base, reasons over policy and precedent to give cited, confidence-scored recommendations, routes every risky or uncertain case to the right human with full context, earns autonomy only through verified human agreement, and turns every approved decision into reusable organizational knowledge — built on a free-tier AWS architecture ready for both the USA and India.**
