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
EVENTS = (POST_TOOL_USE, MESSAGE_DISPLAY)


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
    raise ValueError(f"unknown hook event {event_name!r}; expected one of {', '.join(EVENTS)}")


def read_event(raw: str) -> dict[str, Any]:
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("hook input must be a JSON object")
    return event
