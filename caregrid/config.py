"""Env-driven settings. Values are read once at import; override via environment or .env."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", encoding="utf-8")


def _str(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


def _path(name: str, default: str) -> Path:
    p = Path(_str(name, default))
    return p if p.is_absolute() else ROOT / p


# "Today" for date rules (retroactive changes). Override with CAREGRID_TODAY=YYYY-MM-DD for deterministic runs.
TODAY = date.fromisoformat(os.environ.get("CAREGRID_TODAY") or date.today().isoformat())

LLM_PROVIDER = _str("LLM_PROVIDER", "mock").lower()  # mock | bedrock | anthropic
AWS_REGION = _str("AWS_REGION", "ap-south-1")
LLM_TIMEOUT_S = _float("LLM_TIMEOUT_S", 20)   # per LLM call; on timeout the deterministic path takes over
LLM_MAX_RPM = _int("LLM_MAX_RPM", 10)          # in-process requests-per-minute cap for openai_compat (free tiers are rate limited)
OPENAI_COMPAT_BASE_URL = _str("OPENAI_COMPAT_BASE_URL", "")   # any OpenAI-compatible endpoint: Gemini, Groq, Ollama, ...
OPENAI_COMPAT_API_KEY = _str("OPENAI_COMPAT_API_KEY", "")

_DEFAULT_MODELS = {
    "anthropic": ("claude-haiku-5-5", "claude-sonnet-5-5"),
    "bedrock": ("", ""),  # env-only: set LIGHT_MODEL_ID / STRONG_MODEL_ID
    "openai_compat": ("", ""),  # env-only: model names depend on the endpoint (Gemini, Groq, Ollama, ...)
    "mock": ("mock-light", "mock-strong"),
}
_light, _strong = _DEFAULT_MODELS.get(LLM_PROVIDER, _DEFAULT_MODELS["mock"])
LIGHT_MODEL_ID = _str("LIGHT_MODEL_ID", _light)
STRONG_MODEL_ID = _str("STRONG_MODEL_ID", _strong)
# Embeddings are independent of LLM_PROVIDER: similarity thresholds are tuned on "hashed".
EMBED_PROVIDER = _str("EMBED_PROVIDER", "hashed").lower()  # hashed | bedrock
EMBED_MODEL_ID = _str("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")

POLICY_MIN_SCORE = _float("POLICY_MIN_SCORE", 0.35)
PRECEDENT_MIN_SIM = _float("PRECEDENT_MIN_SIM", 0.6)
TRUST_L1_STREAK = _int("TRUST_L1_STREAK", 10)
TRUST_L1_RATIO = _float("TRUST_L1_RATIO", 0.90)
TRUST_L2_REVIEWS = _int("TRUST_L2_REVIEWS", 25)
TRUST_L2_RATIO = _float("TRUST_L2_RATIO", 0.95)

DATA_DIR = _path("DATA_DIR", "data/synthetic")
BRAIN_DIR = _path("BRAIN_DIR", "second_brain")
DB_PATH = _path("DB_PATH", "data/caregrid.sqlite")
EVAL_DIR = ROOT / "eval"

STORE_BACKEND = _str("STORE_BACKEND", "sqlite")  # sqlite | dynamodb (later)
COMMS_EMAIL = _str("COMMS_EMAIL", "simulated")  # simulated | sns (later)
