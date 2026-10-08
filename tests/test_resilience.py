"""Provider resilience (503 like 429), deterministic reset, `cli demo --provider`, pacing and model labels. No network."""
import json
import socket
import sys
from pathlib import Path

import httpx2
import openai
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from chain import USERS  # noqa: E402

from caregrid import cli, config, llm as llm_mod  # noqa: E402
from caregrid.admin import reset_demo  # noqa: E402
from caregrid.cli import main  # noqa: E402
from caregrid.ingest.compile import compile_brain  # noqa: E402
from caregrid.ingest.generate import generate  # noqa: E402
from caregrid.knowledge.brain import Brain  # noqa: E402
from caregrid.llm import DEFAULT_BACKOFF_S, MockLLM, OpenAICompatLLM, RateLimiter, model_name, pace  # noqa: E402
from caregrid.models import DecisionCode, State  # noqa: E402
from caregrid.reasoning.pipeline import run  # noqa: E402
from caregrid.seed import seed_trust  # noqa: E402
from caregrid.store import SQLiteStore  # noqa: E402

S2 = "Dr. Ramesh Iyer wants to update his billing address to 14 Lake Road, Chennai. NPI 123456789."


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
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


def error(status, message="boom", headers=None, status_text=None):
    body = {"error": {"message": message, "type": "x", **({"status": status_text, "code": status} if status_text else {})}}
    return httpx2.Response(status, headers=headers or {}, json=body)


