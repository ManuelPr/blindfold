"""Vault record dataclasses and composition helpers.

Pure; no I/O, no imports from other blindfold modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Lineage:
    op: str  # "tool_result" | "blind_compute" | "literal"
    inputs: tuple[str, ...] = ()
    code_digest: str | None = None
    tool: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class Policy:
    reveal_to_frontend: bool = True
    can_be_input_to_compute: bool = True


@dataclass(frozen=True)
class Column:
    name: str
    semantic_type: str | None = None
    unit: str | None = None


@dataclass(frozen=True)
class TableSchema:
    """What the model is told about a hidden table: names, never values."""

    columns: tuple[Column, ...] = ()

    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def describe(self) -> str:
        parts = []
        for column in self.columns:
            meta = ", ".join(m for m in (column.semantic_type, column.unit) if m)
            parts.append(f"{column.name}{f' ({meta})' if meta else ''}")
        return ", ".join(parts)


@dataclass(frozen=True)
class VaultRecord:
    token: str
    value: Any
    dtype: str  # "string" | "number" | "boolean" | "object"
    semantic_type: str | None
    unit: str | None
    session_id: str
    created_at: datetime
    ttl: datetime
    lineage: Lineage
    policy: Policy
    #: Set only on collective tokens. Carried on the record rather than looked
    #: up in the config at query time, so a config edit cannot change what an
    #: already-minted token means.
    table: TableSchema | None = None


def compose_policy(inputs: list[Policy]) -> Policy:
    """AND-composition: any restrictive input wins."""
    if not inputs:
        return Policy()
    return Policy(
        reveal_to_frontend=all(p.reveal_to_frontend for p in inputs),
        can_be_input_to_compute=all(p.can_be_input_to_compute for p in inputs),
    )


def compose_ttl(inputs: list[VaultRecord]) -> datetime:
    """Shortest surviving TTL wins."""
    if not inputs:
        raise ValueError("compose_ttl requires at least one input record")
    return min(r.ttl for r in inputs)
