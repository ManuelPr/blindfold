"""E2E: replay a canned transcript through the same wiring the demo uses."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from blindfold import rehydrate
from blindfold.config import (
    BlindfoldConfig,
    SensitiveFieldConfig,
    ToolSchemaConfig,
    schema_fields_for,
)
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.tokenizer import tokenize_result
from blindfold.core.vault import MemoryTokenStore
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import handle_blindfold_compute

FIXTURE = Path(__file__).parent / "recorded_transcript.json"


async def test_demo_flow_end_to_end():
    transcript = json.loads(FIXTURE.read_text(encoding="utf-8"))
    store = MemoryTokenStore()
    policy = SessionBoundPolicy()
    sandbox = SubprocessSandbox()
    session_id = f"e2e_{uuid.uuid4().hex[:8]}"
    ttl = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    config = BlindfoldConfig(
        schemas={
            "get_salary": ToolSchemaConfig(
                sensitive_fields=[
                    SensitiveFieldConfig(path="$.salary", semantic_type="salary", unit="EUR/year")
                ]
            )
        }
    )

    llm_visible_stream: list[str] = []  # anything a real LLM would have seen; probed for leaks below
    bindings: dict[str, Any] = {}

    server_params = StdioServerParameters(
        command=sys.executable, args=["-m", "examples.fake_hr_mcp"], env=None
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for step in transcript["steps"]:
                if step["kind"] == "tool_call":
                    call = await session.call_tool(step["name"], step["arguments"])
                    text = call.content[0].text if call.content else "{}"
                    fields = schema_fields_for(config, step["name"])
                    if fields:
                        payload = json.loads(text)
                        tokenized = tokenize_result(payload, step["name"], fields, store, session_id, ttl)
                        text = json.dumps(tokenized)
                    llm_visible_stream.append(text)
                    bindings[step["bind_result_as"]] = json.loads(text)

                elif step["kind"] == "compute":
                    inputs = [_deref(bindings, ref) for ref in step["inputs_from"]]
                    code = step["code_template"].format(*inputs)
                    token = handle_blindfold_compute(
                        {"code": code, "inputs": inputs},
                        store=store, policy=policy, sandbox=sandbox,
                        session_id=session_id, ttl_seconds=3600,
                    )
                    llm_visible_stream.append(token)
                    bindings[step["bind_result_as"]] = token

                elif step["kind"] == "final_text":
                    text = step["template"].format(**bindings)
                    llm_visible_stream.append(text)
                    rehydrated = rehydrate(text, session_id, store, policy)
                    assert rehydrated == transcript["expected_final_text"]

    joined = "\n".join(llm_visible_stream)
    for probe in transcript["leak_probes"]:
        assert str(probe) not in joined, f"real value leaked to LLM-visible stream: {probe!r}"


def _deref(bindings: dict, ref: str) -> Any:
    key, *path = ref.split(".")
    node = bindings[key]
    for p in path:
        node = node[p]
    return node
