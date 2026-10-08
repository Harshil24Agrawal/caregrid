"""Case: overview, recommendation, confidence, evidence, audit, graph, and the approval panel (handoff packet + decision)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402
import streamlit as st  # noqa: E402

from caregrid.insights.graph import case_graph  # noqa: E402
from caregrid.models import Channel, ReviewAction, ReviewDecision, State  # noqa: E402
from caregrid.rbac import can_approve, can_view, visible_cases  # noqa: E402
from caregrid.workflow.decisions import DECIDABLE, AlreadyDecidedError, submit_decision  # noqa: E402

st.set_page_config(page_title="Case · CareGrid", page_icon="📂", layout="wide")
user = common.sidebar()
store, brain, llm = common.get_store(), common.get_brain(), common.get_llm_client()
safe = lambda t: common.safe(t, user)  # noqa: E731

st.title("Case")

# ------------------------------------------------------------------ pick a case (only ones this role may see)
cases = sorted(visible_cases(user, store), key=lambda c: c.created_at, reverse=True)
ids = [c.id for c in cases]
wanted = st.query_params.get("case") or st.session_state.get("last_case_id")
if wanted and wanted not in ids and store.get_case(wanted) is not None:
    st.error(f"🔒 {wanted} is not visible to {user.name} ({user.role.value}).")
if not ids:
    st.info("No cases visible to you yet.")
    st.stop()
label = {c.id: f"{c.id} · {c.classification.request_type if c.classification else '?'} · {c.state.value}" for c in cases}
case_id = st.selectbox("Case", ids, index=ids.index(wanted) if wanted in ids else 0, format_func=lambda i: label[i])
case = store.get_case(case_id)
rules, prop, conf = case.rules, case.proposal, case.confidence
rtype = case.classification.request_type if case.classification else "unknown"
full = can_view(user, case, "full")

# ------------------------------------------------------------------ header
trust = store.get_trust(rtype)
head = st.columns(7)
head[0].metric("Case", case.id)
head[1].metric("Type", rtype)
head[2].metric("State", f"{common.STATE_ICON.get(case.state.value, '')} {case.state.value}")
head[3].metric("Age", common.age_text(case))
head[4].metric("Risk", rules.risk.value if rules else "?")
head[5].metric("Confidence", f"{conf.score} {conf.band.value}" if conf else "–")
head[6].metric("Trust level", f"L{trust.level}")
st.caption(f"Team {case.assigned_team} · routing {case.routing} · approver {case.approver_role.value if case.approver_role else '–'} · "
           f"requester {case.requester.name}")

# ------------------------------------------------------------------ overview
st.subheader("Overview")
st.write(safe(case.masked_text))
if rules and rules.risk_reasons:
    for reason in rules.risk_reasons:
        st.markdown(f"- {safe(reason)}")
if case.reason_codes:
    common.chips([r.value for r in case.reason_codes], "#b7791f")

# ------------------------------------------------------------------ recommendation
st.subheader("Recommendation")
if prop:
    st.markdown(f"**Decision:** `{prop.decision_code.value}` → **{prop.route_team or case.assigned_team}**")
    st.write(safe(prop.answer_text))
    if prop.next_steps:
        st.markdown("**Next steps**\n" + "\n".join(f"{i}. {safe(s)}" for i, s in enumerate(prop.next_steps, 1)))
    if full:
        st.markdown("**Summary for the reviewer**")
        st.write(safe(prop.summary_for_reviewer))
    st.markdown("**Sources**")
    common.citation_chips(case, user)
    st.caption(f"Wording by {prop.model_used}; tiers used: {', '.join(case.llm_tiers_used) or 'none'}")

# ------------------------------------------------------------------ confidence, conflicts, notes
if full and conf:
    st.subheader("Confidence")
    common.confidence_bar(case, user)
if full and rules:
    for conflict in rules.conflicts:
        st.error(f"Conflict: {safe(conflict)}")
    for note in rules.notes:
        st.caption(f"ℹ️ {safe(note)}")

# ------------------------------------------------------------------ evidence (RBAC: a locked placeholder, never the data)
st.subheader("Evidence")
SECTION = {"profile": "profile", "invoice": "billing", "logs": "logs", "jira": "logs", "runbook": "logs"}
for key, section in SECTION.items():
    refs = case.related.get(key, [])
    if not refs:
        continue
    if can_view(user, case, section):
        st.markdown(f"**{key}**: " + ", ".join(f"`{r}`" for r in refs))
    else:
        st.markdown(f"**{key}**: 🔒 restricted for {user.role.value}")
if not case.related:
    st.caption("No linked profile, invoice, log, JIRA or runbook records.")

# ------------------------------------------------------------------ audit timeline
st.subheader("Audit timeline")
events = store.list_audit(case.id)
st.dataframe([{"time": e.ts.strftime("%m-%d %H:%M:%S"), "actor": f"{e.actor_id} ({e.actor_role})", "event": e.event,
               "details": safe(str({k: v for k, v in e.details.items()}))[:160]} for e in events], hide_index=True, width="stretch")

# ------------------------------------------------------------------ context graph
st.subheader("Context graph")
try:
    graph = case_graph(case.id, user, store, brain)
    st.dataframe([{"id": n.id, "kind": n.kind, "label": safe(n.label), "status": n.status, "version": n.version} for n in graph.nodes],
                 hide_index=True, width="stretch")
    with st.expander(f"{len(graph.edges)} relationships"):
        st.dataframe([{"from": e.source, "relation": e.relation, "to": e.target} for e in graph.edges], hide_index=True,
                     width="stretch")
except PermissionError:
    st.caption("🔒 The context graph is not available for your role.")

# ------------------------------------------------------------------ approval
st.divider()
st.header("Approval")
decision_note = st.session_state.get("last_decision")
if decision_note and decision_note.get("case_id") == case.id:
    st.success("Decision recorded.")
    st.markdown(f"**State path:** {decision_note['path']}")
    st.markdown(f"**Precedent:** {decision_note['precedent'] or 'none saved'}")
    st.markdown(f"**Trust:** {decision_note['trust'] or 'unchanged'}")
    if decision_note.get("pr"):
        st.markdown(f"**Knowledge PR opened:** {decision_note['pr']} (decide it on the Knowledge page)")

if case.state not in DECIDABLE and case.state != State.NEEDS_INFO:
    st.info(f"Case is {case.state.value}: nothing to decide.")
    st.stop()

# Handoff packet
st.subheader("Handoff packet")
pk1, pk2, pk3 = st.columns(3)
with pk1:
    st.markdown("**Checked**")
    required = rules.required_fields if rules else []
    for f in required:
        ok = f not in (rules.missing_fields if rules else []) and f not in (rules.invalid_fields if rules else {})
        st.markdown(f"{'✅' if ok else '❌'} {f}")
    st.markdown("**Missing / invalid**")
    if rules and (rules.missing_fields or rules.invalid_fields):
        for f in rules.missing_fields:
            st.markdown(f"- {f}: missing")
        for f, why in rules.invalid_fields.items():
            st.markdown(f"- {f}: {safe(why)}")
    else:
        st.caption("none")
with pk2:
    st.markdown("**Proposed**")
    st.markdown(f"`{prop.decision_code.value}` → {prop.route_team or case.assigned_team}" if prop else "–")
    st.markdown("**Confidence**")
    st.markdown(f"{conf.score} ({conf.band.value})" if conf else "–")
with pk3:
    st.markdown("**Conflicts**")
    for c in (rules.conflicts if rules else []):
        st.error(safe(c))
    if not (rules and rules.conflicts):
        st.caption("none")
    st.markdown("**Sources**")
    common.citation_chips(case, user)

# Decision form
ACTIONS = {"Approve": ReviewAction.APPROVE, "Edit and approve": ReviewAction.EDIT_APPROVE, "Reject": ReviewAction.REJECT,
           "Escalate": ReviewAction.ESCALATE, "Ask requester": ReviewAction.ASK_REQUESTER}
choice = st.radio("Action", list(ACTIONS), horizontal=True, key=f"act_{case.id}")
action = ACTIONS[choice]
ask = action == ReviewAction.ASK_REQUESTER
edited = st.text_area("Edited answer", key=f"edit_{case.id}", height=120) if action == ReviewAction.EDIT_APPROVE else ""
note = st.text_area("Note (question to the requester when asking)" if ask else "Note", key=f"note_{case.id}", height=70)
c1, c2 = st.columns(2)
save_prec = c1.checkbox("Save as precedent", value=True, key=f"prec_{case.id}", disabled=ask)
propose_pr = c2.checkbox("Propose a Knowledge PR from this decision", key=f"pr_{case.id}", disabled=ask)
meta: dict = {}
if propose_pr and not ask:
    policies = [c.page_id for c in (prop.citations if prop else []) if c.page_type.value == "policy"]
    if policies:
        target = st.selectbox("Policy to change", policies, key=f"tgt_{case.id}")
        meta["target_page"] = target
        if st.checkbox("Retire this policy (no replacement)", key=f"ret_{case.id}"):
            meta["retire"] = True
    else:
        st.caption("This case cites no policy, so no PR can be opened.")
e1, e2 = st.columns(2)
email = e1.text_input("Contact email (official)", key=f"em_{case.id}")
phone = e2.text_input("Contact phone (official)", key=f"ph_{case.id}")
channels = st.multiselect("Notify via", [c.value for c in Channel], default=[], key=f"ch_{case.id}", disabled=ask)

# permission, with the reason shown
if ask:
    allowed = can_view(user, case, "full") and case.requester.id != user.id
    why = "" if allowed else "Only someone who can see the whole case, and is not the requester, may ask for more information."
else:
    allowed = can_approve(user, case)
    if allowed:
        why = ""
    elif case.requester.id == user.id:
        why = "Separation of duties: you cannot approve your own request."
    elif rules is None or user.role.value not in {"team_specialist", "ops_manager", "senior_reviewer"}:
        why = f"The {user.role.value} role cannot approve cases."
    elif user.role.value == "team_specialist" and user.team != case.assigned_team:
        why = f"This case belongs to {case.assigned_team}; you are in {user.team}."
    else:
        why = f"{rules.risk.value} risk needs {case.approver_role.value if case.approver_role else 'a more senior reviewer'}."
if not allowed:
    st.warning(f"🔒 {why}")

if st.button("Submit decision", type="primary", disabled=not allowed, key=f"go_{case.id}"):
    d = ReviewDecision(case_id=case.id, reviewer=user, action=action, edited_answer=edited or None, note=note or "",
                       save_as_precedent=save_prec and not ask, propose_pr=propose_pr and not ask, contact_email=email or None,
                       contact_phone=phone or None, channels=[Channel(c) for c in channels], meta_changes=meta)
    try:
        before_prs = {p.id for p in store.list_prs()}
        done = submit_decision(d, store, brain, llm)
    except (PermissionError, AlreadyDecidedError, ValueError, KeyError) as e:
        st.error(f"Not submitted: {type(e).__name__}: {e}")
    else:
        evs = store.list_audit(case.id)
        prec = next((e.details.get("precedent") for e in reversed(evs) if e.event == "precedent_saved"), None)
        tr = next((e.details for e in reversed(evs) if e.event == "trust_updated"), None)
        new_pr = [p.id for p in store.list_prs() if p.id not in before_prs]
        st.session_state["last_decision"] = {
            "case_id": case.id, "path": " → ".join(s.value for s, _ in done.state_history[-4:]), "precedent": prec,
            "trust": (f"{tr.get('request_type')}: level {tr.get('level_before')} → {tr.get('level_after')}, "
                      f"streak {tr.get('streak_before')} → {tr.get('streak_after')}, "
                      f"{'agreed' if tr.get('agreed') else 'override'}") if tr else None,
            "pr": new_pr[0] if new_pr else None}
        st.rerun()
