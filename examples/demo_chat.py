"""Interactive demo — Claude answering an HR question through Blindfold.

Run with:
    uv run --extra demo python examples/demo_chat.py "Who earns more, Manuel Pernigotto or Andrea Tuscano?"

Requires ANTHROPIC_API_KEY in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from blindfold import PLACEHOLDER_PROMPT, rehydrate
from blindfold.config import (
    BlindfoldConfig,
    SensitiveFieldConfig,
    ToolSchemaConfig,
    schema_fields_for,
)
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.tokenizer import describe_schema, tokenize_result
from blindfold.core.vault import MemoryTokenStore
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import (
    BLINDFOLD_COMPUTE_TOOL_NAME,
    build_tool_definition,
    handle_blindfold_compute,
)

MODEL = "claude-opus-4-7"
# Ships with the package, so an integration does not have to know to copy it
# out of an example. Rehydration only works on placeholders the model
# reproduced exactly, and nothing else enforces that.
SYSTEM_PROMPT = PLACEHOLDER_PROMPT


async def _amain(question: str) -> None:
    store = MemoryTokenStore()
    policy = SessionBoundPolicy()
    sandbox = SubprocessSandbox()
    session_id = f"demo_{uuid.uuid4().hex[:8]}"
    config = BlindfoldConfig(
        schemas={
            "get_salary": ToolSchemaConfig(
                sensitive_fields=[
                    SensitiveFieldConfig(path="$.salary", semantic_type="salary", unit="EUR/year")
                ]
            )
        }
    )
    ttl = datetime.now(tz=timezone.utc) + timedelta(hours=1)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "examples.fake_hr_mcp"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            # Integration point 1a: tell the model what each protected tool's
            # placeholders mean. Mode A does this for you by rewriting the
            # tools/list response; in-process, it is this append.
            tools = []
            for t in listed.tools:
                description = t.description or ""
                note = describe_schema(schema_fields_for(config, t.name))
                if note is not None:
                    description = f"{description.rstrip()}\n\n{note}".lstrip()
                tools.append(
                    {"name": t.name, "description": description, "input_schema": t.inputSchema}
                )
            tool_def = build_tool_definition()
            tools.append({
                "name": BLINDFOLD_COMPUTE_TOOL_NAME,
                "description": tool_def["description"],
                "input_schema": tool_def["inputSchema"],
            })

            client = Anthropic()
            messages: list[dict] = [{"role": "user", "content": question}]

            while True:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason != "tool_use":
                    break

                tool_results: list[dict] = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    if block.name == BLINDFOLD_COMPUTE_TOOL_NAME:
                        try:
                            token = handle_blindfold_compute(
                                block.input,
                                store=store,
                                policy=policy,
                                sandbox=sandbox,
                                session_id=session_id,
                                ttl_seconds=3600,
                            )
                            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": token})
                        except Exception as exc:
                            tool_results.append({
                                "type": "tool_result", "tool_use_id": block.id,
                                "content": f"error: {exc}", "is_error": True,
                            })
                        continue

                    call = await session.call_tool(block.name, block.input)
                    text = call.content[0].text if call.content else "{}"
                    fields = schema_fields_for(config, block.name)
                    if fields:
                        payload = json.loads(text)
                        tokenized = tokenize_result(payload, block.name, fields, store, session_id, ttl)
                        text = json.dumps(tokenized)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": text})

                messages.append({"role": "user", "content": tool_results})

            final_text = "".join(b.text for b in response.content if b.type == "text")
            print(rehydrate(final_text, session_id, store, policy))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", default="Who earns more, Manuel Pernigotto or Andrea Tuscano?")
    args = parser.parse_args()
    asyncio.run(_amain(args.question))


if __name__ == "__main__":
    main()
