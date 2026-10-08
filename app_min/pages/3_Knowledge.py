"""Knowledge: pages browser, lint report, Knowledge PRs (knowledge_owner decides)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402
import streamlit as st  # noqa: E402

from caregrid.knowledge.lint import lint  # noqa: E402
from caregrid.models import PageStatus, PageType, Role  # noqa: E402
from caregrid.workflow.prs import PRStateError, decide_pr  # noqa: E402

st.set_page_config(page_title="Knowledge · CareGrid", page_icon="📚", layout="wide")
user = common.sidebar()
store, brain = common.get_store(), common.get_brain()
safe = lambda t: common.safe(t, user)  # noqa: E731

st.title("Second Brain")
pages_tab, lint_tab, pr_tab = st.tabs(["Pages", "Lint report", "Knowledge PRs"])

STATUS_COLOR = {"approved": "#1a7f37", "active": "#1a7f37", "draft": "#b7791f", "expired": "#718096", "stale": "#c0392b"}

with pages_tab:
    f1, f2, f3 = st.columns(3)
    types = f1.multiselect("Type", [t.value for t in PageType], default=[])
    statuses = f2.multiselect("Status", [s.value for s in PageStatus], default=[])
    query = f3.text_input("Search id or title")
    rows = []
    for p in brain.all_pages():
        rows.append((p.id, p.version, p.type.value, p.status.value, p.title, p.id, "page"))
    for pr in brain.precedents():
        rows.append((pr.id, 1, "precedent", pr.status.value, pr.summary[:60], pr.id, "precedent"))
    rows = [r for r in rows if (not types or r[2] in types) and (not statuses or r[3] in statuses)
            and (not query or query.lower() in r[0].lower() or query.lower() in r[4].lower())]
    st.caption(f"{len(rows)} page(s). Older versions stay visible (KA-12 v2 expired, v3 approved). DRAFT pages are never cited.")
    st.dataframe([{"id": r[0], "version": r[1], "type": r[2], "status": ("⚠️ DRAFT" if r[3] == "draft" else r[3]), "title": safe(r[4])}
                  for r in sorted(rows, key=lambda r: (r[0], -r[1]))], hide_index=True, width="stretch")
    pick = st.selectbox("Open a page", [f"{r[0]} v{r[1]}" for r in sorted(rows, key=lambda r: (r[0], -r[1]))] or [""], key="kb_pick")
    if pick:
        pid, ver = pick.rsplit(" v", 1)
        page = brain.get(pid, int(ver))
        if page is not None:
            common.chips([page.type.value, page.status.value.upper(), f"v{page.version}"], STATUS_COLOR.get(page.status.value, "#4a5568"))
            if page.status == PageStatus.DRAFT:
                st.warning("DRAFT: this page is not approved and is never cited.")
            st.markdown(safe(page.body))
            if page.links:
                st.caption("Links: " + ", ".join(page.links))

with lint_tab:
    findings = lint(brain, store)
    st.caption(f"{len(findings)} finding(s)")
    for sev, fn in (("error", st.error), ("warning", st.warning), ("info", st.info)):
        for f in [f for f in findings if f.severity == sev]:
            fn(f"[{f.code}] {safe(f.message)}")

with pr_tab:
    is_owner = user.role == Role.KNOWLEDGE_OWNER
    if not is_owner:
        st.info(f"Only the knowledge owner can approve or reject PRs. You are {user.name} ({user.role.value}); the PRs are read-only.")
    if st.session_state.get("pr_msg"):
        st.success(st.session_state.pop("pr_msg"))
    open_prs = store.list_prs("open")
    st.subheader(f"Open PRs ({len(open_prs)})")
    if not open_prs:
        st.caption("No open PRs. A reviewer can propose one when deciding a case.")
    for pr in open_prs:
        with st.container(border=True):
            st.markdown(f"**{pr.id}** on `{pr.target_page_id}` (base v{pr.base_version}) · by {pr.author_id} · {pr.created_at:%Y-%m-%d %H:%M}")
            st.write(safe(pr.reason))
            if pr.meta_changes:
                st.markdown("**Structured change (from the reviewer):** " + ", ".join(f"`{k}`={v}" for k, v in pr.meta_changes.items()))
            st.code(pr.diff or "(no text change)", language="diff")
            a, b = st.columns(2)
            ok = a.button("Approve", key=f"ap_{pr.id}", disabled=not is_owner, type="primary")
            no = b.button("Reject", key=f"rj_{pr.id}", disabled=not is_owner)
            if ok or no:
                try:
                    done = decide_pr(pr.id, ok, user, store, brain)
                    st.session_state["pr_msg"] = (f"{done.id} {done.status}."
                                                  + (" The page change is live; dependent precedents were marked stale." if ok else ""))
                    st.rerun()
                except (PermissionError, PRStateError, KeyError) as e:
                    st.error(f"{type(e).__name__}: {e}")
    decided = [p for p in store.list_prs() if p.status != "open"]
    if decided:
        st.subheader("Decided")
        st.dataframe([{"pr": p.id, "page": p.target_page_id, "status": p.status, "by": p.decided_by} for p in decided], hide_index=True,
                     width="stretch")
