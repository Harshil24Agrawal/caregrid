# CONTRACTS.md — Data models, interfaces & formulas (source of truth)

All models live in `caregrid/models.py` (pydantic v2). All modules exchange these models only.

---

## 1. Enums

```python
from enum import Enum

class Role(str, Enum):
    OPS_EMPLOYEE = "ops_employee"
    TEAM_SPECIALIST = "team_specialist"
    OPS_MANAGER = "ops_manager"
    SENIOR_REVIEWER = "senior_reviewer"
    KNOWLEDGE_OWNER = "knowledge_owner"
    AUDITOR = "auditor"

class PageType(str, Enum):
    POLICY = "policy"; WORKFLOW = "workflow"; TEAM = "team"; FIELD = "field"
    PRECEDENT = "precedent"; REGULATORY = "regulatory"; RUNBOOK = "runbook"

class PageStatus(str, Enum):
    APPROVED = "approved"; DRAFT = "draft"; EXPIRED = "expired"   # policies/workflows
    ACTIVE = "active"; STALE = "stale"                              # precedents

class State(str, Enum):
    NEW = "new"; CLASSIFIED = "classified"; NEEDS_INFO = "needs_info"; READY = "ready"
    PROPOSED = "proposed"; ANSWERED = "answered"                    # ANSWERED = auto Read-tier reply
    IN_REVIEW = "in_review"; APPROVED = "approved"; REJECTED = "rejected"; ESCALATED = "escalated"
    ACTIONED = "actioned"; NOTIFIED = "notified"; CLOSED = "closed"

class ReasonCode(str, Enum):
    MISSING_DATA = "MISSING_DATA"; POLICY_GAP = "POLICY_GAP"; POLICY_CONFLICT = "POLICY_CONFLICT"
    CLINICAL = "CLINICAL"; ACCOUNT_SPECIFIC = "ACCOUNT_SPECIFIC"; SENSITIVE = "SENSITIVE"
    UNCLEAR_INTENT = "UNCLEAR_INTENT"; IRREVERSIBLE_ACTION = "IRREVERSIBLE_ACTION"
    HIGH_RISK = "HIGH_RISK"; ACCESS_DENIED = "ACCESS_DENIED"; LOW_CONFIDENCE = "LOW_CONFIDENCE"

HARD_OVERRIDES = {ReasonCode.CLINICAL, ReasonCode.ACCOUNT_SPECIFIC, ReasonCode.SENSITIVE,
                  ReasonCode.IRREVERSIBLE_ACTION, ReasonCode.ACCESS_DENIED}

class Risk(str, Enum): LOW = "low"; MEDIUM = "medium"; HIGH = "high"; CRITICAL = "critical"
class Band(str, Enum): HIGH = "high"; MEDIUM = "medium"; LOW = "low"
class ActionTier(str, Enum): READ = "read"; DRAFT = "draft"; WRITE = "write"

class DecisionCode(str, Enum):
    ANSWER_FROM_POLICY = "answer_from_policy"
    REQUEST_MISSING_INFO = "request_missing_info"
    ROUTE_TO_TEAM = "route_to_team"
    ESCALATE_SENIOR = "escalate_senior"
    REFUSE_AND_ROUTE = "refuse_and_route"
    NOT_ENOUGH_EVIDENCE = "not_enough_evidence"

class ReviewAction(str, Enum):
    APPROVE = "approve"; EDIT_APPROVE = "edit_approve"; REJECT = "reject"
    ESCALATE = "escalate"; ASK_REQUESTER = "ask_requester"

class Channel(str, Enum): EMAIL = "email"; WHATSAPP = "whatsapp"; SMS = "sms"; PORTAL = "portal"
```

---

## 2. Models

