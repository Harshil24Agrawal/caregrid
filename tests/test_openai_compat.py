"""OpenAICompatLLM (Gemini / Groq / Ollama style endpoints). HTTP is mocked with httpx2.MockTransport: no network."""
import json
import socket
import sys
from pathlib import Path

import httpx2
import openai
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from chain import USERS  # noqa: E402

from caregrid import config  # noqa: E402
from caregrid.cli import main  # noqa: E402
from caregrid.ingest.compile import compile_brain  # noqa: E402
from caregrid.ingest.generate import generate  # noqa: E402
from caregrid.knowledge.brain import Brain  # noqa: E402
from caregrid.llm import (  # noqa: E402
    DEFAULT_BACKOFF_S, JSON_INSTRUCTION, LocalRateLimited, MockLLM, OpenAICompatLLM, RateLimiter, get_llm, parse_json_tolerant,
)
from caregrid.models import DecisionCode, State  # noqa: E402
from caregrid.reasoning.pipeline import run  # noqa: E402
from caregrid.seed import seed_trust  # noqa: E402
from caregrid.store import SQLiteStore  # noqa: E402

ASHA = USERS["asha"]
S1 = "What supporting documents are accepted for provider record changes?"
S2 = "Dr. Ramesh Iyer wants to update his billing address to 14 Lake Road, Chennai. NPI 123456789."


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("a test tried to open a real network connection")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(config, "LIGHT_MODEL_ID", "light-m")
    monkeypatch.setattr(config, "STRONG_MODEL_ID", "strong-m")
    monkeypatch.setattr(config, "LLM_MAX_RPM", 1000)


