"""CareGrid safety-net UI - Dashboard. Run: streamlit run app_min/Home.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import streamlit as st  # noqa: E402

from caregrid import config  # noqa: E402
from caregrid.insights import metrics  # noqa: E402
from caregrid.models import State  # noqa: E402
from caregrid.rbac import visible_cases  # noqa: E402

st.set_page_config(page_title="CareGrid", page_icon="🩺", layout="wide")
user = common.sidebar()
store, brain = common.get_store(), common.get_brain()

st.title("CareGrid · Operations Assistant")
st.caption("AI prepares the decision. Humans own the decision. Workflows execute the approved action.")

counts = metrics.dashboard_counts(store)
cols = st.columns(6)
for col, (label, key) in zip(cols, [("Cases", "total"), ("Open", "open"), ("Awaiting review", "awaiting_review"), ("Needs info", "needs_info"),
                                    ("Escalated", "escalated"), ("Auto-answered", "auto_answered")]):
    col.metric(label, counts[key])
cols = st.columns(4)
cols[0].metric("Completed", counts["completed"])
cols[1].metric("Hard-override cases open", counts["hard_override_open"])
cols[2].metric("Avg confidence", counts["avg_confidence"] if counts["avg_confidence"] is not None else "–")
cols[3].metric("Open by team", ", ".join(f"{t}: {n}" for t, n in counts["by_team"].items()) or "–")

# ------------------------------------------------------------------ my queue (RBAC before listing)
st.subheader("My queue")
mine = [c for c in visible_cases(user, store) if c.state in (State.IN_REVIEW, State.ESCALATED, State.NEEDS_INFO)]
st.caption(f"{user.name} ({user.role.value}) can see {len(visible_cases(user, store))} case(s); {len(mine)} waiting.")
if mine:
    st.dataframe([{"case": c.id, "type": c.classification.request_type if c.classification else "?", "state": c.state.value,
                   "team": c.assigned_team, "risk": c.rules.risk.value if c.rules else "?",
                   "band": c.confidence.band.value if c.confidence else "?", "age": common.age_text(c)} for c in mine],
                 hide_index=True, width="stretch")
    common.page_link("pages/2_Case.py", "Open the first case", mine[0].id)
else:
    st.info("Nothing waiting for you.")

left, right = st.columns(2)
with left:
    st.subheader("Trust ladder")
    st.dataframe([{"request type": t.request_type, "level": t.level, "reviews": t.total_reviews, "agreements": t.agreements,
                   "streak": t.consecutive_agreements, "overrides": t.overrides} for t in metrics.trust_overview(store)],
                 hide_index=True, width="stretch")
with right:
    st.subheader("Gap radar")
    radar = metrics.gap_radar(store, brain)
    if radar:
        st.dataframe(radar, hide_index=True, width="stretch")
    else:
        st.info("No recurring gaps yet.")

st.subheader("Queue ageing")
aging = metrics.queue_aging(store)
st.dataframe(aging, hide_index=True, width="stretch") if aging else st.caption("Empty queue.")

# ------------------------------------------------------------------ scorecard tiles (eval/scorecard.json)
st.subheader("Evaluation scorecard")
path = config.EVAL_DIR / "scorecard.json"
if path.exists():
    card = json.loads(path.read_text(encoding="utf-8"))
    m = card["metrics"]
    tiles = [("Type accuracy", m["request_type_accuracy"]), ("Routing first-time-right", m["routing_first_time_right"]),
             ("Missing-field recall", m["missing_field_recall"]), ("Citation validity", m["citation_validity"]),
             ("Safety pass", m["safety_pass_rate"]), ("Correct abstention", m["correct_abstention_rate"])]
    cols = st.columns(len(tiles))
    for col, (label, v) in zip(cols, tiles):
        col.metric(label, "n/a" if v["value"] is None else f"{v['value']:.0f}%", f"{v['num']}/{v['den']}", delta_color="off")
    st.caption(f"{card['rows']} rows · provider {card['llm_provider']} · {card['started_at']}")
else:
    st.info("No scorecard yet: run `python -m caregrid.cli eval`.")
