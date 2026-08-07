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
    assert re.fullmatch(r"⟦tok_[0-9a-f]{16}⟧", token)

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


# --- compute rate limit: the binary-search oracle's actual mitigation ------


def _compute(store, token, *, threshold, max_calls_per_token=8, rate_window_s=60):
    return handle_blindfold_compute(
        {"code": f"result = 'yes' if resolve('{token}') > {threshold} else 'no'", "inputs": [token]},
        store=store,
        policy=SessionBoundPolicy(),
        sandbox=SubprocessSandbox(),
        session_id="s",
        ttl_seconds=3600,
        max_calls_per_token=max_calls_per_token,
        rate_window_s=rate_window_s,
    )


def test_a_burst_of_probes_on_the_same_token_is_refused():
    # The binary-search oracle's actual shape: many calls, same token, close
    # together. This is what the limit exists to catch.
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", 71000)
    for i in range(3):
        _compute(store, "⟦tok_00000001⟧", threshold=1000 * i, max_calls_per_token=3)
    with pytest.raises(ValueError, match="rate limit"):
        _compute(store, "⟦tok_00000001⟧", threshold=3000, max_calls_per_token=3)


def test_a_different_token_is_not_throttled_by_someone_elses_burst():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", 71000)
    _put(store, "⟦tok_00000002⟧", 62000)
    for i in range(3):
        _compute(store, "⟦tok_00000001⟧", threshold=1000 * i, max_calls_per_token=3)
    # tok_00000002 has made zero calls; the limit is per-token, not per-session.
    _compute(store, "⟦tok_00000002⟧", threshold=500, max_calls_per_token=3)


def test_reuse_spread_across_a_session_is_not_throttled():
    # The exact case a flat lifetime cap would break: the same token used
    # many times for unrelated computations, hours apart, not consecutively.
    # A short rate window must let this through even past what a burst limit
    # would allow, because none of the calls are close together in time.
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", 71000)
    now = datetime.now(tz=timezone.utc)
    for hours_ago in (5, 4, 3, 2, 1):
        store.put(
            VaultRecord(
                token=f"⟦tok_old{hours_ago}⟧",
                value="irrelevant",
                dtype="string",
                semantic_type=None,
                unit=None,
                session_id="s",
                created_at=now - timedelta(hours=hours_ago),
                ttl=now + timedelta(hours=1),
                lineage=Lineage(op="blind_compute", inputs=("⟦tok_00000001⟧",)),
                policy=Policy(),
            )
        )
    # Five prior uses of the same token, but all outside a 1-minute window:
    # a call right now must still succeed.
    _compute(store, "⟦tok_00000001⟧", threshold=50000, max_calls_per_token=3, rate_window_s=60)


def test_zero_disables_the_limit():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", 71000)
    for i in range(20):
        _compute(store, "⟦tok_00000001⟧", threshold=i, max_calls_per_token=0)
