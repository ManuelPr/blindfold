"""JSON tokenizer: walks a payload against schema fields, mints tokens, and
returns a deep-copied tree with sensitive leaves replaced by token strings.

Supports the MVP JSONPath dialect: `$.key.subkey` and `$.list[*].key`. No
filters, no recursive descent.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.core.vault import MemoryTokenStore
from blindfold.ports.token_store import TokenStore


@dataclass(frozen=True)
class SchemaField:
    path: str
    semantic_type: str | None = None
    unit: str | None = None


def tokenize_result(
    payload: Any,
    tool_name: str,
    fields: list[SchemaField],
    store: TokenStore,
    session_id: str,
    ttl: datetime,
) -> Any:
    result = copy.deepcopy(payload)
    now = datetime.now(tz=timezone.utc)

    for field in fields:
        for pointer, value in _resolve_paths(result, field.path):
            token = MemoryTokenStore.mint_token()
            record = VaultRecord(
                token=token,
                value=copy.deepcopy(value),
                dtype=_infer_dtype(value),
                semantic_type=field.semantic_type,
                unit=field.unit,
                session_id=session_id,
                created_at=now,
                ttl=ttl,
                lineage=Lineage(op="tool_result", tool=tool_name, path=field.path),
                policy=Policy(),
            )
            store.put(record)
            _set_by_pointer(result, pointer, token)

    return result


def _infer_dtype(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def _resolve_paths(payload: Any, path: str) -> list[tuple[list[str | int], Any]]:
    if not path.startswith("$"):
        raise ValueError(f"path must start with '$': {path!r}")
    body = path[1:]
    if body.startswith("."):
        body = body[1:]
    segments = _tokenize_path(body)
    return list(_walk([], payload, segments))


def _tokenize_path(body: str) -> list[str | int]:
    """Turn 'items[*].name' into ['items', '*', 'name']."""
    parts: list[str | int] = []
    if not body:
        return parts
    for chunk in body.split("."):
        while "[" in chunk:
            head, rest = chunk.split("[", 1)
            if head:
                parts.append(head)
            idx_str, _, rest2 = rest.partition("]")
            parts.append("*" if idx_str == "*" else int(idx_str))
            chunk = rest2
        if chunk:
            parts.append(chunk)
    return parts


def _walk(prefix, node, segments):
    if not segments:
        yield (list(prefix), node)
        return
    head, *rest = segments
    if head == "*":
        if not isinstance(node, list):
            return
        for i, item in enumerate(node):
            yield from _walk([*prefix, i], item, rest)
    elif isinstance(head, int):
        if isinstance(node, list) and 0 <= head < len(node):
            yield from _walk([*prefix, head], node[head], rest)
    else:
        if isinstance(node, dict) and head in node:
            yield from _walk([*prefix, head], node[head], rest)


def _set_by_pointer(tree: Any, pointer: list[str | int], value: Any) -> None:
    parent = tree
    for step in pointer[:-1]:
        parent = parent[step]
    parent[pointer[-1]] = value
