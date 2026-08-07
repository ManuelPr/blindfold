"""Claude Code hook handlers.

The scenario under test is the one the proxy cannot do: tokenizing and
revealing happen in different processes, so every case that matters uses a
separate store instance over the same file.
"""

import io
import json

import pytest

from blindfold import hooks
from blindfold.cli import run_hook
from blindfold.config import (
    BlindfoldConfig,
    SensitiveFieldConfig,
    ToolSchemaConfig,
)
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.sqlite_store import SQLiteTokenStore

TOOL = "mcp__hr__get_salary"
SESSION = "sess_abc123"


@pytest.fixture
def config():
    return BlindfoldConfig(
        schemas={
            TOOL: ToolSchemaConfig(
                sensitive_fields=[
                    SensitiveFieldConfig(path="$.salary", semantic_type="salary", unit="EUR/year")
                ]
            )
        }
    )


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "vault.db"


def _store(path):
    return SQLiteTokenStore(path)


def _post_tool_use_event(output, *, tool=TOOL, session=SESSION):
    return {
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": {"name": "Andrea Tuscano"},
        "tool_output": output,
        "tool_use_id": "toolu_01ABC",
    }


def _display_event(text, *, session=SESSION):
    return {
        "session_id": session,
        "hook_event_name": "MessageDisplay",
        "message_text": text,
    }


# --- PostToolUse ----------------------------------------------------------


def test_declared_field_is_replaced_before_the_model_sees_it(config, vault_path):
    store = _store(vault_path)
    try:
        out = hooks.handle_post_tool_use(
            _post_tool_use_event('{"name": "Andrea Tuscano", "salary": 71000}'),
            config=config,
            store=store,
        )
    finally:
        store.close()

    rewritten = json.loads(out["hookSpecificOutput"]["updatedToolOutput"])
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert rewritten["name"] == "Andrea Tuscano"  # undeclared, passes through
    assert rewritten["salary"].startswith("⟦tok_")
    assert "71000" not in out["hookSpecificOutput"]["updatedToolOutput"]


def test_tool_without_declared_fields_is_left_alone(config, vault_path):
    store = _store(vault_path)
    try:
        assert (
            hooks.handle_post_tool_use(
                _post_tool_use_event('{"anything": 1}', tool="Bash"),
                config=config,
                store=store,
            )
            is None
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    "output",
    ["not json at all", "", None, 71000],
)
def test_declared_tool_that_cannot_be_tokenized_is_blocked(config, vault_path, output):
    # Returning None here would hand the host the original output, which is the
    # real value on its way to the model. Silence is only safe when nothing was
    # supposed to be protected.
    store = _store(vault_path)
    try:
        out = hooks.handle_post_tool_use(
            _post_tool_use_event(output), config=config, store=store
        )
    finally:
        store.close()
    assert out["decision"] == "block"
    assert TOOL in out["reason"]


def test_the_no_text_output_block_names_the_events_shape_without_its_values(config, vault_path):
    # Reproduced against a real host: PostToolUse blocked with only "returned
    # no text output" and no way to tell what Claude Code had actually sent for
    # an MCP tool call. The reason now carries the event's key names and Python
    # types — never a value, since the payload can legitimately hold the real
    # hidden data at this point, masking not having run yet.
    store = _store(vault_path)
    try:
        out = hooks.handle_post_tool_use(
            {
                "session_id": SESSION,
                "tool_name": TOOL,
                "tool_output": {"salary": 71000},  # e.g. structured, not a string
                "tool_response": "Andrea Tuscano 71000",  # a real value, must never appear
            },
            config=config,
            store=store,
        )
    finally:
        store.close()
    assert out["decision"] == "block"
    assert "tool_output is dict" in out["reason"]
    assert "tool_output" in out["reason"] and "tool_response" in out["reason"]
    assert "71000" not in out["reason"]
    assert "Andrea Tuscano" not in out["reason"]


# --- MessageDisplay -------------------------------------------------------


def test_display_is_untouched_when_there_are_no_tokens(vault_path):
    store = _store(vault_path)
    try:
        assert (
            hooks.handle_message_display(
                _display_event("Nothing to reveal here."),
                store=store,
                policy=SessionBoundPolicy(),
            )
            is None
        )
    finally:
        store.close()


