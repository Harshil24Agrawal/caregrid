"""CLI entry: python -m caregrid.cli <command>."""
from __future__ import annotations

import argparse
import sys

from caregrid import config

# command -> phase that implements it (stubs until then)
_STUBS = {"eval": 7}
_COMMANDS = ["data", "brain", "leakscan", "lint", "reset", "demo", *_STUBS]


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


def cmd_demo() -> int:
    """Run the acceptance scenarios headless against a throw-away in-memory store."""
    from caregrid.demo import run_demo
    from caregrid.knowledge.brain import Brain
    from caregrid.llm import get_llm
    from caregrid.store import SQLiteStore

    if not (config.BRAIN_DIR / "index.md").exists():
        print("second_brain/ is empty: run `python -m caregrid.cli reset` first.")
        return 1
    import shutil
    import tempfile
    from pathlib import Path

    print(f"LLM_PROVIDER={config.LLM_PROVIDER} EMBED_PROVIDER={config.EMBED_PROVIDER}\n")
    with tempfile.TemporaryDirectory() as tmp:       # S5/S6 write precedents: run on a COPY so the real second_brain/ is never touched
        shutil.copytree(config.BRAIN_DIR, Path(tmp) / "brain")
        return run_demo(SQLiteStore(":memory:"), Brain(Path(tmp) / "brain"), get_llm(), config.DATA_DIR)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m caregrid.cli", description="CareGrid command line")
    parser.add_argument("command", choices=[*_COMMANDS, "llmcheck"])
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
        return cmd_demo()
    print(f"'{args.command}' is not implemented yet (planned for phase {_STUBS[args.command]}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