def make(*responses):
    seen, queue, clock = [], list(responses), Clock()

    def handler(request):
        seen.append(json.loads(request.content))
        r = queue.pop(0) if len(queue) > 1 else queue[0]
        return r(request) if callable(r) else r

    client = openai.OpenAI(base_url="http://fake.local/v1", api_key="k", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return OpenAICompatLLM(client=client, sleep=clock.sleep, clock=clock), seen, clock


# ================================================================== 503 / UNAVAILABLE is retried once, like 429
def test_503_is_retried_once_after_the_default_backoff():
    llm, seen, clock = make(error(503, "The model is overloaded. Please try again later.", status_text="UNAVAILABLE"), completion('{"ok": true}'))
    assert llm.complete_json("s", "u", "light") == {"ok": True}
    assert len(seen) == 2 and clock.slept == [DEFAULT_BACKOFF_S]


def test_503_respects_retry_after():
    llm, seen, clock = make(error(503, "busy", {"retry-after": "4"}), completion('{"ok": true}'))
    llm.complete_json("s", "u", "light")
    assert clock.slept == [4.0]


def test_a_second_503_gives_up_and_a_long_retry_after_is_not_waited_for():
    llm, seen, clock = make(error(503, "busy"))
    with pytest.raises(openai.APIStatusError) as e:
        llm.complete_json("s", "u", "light")
    assert e.value.status_code == 503 and len(seen) == 2 and clock.slept == [DEFAULT_BACKOFF_S]
    llm, seen, clock = make(error(503, "down for the day", {"retry-after": "600"}))
    with pytest.raises(openai.APIStatusError):
        llm.complete_json("s", "u", "light")
    assert len(seen) == 1 and clock.slept == []


def test_429_and_503_share_a_single_retry():
    llm, seen, clock = make(error(429, "slow", {"retry-after": "1"}), error(503, "busy"), completion('{"ok": true}'))
    with pytest.raises(openai.APIStatusError):
        llm.complete_json("s", "u", "light")
    assert len(seen) == 2 and clock.slept == [1.0]                       # 429 retried; the 503 that followed was NOT retried again
    llm, seen, _ = make(error(503, "busy"), error(429, "slow", {"retry-after": "1"}), completion('{"ok": true}'))
    with pytest.raises(openai.RateLimitError):
        llm.complete_json("s", "u", "light")
    assert len(seen) == 2


@pytest.mark.parametrize("status", [500, 502, 504, 400 + 1])
def test_other_server_errors_are_not_retried(status):
    llm, seen, clock = make(error(status, "nope"))
    with pytest.raises(openai.APIStatusError):
        llm.complete_json("s", "u", "light")
    assert len(seen) == 1 and clock.slept == []


def test_503_on_a_text_call_is_retried_too():
    llm, seen, _ = make(error(503, "busy"), completion("hello"))
    assert llm.complete_text("s", "u", "strong") == "hello" and len(seen) == 2


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("res")
    generate(root / "data", eval_dir=root / "eval")
    compile_brain(root / "data", root / "brain")
    return root, Brain(root / "brain")


def test_persistent_503_falls_back_with_llm_fallback_and_the_case_still_completes(world):
    root, brain = world
    llm, seen, clock = make(error(503, "overloaded", status_text="UNAVAILABLE"))
    store = SQLiteStore(":memory:")
    seed_trust(store, root / "data")
    c = store.get_case(run(S2, USERS["asha"], store, brain, llm).id)
    assert c.rules.notes.count("llm_fallback") == 1 and c.proposal.model_used == "deterministic"
    assert c.classification.model_used == "fallback:deterministic"
    assert c.proposal.decision_code == DecisionCode.REQUEST_MISSING_INFO and c.state == State.NEEDS_INFO and c.confidence.score == 75
    assert clock.slept and all(s == DEFAULT_BACKOFF_S for s in clock.slept)            # one backoff per failing call, no sleeping loop


def test_a_single_503_is_invisible_to_the_pipeline(world):
    root, brain = world
    from caregrid.reasoning.propose import PROPOSER_SYSTEM  # noqa: F401  (import check)
    mock = MockLLM()
    state = {"first": True}

    def handler(request):
        body = json.loads(request.content)
        if state.pop("first", False):
            return error(503, "blip", status_text="UNAVAILABLE")
        return completion(json.dumps(mock._respond(body["messages"][0]["content"], body["messages"][1]["content"])))

    client = openai.OpenAI(base_url="http://fake.local/v1", api_key="k", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    clock = Clock()
    llm = OpenAICompatLLM(client=client, sleep=clock.sleep, clock=clock)
    store = SQLiteStore(":memory:")
    c = run(S2, USERS["asha"], store, brain, llm)
    assert "llm_fallback" not in c.rules.notes and c.classification.model_used == "light-m" and clock.slept == [DEFAULT_BACKOFF_S]


# ================================================================== labels + pacing
def test_model_labels_come_from_the_llm_instance():
    assert MockLLM().model_name("light") == "mock-light" and MockLLM().model_name("strong") == "mock-strong"
    assert model_name(MockLLM(), "strong") == "mock-strong"
    llm, _, _ = make(completion("{}"))
    assert model_name(llm, "light") == "light-m" and model_name(llm, "strong") == "strong-m"

    class Bare:                                                      # a provider without model_name falls back to the config ids
        pass

    assert model_name(Bare(), "light") == "light-m"


def test_pace_is_a_noop_for_providers_without_a_limit():
    pace(MockLLM())
    pace(object())


def test_rate_limiter_wait_for_blocks_until_enough_slots_are_free():
    clock = Clock()
    lim = RateLimiter(10, clock=clock, sleep=clock.sleep)
    for _ in range(9):
        lim.acquire(max_wait=0)
        clock.now += 1.0                                             # stamps at t = 1000 .. 1008, now = 1009
    lim.wait_for(1)
    assert clock.slept == []                                         # one slot is free
    lim.wait_for(2)                                                  # needs the oldest stamp (t=1000) to expire
    assert clock.slept and 50.9 < clock.slept[0] < 51.1 and clock.now >= 1060
    clock.slept.clear()
    lim.wait_for(10)
    assert clock.slept                                               # now needs ALL stamps to expire
    clock.slept.clear()
    lim.wait_for(10)
    assert clock.slept == []                                         # window is empty again
    big = RateLimiter(3, clock=clock, sleep=clock.sleep)
    big.wait_for(99)                                                 # asking for more than the cap is clamped, never an endless wait


def test_pace_consumes_nothing_so_the_following_calls_are_not_double_counted():
    llm, seen, clock = make(completion('{"ok": true}'))
    llm._limiter = RateLimiter(2, clock=clock, sleep=clock.sleep)
    llm.pace(2)
    assert llm.complete_json("s", "u", "light") == {"ok": True} and llm.complete_json("s", "u", "light") == {"ok": True}
    assert clock.slept == [] and len(seen) == 2


# ================================================================== reset is deterministic and never touches the provider
@pytest.fixture()
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "BRAIN_DIR", tmp_path / "brain")
    monkeypatch.setattr(config, "EVAL_DIR", tmp_path / "eval")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "db.sqlite")
    return tmp_path


