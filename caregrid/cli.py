"""CLI entry: python -m caregrid.cli <command>."""
from __future__ import annotations

import argparse
import sys

from caregrid import config

# command -> phase that implements it (stubs until then)
_STUBS = {
    "data": 1,
    "brain": 1,
    "lint": 2,
    "reset": 5,
    "demo": 4,
    "eval": 7,
}


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


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m caregrid.cli", description="CareGrid command line")
    parser.add_argument("command", choices=[*_STUBS, "llmcheck"])
    args = parser.parse_args(argv)

    if args.command == "llmcheck":
        return llmcheck()
    print(f"'{args.command}' is not implemented yet (planned for phase {_STUBS[args.command]}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
