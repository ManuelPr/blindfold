"""SessionBoundPolicy — MVP detokenize policy.

Tokens are only revealable/computable from the session that minted them.
"""

from __future__ import annotations

from blindfold.core.lineage import VaultRecord
from blindfold.ports.policy import DetokenizeContext, DetokenizePolicy


class SessionBoundPolicy(DetokenizePolicy):
    def can_reveal(self, context: DetokenizeContext, record: VaultRecord) -> bool:
        return (
            record.policy.reveal_to_frontend
            and context.session_id == record.session_id
        )

    def can_compute(self, context: DetokenizeContext, record: VaultRecord) -> bool:
        return (
            record.policy.can_be_input_to_compute
            and context.session_id == record.session_id
        )
