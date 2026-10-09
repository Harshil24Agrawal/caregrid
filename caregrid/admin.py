"""Operations the UI and the CLI share. reset_demo() is what `python -m caregrid.cli reset` runs and what the UI's "Reset demo" button calls."""
from __future__ import annotations

from collections.abc import Callable

from caregrid import config
from caregrid.ingest.compile import compile_brain
from caregrid.ingest.generate import generate
from caregrid.ingest.leakscan import leak_scan
from caregrid.knowledge.brain import Brain
from caregrid.llm import LLM, MockLLM
from caregrid.seed import seed_all
from caregrid.store import SQLiteStore


def reset_demo(llm: LLM | None = None, brain: Brain | None = None, echo: Callable[[str], None] | None = None) -> dict:
    from caregrid import alerts

    with alerts.suppressed():                                    # a reset never publishes, and leaves no alert dedup entries behind
        return _reset_demo(llm, brain, echo)


def _reset_demo(llm: LLM | None = None, brain: Brain | None = None, echo: Callable[[str], None] | None = None) -> dict:
    """Clean, repeatable demo state: wipe SQLite, regenerate the synthetic data, recompile the Second Brain, scan it for PII, then
    seed trust, ~20 historical cases and CASE-1024 (through the real pipeline).

    Seeding ALWAYS uses the mock LLM, whatever LLM_PROVIDER / .env say, so CASE-1024 and the seeded history are identical on every
    run and a reset never spends provider quota. (`llm` is accepted for backward compatibility and ignored.)

    Pass the app's cached `brain` and it is reloaded from disk, so nothing stale (e.g. precedents learned before the reset) survives.
    Returns {"brain": {page type: count, "stale_precedents": n}, "leak_findings": [...messages], "seeded": {...}, "db": path}."""
    say = echo or (lambda _msg: None)
    store = SQLiteStore()
    store.wipe()
    say(f"wiped {config.DB_PATH}")
    generate(config.DATA_DIR, eval_dir=config.EVAL_DIR)
    say(f"generated synthetic data in {config.DATA_DIR}")
    counts = compile_brain(config.DATA_DIR, config.BRAIN_DIR)
    findings = [f.message for f in leak_scan(config.BRAIN_DIR, config.DATA_DIR)]
    live = brain if brain is not None else Brain(config.BRAIN_DIR)
    if brain is not None:
        brain.reload()
    seeded = seed_all(store, live, MockLLM(), config.DATA_DIR)
    return {"brain": counts, "leak_findings": findings, "seeded": seeded, "db": str(config.DB_PATH)}
