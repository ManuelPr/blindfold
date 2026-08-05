"""Integration: spins up `python -m blindfold` as a subprocess wrapping
`python -m examples.fake_hr_mcp` and drives the raw MCP JSON-RPC over stdio.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

TOKEN_RE = re.compile(r"⟦tok_[0-9a-f]{8}⟧")


@pytest_asyncio.fixture()
async def proxy_subprocess(tmp_path: Path):
    cfg = tmp_path / "blindfold.yaml"
    cfg.write_text(
        """
schemas:
  get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
""",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "blindfold",
        "--config", str(cfg),
        "--",
        sys.executable, "-m", "examples.fake_hr_mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        yield proc
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()


async def _send(proc, msg: dict[str, Any]) -> None:
    line = (json.dumps(msg) + "\n").encode("utf-8")
    proc.stdin.write(line)
    await proc.stdin.drain()


# Generous on purpose. Every test here starts two Python processes and drives
# an MCP handshake through them; on a loaded machine that is not a five-second
# operation, and these tests assert what came back, never how fast. A tight
# timeout here only produces failures that say nothing about the code.
async def _recv(proc, timeout: float = 30.0) -> dict[str, Any]:
    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    if not raw:
        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace")
        raise RuntimeError(f"proxy closed stdout; stderr={stderr!r}")
    return json.loads(raw)


async def _initialize(proc, next_id: int) -> int:
    await _send(proc, {
        "jsonrpc": "2.0", "id": next_id, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    })
    await _recv(proc)
    await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    return next_id + 1


async def test_tools_list_includes_injected_compute_tool(proxy_subprocess):
    proc = proxy_subprocess
    nid = await _initialize(proc, 1)
    await _send(proc, {"jsonrpc": "2.0", "id": nid, "method": "tools/list", "params": {}})
    resp = await _recv(proc)
    names = {t["name"] for t in resp["result"]["tools"]}
    assert "get_salary" in names
    assert "blindfold_compute" in names


async def test_get_salary_response_is_tokenized(proxy_subprocess):
    proc = proxy_subprocess
    nid = await _initialize(proc, 1)
    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "tools/call",
        "params": {"name": "get_salary", "arguments": {"name": "Manuel Pernigotto"}},
    })
    resp = await _recv(proc)
    text = resp["result"]["content"][0]["text"]
    parsed = json.loads(text)
    assert parsed["name"] == "Manuel Pernigotto"
    assert isinstance(parsed["salary"], str)
    assert TOKEN_RE.fullmatch(parsed["salary"])


async def test_blindfold_compute_returns_derived_token(proxy_subprocess):
    proc = proxy_subprocess
    nid = await _initialize(proc, 1)

    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "tools/call",
        "params": {"name": "get_salary", "arguments": {"name": "Manuel Pernigotto"}},
    })
    a = json.loads((await _recv(proc))["result"]["content"][0]["text"])
    nid += 1

    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "tools/call",
        "params": {"name": "get_salary", "arguments": {"name": "Andrea Tuscano"}},
    })
    b = json.loads((await _recv(proc))["result"]["content"][0]["text"])
    nid += 1

    a_tok, b_tok = a["salary"], b["salary"]
    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "tools/call",
        "params": {"name": "blindfold_compute", "arguments": {
            "code": f"result = 'Manuel Pernigotto' if resolve({a_tok!r}) > resolve({b_tok!r}) else 'Andrea Tuscano'",
            "inputs": [a_tok, b_tok],
        }},
    })
    compute_resp = await _recv(proc)
    new_token = compute_resp["result"]["content"][0]["text"]
    assert TOKEN_RE.fullmatch(new_token)

    nid += 1
    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "blindfold/rehydrate",
        "params": {"text": f"The higher earner is {new_token}.", "session_id": "PROBE_SESSION_UNUSED"},
    })
    rehy = await _recv(proc)
    assert rehy["result"]["text"] == "The higher earner is Andrea Tuscano."


async def test_protected_tool_description_explains_its_tokens(proxy_subprocess):
    proc = proxy_subprocess
    nid = await _initialize(proc, 1)
    await _send(proc, {"jsonrpc": "2.0", "id": nid, "method": "tools/list", "params": {}})
    tools = {t["name"]: t for t in (await _recv(proc))["result"]["tools"]}

    described = tools["get_salary"]["description"]
    assert "Return the annual gross salary in EUR" in described  # upstream text kept
    assert "$.salary" in described
    assert "EUR/year" in described
    assert "blindfold_compute" in described


async def test_tool_without_declared_fields_is_left_alone(proxy_subprocess):
    proc = proxy_subprocess
    nid = await _initialize(proc, 1)
    await _send(proc, {"jsonrpc": "2.0", "id": nid, "method": "tools/list", "params": {}})
    tools = {t["name"]: t for t in (await _recv(proc))["result"]["tools"]}

    assert "Blindfold: values at these paths" not in tools["blindfold_compute"]["description"]


async def test_tool_result_carries_no_extra_parts(proxy_subprocess):
    proc = proxy_subprocess
    nid = await _initialize(proc, 1)
    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "tools/call",
        "params": {"name": "get_salary", "arguments": {"name": "Manuel Pernigotto"}},
    })
    content = (await _recv(proc))["result"]["content"]

    assert len(content) == 1, "the explanation lives on the tool, not on every result"
    assert "62000" not in content[0]["text"]
