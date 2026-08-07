"""Claude Code hook handlers — Blindfold without a proxy.

A host offers different seams than a protocol does, and on balance better ones:

- ``SessionStart`` can inject context before the first prompt
  (``additionalContext``). That carries ``describe_config`` — what the
  placeholders mean and how to operate on them. It exists because **no hook can
  edit a tool description**, which is where Mode A puts the same information.
- ``PostToolUse`` can rewrite the tool result *the model receives*
  (``updatedToolOutput``). That is tokenization, and unlike the proxy it covers
  every tool, not only stdio MCP servers: ``Bash``, ``Read`` and ``WebFetch``
  included.
- ``MessageDisplay`` can rewrite *what the screen shows* without touching the
  transcript (``displayContent``). That is rehydration — and the display-only
  restriction is the feature, not a limitation: the user reads real values
  while the conversation keeps the placeholders, so the values never re-enter
  the model's context on the next turn. The proxy cannot make that distinction.

The fourth piece is not a hook at all: **no hook can add a tool**, so
``blindfold_compute`` arrives as its own MCP server — see
:mod:`blindfold.mcp_server`. Without it the model can read placeholders and do
nothing with them.

Every handler is a pure function of its event: it takes the parsed hook input
and returns the JSON to print, or ``None`` for "change nothing".

Two properties this module has to hold:

**A persistent vault is mandatory.** Every hook invocation is a fresh process.
A token minted while a tool result is rewritten has to still resolve when the
answer is displayed, seconds later, from somewhere else. With
``storage.backend: memory`` these handlers would mint tokens into a dictionary
that dies immediately, and the user would read ``[unknown token]`` forever. The
CLI refuses to run them without a shared store.

**Failure blocks rather than passes.** Printing nothing means the host keeps
the original tool output — the real values, on their way to the model. So when
a tool has declared sensitive fields and its result cannot be tokenized, the
handler blocks the result instead of letting it through. Silence is only safe
where nothing was supposed to be protected in the first place.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from blindfold.config import (
    BlindfoldConfig,
    describe_config,
    schema_fields_for,
    table_schemas_for,
)
from blindfold.core.rehydrator import TOKEN_PATTERN, rehydrate
from blindfold.core.tokenizer import tokenize_result
from blindfold.ports.policy import DetokenizePolicy
from blindfold.ports.token_store import TokenStore

POST_TOOL_USE = "post-tool-use"
MESSAGE_DISPLAY = "message-display"
SESSION_START = "session-start"
EVENTS = (POST_TOOL_USE, MESSAGE_DISPLAY, SESSION_START)


def handle_session_start(event: dict, *, config: BlindfoldConfig) -> dict | None:
    """Tell the model what the placeholders mean, once, before the first prompt."""
    brief = describe_config(config)
    if brief is None:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": brief,
        }
    }


def _extract_tool_text(event: dict) -> str | None:
    """The tool's textual result, whichever shape the host used to send it.

    Confirmed against a real host, not assumed: built-in tools (Edit, Bash,
    ...) send a flat string under ``tool_output``. MCP tools send
    ``tool_response`` instead — a list mirroring MCP's own result shape,
    ``[{"type": "text", "text": "..."}]`` — and ``tool_output`` is absent
    entirely, which is what made every MCP call blocked as "no text output"
    before this.

    Only the single-text-part case is handled. Multiple parts, or anything
    that is not plain text (an image, a resource blob), is a shape Blindfold
    cannot safely mask through this single-string path — treated the same as
    absent, so the caller blocks rather than guesses.
    """
    raw = event.get("tool_output")
    if isinstance(raw, str):
        return raw

    response = event.get("tool_response")
    if isinstance(response, list) and len(response) == 1:
        part = response[0]
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            return part["text"]
    return None


def handle_post_tool_use(
    event: dict,
    *,
    config: BlindfoldConfig,
    store: TokenStore,
) -> dict | None:
    """Tokenize a tool result before the model sees it."""
    tool_name = event.get("tool_name") or ""
    fields = schema_fields_for(config, tool_name)
    tables = table_schemas_for(config, tool_name)
    if not fields and not tables:
        # Nothing declared for this tool: passing it through is the intended
        # behaviour, not a failure.
        return None

    raw = _extract_tool_text(event)
    if raw is None:
        # Diagnostic, not guesswork: the shapes handled here are what actually
        # showed up from a real host. Types and key names only, never a value —
        # the payload can legitimately hold the real hidden data at this point,
        # masking not having run yet.
        shape = {k: type(v).__name__ for k, v in event.items()}
        return _block(
            f"{tool_name} declares protected fields but returned no usable text "
            f"(event shape: {shape})"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _block(
            f"{tool_name} declares protected fields but did not return JSON, so Blindfold "
            f"cannot tell which parts are sensitive"
        )

    ttl = datetime.now(tz=timezone.utc) + timedelta(seconds=config.tokens.default_ttl)
    tokenized = tokenize_result(
        payload, tool_name, fields, store, _session_of(event), ttl, tables=tables
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": json.dumps(tokenized, ensure_ascii=False),
        }
    }


def handle_message_display(
    event: dict,
    *,
    store: TokenStore,
    policy: DetokenizePolicy,
) -> dict | None:
    """Show the user real values while the transcript keeps the placeholders.

    The field is ``delta`` — "the newly completed lines" of the message as it
    streams — not a ``message_text`` holding the whole thing. No page of the
    hosted docs states this; it was found live, in the host's own `/hooks`
    inspector, after the general hook documentation's generic "message_text"
    field (real for other events, assumed here) sent this hook chasing a key
    that was never in the payload. It always returned None — correctly, since
    the field it checked was always absent — which is why nothing ever showed
    up as a failure: there was nothing to fail. Every delta is handled on its
    own; a token is expected to land whole within one, since deltas are
    "newly completed lines" and delimiters don't span line breaks.
    """
    text = event.get("delta")
    if not isinstance(text, str) or not TOKEN_PATTERN.search(text):
        # This fires on every assistant message and the host allows 10 seconds,
        # so the overwhelmingly common case gets out before touching the vault.
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "MessageDisplay",
            "displayContent": rehydrate(text, _session_of(event), store, policy),
        }
    }


def _block(reason: str) -> dict:
    return {"decision": "block", "reason": f"Blindfold: {reason}"}


def _session_of(event: dict) -> str:
    # The host's session id is what ties the two hooks together, and what
    # SessionBoundPolicy uses to refuse another session's tokens.
    return str(event.get("session_id") or "unknown")


def dispatch(
    event_name: str,
    event: dict,
    *,
    config: BlindfoldConfig,
    store: TokenStore,
    policy: DetokenizePolicy,
) -> dict | None:
    if event_name == POST_TOOL_USE:
        return handle_post_tool_use(event, config=config, store=store)
    if event_name == MESSAGE_DISPLAY:
        return handle_message_display(event, store=store, policy=policy)
    if event_name == SESSION_START:
        return handle_session_start(event, config=config)
    raise ValueError(f"unknown hook event {event_name!r}; expected one of {', '.join(EVENTS)}")


def read_event(raw: str) -> dict[str, Any]:
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("hook input must be a JSON object")
    return event
