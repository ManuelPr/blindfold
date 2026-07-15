"""Blindfold CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from blindfold.proxy import run_proxy

USAGE = (
    "blindfold [--config PATH] -- <downstream-mcp-command> [args...]\n\n"
    "Wraps the given stdio MCP server, tokenizing tool results and exposing\n"
    "the blindfold_compute tool + blindfold/rehydrate JSON-RPC method."
)


def _split_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        return argv, []
    idx = argv.index("--")
    return argv[:idx], argv[idx + 1 :]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    own, downstream = _split_argv(argv)

    parser = argparse.ArgumentParser(
        prog="blindfold",
        description="Privacy proxy for stdio MCP servers.",
        usage=USAGE,
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to blindfold.yaml")
    args = parser.parse_args(own)

    if not downstream:
        print("error: no downstream command after `--`", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    asyncio.run(run_proxy(downstream, config_path=args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
