"""LLM interface with mock / bedrock / anthropic providers (CONTRACTS §3, PROMPTS §6)."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import deque
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
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I).strip()      # reasoning models (e.g. local r1 builds)
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


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed by config.EMBED_PROVIDER, never by LLM_PROVIDER."""
    provider = config.EMBED_PROVIDER
    if provider == "hashed":
        return [hashed_embedding(t) for t in texts]
    if provider == "bedrock":
        import boto3

        client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)
        out = []
        for t in texts:
            resp = client.invoke_model(
                modelId=config.EMBED_MODEL_ID,
                body=json.dumps({"inputText": t, "dimensions": EMBED_DIM, "normalize": True}),
            )
            out.append(json.loads(resp["body"].read())["embedding"])
        return out
    raise ValueError(f"unknown EMBED_PROVIDER {provider!r} (expected hashed | bedrock)")


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


def call_with_timeout(fn, timeout_s: float):
    """Run fn() with a wall-clock limit. Raises TimeoutError; the worker thread is abandoned (never blocks the caller)."""
    from concurrent.futures import ThreadPoolExecutor

    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn).result(timeout=timeout_s)
    finally:
        ex.shutdown(wait=False)


_CTX_HEADER = re.compile(r"^\[([A-Za-z0-9-]+)(?: v(\d+))? \| (\w+) \| (.*)\]$", re.M)
_RULES_LINE = re.compile(r"decision_code=(\w+); route_team=([\w-]+|None);")


def mock_propose(user: str) -> dict:
    """PROMPTS §6: template strings filled from the RULES block and the first lines of the context pages.
    Only text that is present in the prompt is used, so the number guard always accepts it."""
    m = _RULES_LINE.search(user)
    decision, team = (m.group(1), m.group(2)) if m else ("route_to_team", "None")
    body = user.rsplit("CONTEXT PAGES:", 1)[-1]
    heads = list(_CTX_HEADER.finditer(body))
    blocks = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        blocks.append((h.group(1), h.group(3), h.group(4), body[h.end():end].strip()))
    policies = [b for b in blocks if b[1] == "policy"]
    first_sentence = (policies[0][3].split(". ")[0].strip().rstrip(".") + ".") if policies and policies[0][3] else ""
    where = team if team != "None" else "the right team"
    if decision == "answer_from_policy" and first_sentence:
        answer = f"1. {first_sentence}\n2. See {policies[0][0]} for the full guidance."
    elif decision == "request_missing_info":
        answer = "1. Thank you. A few details are still needed before this can move forward.\n2. Please send them all in one reply."
    elif decision == "escalate_senior":
        answer = f"1. This request needs senior review and has been prepared for {where}."
    elif decision == "not_enough_evidence":
        answer = f"1. There is not enough approved guidance to answer this yet.\n2. It has been passed to {where}."
    else:
        answer = f"1. Your request has been prepared for {where}.\n2. A specialist will review it."
    cites = ([b[0] for b in blocks if b[1] == "policy"][:1] + [b[0] for b in blocks if b[1] == "workflow"]
             + [b[0] for b in blocks if b[1] == "precedent"][:1])
    return {
        "answer_text": answer,
        "next_steps": ["A specialist will review the request."],
        "questions_for_requester": [],
        "summary_for_reviewer": f"Prepared for {where} with decision {decision}.",
        "citations": cites,
    }


# ---------------------------------------------------------------- mock
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
        return embed_texts(texts)

    # -- behaviours
    def _respond(self, system: str, user: str) -> dict:
        if "classify healthcare operations requests" in system:
            return self._classify(user)
        if "draft a minimal edit" in system:
            m = re.search(r"CURRENT ARTICLE \[[^\]]*\]:\n(.*?)\n\nCASE SUMMARY", user, re.S)
            return {"proposed_body": m.group(1) if m else "", "reason": "mock: no change drafted"}
        if "You are CareGrid" in system:
            return mock_propose(user)
        return {"ok": True}

    @staticmethod
    def _classify(user: str) -> dict:
        from caregrid.reasoning.extract import extract_fields, keyword_type
        from caregrid.reasoning.guards import CLINICAL

        text = user.split("REQUEST:", 1)[-1].strip()
        request_type, hit = keyword_type(text)
        return {
            "request_type": request_type,
            "confidence": 0.9 if hit else 0.3,
            "extracted_fields": extract_fields(text),
            "urgency": "normal",
            "sentiment": "negative" if request_type == "complaint_grievance" else "neutral",
            "is_clinical": bool(CLINICAL.search(text)),
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
        return embed_texts(texts)


# ---------------------------------------------------------------- anthropic
class AnthropicLLM(_JsonRetryMixin):
    """Anthropic has no embeddings endpoint; embed() follows EMBED_PROVIDER like every provider."""

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
        return embed_texts(texts)


# ---------------------------------------------------------------- openai-compatible (free tiers, local models)
JSON_INSTRUCTION = "\n\nRespond with ONE JSON object only. No text before or after it and no markdown code fences."
RETRY_AFTER_CAP_S = 10.0          # never sleep longer than this on a 429; beyond it the deterministic fallback is faster
DEFAULT_BACKOFF_S = 2.0


class LocalRateLimited(RuntimeError):
    """The in-process requests-per-minute cap would make the call wait longer than its budget."""


class RateLimiter:
    """Sliding 60-second window. acquire() waits for a free slot, or raises LocalRateLimited if that would exceed max_wait."""

    def __init__(self, max_rpm: int, clock=time.monotonic, sleep=time.sleep) -> None:
        self.max_rpm, self._clock, self._sleep = max(1, int(max_rpm)), clock, sleep
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, max_wait: float) -> None:
        while True:
            with self._lock:
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= 60.0:
                    self._stamps.popleft()
                if len(self._stamps) < self.max_rpm:
                    self._stamps.append(now)
                    return
                wait = self._stamps[0] + 60.0 - now
            if wait > max_wait:
                raise LocalRateLimited(f"local rate limit of {self.max_rpm} requests/minute reached; next slot in {wait:.0f}s")
            self._sleep(wait)