```python
from datetime import datetime, date
from typing import Literal
from pydantic import BaseModel, Field

class User(BaseModel):
    id: str; name: str; role: Role; team: str | None = None

class Page(BaseModel):                       # one markdown file with YAML frontmatter
    id: str                                  # e.g. "KA-12", "WF-03", "TEAM-ENROLL", "FIELD-NPI"
    type: PageType
    title: str
    version: int = 1
    status: PageStatus
    effective_from: date | None = None
    owner: str | None = None
    request_types: list[str] = []            # request types this page applies to
    links: list[str] = []                    # other page ids
    body: str
    meta: dict = {}                          # workflow: {"required_fields": [...], "steps": [...],
                                             #   "team": "TEAM-ENROLL", "risk": "low", "action_tier": "draft",
                                             #   "policy_ids": ["KA-12"], "thresholds": {...}}
    @property
    def key(self) -> str: return f"{self.id}@v{self.version}"

class Precedent(BaseModel):
    id: str                                  # "P-91", new ones "P-<6 hex>"
    request_type: str
    facts: dict[str, str] = {}               # structured, masked: {"category": "...", "missing": "document", ...}
    fields_provided: list[str] = []
    summary: str                             # masked text
    decision_code: DecisionCode
    route_team: str | None = None
    policy_id: str | None = None
    policy_version: int | None = None
    reason_codes: list[ReasonCode] = []
    approver_role: Role
    risk: Risk
    status: PageStatus = PageStatus.ACTIVE   # ACTIVE | STALE
    date: date
    outcome: str
    source_case_id: str | None = None

class Citation(BaseModel):
    page_id: str; version: int | None = None; page_type: PageType; title: str

class GuardResult(BaseModel):
    allowed: bool
    masked_text: str
    pii_types_found: list[str] = []          # types only, never values
    overrides: list[ReasonCode] = []
    injection: bool = False
    validated_fields: dict[str, str] = {}    # structured IDs validated on RAW text before masking,
                                             # e.g. {"npi": "invalid: 9 digits", "member_id": "valid"} — flags only, never values
    notes: list[str] = []

class Classification(BaseModel):
    request_type: str                        # one of DATA_SPEC request types, or "unknown"
    llm_confidence: float = 0.0              # 0..1 from light model (used ONLY for clarity signal)
    rules_type: str | None = None            # keyword classifier result
    extracted_fields: dict[str, str] = {}    # field_name -> value (values masked where PII)
    urgency: Literal["low", "normal", "high"] = "normal"
    sentiment: Literal["negative", "neutral", "positive"] = "neutral"
    is_clinical: bool = False
    is_account_specific: bool = False
    is_sensitive: bool = False
    model_used: str = "mock"

class ScoredPage(BaseModel):
    page: Page; score: float; linked: bool   # linked = reached via workflow link (not just search)

class ScoredPrecedent(BaseModel):
    precedent: Precedent; similarity: float

class RetrievalResult(BaseModel):
    workflow: Page | None = None
    team: Page | None = None
    policies: list[ScoredPage] = []          # approved, current versions only
    fields: list[Page] = []
    precedents_active: list[ScoredPrecedent] = []
    precedents_stale: list[ScoredPrecedent] = []
    regulatory: list[Page] = []

class RuleResult(BaseModel):
    required_fields: list[str] = []
    missing_fields: list[str] = []
    invalid_fields: dict[str, str] = {}      # field -> human-readable problem
    risk: Risk = Risk.LOW
    risk_reasons: list[str] = []
    action_tier: ActionTier = ActionTier.READ
    route_team: str | None = None            # team page id
    approver_role: Role = Role.TEAM_SPECIALIST
    reason_codes: list[ReasonCode] = []
    conflicts: list[str] = []                # human-readable conflict descriptions
    notes: list[str] = []                    # e.g. stale precedent diverges (informational)
    hard_override: bool = False

class Proposal(BaseModel):
    decision_code: DecisionCode
    route_team: str | None = None
    answer_text: str                         # what the requester sees
    next_steps: list[str] = []
    questions_for_requester: list[str] = []  # one-shot: ALL missing info in one list
    summary_for_reviewer: str
    citations: list[Citation] = []
    model_used: str = "mock"

class Confidence(BaseModel):
    score: int                               # 0..100
    band: Band
    breakdown: dict[str, int]                # keys: policy, precedent, fields, clarity, no_conflict
    explanation: str

class TrustRecord(BaseModel):
    request_type: str
    level: int = 0                           # 0 shadow | 1 assist | 2 auto-with-audit
    total_reviews: int = 0
    agreements: int = 0
    consecutive_agreements: int = 0
    overrides: int = 0
    updated_at: datetime

class Case(BaseModel):
    id: str                                  # "REQ-0001".. or seeded "CASE-1024"
    created_at: datetime
    requester: User
    channel: Channel = Channel.PORTAL
    masked_text: str                         # raw text is NEVER persisted
    state: State = State.NEW
    state_history: list[tuple[State, datetime]] = []
    classification: Classification | None = None
    citations_considered: list[Citation] = []
    rules: RuleResult | None = None
    proposal: Proposal | None = None
    confidence: Confidence | None = None
    trust_level: int = 0
    routing: Literal["auto", "human"] = "human"
    assigned_team: str | None = None
    approver_role: Role | None = None
    reason_codes: list[ReasonCode] = []
    related: dict[str, list[str]] = {}       # {"profile": [...], "invoice": [...], "logs": [...], "jira": [...], "runbook": [...]}
    llm_tiers_used: list[str] = []           # ["light"] or ["light","strong"] — cost story

class ReviewDecision(BaseModel):
    case_id: str
    reviewer: User
    action: ReviewAction
    edited_answer: str | None = None
    note: str = ""
    save_as_precedent: bool = True
    propose_pr: bool = False
    contact_email: str | None = None
    contact_phone: str | None = None
    channels: list[Channel] = [Channel.EMAIL]

class AuditEvent(BaseModel):
    id: str; ts: datetime; case_id: str | None; actor_id: str; actor_role: str
    event: str                               # e.g. "request_received","guard_blocked","context_assembled",
                                             # "policy_identified","precedent_identified","proposal_generated",
                                             # "confidence_scored","routed","review_submitted","precedent_saved",
                                             # "trust_updated","communication_sent","pr_opened","pr_decided","state_changed"
    details: dict = {}                       # never raw PII

class KnowledgePR(BaseModel):
    id: str; target_page_id: str; base_version: int
    proposed_body: str; diff: str            # unified diff
    reason: str; author_id: str
    status: Literal["open", "approved", "rejected"] = "open"
    created_at: datetime; decided_by: str | None = None

class Communication(BaseModel):
    id: str; case_id: str; channel: Channel; recipient: str
    message: str; status: Literal["simulated", "sent", "failed"]; ts: datetime

class LintFinding(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: Literal["CONTRADICTION", "EXPIRED_LINKED", "STALE_PRECEDENT", "ORPHAN",
                  "MISSING_TEAM", "ESCALATION_HOTSPOT", "PII_LEAK"]
    page_ids: list[str]
    message: str
```

