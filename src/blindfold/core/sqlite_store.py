"""SQLiteTokenStore — a vault that outlives the process that filled it.

Two things need this, and they need it for different reasons.

A multi-process deployment needs it because the placeholders already sent to a
model do not die with a restart: they sit in conversation history, in logs, in
the application's database, and a memory vault leaves them pointing at values
that no longer exist anywhere.

Host integrations need it because tokenizing and rehydrating can happen in
*different processes*. A Claude Code hook, for instance, is a fresh process per
invocation — the token minted while a tool result is rewritten has to still
resolve when the answer is displayed, seconds later, from somewhere else.

**The file holds cleartext values.** There is no encryption at rest here; see
LIMITATIONS.md#storage for why a key stored beside the database would be
decoration. The store narrows the file's permissions where the platform
supports it, and that is the whole of its protection. Treat the file as the
secret it contains.

Uses `sqlite3` from the standard library — no new dependency.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.ports.token_store import TokenStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    token         TEXT PRIMARY KEY,
    value         TEXT NOT NULL,   -- JSON
    dtype         TEXT NOT NULL,
    semantic_type TEXT,
    unit          TEXT,
    session_id    TEXT NOT NULL,
    created_at    TEXT NOT NULL,   -- ISO 8601, for exact reconstruction
    ttl           TEXT NOT NULL,   -- ISO 8601, for exact reconstruction
    ttl_epoch     REAL NOT NULL,   -- seconds, for range queries
    lineage       TEXT NOT NULL,   -- JSON
    policy        TEXT NOT NULL    -- JSON
);
CREATE INDEX IF NOT EXISTS idx_records_session ON records(session_id);
CREATE INDEX IF NOT EXISTS idx_records_ttl ON records(ttl_epoch);
"""


class SQLiteTokenStore(TokenStore):
    #: Seconds between expiry sweeps, as in MemoryTokenStore.
    purge_interval_s: float = 60.0

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: autocommit. Every method here is a single
        # statement or an explicit transaction, and a vault that loses the last
        # write on a crash is worse than one that does not.
        # check_same_thread=False plus an explicit lock: the proxy runs blind
        # compute in a worker thread, so the connection outlives the thread
        # that made it. Every statement below goes through the lock.
        # Reentrant: put() sweeps, resolve() goes through get().
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        # WAL is what makes two processes on one file safe, which is the whole
        # reason this class exists. busy_timeout turns a concurrent write from
        # an immediate "database is locked" into a short wait.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._restrict_permissions()
        self._last_purge = self._now()

    # --- TokenStore -------------------------------------------------------

    def put(self, record: VaultRecord) -> None:
        with self._lock:
            self._sweep_if_due()
            self._put(record)

    def _put(self, record: VaultRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO records "
            "(token, value, dtype, semantic_type, unit, session_id, created_at, "
            " ttl, ttl_epoch, lineage, policy) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.token,
                json.dumps(record.value),
                record.dtype,
                record.semantic_type,
                record.unit,
                record.session_id,
                record.created_at.isoformat(),
                record.ttl.isoformat(),
                record.ttl.timestamp(),
                json.dumps(
                    {
                        "op": record.lineage.op,
                        "inputs": list(record.lineage.inputs),
                        "code_digest": record.lineage.code_digest,
                        "tool": record.lineage.tool,
                        "path": record.lineage.path,
                    }
                ),
                json.dumps(
                    {
                        "reveal_to_frontend": record.policy.reveal_to_frontend,
                        "can_be_input_to_compute": record.policy.can_be_input_to_compute,
                    }
                ),
            ),
        )

    def get(self, token: str) -> VaultRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM records WHERE token = ? AND ttl_epoch > ?",
                (token, self._now().timestamp()),
            ).fetchone()
        return _from_row(row) if row is not None else None

    def resolve(self, token: str) -> Any | None:
        record = self.get(token)
        return record.value if record is not None else None

    def find_by_session(self, session_id: str) -> list[VaultRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM records WHERE session_id = ? AND ttl_epoch > ?",
                (session_id, self._now().timestamp()),
            ).fetchall()
        return [_from_row(r) for r in rows]

    def invalidate_cascade(self, token: str) -> int:
        with self._lock:
            return self._invalidate_cascade(token)

    def _invalidate_cascade(self, token: str) -> int:
        exists = self._conn.execute(
            "SELECT 1 FROM records WHERE token = ?", (token,)
        ).fetchone()
        if exists is None:
            return 0

        # ponytail: loads every (token, inputs) pair and closes the descendant
        # set in Python, same shape as MemoryTokenStore. An edges table would
        # let SQLite do it with a recursive CTE; worth it only if lineage DAGs
        # get big enough to notice.
        edges = [
            (r["token"], tuple(json.loads(r["lineage"])["inputs"]))
            for r in self._conn.execute("SELECT token, lineage FROM records")
        ]
        to_remove = {token}
        changed = True
        while changed:
            changed = False
            for tok, inputs in edges:
                if tok in to_remove:
                    continue
                if any(i in to_remove for i in inputs):
                    to_remove.add(tok)
                    changed = True

        self._conn.executemany(
            "DELETE FROM records WHERE token = ?", [(t,) for t in to_remove]
        )
        return len(to_remove)

    def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = (now if now is not None else self._now()).timestamp()
        with self._lock:
            cur = self._conn.execute("DELETE FROM records WHERE ttl_epoch <= ?", (cutoff,))
            return cur.rowcount

    # --- housekeeping -----------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _sweep_if_due(self) -> None:
        now = self._now()
        if (now - self._last_purge).total_seconds() < self.purge_interval_s:
            return
        self._last_purge = now
        self.purge_expired(now)

    def _restrict_permissions(self) -> None:
        # Owner-only on POSIX. Windows ignores the mode bits, which is why this
        # is documented as "narrows where supported" rather than as protection.
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=timezone.utc)


def _from_row(row: sqlite3.Row) -> VaultRecord:
    lineage = json.loads(row["lineage"])
    policy = json.loads(row["policy"])
    return VaultRecord(
        token=row["token"],
        value=json.loads(row["value"]),
        dtype=row["dtype"],
        semantic_type=row["semantic_type"],
        unit=row["unit"],
        session_id=row["session_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        ttl=datetime.fromisoformat(row["ttl"]),
        lineage=Lineage(
            op=lineage["op"],
            inputs=tuple(lineage["inputs"]),
            code_digest=lineage["code_digest"],
            tool=lineage["tool"],
            path=lineage["path"],
        ),
        policy=Policy(
            reveal_to_frontend=policy["reveal_to_frontend"],
            can_be_input_to_compute=policy["can_be_input_to_compute"],
        ),
    )
