from datetime import datetime, timedelta, timezone

import pytest

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.vault import MemoryTokenStore
from blindfold.ports.sandbox import SandboxError
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import (
    BLINDFOLD_COMPUTE_TOOL_NAME,
    build_tool_definition,
    handle_blindfold_compute,
)


def _put(store: MemoryTokenStore, token: str, value, *, session: str = "s", ttl_min: int = 60,
         reveal: bool = True, compute: bool = True) -> None:
    now = datetime.now(tz=timezone.utc)
    store.put(
        VaultRecord(
            token=token,
            value=value,
            dtype="number" if isinstance(value, (int, float)) else "string",
            semantic_type=None,
            unit=None,
            session_id=session,
            created_at=now,
            ttl=now + timedelta(minutes=ttl_min),
            lineage=Lineage(op="tool_result", tool="hr", path="$.x"),
            policy=Policy(reveal_to_frontend=reveal, can_be_input_to_compute=compute),
        )
    )


def test_tool_definition_shape():
    spec = build_tool_definition()
    assert spec["name"] == BLINDFOLD_COMPUTE_TOOL_NAME
    assert "description" in spec and len(spec["description"]) > 20
    schema = spec["inputSchema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"code", "inputs"}
    assert schema["properties"]["code"]["type"] == "string"
    assert schema["properties"]["inputs"]["type"] == "array"


def test_handler_happy_path_returns_token_and_stores_record():
    import re
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", 100)
    _put(store, "⟦tok_00000002⟧", 200)

    token = handle_blindfold_compute(
        {"code": "result = 'A' if resolve('⟦tok_00000001⟧') > resolve('⟦tok_00000002⟧') else 'B'",
         "inputs": ["⟦tok_00000001⟧", "⟦tok_00000002⟧"]},
        store=store,
        policy=SessionBoundPolicy(),
        sandbox=SubprocessSandbox(),
        session_id="s",
        ttl_seconds=3600,
    )
    assert re.fullmatch(r"⟦tok_[0-9a-f]{8}⟧", token)

    rec = store.get(token)
    assert rec is not None
    assert rec.value == "B"
    assert rec.dtype == "string"
    assert rec.lineage.op == "blind_compute"
    assert set(rec.lineage.inputs) == {"⟦tok_00000001⟧", "⟦tok_00000002⟧"}
    assert rec.lineage.code_digest is not None
    assert len(rec.lineage.code_digest) == 64  # sha256 hex


def test_handler_ttl_is_min_of_inputs():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", 1, ttl_min=10)
    _put(store, "⟦tok_00000002⟧", 2, ttl_min=999)

    token = handle_blindfold_compute(
        {"code": "result = resolve('⟦tok_00000001⟧') + resolve('⟦tok_00000002⟧')",
         "inputs": ["⟦tok_00000001⟧", "⟦tok_00000002⟧"]},
        store=store,
        policy=SessionBoundPolicy(),
        sandbox=SubprocessSandbox(),
        session_id="s",
        ttl_seconds=3600,
    )
    a = store.get("⟦tok_00000001⟧")
    new = store.get(token)
    assert new.ttl == a.ttl


def test_handler_policy_denies_cross_session():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", 1, session="other")
    with pytest.raises(ValueError) as ei:
        handle_blindfold_compute(
            {"code": "result = resolve('⟦tok_00000001⟧')",
             "inputs": ["⟦tok_00000001⟧"]},
            store=store,
            policy=SessionBoundPolicy(),
            sandbox=SubprocessSandbox(),
            session_id="s",
            ttl_seconds=3600,
        )
    assert "policy" in str(ei.value).lower() or "denied" in str(ei.value).lower()


def test_handler_rejects_unknown_input_token():
    store = MemoryTokenStore()
    with pytest.raises(ValueError):
        handle_blindfold_compute(
            {"code": "result = 1",
             "inputs": ["⟦tok_deadbeef⟧"]},
            store=store,
            policy=SessionBoundPolicy(),
            sandbox=SubprocessSandbox(),
            session_id="s",
            ttl_seconds=3600,
        )


def test_handler_missing_args_raises_value_error():
    store = MemoryTokenStore()
    with pytest.raises(ValueError):
        handle_blindfold_compute(
            {"code": "result = 1"},  # missing 'inputs'
            store=store,
            policy=SessionBoundPolicy(),
            sandbox=SubprocessSandbox(),
            session_id="s",
            ttl_seconds=3600,
        )


def test_handler_propagates_sandbox_error():
    store = MemoryTokenStore()
    with pytest.raises(SandboxError):
        handle_blindfold_compute(
            {"code": "result = object()", "inputs": []},
            store=store,
            policy=SessionBoundPolicy(),
            sandbox=SubprocessSandbox(),
            session_id="s",
            ttl_seconds=3600,
        )
