"""Blindfold CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from blindfold import audit as audit_mod
from blindfold import hooks, mcp_server
from blindfold.config import BlindfoldConfig, build_token_store, load_config
from blindfold.core.policy import SessionBoundPolicy
from blindfold.proxy import run_proxy

USAGE = (
    "blindfold [--config PATH] -- <downstream-mcp-command> [args...]\n"
    "blindfold hook <post-tool-use|message-display|session-start> [--config PATH]\n"
    "blindfold mcp-server [--config PATH]\n"
    "blindfold audit <transcript> [--session ID] [--config PATH]\n\n"
    "Wraps the given stdio MCP server, tokenizing tool results and exposing\n"
    "the blindfold_compute tool + blindfold/rehydrate JSON-RPC method.\n\n"
    "`hook` reads one Claude Code hook event as JSON on stdin and writes the\n"
    "hook response on stdout. `mcp-server` exposes blindfold_compute so a host\n"
    "can offer it as an ordinary tool. Both need a shared vault\n"
    "(storage.backend: sqlite): they run as separate processes.\n\n"
    "`audit` checks a transcript against the vault and reports whether any\n"
    "hidden value reached the model — the question the screen cannot answer."
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

    if argv and argv[0] == "mcp-server":
        return run_mcp_server(argv[1:])

    if argv and argv[0] == "audit":
        return run_audit(argv[1:])

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


def run_mcp_server(argv: list[str]) -> int:
    """Serve blindfold_compute over stdio MCP."""
    parser = argparse.ArgumentParser(prog="blindfold mcp-server", usage=USAGE)
    parser.add_argument("--config", type=Path, default=Path("blindfold.yaml"))
    args = parser.parse_args(argv)

    try:
        mcp_server.run(args.config)
    except mcp_server.SharedVaultRequired as exc:
        print(f"[blindfold] {exc}", file=sys.stderr)
        return 1
    return 0


def run_audit(argv: list[str]) -> int:
    """Answer "is it actually working" from the transcript, not the screen.

    Exits non-zero when a hidden value is found, so it can gate a pipeline.
    """
    parser = argparse.ArgumentParser(prog="blindfold audit", usage=USAGE)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--session", default=None, help="defaults to the transcript's own")
    parser.add_argument("--config", type=Path, default=Path("blindfold.yaml"))
    args = parser.parse_args(argv)

    if not args.transcript.exists():
        print(f"[blindfold] no such transcript: {args.transcript}", file=sys.stderr)
        return 2

    config = load_config(args.config) if args.config.exists() else BlindfoldConfig()
    store = build_token_store(config)
    try:
        text = audit_mod.read_transcript(args.transcript)
        sessions = [args.session] if args.session else audit_mod.session_ids_in(args.transcript)
        if not sessions:
            print(
                "[blindfold] the transcript names no session; pass --session",
                file=sys.stderr,
            )
            return 2
        clean = True
        for session in sessions:
            if len(sessions) > 1:
                print(f"\n--- session {session} ---")
            report = audit_mod.audit(text, store, session)
            print(report.render())
            clean = clean and report.clean
    finally:
        close = getattr(store, "close", None)
        if close is not None:
            close()
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
