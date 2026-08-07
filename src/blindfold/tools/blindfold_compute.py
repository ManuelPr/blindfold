"""blindfold_compute MCP tool definition and handler."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from blindfold.core.lineage import Lineage, Policy, VaultRecord, compose_policy, compose_ttl
from blindfold.ports.policy import DetokenizeContext, DetokenizePolicy
from blindfold.ports.sandbox import ComputeSandbox
from blindfold.ports.token_store import TokenStore

BLINDFOLD_COMPUTE_TOOL_NAME = "blindfold_compute"

_TOOL_DESCRIPTION = (
    "Run Python code on hidden values behind tokens ⟦tok_…⟧. "
    "Every token the code touches via resolve(...) MUST be listed in `inputs`. "
    "resolve(token) returns the hidden value ALREADY in its real type (a number stays "
    "a number, a string stays a string) — never a JSON string, never the whole tool "
    "response. Do not call json.loads(...) on it or index into it with ['field']. "
    "The code runs with a restricted set of builtins: no import, no open, no eval. "
    "The code MUST assign its result to a variable named `result` and the result "
    "MUST be JSON-serializable. The tool returns a NEW token (not the value); "
    "compose further tokens by calling this tool again."
)


def build_tool_definition() -> dict:
    return {
        "name": BLINDFOLD_COMPUTE_TOOL_NAME,
        "description": _TOOL_DESCRIPTION,
        "inputSchema": {
            "type": "object",
            "required": ["code", "inputs"],
            "properties": {
                "code": {"type": "string", "description": "Python code assigning `result`."},
                "inputs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Token strings the code will resolve.",
                },
            },
        },
    }


def _infer_dtype(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def handle_blindfold_compute(
    args: dict,
    *,
    store: TokenStore,
    policy: DetokenizePolicy,
    sandbox: ComputeSandbox,
    session_id: str,
    ttl_seconds: int,
    code_timeout_s: float = 5.0,
) -> str:
    code = args.get("code")
    inputs = args.get("inputs")
    if not isinstance(code, str) or not isinstance(inputs, list):
        raise ValueError("blindfold_compute requires string `code` and list `inputs`")

    ctx = DetokenizeContext(session_id=session_id)
    resolved: dict[str, Any] = {}
    input_records: list[VaultRecord] = []
    for token in inputs:
        record = store.get(token)
        if record is None:
            raise ValueError(f"unknown or expired input token: {token}")
        if not policy.can_compute(ctx, record):
            raise ValueError(f"policy denied compute on token: {token}")
        resolved[token] = record.value
        input_records.append(record)

    value = sandbox.run(code=code, inputs=resolved, timeout_s=code_timeout_s)

    new_token = TokenStore.mint_token()
    now = datetime.now(tz=timezone.utc)
    ttl = (
        compose_ttl(input_records)
        if input_records
        else now + timedelta(seconds=ttl_seconds)
    )
    derived_policy = compose_policy([r.policy for r in input_records])
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()

    store.put(
        VaultRecord(
            token=new_token,
            value=value,
            dtype=_infer_dtype(value),
            semantic_type=None,
            unit=None,
            session_id=session_id,
            created_at=now,
            ttl=ttl,
            lineage=Lineage(op="blind_compute", inputs=tuple(inputs), code_digest=digest),
            policy=derived_policy,
        )
    )
    return new_token
