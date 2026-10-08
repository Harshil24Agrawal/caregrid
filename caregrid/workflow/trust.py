"""Trust Ladder (CONTRACTS §9). Thresholds come from config at call time (demo: TRUST_L1_STREAK=3).

- agreed = APPROVE only. EDIT_APPROVE / REJECT / ESCALATE are overrides: level - 1 (min 0), streak = 0, overrides + 1.
- Level 0 -> 1: consecutive_agreements >= TRUST_L1_STREAK and agreements / total_reviews >= TRUST_L1_RATIO.
- Level 1 -> 2: total_reviews >= TRUST_L2_REVIEWS, ratio >= TRUST_L2_RATIO and the workflow risk is low.
- Ceiling: a workflow with meta.never_auto stays at level 0 (its statistics are still recorded).
Callers must NOT call this for ASK_REQUESTER or for cases that were auto-answered: neither is a review.
"""
from __future__ import annotations

from datetime import datetime

from caregrid import config
from caregrid.knowledge.brain import Brain
from caregrid.models import TrustRecord, User
from caregrid.store import Store
from caregrid.workflow.audit import log


def record_review(store: Store, request_type: str, agreed: bool, brain: Brain | None = None,
                  case_id: str | None = None, actor: User | None = None) -> TrustRecord:
    """`brain` (optional, an extension of the CONTRACTS signature) supplies the never_auto ceiling and the workflow risk;
    without it no ceiling applies and the workflow counts as low risk."""
    rec = store.get_trust(request_type)
    before = (rec.level, rec.consecutive_agreements)

    rec.total_reviews += 1
    if agreed:
        rec.agreements += 1
        rec.consecutive_agreements += 1
    else:
        rec.overrides += 1
        rec.consecutive_agreements = 0
        rec.level = max(0, rec.level - 1)

    wf = brain.workflow_for(request_type) if brain is not None else None
    never_auto = bool(wf and wf.meta.get("never_auto"))
    risk_low = wf is None or str(wf.meta.get("risk", "low")) == "low"
    ratio = rec.agreements / rec.total_reviews
    if never_auto:
        rec.level = 0
    elif agreed and rec.level == 0 and rec.consecutive_agreements >= config.TRUST_L1_STREAK and ratio >= config.TRUST_L1_RATIO:
        rec.level = 1
    elif agreed and rec.level == 1 and rec.total_reviews >= config.TRUST_L2_REVIEWS and ratio >= config.TRUST_L2_RATIO and risk_low:
        rec.level = 2
    rec.updated_at = datetime.now()
    store.save_trust(rec)
    log(store, "trust_updated", actor, case_id, request_type=request_type, agreed=agreed,
        level_before=before[0], level_after=rec.level, streak_before=before[1], streak_after=rec.consecutive_agreements,
        total_reviews=rec.total_reviews, never_auto=never_auto)
    return rec