---

## 3. Module interfaces (signatures are fixed)

```python
# caregrid/llm.py
class LLM(Protocol):
    def complete_json(self, system: str, user: str, tier: Literal["light", "strong"]) -> dict: ...
    def complete_text(self, system: str, user: str, tier: Literal["light", "strong"]) -> str: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...
def get_llm() -> LLM          # by config.LLM_PROVIDER: "mock" | "bedrock" | "anthropic"
# MockLLM: deterministic keyword rules for classify/propose; hashed bag-of-words (512-d) embeddings.
# Every call records tier used (for cost metrics).

# caregrid/store.py
class Store(Protocol):
    def save_case(self, case: Case) -> None; def get_case(self, case_id: str) -> Case | None
    def list_cases(self, **filters) -> list[Case]
    def append_audit(self, ev: AuditEvent) -> None; def list_audit(self, case_id: str | None = None) -> list[AuditEvent]
    def get_trust(self, request_type: str) -> TrustRecord; def save_trust(self, t: TrustRecord) -> None
    def save_pr(self, pr: KnowledgePR) -> None; def list_prs(self, status: str | None = None) -> list[KnowledgePR]
    def save_comm(self, c: Communication) -> None; def list_comms(self, case_id: str | None = None) -> list[Communication]
    def next_case_id(self) -> str
class SQLiteStore(Store): ...  # JSON blobs per table: cases, audit, trust, prs, comms

# caregrid/rbac.py
def can_view(user: User, case: Case, section: Literal["summary","profile","billing","logs","full"]) -> bool
def can_approve(user: User, case: Case) -> bool
def visible_cases(user: User, store: Store) -> list[Case]

# caregrid/ingest/
def generate(out_dir: Path, seed: int = 42) -> None                       # generate.py
def anonymize(text: str) -> tuple[str, list[str]]                          # anonymize.py -> (masked, pii_types)
def compile_brain(data_dir: Path, brain_dir: Path) -> dict                 # compile.py -> counts per page type
def leak_scan(brain_dir: Path) -> list[LintFinding]                        # leakscan.py

# caregrid/knowledge/brain.py
class Brain:
    def __init__(self, brain_dir: Path)
    def get(self, page_id: str, version: int | None = None) -> Page | None    # None version = current approved
    def current_policies(self) -> list[Page]
    def workflow_for(self, request_type: str) -> Page | None
    def team(self, team_id: str) -> Page | None
    def precedents(self, request_type: str | None = None, status: PageStatus | None = None) -> list[Precedent]
    def write_precedent(self, p: Precedent) -> None
    def write_page(self, page: Page) -> None                  # bumps version, old version -> EXPIRED, marks dependent precedents STALE
    def mark_stale_for_policy(self, policy_id: str, current_version: int) -> list[str]
    def append_log(self, actor: str, action: str, page_id: str, reason: str) -> None
    def rebuild_index(self) -> None
# caregrid/knowledge/retrieve.py
def retrieve(brain: Brain, cls: Classification, masked_text: str, llm: LLM) -> RetrievalResult
# caregrid/knowledge/lint.py
def lint(brain: Brain, store: Store | None = None) -> list[LintFinding]

# caregrid/reasoning/
def check_input(text: str, user: User) -> GuardResult                     # guards.py
def check_output(text: str, user: User) -> tuple[bool, str, list[str]]    # guards.py -> (ok, cleaned, issues)
def classify(masked_text: str, llm: LLM) -> Classification                # classify.py (light tier + keyword rules)
def apply_rules(cls: Classification, ret: RetrievalResult, brain: Brain, guard: GuardResult) -> RuleResult  # rules.py
def propose(masked_text: str, cls: Classification, ret: RetrievalResult, rules: RuleResult, llm: LLM) -> Proposal  # propose.py
def verify_citations(p: Proposal, brain: Brain) -> tuple[Proposal, list[str]]  # citations.py
def score(cls: Classification, ret: RetrievalResult, rules: RuleResult, p: Proposal) -> Confidence  # confidence.py
def run(text: str, user: User, store: Store, brain: Brain, llm: LLM,
        channel: Channel = Channel.PORTAL, case_id: str | None = None) -> Case  # pipeline.py

# caregrid/workflow/
def decide_route(case: Case, trust: TrustRecord) -> Case                   # routing.py
def record_review(store: Store, request_type: str, agreed: bool) -> TrustRecord  # trust.py
def submit_decision(d: ReviewDecision, store: Store, brain: Brain, llm: LLM) -> Case  # decisions.py
def capture_precedent(case: Case, d: ReviewDecision, brain: Brain) -> Precedent  # precedents.py
def draft_pr(case: Case, d: ReviewDecision, brain: Brain, llm: LLM) -> KnowledgePR  # prs.py
def decide_pr(pr_id: str, approve: bool, user: User, store: Store, brain: Brain) -> KnowledgePR  # prs.py
def send_communications(case: Case, d: ReviewDecision, store: Store) -> list[Communication]  # comms.py
def log(store: Store, event: str, actor: User | None, case_id: str | None, **details) -> None  # audit.py

# caregrid/insights/metrics.py
def dashboard_counts(store) -> dict
def gap_radar(store) -> list[dict]       # rows: request_type, reason_code, count, avg_hours_in_queue, est_hours_saved
def queue_aging(store) -> list[dict]     # rows: state, team, count, avg_hours, max_hours
def trust_overview(store) -> list[TrustRecord]
def cost_split(store) -> dict            # % requests light-only vs light+strong
```

