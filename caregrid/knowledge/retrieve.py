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


def derive_case_facts(wf: Page | None, cls: Classification, topic: str | None = None) -> dict[str, str]:
    """category / missing / risk / team from workflow meta + extracted fields (decision P0-a, refined in P2).
    For general_policy_question `topic` (id of the top relevant POLICY page, or "none") is added as a fifth fact (P4.1)."""
    if wf is None:
        facts = {"category": REQUEST_CATEGORY.get(cls.request_type, "unknown"), "missing": "none", "risk": "low",
                 "team": "TEAM-OPS-TRIAGE"}
    else:
        have = {k for k, v in cls.extracted_fields.items() if v}
        have |= {FIELD_ALIASES[k] for k in have if k in FIELD_ALIASES}
        missing = [f for f in wf.meta.get("required_fields", []) if f not in have]
        facts = {"category": REQUEST_CATEGORY.get(cls.request_type, "unknown"), "missing": facts_missing(missing),
                 "risk": str(wf.meta.get("risk", "low")), "team": str(wf.meta.get("team", "TEAM-OPS-TRIAGE"))}
    if cls.request_type == "general_policy_question" and topic is not None:
        facts["topic"] = topic
    return facts


def facts_match(case_facts: dict[str, str], prec_facts: dict[str, str]) -> float:
    if not case_facts:
        return 0.0
    return sum(prec_facts.get(k) == v for k, v in case_facts.items()) / len(case_facts)


_MASK_TOKEN = re.compile(r"\[[A-Z_]+\d*\]")


def _search_scores(candidates: list[Page], query: str, llm: LLM) -> dict[str, float]:
    """Absolute relevance in 0..1, keyed by page id: 0.5 * bm25_abs + 0.5 * cosine (candidates are current approved pages).

    bm25_abs = BM25 score / the score an AVERAGE-LENGTH page would get if it contained every query term once
    (= sum of the query terms' idf; a term the corpus has never seen counts at the corpus' maximum idf), clipped to 0..1.
    Unlike normalising by the best page in the corpus, an unrelated best hit therefore stays low: it covers few query terms.
    Mask tokens such as [PERSON_1] are removed from the query first (they are not content)."""
    if not candidates:
        return {}
    texts = [f"{p.title}. {p.body}" for p in candidates]
    q_text = _MASK_TOKEN.sub(" ", query)
    q_tokens = tokenize(q_text)
    bm_abs = np.zeros(len(candidates))
    if q_tokens:
        bm = BM25Okapi([tokenize(t) for t in texts])
        idf_max = max(bm.idf.values(), default=0.0)
        achievable = sum(max(bm.idf.get(t, idf_max), 0.0) for t in q_tokens)
        if achievable > 0:
            bm_abs = np.clip(np.asarray(bm.get_scores(q_tokens), dtype=float) / achievable, 0.0, 1.0)
    q_vec = llm.embed([q_text])[0]
    cos = [max(_cosine(q_vec, d), 0.0) for d in _embed_docs(llm, texts)]
    return {p.id: 0.5 * float(bm_abs[i]) + 0.5 * cos[i] for i, p in enumerate(candidates)}


def find_howto_workflow(brain: Brain, masked_text: str, llm: LLM) -> Page | None:
    """The approved current workflow a "how do I ..." question is about: same relevance rule as policies (combined BM25 + cosine score of this
    query >= POLICY_MIN_SCORE). The generic General Policy Question workflow is not a process, so it never matches. Best score wins, ties by id."""
    from caregrid.reasoning.extract import is_howto       # lazy: keeps retrieval importable without the extractor

    if not is_howto(masked_text):
        return None
    candidates = [p for p in brain.current_pages(PageType.WORKFLOW) if "general_policy_question" not in p.request_types and p.meta.get("steps")]
    scores = _search_scores(candidates, masked_text, llm)
    ranked = sorted((p for p in candidates if scores[p.id] >= config.POLICY_MIN_SCORE), key=lambda p: (-scores[p.id], p.id))
    if not ranked:
        return None
    rules: dict[str, set] = {}
    for pid in ranked[0].meta.get("policy_ids", []):         # linked policies that disagree: this is not a clean how-to, the normal pipeline (conflict, human) handles it
        pol = brain.get(pid)
        if pol is not None and pol.meta.get("rule_key"):
            rules.setdefault(pol.meta["rule_key"], set()).add(str(pol.meta.get("rule_value")))
    return None if any(len(v) > 1 for v in rules.values()) else ranked[0]


def attach_howto(ret: RetrievalResult, brain: Brain, howto: Page | None) -> None:
    """Record the matched workflow on the retrieval result together with its linked approved current policies."""
    ret.howto_workflow = howto
    ret.howto_policies = [p for pid in (howto.meta.get("policy_ids", []) if howto else [])
                          if (p := brain.get(pid)) is not None and p.type == PageType.POLICY and p.status == PageStatus.APPROVED]


def retrieve(brain: Brain, cls: Classification, masked_text: str, llm: LLM) -> RetrievalResult:
    wf = brain.workflow_for(cls.request_type)
    team = brain.team(str(wf.meta.get("team"))) if wf else None
    fields = [p for f in (wf.meta.get("required_fields", []) if wf else []) if (p := brain.get(field_page_id(f))) is not None]

    # ---- policies (+ regulatory, runbook) -- approved & current only
    candidates = brain.current_pages(PageType.POLICY, PageType.REGULATORY, PageType.RUNBOOK)
    combined = _search_scores(candidates, masked_text, llm)
    by_id = {p.id: p for p in candidates}
    relevant_policy_ids = sorted((p.id for p in candidates if p.type == PageType.POLICY and combined[p.id] >= config.POLICY_MIN_SCORE),
                                 key=lambda i: (-combined[i], i))
    topic = relevant_policy_ids[0] if relevant_policy_ids else "none"
    case_facts = derive_case_facts(wf, cls, topic)

    linked_ids = [pid for pid in (wf.meta.get("policy_ids", []) if wf else []) if pid in by_id and by_id[pid].type == PageType.POLICY]
    chosen: list[tuple[Page, float, bool]] = [(by_id[pid], combined[pid] + LINK_BOOST, True) for pid in linked_ids]   # boost: ranking only
    search_only = sorted(((p, combined[p.id]) for p in candidates if p.id not in linked_ids and combined[p.id] >= SEARCH_FLOOR),
                         key=lambda x: (-x[1], x[0].id))
    chosen += [(p, s, False) for p, s in search_only[: max(TOP_K - len(chosen), 0)]]
    chosen.sort(key=lambda x: (-x[1], x[0].id))

    policies = [ScoredPage(page=p, score=round(s, 4), linked=l, relevance=round(combined[p.id], 4))
                for p, s, l in chosen if p.type != PageType.REGULATORY]
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
