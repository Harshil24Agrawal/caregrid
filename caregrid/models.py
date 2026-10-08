"""All pydantic models and enums from CONTRACTS.md §1-2. Do not deviate without updating CONTRACTS.md."""
from __future__ import annotations

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
    relevance: float                         # 0..1 query relevance WITHOUT the link boost; `score` = relevance (+0.3 if linked) and is used only for ranking

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
    case_facts: dict[str, str] = {}          # derived before rules: category, missing, risk, team (used for precedent matching)

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
