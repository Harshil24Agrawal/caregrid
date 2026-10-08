"""Case context graph: case <-> policies <-> precedents <-> workflow <-> team <-> evidence (profile, invoice, logs, JIRA, runbook) <-> comms.

Pure data for the UI to draw (PyVis / graphviz / anything). RBAC first: evidence nodes only appear for sections the viewer may see
(profile / billing / logs); labels are ids and page titles only - never amounts, names or free text from records.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from caregrid.knowledge.brain import Brain
from caregrid.models import PageType, User
from caregrid.rbac import can_view
from caregrid.store import Store

Kind = Literal["case", "requester", "policy", "workflow", "precedent", "team", "profile", "invoice", "log", "jira", "runbook", "comm"]
_ID = re.compile(r"\b(?:KA|WF|FIELD|REG)-[A-Za-z0-9]+\b")
_EVIDENCE = (("profile", "profile", "profile"), ("invoice", "invoice", "billing"), ("logs", "log", "logs"),
             ("jira", "jira", "logs"), ("runbook", "runbook", "logs"))      # (case.related key, node kind, section the viewer needs)


class GraphNode(BaseModel):
    id: str
    kind: Kind
    label: str
    status: str | None = None            # page / precedent / communication status
    version: int | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str


class CaseGraph(BaseModel):
    case_id: str
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    def node(self, node_id: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def neighbours(self, node_id: str) -> list[str]:
        return sorted({e.target if e.source == node_id else e.source for e in self.edges if node_id in (e.source, e.target)})


def case_graph(case_id: str, viewer: User, store: Store, brain: Brain) -> CaseGraph:
    case = store.get_case(case_id)
    if case is None:
        raise KeyError(f"unknown case {case_id}")
    if not can_view(viewer, case, "summary"):
        raise PermissionError(f"{viewer.role.value} may not view {case_id}")
    g = CaseGraph(case_id=case.id)

    def add(node: GraphNode) -> None:
        if g.node(node.id) is None:
            g.nodes.append(node)

    def link(source: str, target: str, relation: str) -> None:
        if g.node(source) and g.node(target) and not any((e.source, e.target, e.relation) == (source, target, relation) for e in g.edges):
            g.edges.append(GraphEdge(source=source, target=target, relation=relation))

    add(GraphNode(id=case.id, kind="case", label=f"{case.id} ({case.state.value})"))
    add(GraphNode(id=case.requester.id, kind="requester", label=case.requester.role.value))
    link(case.id, case.requester.id, "requested_by")
    if case.assigned_team:
        team = brain.team(case.assigned_team)
        add(GraphNode(id=case.assigned_team, kind="team", label=team.title if team else case.assigned_team))
        link(case.id, case.assigned_team, "routed_to")

    for c in (case.proposal.citations if case.proposal else []):
        if c.page_type == PageType.PRECEDENT:
            prec = brain.get_precedent(c.page_id)
            add(GraphNode(id=c.page_id, kind="precedent", label=c.title or c.page_id, status=prec.status.value if prec else None))
            link(case.id, c.page_id, "cites")
            if prec and prec.policy_id:
                page = brain.get(prec.policy_id, prec.policy_version) if prec.policy_version else brain.get(prec.policy_id)
                add(GraphNode(id=prec.policy_id, kind="policy", label=page.title if page else prec.policy_id,
                              status=page.status.value if page else "missing", version=prec.policy_version))
                link(c.page_id, prec.policy_id, "based_on")
        else:
            kind = {PageType.POLICY: "policy", PageType.WORKFLOW: "workflow", PageType.TEAM: "team",
                    PageType.RUNBOOK: "runbook"}.get(c.page_type)
            if kind:
                add(GraphNode(id=c.page_id, kind=kind, label=c.title or c.page_id, status="approved", version=c.version))
                link(case.id, c.page_id, "cites")
    for node in list(g.nodes):                                   # page-to-page links, only between nodes already on the graph
        page = brain.get(node.id) if node.kind in ("policy", "workflow") else None
        for other in (page.links if page else []):
            link(node.id, other, "links_to")
    if case.rules:
        for text in case.rules.conflicts:
            ids = list(dict.fromkeys(_ID.findall(text)))
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    link(a, b, "conflicts_with")

    for key, kind, section in _EVIDENCE:                         # RBAC before showing evidence
        if not can_view(viewer, case, section):
            continue
        for ref in case.related.get(key, []):
            page = brain.get(ref) if kind == "runbook" else None
            add(GraphNode(id=ref, kind=kind, label=page.title if page else ref))
            link(case.id, ref, "evidence")
    for comm in store.list_comms(case.id):
        add(GraphNode(id=comm.id, kind="comm", label=f"{comm.channel.value} ({comm.status})", status=comm.status))
        link(case.id, comm.id, "notified")
    return g
