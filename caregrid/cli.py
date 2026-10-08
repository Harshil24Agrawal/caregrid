"""CLI entry: python -m caregrid.cli <command>."""
from __future__ import annotations

import argparse
import sys

from caregrid import config

# command -> phase that implements it (stubs until then)
_STUBS: dict[str, int] = {}
_COMMANDS = ["data", "brain", "leakscan", "lint", "reset", "demo", "eval", "check", "serve", *_STUBS]


def _short_err(e: Exception) -> str:
    msg = " ".join(str(e).split())
    return f"{type(e).__name__}: {msg[:200]}"


def llmcheck() -> int:
    """One light + one strong complete_json call and one embed call. Never raises on provider errors."""
    from caregrid.llm import get_llm

    where = f"region={config.AWS_REGION}"
    if config.LLM_PROVIDER == "openai_compat":
        from urllib.parse import urlparse

        where = f"endpoint={urlparse(config.OPENAI_COMPAT_BASE_URL).netloc or '(OPENAI_COMPAT_BASE_URL not set)'} max_rpm={config.LLM_MAX_RPM}"
    print(f"provider={config.LLM_PROVIDER} light={config.LIGHT_MODEL_ID or '(unset)'} strong={config.STRONG_MODEL_ID or '(unset)'} {where}")
    try:
        llm = get_llm()
    except Exception as e:  # missing creds / SDK / bad provider
        print(f"FAIL init: {_short_err(e)}")
        return 1

    failed = 0
    for tier in ("light", "strong"):
        try:
            out = llm.complete_json('Return JSON only: {"ok": true}', "Reply with the JSON object now.", tier)
            print(f"OK   {tier}: {out}")
        except Exception as e:
            failed += 1
            print(f"FAIL {tier}: {_short_err(e)}")
    try:
        vecs = llm.embed(["address change for a provider"])
        print(f"OK   embed: {len(vecs)} vector(s) x {len(vecs[0])} dims")
    except Exception as e:
        failed += 1
        print(f"FAIL embed: {_short_err(e)}")
    return 1 if failed else 0


def cmd_data() -> int:
    from caregrid.ingest.generate import generate

    generate(config.DATA_DIR, eval_dir=config.EVAL_DIR)
    print(f"generated synthetic data in {config.DATA_DIR} and {config.EVAL_DIR / 'requests_eval.csv'}")
    return 0


def cmd_brain() -> int:
    from caregrid.ingest.compile import compile_brain
    from caregrid.ingest.leakscan import leak_scan

    counts = compile_brain(config.DATA_DIR, config.BRAIN_DIR)
    findings = leak_scan(config.BRAIN_DIR, config.DATA_DIR)
    print(f"compiled second brain in {config.BRAIN_DIR}")
    for k, v in counts.items():
        print(f"  {k:<18}{v}")
    print(f"leak scan findings: {len(findings)}")
    for f in findings:
        print(f"  PII_LEAK {f.message}")
    return 1 if findings else 0


def cmd_leakscan() -> int:
    """Scan the Second Brain files AND the SQLite cases/audit/comms rows for PII."""
    from caregrid.ingest.leakscan import leak_scan, leak_scan_store
    from caregrid.store import SQLiteStore

    findings = leak_scan(config.BRAIN_DIR, config.DATA_DIR)
    print(f"second_brain: {len(findings)} finding(s)")
    store_findings = []
    if config.DB_PATH.exists():
        store_findings = leak_scan_store(SQLiteStore(), config.DATA_DIR)
        print(f"sqlite (cases, audit, comms): {len(store_findings)} finding(s)")
    else:
        print("sqlite: no database yet, skipped")
    for f in [*findings, *store_findings]:
        print(f"  PII_LEAK {f.message}")
    return 1 if findings or store_findings else 0


def cmd_lint() -> int:
    from caregrid.knowledge.brain import Brain
    from caregrid.knowledge.lint import lint
    from caregrid.store import SQLiteStore

    findings = lint(Brain(config.BRAIN_DIR), SQLiteStore() if config.DB_PATH.exists() else None)
    print(f"lint: {len(findings)} finding(s)")
    for sev in ("error", "warning", "info"):
        group = [f for f in findings if f.severity == sev]
        if group:
            print(f"\n{sev.upper()} ({len(group)})")
            for f in group:
                print(f"  [{f.code}] {f.message}")
    return 0


def cmd_reset() -> int:
    """Clean demo state: wipe SQLite, regenerate data, recompile the brain, then seed trust, ~20 historical cases and CASE-1024."""
    from caregrid.admin import reset_demo

    result = reset_demo(echo=print)
    print(f"compiled second brain in {config.BRAIN_DIR}")
    for k, v in result["brain"].items():
        print(f"  {k:<18}{v}")
    print(f"leak scan findings: {len(result['leak_findings'])}")
    for msg in result["leak_findings"]:
        print(f"  PII_LEAK {msg}")
    print("seeded: " + ", ".join(f"{k}={v}" for k, v in result["seeded"].items()))
    return 1 if result["leak_findings"] else 0


