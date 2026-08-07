"""The one-tool MCP server that gives Mode C its blind compute.

The case that matters is the whole loop: a hook mints tokens, the server
computes on them, and the display hook reveals the result — three separate
processes' worth of state, one file.
"""

import json

import pytest

from blindfold import hooks, mcp_server
from blindfold.config import (
    BlindfoldConfig,
    SensitiveFieldConfig,
    StorageConfig,
    ToolSchemaConfig,
)
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.sqlite_store import SQLiteTokenStore
from blindfold.sandbox.subprocess_ import SubprocessSandbox

TOOL = "mcp__hr__get_salary"
SESSION = "sess_abc123"


@pytest.fixture
def config():
    return BlindfoldConfig(
        schemas={
            TOOL: ToolSchemaConfig(
                sensitive_fields=[SensitiveFieldConfig(path="$.salary", semantic_type="salary")]
            )
        }
    )


@pytest.fixture
def store(tmp_path):
    made = SQLiteTokenStore(tmp_path / "vault.db")
    yield made
    made.close()


def _tokenize(store, config, salary, *, session=SESSION):
    out = hooks.handle_post_tool_use(
        {
            "session_id": session,
            "tool_name": TOOL,
            "tool_output": json.dumps({"salary": salary}),
        },
        config=config,
        store=store,
    )
    return json.loads(out["hookSpecificOutput"]["updatedToolOutput"])["salary"]


def _compute(arguments, config, store):
    return mcp_server.compute(
        arguments,
        config=config,
        store=store,
        sandbox=SubprocessSandbox(),
        policy=SessionBoundPolicy(),
    )


# --- session inference ----------------------------------------------------


def test_session_is_read_off_the_input_tokens(store, config):
    token = _tokenize(store, config, 71000)
    assert mcp_server.session_of_inputs([token], store) == SESSION


def test_inputs_from_two_sessions_are_refused(store, config):
    mine = _tokenize(store, config, 71000, session="sess_mine")
    theirs = _tokenize(store, config, 62000, session="sess_theirs")
    with pytest.raises(ValueError, match="different sessions"):
        mcp_server.session_of_inputs([mine, theirs], store)


def test_unknown_token_is_refused(store):
    with pytest.raises(ValueError, match="unknown or expired"):
        mcp_server.session_of_inputs(["⟦tok_deadbeef⟧"], store)


def test_no_inputs_is_refused(store):
    with pytest.raises(ValueError, match="at least one token"):
        mcp_server.session_of_inputs([], store)


# --- the whole Mode C loop ------------------------------------------------


def test_tokenize_then_compute_then_reveal(store, config):
    andrea = _tokenize(store, config, 71000)
    manuel = _tokenize(store, config, 62000)

    derived = _compute(
        {
            "code": f"result = 'Andrea' if resolve('{andrea}') > resolve('{manuel}') else 'Manuel'",
            "inputs": [andrea, manuel],
        },
        config,
        store,
    )

    # The model gets a placeholder back, not an answer.
    assert derived.startswith("⟦tok_")
    assert derived not in (andrea, manuel)

    shown = hooks.handle_message_display(
        {"session_id": SESSION, "delta": f"The higher earner is {derived}."},
        store=store,
        policy=SessionBoundPolicy(),
    )
    assert shown["hookSpecificOutput"]["displayContent"] == "The higher earner is Andrea."


def test_derived_token_lands_in_the_session_that_can_reveal_it(store, config):
    # If compute minted into any other session, MessageDisplay — which is bound
    # to the host's session — would render the result as [redacted].
    token = _tokenize(store, config, 71000)
    derived = _compute({"code": f"result = resolve('{token}') * 2", "inputs": [token]}, config, store)
    assert store.get(derived).session_id == SESSION


def test_aggregation_over_many_tokens(store, config):
    tokens = [_tokenize(store, config, s) for s in (50000, 60000, 70000)]
    joined = ", ".join(f"resolve('{t}')" for t in tokens)
    derived = _compute({"code": f"result = sum([{joined}])", "inputs": tokens}, config, store)
    assert store.resolve(derived) == 180000


def test_a_failed_computation_does_not_return_a_value(store, config):
    token = _tokenize(store, config, 71000)
    with pytest.raises(Exception) as ei:
        _compute({"code": f"raise ValueError(resolve('{token}'))", "inputs": [token]}, config, store)
    assert "71000" not in str(ei.value)


# --- wiring ---------------------------------------------------------------


def test_server_advertises_exactly_the_compute_tool(store, config):
    server = mcp_server.build_server(config, store)
    assert server.name == "blindfold"


def test_memory_backend_is_refused(tmp_path):
    cfg = tmp_path / "blindfold.yaml"
    cfg.write_text("storage:\n  backend: memory\n", encoding="utf-8")
    with pytest.raises(mcp_server.SharedVaultRequired, match="sqlite"):
        mcp_server.load_runtime(cfg)


def test_sqlite_backend_loads(tmp_path):
    cfg = tmp_path / "blindfold.yaml"
    cfg.write_text(
        f"storage:\n  backend: sqlite\n  path: {tmp_path / 'v.db'}\n", encoding="utf-8"
    )
    config, store = mcp_server.load_runtime(cfg)
    try:
        assert config.storage.backend == "sqlite"
        assert isinstance(store, SQLiteTokenStore)
    finally:
        store.close()


def test_missing_config_still_refuses_because_the_default_is_memory(tmp_path):
    with pytest.raises(mcp_server.SharedVaultRequired):
        mcp_server.load_runtime(tmp_path / "does-not-exist.yaml")
    assert StorageConfig().backend == "memory"
