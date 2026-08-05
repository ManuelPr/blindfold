"""Token rehydration — replace ⟦tok_...⟧ placeholders in a string.

Missing tokens are surfaced as ``[unknown token]``; tokens present but
policy-denied become ``[redacted]``.
"""

from __future__ import annotations

import re

from blindfold.ports.policy import DetokenizeContext, DetokenizePolicy
from blindfold.ports.token_store import TokenStore

TOKEN_PATTERN = re.compile(r"⟦tok_[0-9a-f]{8}⟧")

PLACEHOLDER_PROMPT = (
    "Some tool results in this conversation come back as ⟦tok_XXXXXXXX⟧ placeholders "
    "instead of real values. You cannot read them, and guessing at them is always wrong.\n\n"
    "To compare, sort, aggregate or otherwise derive from them, call the `blindfold_compute` "
    "tool and pass every placeholder your code resolves in its `inputs` array. It returns a "
    "new placeholder, never a value.\n\n"
    "Reproduce placeholders VERBATIM in your answers — never invent, alter, shorten or "
    "paraphrase them. The real values are put back in their place after you are done; a "
    "mangled placeholder shows the user nothing."
)
"""Instruction the model needs for rehydration to survive its answer.

`rehydrate` can only replace placeholders the model reproduced exactly. This
lived in an example for a while, which meant every integration had to know to
go and copy it. Add it to your system prompt in Mode A and Mode B; Mode C sends
it in the `SessionStart` briefing.
"""


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
        return str(record.value)

    return TOKEN_PATTERN.sub(_sub, text)
