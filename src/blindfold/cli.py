"""Blindfold CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from blindfold import hooks
from blindfold.config import BlindfoldConfig, build_token_store, load_config
from blindfold.core.policy import SessionBoundPolicy
from blindfold.proxy import run_proxy

USAGE = (
    "blindfold [--config PATH] -- <downstream-mcp-command> [args...]\n"
    "blindfold hook <post-tool-use|message-display> [--config PATH]\n\n"
    "Wraps the given stdio MCP server, tokenizing tool results and exposing\n"
    "the blindfold_compute tool + blindfold/rehydrate JSON-RPC method.\n\n"
    "`hook` reads one Claude Code hook event as JSON on stdin and writes the\n"
    "hook response on stdout. It needs a shared vault (storage.backend: sqlite)\n"
    "because every hook invocation is a separate process."
)


def _split_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        return argv, []
    idx = argv.index("--")
    return argv[:idx], argv[idx + 1 :]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "hook":
        return run_hook(argv[1:])

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


def run_hook(argv: list[str]) -> int:
    """Handle one Claude Code hook event.

    Failure is asymmetric on purpose. Printing nothing tells the host to keep
    what it had: for ``PostToolUse`` that is the untokenized tool result on its
    way to the model, so anything going wrong there has to block instead. For
    ``MessageDisplay`` the worst case is the user reading a placeholder, which
    is a nuisance and not a disclosure, so that one stays quiet.
    """
    parser = argparse.ArgumentParser(prog="blindfold hook", usage=USAGE)
    parser.add_argument("event", choices=list(hooks.EVENTS))
    parser.add_argument("--config", type=Path, default=Path("blindfold.yaml"))
    args = parser.parse_args(argv)

    protects = args.event == hooks.POST_TOOL_USE

    def fail(message: str) -> int:
        print(f"[blindfold] {message}", file=sys.stderr)
        if protects:
            print(json.dumps({"decision": "block", "reason": f"Blindfold: {message}"}))
        return 0

    try:
        config = load_config(args.config) if args.config.exists() else BlindfoldConfig()
    except Exception as exc:  # a broken config must not silently disable protection
        return fail(f"could not load {args.config}: {type(exc).__name__}")

    if config.storage.backend == "memory":
        return fail(
            "hooks need storage.backend: sqlite — each hook run is a separate process, "
            "and a memory vault would not survive between tokenizing and displaying"
        )

    try:
        event = hooks.read_event(sys.stdin.read())
    except Exception as exc:
        return fail(f"unreadable hook input: {type(exc).__name__}")

    store = build_token_store(config)
    try:
        response = hooks.dispatch(
            args.event,
            event,
            config=config,
            store=store,
            policy=SessionBoundPolicy(),
        )
    except Exception as exc:
        # Only the exception type, never its message: the same reasoning as the
        # compute sandbox, since a message can carry a value it touched.
        return fail(f"{args.event} failed: {type(exc).__name__}")
    finally:
        close = getattr(store, "close", None)
        if close is not None:
            close()

    if response is not None:
        print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
