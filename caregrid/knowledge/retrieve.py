"""Second Brain retrieval (CONTRACTS §10-11): linked policies + hybrid search + precedent matching.

Never returns draft/expired pages. Runs BEFORE apply_rules, so case facts are derived here from the workflow
metadata and the classifier's extracted fields.
"""
from __future__ import annotations

import re

import numpy as np
from rank_bm25 import BM25Okapi

from caregrid import config
from caregrid.constants import REQUEST_CATEGORY, facts_missing
from caregrid.knowledge.brain import Brain
from caregrid.llm import LLM
from caregrid.models import Classification, Page, PageStatus, PageType, RetrievalResult, ScoredPage, ScoredPrecedent

TOP_K = 5
LINK_BOOST = 0.3
SEARCH_FLOOR = 0.10          # search-only pages below this combined score are noise and are not returned
MAX_ACTIVE_PRECEDENTS = 10
FIELD_ALIASES = {"provider_npi": "npi", "npi": "provider_npi"}

_STOP = frozenset("a an and are as at be by for from has have how i in is it me my of on or our please that the this to we what when "
                  "which who with you your can do does need want would should".split())


def stem(tok: str) -> str:
    for suf in ("ing", "ed", "es", "s", "e"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3 and not (suf == "s" and tok.endswith("ss")):
            return tok[: -len(suf)]
    return tok


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1]


_doc_emb_cache: dict[tuple[str, str], list[float]] = {}


def _embed_docs(llm: LLM, texts: list[str]) -> list[list[float]]:
    """Cache document embeddings per embedding provider (page text rarely changes)."""
    out: list[list[float] | None] = [_doc_emb_cache.get((config.EMBED_PROVIDER, t)) for t in texts]
    missing = [i for i, v in enumerate(out) if v is None]
    if missing:
        for i, vec in zip(missing, llm.embed([texts[i] for i in missing])):
            _doc_emb_cache[(config.EMBED_PROVIDER, texts[i])] = vec
            out[i] = vec
    return out  # type: ignore[return-value]


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(np.dot(va, vb) / denom) if denom else 0.0


def field_page_id(field: str) -> str:
    return "FIELD-" + field.upper().replace("_", "-")


def derive_case_facts(wf: Page | None, cls: Classification) -> dict[str, str]:
    """category / missing / risk / team from workflow meta + extracted fields (decision P0-a, refined in P2)."""
    if wf is None:
        return {"category": REQUEST_CATEGORY.get(cls.request_type, "unknown"), "missing": "none", "risk": "low",
                "team": "TEAM-OPS-TRIAGE"}
    have = {k for k, v in cls.extracted_fields.items() if v}
    have |= {FIELD_ALIASES[k] for k in have if k in FIELD_ALIASES}
    missing = [f for f in wf.meta.get("required_fields", []) if f not in have]
    return {"category": REQUEST_CATEGORY.get(cls.request_type, "unknown"), "missing": facts_missing(missing),
            "risk": str(wf.meta.get("risk", "low")), "team": str(wf.meta.get("team", "TEAM-OPS-TRIAGE"))}


def facts_match(case_facts: dict[str, str], prec_facts: dict[str, str]) -> float:
    if not case_facts:
        return 0.0
    return sum(prec_facts.get(k) == v for k, v in case_facts.items()) / len(case_facts)


def _search_scores(candidates: list[Page], query: str, llm: LLM) -> dict[str, float]:
    """combined = 0.5 * bm25_norm + 0.5 * cosine, keyed by page id (candidates are current approved pages)."""
    if not candidates:
        return {}
    texts = [f"{p.title}. {p.body}" for p in candidates]
    q_tokens = tokenize(query)
    bm = np.zeros(len(candidates))
    if q_tokens:
        bm = np.asarray(BM25Okapi([tokenize(t) for t in texts]).get_scores(q_tokens), dtype=float)
    bm = np.clip(bm, 0, None)
    bm_norm = bm / bm.max() if bm.max() > 0 else bm
    q_vec = llm.embed([query])[0]
    cos = [max(_cosine(q_vec, d), 0.0) for d in _embed_docs(llm, texts)]
    return {p.id: 0.5 * float(bm_norm[i]) + 0.5 * cos[i] for i, p in enumerate(candidates)}


def retrieve(brain: Brain, cls: Classification, masked_text: str, llm: LLM) -> RetrievalResult:
    wf = brain.workflow_for(cls.request_type)
    team = brain.team(str(wf.meta.get("team"))) if wf else None
    fields = [p for f in (wf.meta.get("required_fields", []) if wf else []) if (p := brain.get(field_page_id(f))) is not None]
    case_facts = derive_case_facts(wf, cls)

    # ---- policies (+ regulatory, runbook) -- approved & current only
    candidates = brain.current_pages(PageType.POLICY, PageType.REGULATORY, PageType.RUNBOOK)
    combined = _search_scores(candidates, masked_text, llm)
    by_id = {p.id: p for p in candidates}

    linked_ids = [pid for pid in (wf.meta.get("policy_ids", []) if wf else []) if pid in by_id and by_id[pid].type == PageType.POLICY]
    chosen: list[tuple[Page, float, bool]] = [(by_id[pid], combined[pid] + LINK_BOOST, True) for pid in linked_ids]
    search_only = sorted(((p, combined[p.id]) for p in candidates if p.id not in linked_ids and combined[p.id] >= SEARCH_FLOOR),
                         key=lambda x: (-x[1], x[0].id))
    chosen += [(p, s, False) for p, s in search_only[: max(TOP_K - len(chosen), 0)]]
    chosen.sort(key=lambda x: (-x[1], x[0].id))

    policies = [ScoredPage(page=p, score=round(s, 4), linked=l) for p, s, l in chosen if p.type != PageType.REGULATORY]
    regulatory = [p for p, _, _ in chosen if p.type == PageType.REGULATORY]

    # ---- precedents: hard filter on request_type; similarity = 0.6 facts + 0.4 cosine
    active: list[ScoredPrecedent] = []
    stale: list[ScoredPrecedent] = []
    precs = [] if cls.request_type == "unknown" else brain.precedents(request_type=cls.request_type)
    if precs:
        q_vec = llm.embed([masked_text])[0]
        for prec, vec in zip(precs, _embed_docs(llm, [p.summary for p in precs])):
            sim = 0.6 * facts_match(case_facts, prec.facts) + 0.4 * max(_cosine(q_vec, vec), 0.0)
            (active if prec.status == PageStatus.ACTIVE else stale).append(ScoredPrecedent(precedent=prec, similarity=round(sim, 4)))
    active.sort(key=lambda s: (-s.similarity, s.precedent.id))
    stale.sort(key=lambda s: (-s.similarity, s.precedent.id))

    return RetrievalResult(workflow=wf, team=team, policies=policies, fields=fields,
                           precedents_active=active[:MAX_ACTIVE_PRECEDENTS], precedents_stale=stale,
                           regulatory=regulatory, case_facts=case_facts)