def test_tokenize_in_one_store_then_reveal_from_another(config, vault_path):
    # The two-process case, which is the entire reason this exists.
    writer = _store(vault_path)
    try:
        out = hooks.handle_post_tool_use(
            _post_tool_use_event('{"name": "Andrea Tuscano", "salary": 71000}'),
            config=config,
            store=writer,
        )
    finally:
        writer.close()
    token = json.loads(out["hookSpecificOutput"]["updatedToolOutput"])["salary"]

    reader = _store(vault_path)
    try:
        shown = hooks.handle_message_display(
            _display_event(f"Andrea earns {token}."),
            store=reader,
            policy=SessionBoundPolicy(),
        )
    finally:
        reader.close()

    assert shown["hookSpecificOutput"]["hookEventName"] == "MessageDisplay"
    assert shown["hookSpecificOutput"]["displayContent"] == "Andrea earns 71000."


def test_another_session_cannot_reveal_the_token(config, vault_path):
    writer = _store(vault_path)
    try:
        out = hooks.handle_post_tool_use(
            _post_tool_use_event('{"salary": 71000}'), config=config, store=writer
        )
    finally:
        writer.close()
    token = json.loads(out["hookSpecificOutput"]["updatedToolOutput"])["salary"]

    reader = _store(vault_path)
    try:
        shown = hooks.handle_message_display(
            _display_event(f"Andrea earns {token}.", session="sess_someone_else"),
            store=reader,
            policy=SessionBoundPolicy(),
        )
    finally:
        reader.close()

    assert "71000" not in shown["hookSpecificOutput"]["displayContent"]
    assert "[redacted]" in shown["hookSpecificOutput"]["displayContent"]


def test_unknown_event_name_raises(config, vault_path):
    store = _store(vault_path)
    try:
        with pytest.raises(ValueError, match="unknown hook event"):
            hooks.dispatch(
                "nope", {}, config=config, store=store, policy=SessionBoundPolicy()
            )
    finally:
        store.close()


# --- the CLI wrapper ------------------------------------------------------


def _run(monkeypatch, capsys, event_name, payload, config_path):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = run_hook([event_name, "--config", str(config_path)])
    return code, capsys.readouterr()


def _write_config(tmp_path, backend: str, vault_path) -> str:
    p = tmp_path / "blindfold.yaml"
    p.write_text(
        f"storage:\n  backend: {backend}\n  path: {vault_path}\n"
        f"schemas:\n  {TOOL}:\n    sensitive_fields:\n      - path: $.salary\n",
        encoding="utf-8",
    )
    return p


def test_cli_blocks_when_the_vault_cannot_be_shared(monkeypatch, capsys, tmp_path, vault_path):
    # memory would mint tokens into a dictionary that dies with this process.
    cfg = _write_config(tmp_path, "memory", vault_path)
    code, out = _run(
        monkeypatch,
        capsys,
        hooks.POST_TOOL_USE,
        _post_tool_use_event('{"salary": 71000}'),
        cfg,
    )
    assert code == 0
    assert json.loads(out.out)["decision"] == "block"
    assert "sqlite" in out.err


def test_cli_stays_quiet_when_display_cannot_run(monkeypatch, capsys, tmp_path, vault_path):
    # Nothing leaks if the display hook does nothing: the user just reads a
    # placeholder. So this one must not block the session.
    cfg = _write_config(tmp_path, "memory", vault_path)
    code, out = _run(
        monkeypatch, capsys, hooks.MESSAGE_DISPLAY, _display_event("hi"), cfg
    )
    assert code == 0
    assert out.out == ""


def test_cli_round_trip_over_two_invocations(monkeypatch, capsys, tmp_path, vault_path):
    cfg = _write_config(tmp_path, "sqlite", vault_path)

    _, first = _run(
        monkeypatch,
        capsys,
        hooks.POST_TOOL_USE,
        _post_tool_use_event('{"name": "Andrea Tuscano", "salary": 71000}'),
        cfg,
    )
    token = json.loads(json.loads(first.out)["hookSpecificOutput"]["updatedToolOutput"])["salary"]
    assert "71000" not in first.out

    _, second = _run(
        monkeypatch,
        capsys,
        hooks.MESSAGE_DISPLAY,
        _display_event(f"Andrea earns {token}."),
        cfg,
    )
    assert json.loads(second.out)["hookSpecificOutput"]["displayContent"] == "Andrea earns 71000."


def test_cli_blocks_on_unreadable_input(monkeypatch, capsys, tmp_path, vault_path):
    cfg = _write_config(tmp_path, "sqlite", vault_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    code = run_hook([hooks.POST_TOOL_USE, "--config", str(cfg)])
    out = capsys.readouterr()
    assert code == 0
    assert json.loads(out.out)["decision"] == "block"


def test_cli_never_puts_an_exception_message_in_its_output(monkeypatch, capsys, tmp_path, vault_path):
    # Same hygiene as the sandbox: a message can carry a value it touched.
    cfg = _write_config(tmp_path, "sqlite", vault_path)

    def boom(*_a, **_k):
        raise RuntimeError("secret value 71000")

    monkeypatch.setattr(hooks, "dispatch", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_post_tool_use_event("{}"))))
    run_hook([hooks.POST_TOOL_USE, "--config", str(cfg)])
    out = capsys.readouterr()
    assert "71000" not in out.out + out.err
    assert "RuntimeError" in out.err