def llm_for(provider: str):
    """--provider mock (default, deterministic, offline) | env (whatever LLM_PROVIDER / .env configure)."""
    from caregrid.llm import MockLLM, get_llm

    if provider == "mock":
        return MockLLM()
    llm = get_llm()
    if config.LLM_PROVIDER == "mock":
        print("note: --provider env, but LLM_PROVIDER resolves to mock (set it in .env)")
    return llm


def cmd_demo(provider: str = "mock") -> int:
    """Run the acceptance scenarios headless against a throw-away in-memory store."""
    from caregrid.demo import run_demo
    from caregrid.knowledge.brain import Brain
    from caregrid.store import SQLiteStore

    if not (config.BRAIN_DIR / "index.md").exists():
        print("second_brain/ is empty: run `python -m caregrid.cli reset` first.")
        return 1
    import shutil
    import tempfile
    from pathlib import Path

    llm = llm_for(provider)
    label = "mock (forced)" if provider == "mock" else f"env -> {config.LLM_PROVIDER}"
    print(f"LLM provider: {label} | EMBED_PROVIDER={config.EMBED_PROVIDER}\n")
    with tempfile.TemporaryDirectory() as tmp:       # S5/S6 write precedents: run on a COPY so the real second_brain/ is never touched
        shutil.copytree(config.BRAIN_DIR, Path(tmp) / "brain")
        return run_demo(SQLiteStore(":memory:"), Brain(Path(tmp) / "brain"), llm, config.DATA_DIR)


def cmd_eval(provider: str = "mock", limit: int | None = None, file: str | None = None) -> int:
    """Run eval/requests_eval.csv through the pipeline and write eval/scorecard.json + eval/scorecard.md."""
    from pathlib import Path

    from caregrid.scorecard import DEFAULT_EVAL_FILE, misses, render_table, run_eval, write_outputs

    csv_path = Path(file) if file else config.EVAL_DIR / DEFAULT_EVAL_FILE
    if not (config.BRAIN_DIR / "index.md").exists() or not csv_path.exists():
        print(f"second_brain/ or {csv_path} is missing: run `python -m caregrid.cli reset` first (or check --file).")
        return 1
    llm = llm_for(provider)
    if provider == "env":
        print(f"provider env -> {config.LLM_PROVIDER}; pacing to LLM_MAX_RPM={config.LLM_MAX_RPM} (about 2 requests per row)")
    card = run_eval(provider, limit=limit, echo=print, llm=llm, csv_path=csv_path)
    print()
    print(render_table(card))
    for label, adj in (("blind", False), ("adjudicated", True)):
        if adj and not card.get("adjudicated"):
            continue
        for m in misses(card, adjudicated=adj):
            print(f"MISS[{label}] {m['id']}: " + "; ".join(m["why"])
                  + f"  [decision {m['decision']}, score {m['score']}, reasons want {m['expected_reasons']} got {m['got_reasons']}]")
    for path in write_outputs(card):
        print(f"wrote {path.relative_to(config.ROOT) if path.is_relative_to(config.ROOT) else path}")
    return 1 if card["metrics"]["errors"] else 0


def cmd_serve(port: int = 8000, host: str = "127.0.0.1") -> int:
    """Serve the API and the static web UI (web/) on http://127.0.0.1:8000 . Reads LLM_PROVIDER from .env like every other command."""
    import uvicorn

    print(f"CareGrid web UI: http://{host}:{port}/   (API docs: /api/docs)   LLM provider: {config.LLM_PROVIDER}")
    uvicorn.run("caregrid.api:app", host=host, port=port, log_level="warning")
    return 0


def cmd_check(skip_pytest: bool = False) -> int:
    """The midnight checklist, headless: PASS/FAIL per item, exit 1 on any FAIL."""
    from caregrid.check import run_check

    return run_check(skip_pytest=skip_pytest)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m caregrid.cli", description="CareGrid command line")
    parser.add_argument("command", choices=[*_COMMANDS, "llmcheck"])
    parser.add_argument("--limit", type=int, default=None, help="eval only: run just the first N rows (writes a *.partial.* scorecard)")
    parser.add_argument("--port", type=int, default=8000, help="serve only: port (default 8000)")
    parser.add_argument("--skip-pytest", action="store_true", help="check only: skip the (slow) pytest item")
    parser.add_argument("--file", default=None, help="eval only: CSV to evaluate (default eval/requests_eval.csv); never edited")
    parser.add_argument("--provider", choices=["mock", "env"], default="mock",
                        help="demo/eval only: mock = deterministic offline LLM (default); env = the provider configured in .env")
    args = parser.parse_args(argv)

    if args.command == "llmcheck":
        return llmcheck()
    if args.command == "data":
        return cmd_data()
    if args.command == "brain":
        return cmd_brain()
    if args.command == "leakscan":
        return cmd_leakscan()
    if args.command == "lint":
        return cmd_lint()
    if args.command == "reset":
        return cmd_reset()
    if args.command == "demo":
        return cmd_demo(args.provider)
    if args.command == "serve":
        return cmd_serve(args.port)
    if args.command == "check":
        return cmd_check(args.skip_pytest)
    if args.command == "eval":
        return cmd_eval(args.provider, args.limit, args.file)
    print(f"'{args.command}' is not implemented yet (planned for phase {_STUBS[args.command]}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