---

## 4. Pipeline order (`reasoning/pipeline.run`)

```text
1  guards.check_input           → mask PII, detect injection, clinical/sensitive keywords, role check
   └ if injection or access denied: Case(state=IN_REVIEW, decision REFUSE_AND_ROUTE, reason ACCESS_DENIED/SENSITIVE), audit, return
2  classify (light)             → Classification   [RETRIEVE prerequisite: INTERPRET]
3  retrieve                     → RetrievalResult  [RETRIEVE: policy + precedent]
4  apply_rules                  → RuleResult       [APPLY RULES]
5  propose (strong only if needed, see §6) → Proposal  [PROPOSE]
6  verify_citations             → Proposal         [CITE]
7  score                        → Confidence       [SCORE]
8  check_output                 → clean answer_text
9  routing.decide_route         → state, routing, approver
10 save case + audit events for every step
```

---

## 5. Confidence formula (`reasoning/confidence.score`) — deterministic

| Key | Max | Rule |
|---|---|---|
| `policy` | 30 | 30 if ≥1 approved current policy is **linked** from the request type's workflow and retrieved; 15 if a relevant policy found only by search (score ≥ `POLICY_MIN_SCORE`, default 0.35) or only a general policy; 0 otherwise |
| `precedent` | 25 | Count active precedents with similarity ≥ `PRECEDENT_MIN_SIM` (0.6) whose `decision_code` **and** `route_team` equal the proposal's: 0→0, 1→15, 2→20, ≥3→25 |
| `fields` | 20 | `round(20 * valid_present / required)`; 20 if no fields required. Invalid formats count as missing |
| `clarity` | 15 | 15 if `request_type != "unknown"` and `rules_type == request_type` and `llm_confidence ≥ 0.7`; 8 if only one of those holds; 0 if unknown |
| `no_conflict` | 10 | 10 if `rules.conflicts` empty, else 0 |

