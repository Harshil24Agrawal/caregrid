"""LLM interface with mock / bedrock / anthropic providers (CONTRACTS §3, PROMPTS §6)."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, Protocol

import numpy as np

from caregrid import config

Tier = Literal["light", "strong"]
EMBED_DIM = 512


# ---------------------------------------------------------------- helpers
def hashed_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic hashed bag-of-words, L2-normalised. Used by MockLLM and as offline fallback."""
    vec = np.zeros(dim, dtype=np.float64)
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec.tolist()


def parse_json_tolerant(raw: str) -> dict:
    """Parse model output as a JSON object, tolerating code fences and surrounding prose."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in model output")
        try:
            out = json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON in model output: {e}") from e
    if not isinstance(out, dict):
        raise ValueError("model output is not a JSON object")
    return out


class LLM(Protocol):
    calls: list[str]  # tier of every call made, for cost metrics

    def complete_json(self, system: str, user: str, tier: Tier) -> dict: ...
    def complete_text(self, system: str, user: str, tier: Tier) -> str: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class _JsonRetryMixin:
    """complete_json = complete_text + tolerant parse; retry once, then raise (callers fall back to mock path)."""

    calls: list[str]

    def complete_text(self, system: str, user: str, tier: Tier) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def complete_json(self, system: str, user: str, tier: Tier) -> dict:
        err: Exception | None = None
        for _ in range(2):
            try:
                return parse_json_tolerant(self.complete_text(system, user, tier))
            except ValueError as e:
                err = e
        raise ValueError(f"model did not return valid JSON after retry: {err}")


# ---------------------------------------------------------------- mock
_CLINICAL = re.compile(r"\b(dose|dosage|medication|symptom|diagnos\w*|treatment)\b|should .* take|double .* dose", re.I)
_TYPE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("provider_address_change", re.compile(r"address", re.I)),
    ("provider_name_change", re.compile(r"name change|changed name|rename|legally changed", re.I)),
    ("portal_access_reset", re.compile(r"password|login|log in|portal access|locked", re.I)),
    ("prior_auth_status", re.compile(r"prior auth|authorization status", re.I)),
    ("dme_equipment_request", re.compile(r"wheelchair|oxygen|equipment|\bDME\b", re.I)),
    ("claim_status_inquiry", re.compile(r"\bclaim", re.I)),
    ("complaint_grievance", re.compile(r"complain|grievance|lawyer|unacceptable", re.I)),
    ("general_policy_question", re.compile(r"what documents|how do i|policy|supporting documents", re.I)),
]


class MockLLM(_JsonRetryMixin):
    """Deterministic, offline. Keyword classifier + template proposer; Phase 4 refines the proposer."""

    def __init__(self) -> None:
        self.calls = []

    def complete_text(self, system: str, user: str, tier: Tier) -> str:
        self.calls.append(tier)
        return json.dumps(self._respond(system, user))

    def complete_json(self, system: str, user: str, tier: Tier) -> dict:
        self.calls.append(tier)
        return self._respond(system, user)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hashed_embedding(t) for t in texts]

    # -- behaviours
    def _respond(self, system: str, user: str) -> dict:
        if "classify healthcare operations requests" in system:
            return self._classify(user)
        if "draft a minimal edit" in system:
            m = re.search(r"CURRENT ARTICLE \[[^\]]*\]:\n(.*?)\n\nCASE SUMMARY", user, re.S)
            return {"proposed_body": m.group(1) if m else "", "reason": "mock: no change drafted"}
        if "You are CareGrid" in system:
            return {
                "answer_text": "1. A team member will review this request.",
                "next_steps": [],
                "questions_for_requester": [],
                "summary_for_reviewer": "Mock summary.",
                "citations": [],
            }
        return {"ok": True}

    @staticmethod
    def _classify(user: str) -> dict:
        text = user.split("REQUEST:", 1)[-1].strip()
        request_type = next((t for t, rx in _TYPE_RULES if rx.search(text)), "unknown")
        return {
            "request_type": request_type,
            "confidence": 0.9 if request_type != "unknown" else 0.3,
            "extracted_fields": {},
            "urgency": "normal",
            "sentiment": "negative" if request_type == "complaint_grievance" else "neutral",
            "is_clinical": bool(_CLINICAL.search(text)),
            "is_account_specific": request_type in {"prior_auth_status", "claim_status_inquiry"},
            "is_sensitive": request_type == "complaint_grievance",
        }


# ---------------------------------------------------------------- bedrock
class BedrockLLM(_JsonRetryMixin):
    def __init__(self) -> None:
        import boto3

        self.calls = []
        self._client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)

    def complete_text(self, system: str, user: str, tier: Tier) -> str:
        self.calls.append(tier)
        model = config.LIGHT_MODEL_ID if tier == "light" else config.STRONG_MODEL_ID
        resp = self._client.converse(
            modelId=model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": 1500, "temperature": 0.0},
        )
        return "".join(b.get("text", "") for b in resp["output"]["message"]["content"])

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            resp = self._client.invoke_model(
                modelId=config.EMBED_MODEL_ID,
                body=json.dumps({"inputText": t, "dimensions": EMBED_DIM, "normalize": True}),
            )
            out.append(json.loads(resp["body"].read())["embedding"])
        return out


# ---------------------------------------------------------------- anthropic
class AnthropicLLM(_JsonRetryMixin):
    """Anthropic has no embeddings endpoint, so embed() uses the deterministic hashed embedding."""

    def __init__(self) -> None:
        import anthropic

        self.calls = []
        self._client = anthropic.Anthropic()

    def complete_text(self, system: str, user: str, tier: Tier) -> str:
        self.calls.append(tier)
        model = config.LIGHT_MODEL_ID if tier == "light" else config.STRONG_MODEL_ID
        msg = self._client.messages.create(
            model=model, max_tokens=1500, system=system, messages=[{"role": "user", "content": user}]
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hashed_embedding(t) for t in texts]


def get_llm() -> LLM:
    provider = config.LLM_PROVIDER
    if provider == "mock":
        return MockLLM()
    if provider == "bedrock":
        return BedrockLLM()
    if provider == "anthropic":
        return AnthropicLLM()
    raise ValueError(f"unknown LLM_PROVIDER {provider!r} (expected mock | bedrock | anthropic)")
