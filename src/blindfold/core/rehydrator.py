"""Token rehydration — replace ⟦tok_...⟧ placeholders in a string.

Missing tokens are surfaced as ``[unknown token]``; tokens present but
policy-denied become ``[redacted]``.
"""

from __future__ import annotations

import json
import re

from blindfold.ports.policy import DetokenizeContext, DetokenizePolicy
from blindfold.ports.token_store import TokenStore

# 16 hex is what `mint_token` produces now; 8 is what it produced before, and a
# persistent vault outlives the version that filled it. Matching both keeps
# yesterday's placeholders — already sitting in conversation history — resolving
# after an upgrade. A width that matches but is not in the vault rehydrates to
# `[unknown token]`, which is the same answer any unknown token gets.
TOKEN_PATTERN = re.compile(r"⟦tok_[0-9a-f]{8}(?:[0-9a-f]{8})?⟧")

PLACEHOLDER_PROMPT = (
    "Some tool results in this conversation come back as ⟦tok_…⟧ placeholders "
    "instead of real values. You cannot read them, and guessing at them is always wrong.\n\n"
    "To compare, sort, aggregate or otherwise derive from them, call the `blindfold_compute` "
    "tool and pass every placeholder your code resolves in its `inputs` array. It returns a "
    "new placeholder, never a value.\n\n"
    "Reproduce placeholders VERBATIM in your answers — never invent, alter, shorten or "
    "paraphrase them. The real values are put back in their place after you are done; a "
    "mangled placeholder shows the user nothing.\n\n"
    "If a computation keeps failing, SAY SO instead of guessing a final answer. A wrong "
    "answer stated with confidence is worse than admitting you could not complete the "
    "comparison — the user cannot tell the difference between a real result and a guess "
    "unless you tell them which one it is."
)
"""Instruction the model needs for rehydration to survive its answer.

`rehydrate` can only replace placeholders the model reproduced exactly. This
lived in an example for a while, which meant every integration had to know to
go and copy it. Add it to your system prompt in Mode A and Mode B; Mode C sends
it in the `SessionStart` briefing.
"""


def _render(value: object) -> str:
    """Scalars as themselves, containers as JSON.

    `str()` on a list of rows gives a Python repr — single quotes, `True`,
    `None` — which is not what a user asked for and not valid anything.
    Collective tokens made that reachable.
    """
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def rehydrate(
    text: str,
    session_id: str,
    store: TokenStore,
    policy: DetokenizePolicy,
) -> str:
    ctx = DetokenizeContext(session_id=session_id)

    def _sub(match: re.Match[str]) -> str:
        token = match.group(0)
        record = store.get(token)
        if record is None:
            return "[unknown token]"
        if not policy.can_reveal(ctx, record):
            return "[redacted]"
        return _render(record.value)

    return TOKEN_PATTERN.sub(_sub, text)
