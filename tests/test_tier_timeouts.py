"""Per-tier timeouts: strong times out -> ONE retry on the light model -> deterministic fallback; llm_tiers_used = tiers that answered."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_phase5 import LLM, NAME_A, S1, env, world, data_dir  # noqa: E402,F401

from caregrid import config  # noqa: E402
from caregrid.llm import MockLLM, complete_json_tiered, timeout_for  # noqa: E402


def set_cfg(monkeypatch, generic=None, light=None, strong=None):
    monkeypatch.setattr(config, "LLM_TIMEOUT_S", generic)
    monkeypatch.setattr(config, "LLM_TIMEOUT_LIGHT_S", light)
    monkeypatch.setattr(config, "LLM_TIMEOUT_STRONG_S", strong)


def test_timeout_resolution(monkeypatch):
    set_cfg(monkeypatch)
    assert (timeout_for("light"), timeout_for("strong")) == (15, 35)
    set_cfg(monkeypatch, generic=20)
    assert (timeout_for("light"), timeout_for("strong")) == (20, 20)          # LLM_TIMEOUT_S is the fallback for both
    set_cfg(monkeypatch, generic=20, light=7, strong=40)
    assert (timeout_for("light"), timeout_for("strong")) == (7, 40)           # per-tier settings win


class TierLLM(MockLLM):
    """Strong tier misbehaves in the given way; light answers normally (via the mock)."""
    def __init__(self, strong_mode):
        super().__init__()
        self.strong_mode = strong_mode

    def complete_json(self, system, user, tier):
        if tier == "strong":
            self.calls.append(tier)
            if self.strong_mode == "slow":
                time.sleep(1.0)
            raise RuntimeError("strong down")
        return super().complete_json(system, user, tier)


@pytest.mark.parametrize("mode", ["slow", "raise"])
def test_strong_failure_retries_once_on_light(monkeypatch, mode):
    set_cfg(monkeypatch, light=2, strong=0.2)
    llm = TierLLM(mode)
    raw, used = complete_json_tiered(llm, "You classify.", "REQUEST:\nhello", "strong")
    assert used == "light" and isinstance(raw, dict) and llm.tiers_used == ["light"] and llm.calls == ["strong", "light"]


def test_light_failure_is_not_retried(monkeypatch):
    set_cfg(monkeypatch, light=0.2, strong=0.2)

    class Down(MockLLM):
        def complete_json(self, *a):
            self.calls.append(a[2])
            raise RuntimeError("down")
    llm = Down()
    with pytest.raises(RuntimeError):
        complete_json_tiered(llm, "s", "u", "light")
    assert llm.calls == ["light"] and llm.tiers_used == []


S3 = "A clinic staff member is locked out of the provider portal, email staff@clinic.example, provider NPI 1234567890. Can we reset it?"


def test_pipeline_downgrades_then_falls_back(env, monkeypatch):
    from caregrid.reasoning.pipeline import run
    set_cfg(monkeypatch, light=2, strong=0.2)
    c = run(S3, env.asha, env.store, env.brain, TierLLM("slow"))
    c = env.store.get_case(c.id)
    assert "strong" not in c.llm_tiers_used and c.llm_tiers_used == ["light"]            # recorded: the tier that answered
    assert "llm_fallback" not in c.rules.notes and c.proposal.model_used == "mock-light"
    assert "llm_tier_downgrade" in c.rules.notes

    class AllDown(MockLLM):
        def complete_json(self, *a):
            self.calls.append(a[2])
            raise RuntimeError("down")
    c = env.store.get_case(run(S3, env.asha, env.store, env.brain, AllDown()).id)
    assert c.rules.notes.count("llm_fallback") == 1 and c.llm_tiers_used == [] and c.proposal.model_used == "deterministic"


def test_mock_unaffected(env):
    c = env.store.get_case(env.run(S1).id)
    assert set(c.llm_tiers_used) <= {"light", "strong"} and c.proposal.model_used.startswith("mock")


# ---------------------------------------------------------------- failure TYPES (audit + case notes, never payloads)
def test_failure_type_mapping():
    from caregrid.llm import failure_type

    class E(Exception):
        def __init__(self, code):
            super().__init__("SECRET PAYLOAD")
            self.status_code = code
    assert failure_type(TimeoutError()) == "timeout" and failure_type(E(429)) == "http_429" and failure_type(E(503)) == "http_503"
    assert failure_type(E(500)) == "other" and failure_type(ValueError("bad json")) == "parse_error" and failure_type(RuntimeError()) == "other"


def test_pipeline_logs_failure_types_without_payloads(env, monkeypatch):
    from caregrid.reasoning.pipeline import run
    set_cfg(monkeypatch, light=2, strong=0.2)
    c = env.store.get_case(run(S3, env.asha, env.store, env.brain, TierLLM("slow")).id)
    ev = [e for e in env.store.list_audit(c.id) if e.event == "llm_failure"]
    assert len(ev) == 1 and ev[0].details["types"] == ["timeout"]
    assert "llm_failure: timeout" in c.rules.notes

    class Payload(MockLLM):
        def complete_json(self, *a):
            self.calls.append(a[2])
            e = RuntimeError("echo of prompt: staff@clinic.example")
            e.status_code = 503
            raise e
    c = env.store.get_case(run(S3, env.asha, env.store, env.brain, Payload()).id)
    ev = [e for e in env.store.list_audit(c.id) if e.event == "llm_failure"]
    assert ev[0].details["types"] == ["http_503", "http_503", "http_503"] or set(ev[0].details["types"]) == {"http_503"}
    assert "staff@clinic" not in str([e.model_dump_json() for e in env.store.list_audit(c.id)]) + " ".join(c.rules.notes)
    assert "llm_failure: http_503" in c.rules.notes and "llm_fallback" in c.rules.notes
