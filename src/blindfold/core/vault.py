"""MemoryTokenStore — in-memory implementation of TokenStore.

Not thread-safe by design; MVP is single-process, single-session.

Expiry is enforced twice, for two different reasons. `get` hides expired
records so a stale token never resolves. `put` sweeps them, on an interval, so
an expired value stops *existing*: a TTL that only governs resolvability would
leave yesterday's salary sitting in this process's memory in cleartext, and
short TTLs are the mitigation this project recommends. The sweep lives here
rather than in the proxy because the in-process library builds its own store
and never goes near the proxy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from blindfold.core.lineage import VaultRecord
from blindfold.ports.token_store import TokenStore


class MemoryTokenStore(TokenStore):
    #: Seconds between sweeps. Set it lower on an instance if a deployment
    #: wants expired values gone sooner than this.
    purge_interval_s: float = 60.0

    def __init__(self) -> None:
        self._records: dict[str, VaultRecord] = {}
        self._last_purge = self._now()

    def put(self, record: VaultRecord) -> None:
        self._sweep_if_due()
        self._records[record.token] = record

    def _sweep_if_due(self) -> None:
        # ponytail: linear scan over every record, amortized by the interval.
        # An expiry-ordered index is the upgrade if a vault ever gets big
        # enough for the scan to show up.
        now = self._now()
        if (now - self._last_purge).total_seconds() < self.purge_interval_s:
            return
        self._last_purge = now
        self.purge_expired(now)

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
