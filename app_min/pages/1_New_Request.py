"""New request: submit text through the real pipeline and show the result card."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402
import streamlit as st  # noqa: E402

from caregrid.models import Channel, DecisionCode, State  # noqa: E402
from caregrid.reasoning.guards import MAX_INPUT_CHARS  # noqa: E402
from caregrid.reasoning.pipeline import run  # noqa: E402

st.set_page_config(page_title="New request · CareGrid", page_icon="📝", layout="wide")
user = common.sidebar()
store, brain, llm = common.get_store(), common.get_brain(), common.get_llm_client()

st.title("New request")
st.caption(f"Submitting as {user.name} ({user.role.value}). Personal data is masked before anything is stored or sent to a model.")


def _submit() -> None:
    """Button callback: hand the text to this run once, then clear the box. Raw text never stays in session state."""
    st.session_state["_pending"] = (st.session_state.get("req_text", ""), st.session_state.get("req_channel", Channel.PORTAL.value))
    st.session_state["req_text"] = ""


pending = st.session_state.pop("_pending", None)
if pending and pending[0].strip():
    text, channel = pending
    with st.spinner("Reasoning over policy and precedents…"):
        case = run(text, user, store, brain, llm, Channel(channel))
    del text, pending
    st.session_state["last_case_id"] = case.id
elif pending:
    st.warning("Please type a request first.")

st.selectbox("Channel", [c.value for c in Channel], key="req_channel")
st.text_area("Request", key="req_text", height=160, max_chars=MAX_INPUT_CHARS,
             placeholder="e.g. What supporting documents are accepted for provider record changes?")
st.caption(f"Up to {MAX_INPUT_CHARS} characters.")
st.button("Submit", type="primary", on_click=_submit)

# ------------------------------------------------------------------ result card (rebuilt from the stored, masked case)
case_id = st.session_state.get("last_case_id")
case = store.get_case(case_id) if case_id else None
if case is not None and case.requester.id == user.id:
    st.divider()
    prop = case.proposal
    rtype = case.classification.request_type if case.classification else "unknown"
    icon = common.STATE_ICON.get(case.state.value, "•")
    st.subheader(f"{icon} {case.id} · {rtype} · {case.state.value}")
    received = next((e for e in store.list_audit(case.id) if e.event == "request_received"), None)
    pii = list(received.details.get("pii_types", [])) if received else []
    if pii:
        st.caption("Masked before storage:")
        common.chips(sorted(set(pii)), "#805ad5")
    else:
        st.caption("No personal data detected in the request.")

    answer = common.safe(prop.answer_text, user) if prop else ""
    refused = bool(prop and prop.decision_code == DecisionCode.REFUSE_AND_ROUTE)
    if case.state == State.ANSWERED:
        st.success("Answered automatically from approved policy.")
        st.write(answer)
        common.citation_chips(case, user)
    elif case.state == State.NEEDS_INFO:
        st.info("We need a few details to continue. Please reply to this ONE message:")
        st.write(answer)
    elif refused:
        st.error(f"This request cannot be answered by the assistant. It was routed to {case.assigned_team} for a person to handle.")
        st.write(answer)
        common.chips([r.value for r in case.reason_codes], "#c0392b")
    else:
        st.warning(f"Sent for human review → {case.assigned_team} ({case.approver_role.value if case.approver_role else 'reviewer'}).")
        common.chips([r.value for r in case.reason_codes], "#b7791f")
        if prop:
            st.write(answer)
    common.page_link("pages/2_Case.py", f"Open case {case.id}", case.id)