def _retry_after_seconds(err) -> float | None:
    try:
        value = err.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


class OpenAICompatLLM(_JsonRetryMixin):
    """Any OpenAI-compatible chat endpoint (Gemini's OpenAI endpoint, Groq, Ollama, ...) via the `openai` package.

    Never crashes the caller: configuration problems and provider errors raise from the call, where classify()/propose()
    catch them and switch to the deterministic path (note `llm_fallback`). embed() follows EMBED_PROVIDER (hashed by default).
    """

    def __init__(self, client=None, sleep=time.sleep, clock=time.monotonic) -> None:
        import openai

        self.calls: list[str] = []
        self._openai, self._sleep = openai, sleep
        self._json_mode = True                               # flipped off once the endpoint rejects response_format
        self._limiter = RateLimiter(config.LLM_MAX_RPM, clock=clock, sleep=sleep)
        self._config_error = None if (client is not None or config.OPENAI_COMPAT_BASE_URL) else \
            "OPENAI_COMPAT_BASE_URL is not set (refusing to fall back to api.openai.com)"
        self._client = client
        if client is None and not self._config_error:
            self._client = openai.OpenAI(base_url=config.OPENAI_COMPAT_BASE_URL, api_key=config.OPENAI_COMPAT_API_KEY or "not-needed",
                                         timeout=config.LLM_TIMEOUT_S, max_retries=0)

    # -- transport
    def _chat(self, system: str, user: str, tier: Tier, want_json: bool) -> str:
        if self._config_error:
            raise RuntimeError(self._config_error)
        model = config.LIGHT_MODEL_ID if tier == "light" else config.STRONG_MODEL_ID
        if not model:
            raise RuntimeError(f"{'LIGHT' if tier == 'light' else 'STRONG'}_MODEL_ID is not set")
        kwargs = {"model": model, "temperature": 0, "max_tokens": 1500,
                  "messages": [{"role": "system", "content": system + (JSON_INSTRUCTION if want_json else "")},
                               {"role": "user", "content": user}]}
        if want_json and self._json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        retried_429 = False
        while True:
            self._limiter.acquire(max_wait=max(config.LLM_TIMEOUT_S / 2, 1.0))
            try:
                resp = self._client.chat.completions.create(**kwargs)
                break
            except self._openai.RateLimitError as e:
                wait = _retry_after_seconds(e)
                wait = DEFAULT_BACKOFF_S if wait is None else wait
                if retried_429 or wait > RETRY_AFTER_CAP_S:
                    raise
                retried_429 = True
                self._sleep(max(wait, 0.0))
            except (self._openai.BadRequestError, self._openai.UnprocessableEntityError) as e:
                msg = str(e).lower()
                if "response_format" in kwargs and ("response_format" in msg or "json" in msg):
                    self._json_mode = False                  # this endpoint cannot do JSON mode: rely on the prompt + tolerant parser
                    kwargs.pop("response_format", None)
                    continue
                raise
        return (resp.choices[0].message.content or "") if resp.choices else ""

    # -- LLM protocol
    def complete_text(self, system: str, user: str, tier: Tier) -> str:
        self.calls.append(tier)
        return self._chat(system, user, tier, want_json=False)

    def complete_json(self, system: str, user: str, tier: Tier) -> dict:
        self.calls.append(tier)
        err: Exception | None = None
        for _ in range(2):                                   # one retry on unparseable output, then the caller falls back
            try:
                return parse_json_tolerant(self._chat(system, user, tier, want_json=True))
            except ValueError as e:
                err = e
        raise ValueError(f"model did not return valid JSON after retry: {err}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return embed_texts(texts)


def get_llm() -> LLM:
    provider = config.LLM_PROVIDER
    if provider == "mock":
        return MockLLM()
    if provider == "bedrock":
        return BedrockLLM()
    if provider == "anthropic":
        return AnthropicLLM()
    if provider == "openai_compat":
        return OpenAICompatLLM()
    raise ValueError(f"unknown LLM_PROVIDER {provider!r} (expected mock | bedrock | anthropic | openai_compat)")