def stable(case):
    d = json.loads(case.model_dump_json())
    for k in ("created_at", "state_history", "id"):
        d.pop(k, None)
    return d


def test_reset_demo_always_seeds_with_the_mock_even_when_env_selects_a_provider(paths, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(config, "OPENAI_COMPAT_BASE_URL", "http://fake.local/v1")

    def boom():
        raise AssertionError("reset must not construct the configured provider")

    monkeypatch.setattr(llm_mod, "get_llm", boom)

    class Exploding:
        def __getattr__(self, name):
            raise AssertionError("a caller-supplied llm must be ignored by reset_demo")

    first = reset_demo(llm=Exploding())
    c1 = SQLiteStore().get_case("CASE-1024")
    second = reset_demo()
    c2 = SQLiteStore().get_case("CASE-1024")
    assert first["seeded"] == second["seeded"] == {"trust": 8, "historical_cases": 20, "demo_case": 1}
    assert c1.classification.model_used == "mock-light" and c1.proposal.model_used == "mock-strong" and c1.llm_tiers_used == ["light", "strong"]
    assert stable(c1) == stable(c2)                                                       # identical every run
    assert c1.confidence.score == 95 and c1.rules.risk.value == "high"
    hist = [stable(c) for c in sorted(SQLiteStore().list_cases(), key=lambda c: c.id)]
    reset_demo()
    assert hist == [stable(c) for c in sorted(SQLiteStore().list_cases(), key=lambda c: c.id)]


def test_cli_reset_uses_the_same_deterministic_path(paths, monkeypatch, capsys):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    monkeypatch.setattr(llm_mod, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("provider constructed")))
    assert main(["reset"]) == 0
    assert "seeded: trust=8, historical_cases=20, demo_case=1" in capsys.readouterr().out


# ================================================================== cli demo --provider
def test_cli_demo_provider_switch(paths, monkeypatch, capsys):
    assert main(["reset"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")                  # what .env would say
    constructed = []

    def fake_get_llm():
        constructed.append(1)
        return MockLLM()

    monkeypatch.setattr(llm_mod, "get_llm", fake_get_llm)

    assert main(["demo"]) == 0                                                    # default = mock: the configured provider is never built
    out = capsys.readouterr().out
    assert "LLM provider: mock (forced)" in out and "11/11 scenarios passed" in out and constructed == []
    assert main(["demo", "--provider", "mock"]) == 0 and constructed == []
    capsys.readouterr()

    assert main(["demo", "--provider", "env"]) == 0                               # env: whatever get_llm() returns
    out = capsys.readouterr().out
    assert constructed == [1] and "LLM provider: env -> openai_compat" in out and "11/11 scenarios passed" in out


def test_cli_demo_env_with_a_mock_config_says_so(paths, monkeypatch, capsys):
    assert main(["reset"]) == 0
    capsys.readouterr()
    assert config.LLM_PROVIDER == "mock"
    assert main(["demo", "--provider", "env"]) == 0
    assert "LLM_PROVIDER resolves to mock" in capsys.readouterr().out


def test_demo_paces_a_rate_limited_provider_before_every_pipeline_run(paths, monkeypatch, capsys):
    assert main(["reset"]) == 0
    paced = []

    class Recording(MockLLM):
        def pace(self, calls=2):
            paced.append(calls)

    monkeypatch.setattr(llm_mod, "get_llm", lambda: Recording())
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compat")
    assert main(["demo", "--provider", "env"]) == 0
    assert len(paced) >= 8 and set(paced) == {2}            # S1 S2 S3 S4a S4b CASE-1024 S6.1 S6.2


def test_cli_exposes_llm_for_and_ignores_provider_for_other_commands(capsys):
    assert cli.llm_for("mock").model_name("light") == "mock-light"
    assert main(["lint", "--provider", "env"]) in (0, 1)         # accepted everywhere, only demo/eval use it


# ================================================================== hygiene: scratch files never get committed
def test_gitignore_covers_scratch_scripts_and_none_are_tracked():
    root = Path(__file__).resolve().parent.parent
    assert "tmp_*.py" in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    import subprocess

    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.splitlines()
    assert [f for f in tracked if Path(f).name.startswith("tmp_")] == []
