"""Run each of the three modes against the fake HR server and show what happens.

No API key, no Ollama, no Docker. The "model" is scripted, because the point is
to watch what Blindfold does to the data, not whether an LLM gets the answer
right.

    uv run python examples/try_modes.py           # all three
    uv run python examples/try_modes.py a         # just one

Mode C is run the way Claude Code runs it: the hooks are invoked as separate
processes over a shared SQLite vault, which is the whole reason that mode needs
one. Launching it inside a real Claude Code session is the last section.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from blindfold import PLACEHOLDER_PROMPT, rehydrate
from blindfold.config import (
    load_config,
    schema_fields_for,
    table_schemas_for,
)
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.tokenizer import describe_schema, describe_tables, tokenize_result
from blindfold.core.vault import MemoryTokenStore
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools import blindfold_compute, blindfold_table

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

CONFIG = """
schemas:
  get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
  list_employees:
    tables:
      - path: $.employees
        columns:
          - name: name
            semantic_type: person_name
          - name: salary
            semantic_type: salary
            unit: EUR/year
          - name: dept
"""

ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def head(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def step(n: str, what: str) -> None:
    print(f"\n[{n}] {what}")


def show(label: str, value: object) -> None:
    print(f"      {label}: {value}")


# --------------------------------------------------------------------------
# Mode A — the CLI proxy
# --------------------------------------------------------------------------


async def mode_a(config_path: Path) -> None:
    head("MODE A — CLI proxy.  blindfold -- python -m examples.fake_hr_mcp")
    print(
        "\nThe proxy sits between an MCP client and an MCP server. Nothing here\n"
        "is application code: this script is just an ordinary MCP client."
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "blindfold", "--config", str(config_path),
            "--", sys.executable, "-m", "examples.fake_hr_mcp",
        ],
        env=ENV,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            step("1", "The tool list the model receives")
            listed = await session.list_tools()
            show("tools", [t.name for t in listed.tools])
            print("\n      get_salary's description, as the proxy rewrote it:")
            for line in (listed.tools[0].description or "").splitlines():
                print(f"        {line}")

            step("2", "A single value: the model asks for a salary")
            result = await session.call_tool("get_salary", {"name": "Andrea Tuscano"})
            tokenized = result.content[0].text
            show("what the model sees", tokenized)
            token = json.loads(tokenized)["salary"]

            step("3", "A whole list: one placeholder for five employees")
            listing = await session.call_tool("list_employees", {})
            show("what the model sees", listing.content[0].text)
            table_token = json.loads(listing.content[0].text)["employees"]

            step("4", "The model queries the hidden table")
            queried = await session.call_tool(
                "blindfold_table",
                {
                    "table": table_token,
                    "ops": [
                        {"op": "filter", "column": "dept", "cmp": "==", "value": "Engineering"},
                        {"op": "sort_by", "column": "salary", "desc": True},
                        {"op": "limit", "n": 2},
                        {"op": "select", "columns": ["name", "salary"]},
                    ],
                },
            )
            top = queried.content[0].text
            show("answer to the model", top)

            step("5", "The catch: rehydration is a method no third-party client calls")
            print(
                "      This script asks for it explicitly. Claude Desktop, Cursor and\n"
                "      Zed do not know it exists, so under them the user would read the\n"
                "      placeholder below instead of the values."
            )
            print(f"\n      the model's answer : Top earners: {top}. Andrea earns {token}.")
            print("      (a normal client would stop here and show exactly that)")

    print("\n      Nothing above ever contained 71000 or 83000.")


# --------------------------------------------------------------------------
# Mode B — the library, inside a loop you wrote
# --------------------------------------------------------------------------


async def mode_b(config_path: Path) -> None:
    head("MODE B — in-process library.  from blindfold import ...")
    print(
        "\nNo proxy. This is the agent loop you already have, with five calls\n"
        "added. The 'model' below is scripted so no API key is needed."
    )

    config = load_config(config_path)
    store, policy, sandbox = MemoryTokenStore(), SessionBoundPolicy(), SubprocessSandbox()
    session_id = f"user_{uuid.uuid4().hex[:8]}"
    ttl = datetime.now(tz=timezone.utc) + timedelta(hours=1)

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "examples.fake_hr_mcp"], env=ENV
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            step("1", "Put PLACEHOLDER_PROMPT in your system prompt")
            show("first line", PLACEHOLDER_PROMPT.splitlines()[0])

            step("2+3", "Describe protected tools, and advertise the compute tools")
            listed = await session.list_tools()
            tools = []
            for t in listed.tools:
                notes = [
                    describe_schema(schema_fields_for(config, t.name)),
                    describe_tables(table_schemas_for(config, t.name)),
                ]
                note = "\n\n".join(n for n in notes if n)
                tools.append({"name": t.name, "description": f"{t.description}\n\n{note}".strip()})
            tools += [
                {"name": blindfold_compute.BLINDFOLD_COMPUTE_TOOL_NAME, "description": "..."},
                {"name": blindfold_table.BLINDFOLD_TABLE_TOOL_NAME, "description": "..."},
            ]
            show("tools passed to the LLM", [t["name"] for t in tools])

            step("4", "Tokenize every tool result before feeding it back")
            call = await session.call_tool("get_salary", {"name": "Manuel Pernigotto"})
            payload = json.loads(call.content[0].text)
            show("what the tool really returned", payload)
            tokenized = tokenize_result(
                payload, "get_salary", schema_fields_for(config, "get_salary"),
                store, session_id, ttl, tables=table_schemas_for(config, "get_salary"),
            )
            show("what you feed the model", tokenized)
            manuel = tokenized["salary"]

            call = await session.call_tool("get_salary", {"name": "Andrea Tuscano"})
            andrea = tokenize_result(
                json.loads(call.content[0].text), "get_salary",
                schema_fields_for(config, "get_salary"), store, session_id, ttl,
            )["salary"]

            step("5a", "Route the model's compute call")
            derived = blindfold_compute.handle_blindfold_compute(
                {
                    "code": f"result = 'Andrea' if resolve({andrea!r}) > resolve({manuel!r}) else 'Manuel'",
                    "inputs": [andrea, manuel],
                },
                store=store, policy=policy, sandbox=sandbox,
                session_id=session_id, ttl_seconds=3600,
            )
            show("answer to the model", derived)

            step("5b", "Rehydrate before display — this is the step Mode A cannot do")
            answer = f"{derived} earns more: {andrea} against {manuel}."
            show("the model wrote", answer)
            show("the user reads", rehydrate(answer, session_id, store, policy))

            step("!", "A different user cannot resolve those placeholders")
            show("another session reads", rehydrate(answer, "someone_else", store, policy))


# --------------------------------------------------------------------------
# Mode C — the Claude Code plugin, driven the way the host drives it
# --------------------------------------------------------------------------


def hook(event: str, payload: dict, config_path: Path) -> dict | None:
    """One hook invocation — a separate process, exactly as the host does it."""
    proc = subprocess.run(
        [sys.executable, "-m", "blindfold", "hook", event, "--config", str(config_path)],
        input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", env=ENV,
    )
    if proc.stderr.strip():
        print(f"      (stderr) {proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


async def mode_c(workdir: Path) -> None:
    head("MODE C — Claude Code plugin.  three hooks + an MCP server")
    config_path = workdir / "blindfold_c.yaml"
    config_path.write_text(
        f"storage:\n  backend: sqlite\n  path: {workdir / 'vault.db'}\n{CONFIG}",
        encoding="utf-8",
    )
    print(
        "\nEach step below is a SEPARATE PROCESS, which is why this mode needs a\n"
        "shared vault. With backend: memory the placeholders would never resolve."
    )

    step("1", "SessionStart — injected before your first prompt")
    brief = hook("session-start", {"session_id": "S1", "source": "startup"}, config_path)
    for line in brief["hookSpecificOutput"]["additionalContext"].splitlines():
        if line.strip():
            print(f"      {line}")

    step("2", "PostToolUse — rewrites the result before the model sees it")
    out = hook(
        "post-tool-use",
        {
            "session_id": "S1",
            "tool_name": "list_employees",
            "tool_output": json.dumps({"employees": [
                {"name": "Andrea Tuscano", "salary": 71000, "dept": "Engineering"},
                {"name": "Giulia Verdi", "salary": 83000, "dept": "Engineering"},
                {"name": "Maria Rossi", "salary": 55000, "dept": "Sales"},
            ]}),
        },
        config_path,
    )
    rewritten = out["hookSpecificOutput"]["updatedToolOutput"]
    show("what the model sees", rewritten)
    table_token = json.loads(rewritten)["employees"]

    step("3", "The MCP server — a third process, same vault")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "blindfold", "mcp-server", "--config", str(config_path)],
        env=ENV,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            show("tools it offers", [t.name for t in (await session.list_tools()).tools])
            res = await session.call_tool(
                "blindfold_table",
                {"table": table_token, "ops": [{"op": "max", "column": "salary"}]},
            )
            top = res.content[0].text
            show("answer to the model", top)

    step("4", "MessageDisplay — the real values, on screen only")
    shown = hook(
        "message-display",
        # "delta", not "message_text" — see handle_message_display's docstring:
        # the real host sends the newly-completed-lines chunk under this key.
        {"session_id": "S1", "delta": f"The highest salary is {top}."},
        config_path,
    )
    show("the transcript keeps", f"The highest salary is {top}.")
    show("your screen shows", shown["hookSpecificOutput"]["displayContent"])
    print(
        "\n      That difference is the point: on the next turn the model still\n"
        "      sees the placeholder, so the value never re-enters its context."
    )

    print(
        "\n      To run it for real inside Claude Code:\n"
        "        uv tool install .\n"
        "        cp blindfold.example.yaml blindfold.yaml   # add backend: sqlite\n"
        "        claude --plugin-dir ./plugin"
    )


# --------------------------------------------------------------------------


async def main() -> None:
    which = (sys.argv[1].lower() if len(sys.argv) > 1 else "all")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        config_path = workdir / "blindfold.yaml"
        config_path.write_text(CONFIG, encoding="utf-8")

        if which in ("a", "all"):
            await mode_a(config_path)
        if which in ("b", "all"):
            await mode_b(config_path)
        if which in ("c", "all"):
            await mode_c(workdir)

    print(f"\n{'=' * 72}\nWhich one you want, and why: docs/modes.md\n")


if __name__ == "__main__":
    asyncio.run(main())