class Clock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def completion(content):
    return httpx2.Response(200, json={"id": "x", "object": "chat.completion", "created": 1, "model": "m", "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]})


def error(status, message="boom", headers=None):
    return httpx2.Response(status, headers=headers or {}, json={"error": {"message": message, "type": "x"}})


def make(handler, clock=None):
    clock = clock or Clock()
    client = openai.OpenAI(base_url="http://fake.local/v1", api_key="k", max_retries=0,
                           http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return OpenAICompatLLM(client=client, sleep=clock.sleep, clock=clock), clock


def recorder(*responses):
    """Handler that records request bodies and plays the given responses in order (the last one repeats)."""
    seen, queue = [], list(responses)

    def handler(request):
        seen.append(json.loads(request.content))
        r = queue.pop(0) if len(queue) > 1 else queue[0]
        return r(request) if callable(r) else r

    return handler, seen


# ================================================================== request shape
def test_json_request_shape_and_tier_to_model_mapping():
    handler, seen = recorder(completion('{"ok": true}'))
    llm, _ = make(handler)
    assert llm.complete_json("SYSTEM PROMPT", "USER PROMPT", "light") == {"ok": True}
    assert llm.complete_json("SYSTEM PROMPT", "USER PROMPT", "strong") == {"ok": True}
    light, strong = seen
    assert (light["model"], strong["model"]) == ("light-m", "strong-m")
    assert light["response_format"] == {"type": "json_object"} and light["temperature"] == 0
    assert light["messages"][0] == {"role": "system", "content": "SYSTEM PROMPT" + JSON_INSTRUCTION}
    assert light["messages"][1] == {"role": "user", "content": "USER PROMPT"}
    assert llm.calls == ["light", "strong"]


def test_complete_text_has_no_json_mode_and_no_json_instruction():
    handler, seen = recorder(completion("plain answer"))
    llm, _ = make(handler)
    assert llm.complete_text("SYS", "hi", "light") == "plain answer"
    assert "response_format" not in seen[0] and seen[0]["messages"][0]["content"] == "SYS"


def test_embed_stays_on_the_embed_provider_and_never_touches_http():
    def handler(request):
        raise AssertionError("embed() must not call the chat endpoint")

    llm, _ = make(handler)
    vecs = llm.embed(["address change", "address change"])
    assert len(vecs[0]) == 512 and vecs[0] == vecs[1] and config.EMBED_PROVIDER == "hashed"


# ================================================================== JSON handling
@pytest.mark.parametrize("content", [
    '{"a": 1, "b": {"c": [1, 2]}}',
    '```json\n{"a": 1, "b": {"c": [1, 2]}}\n```',
    '```\n{"a": 1, "b": {"c": [1, 2]}}\n```',
    'Sure! Here is the JSON you asked for:\n{"a": 1, "b": {"c": [1, 2]}}\nLet me know if you need more.',
    '<think>I should output json {not really}</think>\n{"a": 1, "b": {"c": [1, 2]}}',
    '  \n\n{"a": 1, "b": {"c": [1, 2]}}  ',
])
def test_fences_prefaces_and_reasoning_tags_are_stripped(content):
    handler, _ = recorder(completion(content))
    llm, _ = make(handler)
    assert llm.complete_json("s", "u", "light") == {"a": 1, "b": {"c": [1, 2]}}
    assert parse_json_tolerant(content) == {"a": 1, "b": {"c": [1, 2]}}


def test_unparseable_output_is_retried_once_then_gives_up():
    handler, seen = recorder(completion("I cannot produce JSON, sorry"), completion('{"ok": 1}'))
    llm, _ = make(handler)
    assert llm.complete_json("s", "u", "light") == {"ok": 1} and len(seen) == 2 and llm.calls == ["light"]
    handler, seen = recorder(completion("nope"))
    llm, _ = make(handler)
    with pytest.raises(ValueError, match="valid JSON"):
        llm.complete_json("s", "u", "light")
    assert len(seen) == 2                                                    # exactly one retry


@pytest.mark.parametrize("content", ["[1, 2, 3]", '"just a string"', "", "{broken"])
def test_non_object_output_is_rejected(content):
    handler, _ = recorder(completion(content))
    llm, _ = make(handler)
    with pytest.raises(ValueError):
        llm.complete_json("s", "u", "light")


def test_empty_choices_is_an_empty_answer_not_a_crash():
    handler, _ = recorder(httpx2.Response(200, json={"id": "x", "object": "chat.completion", "created": 1, "model": "m", "choices": []}))
    llm, _ = make(handler)
    assert llm.complete_text("s", "u", "light") == ""


# ================================================================== response_format support
@pytest.mark.parametrize("status,message", [(400, "response_format is not supported by this model"),
                                            (422, "Unsupported parameter: response_format"), (400, "json mode is not supported")])
def test_response_format_rejection_falls_back_to_the_prompt_and_is_remembered(status, message):
    handler, seen = recorder(error(status, message), completion('{"ok": true}'))
    llm, _ = make(handler)
    assert llm.complete_json("s", "u", "light") == {"ok": True}
    assert "response_format" in seen[0] and "response_format" not in seen[1]
    assert llm.complete_json("s", "u", "light") == {"ok": True}
    assert "response_format" not in seen[2]                                  # remembered: no more doomed first attempts
    assert llm._json_mode is False


def test_other_bad_requests_are_not_swallowed():
    handler, _ = recorder(error(400, "model 'light-m' not found"))
    llm, _ = make(handler)
    with pytest.raises(openai.BadRequestError):
        llm.complete_json("s", "u", "light")
    assert llm._json_mode is True


@pytest.mark.parametrize("status,exc", [(401, openai.AuthenticationError), (500, openai.InternalServerError), (403, openai.PermissionDeniedError)])
def test_provider_errors_surface_for_the_caller_to_handle(status, exc):
    handler, _ = recorder(error(status, "nope"))
    llm, _ = make(handler)
    with pytest.raises(exc):
        llm.complete_json("s", "u", "light")


def test_connection_failure_surfaces_instead_of_hanging():
    def handler(request):
        raise httpx2.ConnectError("connection refused", request=request)

    llm, _ = make(handler)
    with pytest.raises(openai.APIConnectionError):
        llm.complete_json("s", "u", "light")


# ================================================================== 429 handling
def test_429_is_retried_once_after_retry_after():
    handler, seen = recorder(error(429, "slow down", {"retry-after": "3"}), completion('{"ok": true}'))
    llm, clock = make(handler)
    assert llm.complete_json("s", "u", "light") == {"ok": True}
    assert clock.slept == [3.0] and len(seen) == 2


def test_429_without_retry_after_uses_the_default_backoff():
    handler, _ = recorder(error(429), completion('{"ok": true}'))
    llm, clock = make(handler)
    llm.complete_json("s", "u", "light")
    assert clock.slept == [DEFAULT_BACKOFF_S]


def test_a_second_429_gives_up():
    handler, seen = recorder(error(429, "quota", {"retry-after": "1"}))
    llm, clock = make(handler)
    with pytest.raises(openai.RateLimitError):
        llm.complete_json("s", "u", "light")
    assert len(seen) == 2 and clock.slept == [1.0]


def test_a_long_retry_after_is_not_waited_for():
    handler, seen = recorder(error(429, "daily quota", {"retry-after": "3600"}))
    llm, clock = make(handler)
    with pytest.raises(openai.RateLimitError):
        llm.complete_json("s", "u", "light")
    assert len(seen) == 1 and clock.slept == []


def test_garbage_retry_after_falls_back_to_default_backoff():
    handler, _ = recorder(error(429, "x", {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}), completion('{"ok": true}'))
    llm, clock = make(handler)
    llm.complete_json("s", "u", "light")
    assert clock.slept == [DEFAULT_BACKOFF_S]


# ================================================================== in-process limiter
def test_rate_limiter_window_and_wait():
    clock = Clock()
    lim = RateLimiter(3, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        lim.acquire(max_wait=5)
        clock.now += 1
    assert clock.slept == []
    with pytest.raises(LocalRateLimited, match="3 requests/minute"):
        lim.acquire(max_wait=5)                                              # next slot is 57s away
    lim.acquire(max_wait=100)                                                # willing to wait: sleeps ~57s then proceeds
    assert 56 < clock.slept[0] < 58
    clock.now += 61
    lim.acquire(max_wait=0)                                                  # window emptied: free slot, no waiting


def test_rate_limiter_is_applied_to_every_http_request(monkeypatch):
    monkeypatch.setattr(config, "LLM_MAX_RPM", 2)
    monkeypatch.setattr(config, "LLM_TIMEOUT_S", 20)
    handler, seen = recorder(completion('{"ok": true}'))
    llm, clock = make(handler)
    llm.complete_json("s", "u", "light")
    llm.complete_json("s", "u", "light")
    with pytest.raises(LocalRateLimited):
        llm.complete_json("s", "u", "light")                                 # would wait ~60s > half the 20s budget
    assert len(seen) == 2
    clock.now += 61
    assert llm.complete_json("s", "u", "light") == {"ok": True}


def test_default_max_rpm_is_10():
    assert config.__dict__["LLM_MAX_RPM"] == 1000                           # fixture override
    import importlib
    import os

    old = os.environ.pop("LLM_MAX_RPM", None)
    try:
        assert importlib.reload(config).LLM_MAX_RPM == 10
    finally:
        if old is not None:
            os.environ["LLM_MAX_RPM"] = old
        importlib.reload(config)


# ================================================================== configuration problems never crash construction
def test_missing_base_url_does_not_crash_and_never_falls_back_to_openai_dot_com(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_COMPAT_BASE_URL", "")
    llm = OpenAICompatLLM()                                                  # constructing is safe
    with pytest.raises(RuntimeError, match="OPENAI_COMPAT_BASE_URL"):
        llm.complete_json("s", "u", "light")


def test_missing_model_id_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(config, "LIGHT_MODEL_ID", "")
    handler, seen = recorder(completion("{}"))
    llm, _ = make(handler)
    with pytest.raises(RuntimeError, match="LIGHT_MODEL_ID"):
        llm.complete_json("s", "u", "light")
    assert seen == []


def test_get_llm_builds_the_provider_without_touching_the_network(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(config, "OPENAI_COMPAT_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(config, "OPENAI_COMPAT_API_KEY", "")
    assert isinstance(get_llm(), OpenAICompatLLM)
    monkeypatch.setattr(config, "LLM_PROVIDER", "nope")
    with pytest.raises(ValueError, match="openai_compat"):
        get_llm()


# ================================================================== through the whole pipeline
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("oc")
    generate(root / "data", eval_dir=root / "eval")
    compile_brain(root / "data", root / "brain")
    return root, Brain(root / "brain")


def fake_server(seen):
    """A tiny 'provider': answers the classifier and proposer prompts with the deterministic mock's JSON."""
    mock = MockLLM()

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        return completion("```json\n" + json.dumps(mock._respond(system, user)) + "\n```")

    return handler


def test_pipeline_runs_on_the_provider_and_sends_only_masked_text(world):
    root, brain = world
    seen = []
    llm, _ = make(fake_server(seen))
    store = SQLiteStore(":memory:")
    seed_trust(store, root / "data")
    s2 = run(S2, ASHA, store, brain, llm)
    s1 = run(S1, ASHA, store, brain, llm)
    assert s2.state == State.NEEDS_INFO and s2.proposal.decision_code == DecisionCode.REQUEST_MISSING_INFO and len(s2.proposal.questions_for_requester) == 3
    assert s1.routing == "auto" and s1.state == State.ANSWERED and any(c.page_id == "KA-02" for c in s1.proposal.citations)
    assert s2.classification.model_used == "light-m" and s2.proposal.model_used == "light-m"      # one relevant policy: light tier (CONTRACTS 6)
    assert s2.llm_tiers_used == ["light"] and s1.llm_tiers_used == ["light", "strong"] and "llm_fallback" not in s2.rules.notes
    assert s1.proposal.model_used == "strong-m"                                                   # two policies + a precedent: strong tier
    wire = json.dumps(seen)
    for raw in ("Ramesh", "Iyer", "Lake Road", "123456789"):
        assert raw not in wire
    assert all(b["model"] in ("light-m", "strong-m") for b in seen)


@pytest.mark.parametrize("handler_factory", [
    lambda: (lambda r: error(429, "quota", {"retry-after": "1"})),
    lambda: (lambda r: error(500, "server exploded")),
    lambda: (lambda r: error(401, "bad key")),
    lambda: (lambda r: completion("I am a chatty model with no JSON")),
    lambda: (lambda r: (_ for _ in ()).throw(httpx2.ConnectError("refused", request=r))),
], ids=["429", "500", "401", "no-json", "connection"])
def test_provider_failure_still_completes_the_case_with_llm_fallback(world, handler_factory):
    root, brain = world
    llm, _ = make(handler_factory())
    store = SQLiteStore(":memory:")
    seed_trust(store, root / "data")
    c = store.get_case(run(S2, ASHA, store, brain, llm).id)
    assert c.rules.notes.count("llm_fallback") == 1
    assert c.classification.request_type == "provider_address_change" and c.proposal.model_used == "deterministic"
    assert c.proposal.decision_code == DecisionCode.REQUEST_MISSING_INFO and len(c.proposal.questions_for_requester) == 3
    assert c.state == State.NEEDS_INFO and c.confidence.score == 75           # decisions and numbers never needed the model


def test_local_rate_limit_also_ends_in_the_fallback_not_a_crash(world, monkeypatch):
    root, brain = world
    monkeypatch.setattr(config, "LLM_MAX_RPM", 1)
    seen = []
    llm, _ = make(fake_server(seen))
    store = SQLiteStore(":memory:")
    first = run(S1, ASHA, store, brain, llm)                                 # classify uses the only slot, propose is refused
    assert first.rules.notes.count("llm_fallback") == 1 and first.proposal.model_used == "deterministic"
    assert len(seen) == 1 and first.state in (State.ANSWERED, State.IN_REVIEW)


# ================================================================== llmcheck
def test_llmcheck_reports_ok_per_tier_for_a_working_endpoint(monkeypatch, capsys):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(config, "OPENAI_COMPAT_BASE_URL", "http://fake.local/v1")
    handler, _ = recorder(completion('{"ok": true}'))
    real = openai.OpenAI
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: real(**{**kw, "max_retries": 0, "http_client": httpx2.Client(transport=httpx2.MockTransport(handler))}))
    assert main(["llmcheck"]) == 0
    out = capsys.readouterr().out
    assert "endpoint=fake.local" in out and "light=light-m" in out
    assert "OK   light" in out and "OK   strong" in out and "OK   embed" in out


def test_llmcheck_fails_cleanly_on_a_rejected_key_or_missing_config(monkeypatch, capsys):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(config, "OPENAI_COMPAT_BASE_URL", "http://fake.local/v1")
    handler, _ = recorder(error(401, "invalid api key"))
    real = openai.OpenAI
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: real(**{**kw, "max_retries": 0, "http_client": httpx2.Client(transport=httpx2.MockTransport(handler))}))
    assert main(["llmcheck"]) == 1
    out = capsys.readouterr().out
    assert "FAIL light" in out and "FAIL strong" in out and "Traceback" not in out
    monkeypatch.setattr(config, "OPENAI_COMPAT_BASE_URL", "")
    assert main(["llmcheck"]) == 1
    out = capsys.readouterr().out
    assert "(OPENAI_COMPAT_BASE_URL not set)" in out and "FAIL light: RuntimeError" in out and "OK   embed" in out