Bands: **≥75 High · 45–74 Medium · <45 Low**. **If `rules.conflicts` is non-empty, the band is capped at Medium** (a conflict always needs an expert).
`explanation` lists each component + the biggest missing piece.

## 6. Two-level LLM rule

Use `strong` tier for propose only if any: `rules.conflicts` non-empty · risk ∈ {high, critical} · ≥2 policies + ≥1 precedent to reconcile · `request_type == "unknown"` · reviewer explanation requested by assistant. Otherwise `light`. Record tiers in `case.llm_tiers_used`.

## 7. Risk rules (`rules.py`)

- Base risk from workflow `meta.risk`.
- Thresholds from workflow `meta.thresholds` (e.g. DME `estimated_cost_inr > 50000` → HIGH, reason "cost above ₹50,000 threshold").
- Effective date in the past for record changes → MEDIUM ("retroactive change").
- `is_sensitive` complaint with legal words ("lawyer","legal","court") → CRITICAL.
- Approver by risk: LOW→team_specialist · MEDIUM→ops_manager · HIGH→senior_reviewer · CRITICAL→senior_reviewer.

## 8. Routing (`workflow/routing.decide_route`)

```python
if rules.hard_override:                         route="human", state=IN_REVIEW
elif confidence.band == LOW:                    decision=NOT_ENOUGH_EVIDENCE, add LOW_CONFIDENCE (+POLICY_GAP if no policy), route="human"
elif rules.missing_fields or rules.invalid_fields: state=NEEDS_INFO (ask ALL questions at once), route="human" after reply
elif confidence.band == MEDIUM:                 route="human", state=IN_REVIEW
elif (band == HIGH and risk == LOW and trust.level >= 1
      and action_tier in {READ, DRAFT}):        route="auto", state=ANSWERED (READ) or PROPOSED->READY (DRAFT), audit "auto_with_audit"
else:                                           route="human", state=IN_REVIEW
```

## 9. Trust Ladder (`workflow/trust.record_review`)

- `agreed = action == APPROVE` (no edits). `EDIT_APPROVE`, `REJECT`, `ESCALATE` = override.
- Level 0→1: `consecutive_agreements ≥ 10` and `agreements/total_reviews ≥ 0.90`.
- Level 1→2: `total_reviews ≥ 25`, ratio ≥ 0.95, request type's workflow risk == low.
- Any override: `level = max(0, level-1)`, `consecutive_agreements = 0`.
- Ceiling: request types whose workflow `meta.never_auto == true` (clinical, account-specific, complaints) stay 0.
- Thresholds live in `config.py` (demo may lower them via env `TRUST_L1_STREAK=3`).

## 10. Precedent similarity (`knowledge/retrieve.py`)

`similarity = 0.6 * facts_match + 0.4 * cosine(embed(summary), embed(masked_text))`
- Hard filter: same `request_type`.
- `facts_match` = fraction of keys in the case's facts (`category`, `missing`, `risk`, `team`) that match.
- Precedents whose `policy_version` ≠ current version of `policy_id` are `STALE` → returned in `precedents_stale`, never scored.
- Stale precedent whose decision differs from current proposal → `rules.notes` entry ("P-88 is stale (KA-12 v2) and skipped the document check"), no score penalty.
- Active precedent that disagrees with what the current policy requires → `rules.conflicts` + `POLICY_CONFLICT`.

## 11. Policy retrieval (`knowledge/retrieve.py`)

`combined = 0.5 * bm25_norm + 0.5 * cosine` over approved current policies (+ regulatory, runbook pages).
Pages linked from the workflow are always included with `linked=True` (+0.3 boost). Return top 5.
Two approved policies for the same request type with contradictory `meta.rule_key` values → `rules.conflicts`.

## 12. RBAC (`rbac.py`)

| Role | View | Approve |
|---|---|---|
| ops_employee | own cases: summary only | — |
| team_specialist | cases assigned to own team: full except billing amounts edit | LOW risk, own team |
| ops_manager | all cases: full | LOW, MEDIUM |
| senior_reviewer | all cases: full | all risks |
| knowledge_owner | pages, PRs, lint; cases: summary | PRs only |
| auditor | all audit + cases: summary (read-only) | — |
