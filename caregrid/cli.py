"""CLI entry: python -m caregrid.cli <command>."""
from __future__ import annotations

import argparse
import sys

from caregrid import config

# command -> phase that implements it (stubs until then)
_STUBS = {"lint": 2, "demo": 4, "eval": 7}
_COMMANDS = ["data", "brain", "reset", *_STUBS]


def _short_err(e: Exception) -> str:
    msg = " ".join(str(e).split())
    return f"{type(e).__name__}: {msg[:200]}"


def llmcheck() -> int:
    """One light + one strong complete_json call and one embed call. Never raises on provider errors."""
    from caregrid.llm import get_llm

    print(f"provider={config.LLM_PROVIDER} light={config.LIGHT_MODEL_ID} strong={config.STRONG_MODEL_ID} "
          f"region={config.AWS_REGION}")
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


def cmd_reset() -> int:
    """Partial (phase 1): wipe SQLite, regenerate data, recompile brain. Seed cases/trust load in phases 4-5."""
    from caregrid.store import SQLiteStore

    SQLiteStore().wipe()
    print(f"wiped {config.DB_PATH}")
    cmd_data()
    return cmd_brain()


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
    if args.command == "reset":
        return cmd_reset()
    print(f"'{args.command}' is not implemented yet (planned for phase {_STUBS[args.command]}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
