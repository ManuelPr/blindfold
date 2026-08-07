"""A one-tool MCP server exposing `blindfold_compute`.

Mode A injects this tool into the `tools/list` response as it passes the proxy.
Mode B adds it to the tool list you build yourself. Mode C could do neither —
no hook can add a tool or edit a tool description — so in a host, blind compute
has to arrive the way every other tool does: as an MCP server the host starts.

Without it, Mode C protects values and leaves the model unable to do anything
with them: it can read `⟦tok_…⟧` and cannot compare, sum or sort them.

**Which session a computation belongs to is inferred from its inputs.** The
proxy and the library both know the session they are serving; a server started
by a host does not, and there is no session identifier on an MCP connection.
So this server reads the session off the input tokens themselves and refuses to
mix two. That has a consequence worth stating plainly: possession of a token is
treated as proof of belonging to its session. Tokens are unguessable and never
leave the trust zone, but they do reach the model, so a model can compute on
any token it has seen — which are exactly the tokens of its own session.

The derived token is minted into that same session, which is what lets the
`MessageDisplay` hook reveal the result: that hook is bound to the host's
session, and would render anything else as `[redacted]`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from blindfold.config import BlindfoldConfig, build_token_store, load_config
from blindfold.core.policy import SessionBoundPolicy
from blindfold.ports.policy import DetokenizePolicy
from blindfold.ports.sandbox import ComputeSandbox, SandboxError
from blindfold.ports.token_store import TokenStore
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import (
    BLINDFOLD_COMPUTE_TOOL_NAME,
    build_tool_definition,
    handle_blindfold_compute,
)
from blindfold.tools.blindfold_table import (
    BLINDFOLD_TABLE_TOOL_NAME,
    build_tool_definition as build_table_tool_definition,
    handle_blindfold_table,
)


class SharedVaultRequired(RuntimeError):
    pass


def session_of_inputs(tokens: list[str], store: TokenStore) -> str:
    """The one session all input tokens belong to, or an error naming why not."""
    if not tokens:
        raise ValueError("blindfold_compute needs at least one token in `inputs`")
    sessions = set()
    for token in tokens:
        record = store.get(token)
        if record is None:
            raise ValueError(f"unknown or expired input token: {token}")
        sessions.add(record.session_id)
    if len(sessions) > 1:
        raise ValueError(
            "inputs come from different sessions; blindfold_compute will not mix them"
        )
    return sessions.pop()


def compute(
    arguments: dict[str, Any],
    *,
    config: BlindfoldConfig,
    store: TokenStore,
    sandbox: ComputeSandbox,
    policy: DetokenizePolicy,
) -> str:
    """One blind computation, session inferred from the inputs.

    Separate from the MCP handler so it can be exercised without building
    protocol objects — the handler below is only a wrapper.
    """
    session_id = session_of_inputs(list(arguments.get("inputs") or []), store)
    return handle_blindfold_compute(
        arguments,
        store=store,
        policy=policy,
        sandbox=sandbox,
        session_id=session_id,
        ttl_seconds=config.tokens.default_ttl,
        max_calls_per_token=config.compute.max_calls_per_token,
        rate_window_s=config.compute.rate_window_s,
    )


def query_table(
    arguments: dict[str, Any],
    *,
    config: BlindfoldConfig,
    store: TokenStore,
    policy: DetokenizePolicy,
) -> str:
    """One table query, session inferred from the table token, as for compute."""
    session_id = session_of_inputs([arguments.get("table")], store)
    return handle_blindfold_table(
        arguments,
        store=store,
        policy=policy,
        session_id=session_id,
        ttl_seconds=config.tokens.default_ttl,
    )


def build_server(config: BlindfoldConfig, store: TokenStore) -> Server:
    server = Server("blindfold")
    sandbox = SubprocessSandbox()
    policy = SessionBoundPolicy()
    specs = [build_tool_definition(), build_table_tool_definition()]

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["inputSchema"],
            )
            for spec in specs
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name == BLINDFOLD_TABLE_TOOL_NAME:
            try:
                token = query_table(arguments, config=config, store=store, policy=policy)
            except ValueError as exc:
                return [TextContent(type="text", text=f"blindfold_table error: {exc}")]
            return [TextContent(type="text", text=token)]
        if name != BLINDFOLD_COMPUTE_TOOL_NAME:
            raise ValueError(f"unknown tool: {name}")
        # ponytail: the sandbox runs synchronously, so a compute blocks this
        # server for its whole timeout. One user, one call at a time, so it does
        # not show; revisit if this server ever serves concurrent sessions.
        try:
            token = compute(
                arguments, config=config, store=store, sandbox=sandbox, policy=policy
            )
        except (ValueError, SandboxError) as exc:
            # Safe to forward: ValueError carries token names, and SandboxError
            # is already reduced to exception types upstream.
            return [TextContent(type="text", text=f"blindfold_compute error: {exc}")]
        return [TextContent(type="text", text=token)]

    return server


def load_runtime(config_path: Path) -> tuple[BlindfoldConfig, TokenStore]:
    config = load_config(config_path) if config_path.exists() else BlindfoldConfig()
    if config.storage.backend == "memory":
        raise SharedVaultRequired(
            "blindfold mcp-server needs storage.backend: sqlite — it runs as its own "
            "process and has to read the vault the hooks write to"
        )
    return config, build_token_store(config)


async def _amain(config_path: Path) -> None:
    config, store = load_runtime(config_path)
    server = build_server(config, store)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        close = getattr(store, "close", None)
        if close is not None:
            close()


def run(config_path: Path) -> None:
    asyncio.run(_amain(config_path))
