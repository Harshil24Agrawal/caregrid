"""Shared helpers for the safety-net UI (app_min). Everything goes through the real backend: pipeline, rbac, decisions, prs, metrics.

Rules kept here: one cached Brain, every generated string goes through check_output(text, viewer), and no raw request text is ever kept
in session state (only ids).
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st  # noqa: E402

from caregrid import config  # noqa: E402
from caregrid.knowledge.brain import Brain  # noqa: E402
from caregrid.llm import get_llm, model_name  # noqa: E402
from caregrid.models import Case, User  # noqa: E402
from caregrid.reasoning.guards import check_output  # noqa: E402
from caregrid.seed import load_users  # noqa: E402
from caregrid.store import SQLiteStore  # noqa: E402

BAND_COLOR = {"high": "#1a7f37", "medium": "#b7791f", "low": "#c0392b"}
STATE_ICON = {"answered": "✅", "needs_info": "❓", "in_review": "🕒", "escalated": "⬆️", "approved": "👍", "actioned": "⚙️",
              "notified": "📨", "rejected": "🚫", "closed": "📁"}
# parts of the 0-100 confidence score, in order, with their maximum (CONTRACTS section 5)
CONFIDENCE_PARTS = [("policy", 30, "#2b6cb0"), ("precedent", 25, "#6b46c1"), ("fields", 20, "#2f855a"),
                    ("clarity", 15, "#d69e2e"), ("no_conflict", 10, "#718096")]


@st.cache_resource
def get_brain() -> Brain:
    """ONE Brain for the whole app; it is passed to run() and submit_decision() so a learned precedent is visible immediately."""
    return Brain(config.BRAIN_DIR)


@st.cache_resource
def get_llm_client():
    return get_llm()


def get_store() -> SQLiteStore:
    return SQLiteStore()                                # one short-lived connection per operation: safe across Streamlit threads


def users() -> dict[str, User]:
    return load_users(config.DATA_DIR)


def safe(text: str | None, viewer: User | None = None) -> str:
    """Every generated string is shown through the output guard for the CURRENT viewer (amounts, PII, medical advice)."""
    return check_output(text or "", viewer or current_user())[1]


def esc(text: str | None) -> str:
    return html.escape(safe(text))


def current_user() -> User:
    us = users()
    uid = st.session_state.get("uid") or next(iter(us))
    return us.get(uid) or next(iter(us.values()))


def sidebar() -> User:
    """Render the sidebar (user switcher, provider indicator, reset) and return the current user."""
    us = users()
    ids = list(us)
    with st.sidebar:
        st.markdown("### CareGrid")
        uid = st.selectbox("Signed in as", ids, key="uid", format_func=lambda i: f"{us[i].name} · {us[i].role.value}"
                           + (f" · {us[i].team}" if us[i].team else ""))
        user = us[uid]
        llm = get_llm_client()
        provider = config.LLM_PROVIDER
        badge = "🟢 mock (offline, deterministic)" if provider == "mock" else f"🔵 {provider}"
        st.caption(f"LLM provider: {badge}")
        st.caption(f"light: {model_name(llm, 'light')}  \nstrong: {model_name(llm, 'strong')}")
        st.divider()
        with st.expander("Reset demo"):
            st.caption("Wipes the database and recompiles the Second Brain. Seeds with the mock LLM, so it is repeatable.")
            ok = st.checkbox("Yes, wipe everything and reset", key="confirm_reset")
            if st.button("Reset demo", disabled=not ok, type="primary"):
                from caregrid.admin import reset_demo

                with st.spinner("Resetting…"):
                    result = reset_demo(brain=get_brain())
                st.session_state.pop("last_case_id", None)
                st.session_state.pop("last_decision", None)
                st.session_state["confirm_reset"] = False
                st.success("Demo reset: " + ", ".join(f"{k}={v}" for k, v in result["seeded"].items()))
    return user


def chip(label: str, color: str = "#4a5568") -> str:
    return (f"<span style='display:inline-block;padding:2px 10px;margin:2px 4px 2px 0;border-radius:12px;background:{color};"
            f"color:white;font-size:0.82em'>{html.escape(label)}</span>")


def chips(labels: list[str], color: str = "#4a5568") -> None:
    if labels:
        st.markdown("".join(chip(label, color) for label in labels), unsafe_allow_html=True)


def citation_chips(case: Case, viewer: User) -> None:
    cites = case.proposal.citations if case.proposal else []
    if not cites:
        st.caption("No citations (nothing may be claimed without a source).")
        return
    labels = []
    for c in cites:
        ver = f" v{c.version}" if c.version else ""
        labels.append(f"{c.page_id}{ver} · {c.page_type.value} · {safe(c.title, viewer)}")
    chips(labels, "#2b6cb0")


def confidence_bar(case: Case, viewer: User) -> None:
    conf = case.confidence
    if conf is None:
        return
    segs = "".join(
        f"<div title='{name}: {conf.breakdown.get(name, 0)}/{mx}' style='flex:{mx};background:{color};opacity:{0.25 + 0.75 * conf.breakdown.get(name, 0) / mx:.2f};"
        f"color:white;text-align:center;font-size:0.75em;padding:4px 0;border-right:2px solid white'>{name} {conf.breakdown.get(name, 0)}/{mx}</div>"
        for name, mx, color in CONFIDENCE_PARTS)
    st.markdown(f"<div style='display:flex;width:100%;border-radius:6px;overflow:hidden'>{segs}</div>", unsafe_allow_html=True)
    cap = " · capped at Medium (policy conflict)" if case.rules and case.rules.conflicts and conf.band.value == "medium" else ""
    st.caption(f"Score {conf.score}/100 · band {conf.band.value.upper()}{cap}. {safe(conf.explanation, viewer)}")


def age_text(case: Case) -> str:
    from datetime import datetime

    hours = (datetime.now() - case.created_at).total_seconds() / 3600
    return f"{hours:.1f} h" if hours < 48 else f"{hours / 24:.1f} d"


def page_link(page: str, label: str, case_id: str | None = None) -> None:
    """Link to another page; falls back to a hint when the page is not registered (single-file runs)."""
    try:
        if case_id:
            st.page_link(page, label=label, query_params={"case": case_id})
        else:
            st.page_link(page, label=label)
    except Exception:
        st.caption(f"{label}" + (f" (case {case_id})" if case_id else ""))
