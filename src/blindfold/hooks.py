"""Claude Code hook handlers — Blindfold without a proxy.

The host offers exactly the two seams this project needs, and they fit better
than the proxy does:

- ``PostToolUse`` can rewrite the tool result *the model receives*
  (``updatedToolOutput``). That is tokenization, and unlike the proxy it covers
  every tool, not only stdio MCP servers: ``Bash``, ``Read`` and ``WebFetch``
  included.
- ``MessageDisplay`` can rewrite *what the screen shows* without touching the
  transcript (``displayContent``). That is rehydration — and the display-only
  restriction is the feature, not a limitation: the user reads real values
  while the conversation keeps the placeholders, so the values never re-enter
  the model's context on the next turn. The proxy cannot make that distinction.

Both handlers are pure functions of their event: they take the parsed hook
input and return the JSON to print, or ``None`` for "change nothing".

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

from blindfold.config import BlindfoldConfig, schema_fields_for
from blindfold.core.rehydrator import TOKEN_PATTERN, rehydrate
from blindfold.core.tokenizer import tokenize_result
from blindfold.ports.policy import DetokenizePolicy
from blindfold.ports.token_store import TokenStore

POST_TOOL_USE = "post-tool-use"
MESSAGE_DISPLAY = "message-display"
SESSION_START = "session-start"
EVENTS = (POST_TOOL_USE, MESSAGE_DISPLAY, SESSION_START)


def describe_config(config: BlindfoldConfig) -> str | None:
    """The whole session's protected paths, in one briefing.

    Mode A can edit tool descriptions on their way past the proxy. Nothing in
    a host's hook system can — tool definitions are not rewritable — so the
    same information has to arrive as session context instead: once, at the
    start, before the first prompt.

    It carries the placeholder-preservation instruction too. A model that
    paraphrases ``⟦tok_7f3a1b2c⟧`` breaks rehydration, and in this mode there
    is no system prompt of ours to put that rule in.
    """
    lines = []
    for tool_name in sorted(config.schemas):
        fields = schema_fields_for(config, tool_name)
        if not fields:
            continue
        lines.append(f"  {tool_name}")
        for field in fields:
            meta = ", ".join(m for m in (field.semantic_type, field.unit) if m)
            lines.append(f"    {field.path}{f' — {meta}' if meta else ''}")
    if not lines:
        return None
    return (
        "Blindfold is protecting some of this session's tool results.\n\n"
        "Values at the paths listed below come back as ⟦tok_XXXXXXXX⟧ placeholders "
        "instead of real values. You cannot read them, and guessing at them is "
        "always wrong.\n\n"
        "To compare, sort, aggregate or otherwise derive from them, call the "
        "`blindfold_compute` tool (it may appear as `mcp__blindfold__blindfold_compute`). "
        "Pass every placeholder your code resolves in its `inputs` array; it returns a "
        "new placeholder, never a value.\n\n"
        "Reproduce placeholders VERBATIM in your answers — never invent, alter, "
        "shorten or paraphrase them. The user's screen shows the real values in "
        "their place; you never see them, and a mangled placeholder shows the user "
        "nothing.\n\n"
        "Protected paths:\n" + "\n".join(lines)
    )


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


def handle_post_tool_use(
    event: dict,
    *,
    config: BlindfoldConfig,
    store: TokenStore,
) -> dict | None:
    """Tokenize a tool result before the model sees it."""
    tool_name = event.get("tool_name") or ""
    fields = schema_fields_for(config, tool_name)
    if not fields:
        # Nothing declared for this tool: passing it through is the intended
        # behaviour, not a failure.
        return None

    raw = event.get("tool_output")
    if not isinstance(raw, str):
        return _block(f"{tool_name} declares protected fields but returned no text output")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _block(
            f"{tool_name} declares protected fields but did not return JSON, so Blindfold "
            f"cannot tell which parts are sensitive"
        )

    ttl = datetime.now(tz=timezone.utc) + timedelta(seconds=config.tokens.default_ttl)
    tokenized = tokenize_result(
        payload, tool_name, fields, store, _session_of(event), ttl
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
    """Show the user real values while the transcript keeps the placeholders."""
    text = event.get("message_text")
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
