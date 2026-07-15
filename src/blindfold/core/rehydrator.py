"""Token rehydration — replace ⟦tok_...⟧ placeholders in a string.

Missing tokens are surfaced as ``[unknown token]``; tokens present but
policy-denied become ``[redacted]``.
"""

from __future__ import annotations

import re

from blindfold.ports.policy import DetokenizeContext, DetokenizePolicy
from blindfold.ports.token_store import TokenStore

TOKEN_PATTERN = re.compile(r"⟦tok_[0-9a-f]{8}⟧")


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
