"""Blindfold stdio MCP proxy.

Wraps a downstream stdio MCP server, tokenizes outbound tool results,
injects `blindfold_compute` into tools/list, and answers the custom
`blindfold/rehydrate` control-plane method.

stdin/stdout of THIS process are the harness-facing side.
The downstream child's stdin/stdout are asyncio pipes wired by
create_subprocess_exec. stdin on Windows cannot be attached to the
asyncio loop, so we read it via asyncio.to_thread and write to stdout
synchronously (messages are small and JSON-RPC framing is line-based).
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from blindfold.config import (
    BlindfoldConfig,
    build_token_store,
    load_config,
    schema_fields_for,
    schema_fields_for_resource,
)
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.rehydrator import rehydrate
from blindfold.core.tokenizer import describe_schema, tokenize_result
from blindfold.ports.policy import DetokenizePolicy
from blindfold.ports.sandbox import ComputeSandbox, SandboxError
from blindfold.ports.token_store import TokenStore
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import (
    BLINDFOLD_COMPUTE_TOOL_NAME,
    build_tool_definition,
    handle_blindfold_compute,
)


@dataclass
class ProxyState:
    store: TokenStore
    policy: DetokenizePolicy
    sandbox: ComputeSandbox
    config: BlindfoldConfig
    session_id: str
    pending_calls: dict[Any, str] = field(default_factory=dict)  # jsonrpc id -> tool name
    pending_reads: dict[Any, str] = field(default_factory=dict)  # jsonrpc id -> resource uri

    @property
    def ttl_seconds(self) -> int:
        return self.config.tokens.default_ttl


def build_proxy_state(config: BlindfoldConfig) -> ProxyState:
    return ProxyState(
        store=build_token_store(config),
        policy=SessionBoundPolicy(),
        sandbox=SubprocessSandbox(),
        config=config,
        session_id=f"sess_{uuid.uuid4().hex[:12]}",
    )


async def run_proxy(downstream_cmd: list[str], config_path: Path | None = None) -> None:
    cfg = load_config(config_path) if config_path is not None else BlindfoldConfig()
    state = build_proxy_state(cfg)

    child = await asyncio.create_subprocess_exec(
        *downstream_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr,
    )

    stdin_buf: BinaryIO = sys.stdin.buffer
    stdout_buf: BinaryIO = sys.stdout.buffer

    async def read_client_line() -> bytes:
        # Blocking readline on stdin runs in a worker thread so the event loop
        # keeps servicing the child's stdout on Windows too.
        return await asyncio.to_thread(stdin_buf.readline)

    def write_client_line(data: bytes) -> None:
        stdout_buf.write(data)
        stdout_buf.flush()

    tasks = [
        asyncio.create_task(_pump_client_to_child(read_client_line, child, write_client_line, state)),
        asyncio.create_task(_pump_child_to_client(child, write_client_line, state)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        if child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), timeout=5)
            except asyncio.TimeoutError:
                child.kill()


async def _pump_client_to_child(read_line, child, write_client, state: ProxyState) -> None:
    assert child.stdin is not None
    while True:
        line = await read_line()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[blindfold] bad JSON from client: {exc!r}", file=sys.stderr)
            continue
        if not isinstance(msg, dict):
            # A JSON-RPC batch is an array. Blindfold does not inspect batches;
            # forwarding them untouched is honest, and better than the
            # AttributeError that used to kill this task and wedge the pipe.
            print("[blindfold] forwarding a JSON-RPC batch untouched", file=sys.stderr)
            child.stdin.write(line)
            await child.stdin.drain()
            continue

        method = msg.get("method")
        if method == "blindfold/rehydrate":
            _handle_rehydrate(msg, write_client, state)
            continue
        if method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") == BLINDFOLD_COMPUTE_TOOL_NAME:
                # In a worker thread: the sandbox blocks for up to its timeout,
                # and doing that inline stopped the proxy forwarding anything
                # in either direction for those seconds.
                await asyncio.to_thread(_handle_blindfold_compute, msg, write_client, state)
                continue
            state.pending_calls[msg.get("id")] = params.get("name")
        elif method == "resources/read":
            # Resources carry data just like tool results do, and used to pass
            # through untouched — a server exposing salaries as a resource got
            # no protection at all.
            state.pending_reads[msg.get("id")] = ((msg.get("params") or {}).get("uri")) or ""

        child.stdin.write(line)
        await child.stdin.drain()


async def _pump_child_to_client(child, write_client, state: ProxyState) -> None:
    assert child.stdout is not None
    while True:
        line = await child.stdout.readline()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[blindfold] bad JSON from child: {exc!r}", file=sys.stderr)
            write_client(line)
            continue
        if not isinstance(msg, dict):
            print("[blindfold] forwarding a JSON-RPC batch untouched", file=sys.stderr)
            write_client(line)
            continue

        if "result" in msg and isinstance(msg["result"], dict):
            tools = msg["result"].get("tools")
            if isinstance(tools, list):
                _annotate_protected_tools(tools, state)
                tools.append(build_tool_definition())

            msg_id = msg.get("id")
            if msg_id in state.pending_calls:
                tool_name = state.pending_calls.pop(msg_id)
                _tokenize_tool_call_result(msg, tool_name, state)
            elif msg_id in state.pending_reads:
                _tokenize_resource_read(msg, state.pending_reads.pop(msg_id), state)

        write_client((json.dumps(msg) + "\n").encode("utf-8"))


def _annotate_protected_tools(tools: list, state: ProxyState) -> None:
    """Tell the model what each tool's tokens mean, once, on the tool itself.

    Cheaper than repeating it on every result: the same text would otherwise be
    re-sent per call and linger in the transcript for the rest of the session.
    """
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        note = describe_schema(schema_fields_for(state.config, tool.get("name", "")))
        if note is None:
            continue
        tool["description"] = f"{tool.get('description', '').rstrip()}\n\n{note}".lstrip()


def _tokenize_tool_call_result(msg: dict, tool_name: str, state: ProxyState) -> None:
    fields = schema_fields_for(state.config, tool_name)
    if not fields:
        return
    content = (msg.get("result") or {}).get("content") or []
    now = datetime.now(tz=timezone.utc)
    ttl = now + timedelta(seconds=state.ttl_seconds)
    for i, part in enumerate(content):
        if part.get("type") != "text":
            continue
        try:
            payload = json.loads(part.get("text", ""))
        except json.JSONDecodeError:
            continue
        tokenized = tokenize_result(payload, tool_name, fields, state.store, state.session_id, ttl)
        content[i] = {"type": "text", "text": json.dumps(tokenized)}


def _tokenize_resource_read(msg: dict, requested_uri: str, state: ProxyState) -> None:
    contents = (msg.get("result") or {}).get("contents") or []
    now = datetime.now(tz=timezone.utc)
    ttl = now + timedelta(seconds=state.ttl_seconds)
    for part in contents:
        if not isinstance(part, dict):
            continue
        # Each returned part names its own URI; the request URI is the fallback
        # for servers that leave it out.
        uri = part.get("uri") or requested_uri
        fields = schema_fields_for_resource(state.config, uri)
        if not fields:
            continue
        text = part.get("text")
        if not isinstance(text, str):
            # A blob, or no text at all. Declared and unprotectable is worth
            # saying out loud rather than passing along.
            print(
                f"[blindfold] {uri} declares protected paths but returned no text part",
                file=sys.stderr,
            )
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            print(
                f"[blindfold] {uri} declares protected paths but did not return JSON",
                file=sys.stderr,
            )
            continue
        tokenized = tokenize_result(payload, uri, fields, state.store, state.session_id, ttl)
        part["text"] = json.dumps(tokenized)


def _handle_rehydrate(msg: dict, write_client, state: ProxyState) -> None:
    params = msg.get("params") or {}
    text = params.get("text", "")
    result_text = rehydrate(text, state.session_id, state.store, state.policy)
    write_client(
        (json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"text": result_text}}) + "\n").encode("utf-8"),
    )


def _handle_blindfold_compute(msg: dict, write_client, state: ProxyState) -> None:
    args = ((msg.get("params") or {}).get("arguments")) or {}
    try:
        new_token = handle_blindfold_compute(
            args,
            store=state.store,
            policy=state.policy,
            sandbox=state.sandbox,
            session_id=state.session_id,
            ttl_seconds=state.ttl_seconds,
        )
        payload = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {"content": [{"type": "text", "text": new_token}], "isError": False},
        }
    except (ValueError, SandboxError) as exc:
        payload = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {"content": [{"type": "text", "text": f"blindfold_compute error: {exc}"}], "isError": True},
        }
    write_client((json.dumps(payload) + "\n").encode("utf-8"))
