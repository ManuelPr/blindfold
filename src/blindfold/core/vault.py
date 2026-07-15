"""MemoryTokenStore — in-memory implementation of TokenStore.

Not thread-safe by design; MVP is single-process, single-session.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from blindfold.core.lineage import VaultRecord
from blindfold.ports.token_store import TokenStore


class MemoryTokenStore(TokenStore):
    def __init__(self) -> None:
        self._records: dict[str, VaultRecord] = {}

    @staticmethod
    def mint_token() -> str:
        return f"⟦tok_{secrets.token_hex(4)}⟧"

    def put(self, record: VaultRecord) -> None:
        self._records[record.token] = record

    def get(self, token: str) -> VaultRecord | None:
        rec = self._records.get(token)
        if rec is None:
            return None
        if rec.ttl <= self._now():
            return None
        return rec

    def resolve(self, token: str) -> Any | None:
        rec = self.get(token)
        return rec.value if rec is not None else None

    def find_by_session(self, session_id: str) -> list[VaultRecord]:
        now = self._now()
        return [r for r in self._records.values() if r.session_id == session_id and r.ttl > now]

    def invalidate_cascade(self, token: str) -> int:
        if token not in self._records:
            return 0
        to_remove = {token}
        changed = True
        while changed:
            changed = False
            for t, r in self._records.items():
                if t in to_remove:
                    continue
                if any(inp in to_remove for inp in r.lineage.inputs):
                    to_remove.add(t)
                    changed = True
        for t in to_remove:
            del self._records[t]
        return len(to_remove)

    def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = now if now is not None else self._now()
        expired = [t for t, r in self._records.items() if r.ttl <= cutoff]
        for t in expired:
            del self._records[t]
        return len(expired)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=timezone.utc)