# --- SessionStart: the only way to explain placeholders in a host ----------
#
# No hook can rewrite a tool description, so the information Mode A appends to
# tools/list has to arrive as session context instead.


def test_session_start_names_every_protected_path(config, tmp_path, vault_path):
    out = hooks.handle_session_start({"session_id": SESSION}, config=config)
    brief = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert TOOL in brief
    assert "$.salary" in brief
    assert "EUR/year" in brief


def test_session_start_tells_the_model_how_to_operate_and_to_copy_verbatim(config):
    brief = hooks.handle_session_start({"session_id": SESSION}, config=config)[
        "hookSpecificOutput"
    ]["additionalContext"]
    assert "blindfold_compute" in brief
    assert "VERBATIM" in brief


def test_session_start_is_silent_when_nothing_is_declared():
    assert hooks.handle_session_start({"session_id": SESSION}, config=BlindfoldConfig()) is None


def test_session_start_never_contains_a_real_value(config):
    # It is built from config alone and never touches a response.
    brief = hooks.handle_session_start({"session_id": SESSION}, config=config)[
        "hookSpecificOutput"
    ]["additionalContext"]
    assert "71000" not in brief


def test_cli_session_start_round_trip(monkeypatch, capsys, tmp_path, vault_path):
    cfg = _write_config(tmp_path, "sqlite", vault_path)
    _, out = _run(
        monkeypatch,
        capsys,
        hooks.SESSION_START,
        {"session_id": SESSION, "hook_event_name": "SessionStart", "source": "startup"},
        cfg,
    )
    assert TOOL in json.loads(out.out)["hookSpecificOutput"]["additionalContext"]


# --- a tool that declares only a table --------------------------------------
#
# PostToolUse checked sensitive_fields alone, so a tool whose declaration was a
# `tables:` entry got no protection at all: the hook returned None and the host
# kept the original result, real values included. Silent, and fail-open, in the
# mode documented as the best one.


TABLE_TOOL = "mcp__hr__list_employees"


@pytest.fixture
def table_config():
    from blindfold.config import ColumnConfig, TableConfig

    return BlindfoldConfig(
        schemas={
            TABLE_TOOL: ToolSchemaConfig(
                tables=[
                    TableConfig(
                        path="$.employees",
                        columns=[
                            ColumnConfig(name="name", semantic_type="person_name"),
                            ColumnConfig(name="salary", semantic_type="salary", unit="EUR/year"),
                        ],
                    )
                ]
            )
        }
    )


def test_a_table_only_tool_is_tokenized_by_the_hook(table_config, vault_path):
    store = _store(vault_path)
    try:
        out = hooks.handle_post_tool_use(
            {
                "session_id": SESSION,
                "tool_name": TABLE_TOOL,
                "tool_output": json.dumps(
                    {"employees": [{"name": "Andrea", "salary": 71000}]}
                ),
            },
            config=table_config,
            store=store,
        )
    finally:
        store.close()

    assert out is not None, "returning None hands the host the real values"
    rewritten = out["hookSpecificOutput"]["updatedToolOutput"]
    assert "71000" not in rewritten
    assert "Andrea" not in rewritten
    assert json.loads(rewritten)["employees"].startswith("⟦tok_")


def test_the_table_token_is_queryable_from_another_process(table_config, vault_path):
    from blindfold.core.policy import SessionBoundPolicy as _Policy
    from blindfold.tools.blindfold_table import handle_blindfold_table

    writer = _store(vault_path)
    try:
        out = hooks.handle_post_tool_use(
            {
                "session_id": SESSION,
                "tool_name": TABLE_TOOL,
                "tool_output": json.dumps(
                    {"employees": [{"name": "Andrea", "salary": 71000}, {"name": "Maria", "salary": 55000}]}
                ),
            },
            config=table_config,
            store=writer,
        )
    finally:
        writer.close()
    token = json.loads(out["hookSpecificOutput"]["updatedToolOutput"])["employees"]

    reader = _store(vault_path)
    try:
        derived = handle_blindfold_table(
            {"table": token, "ops": [{"op": "max", "column": "salary"}]},
            store=reader, policy=_Policy(), session_id=SESSION, ttl_seconds=3600,
        )
        assert reader.resolve(derived) == 71000
    finally:
        reader.close()
