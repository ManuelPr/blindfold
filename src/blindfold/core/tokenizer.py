"""JSON tokenizer: walks a payload against schema fields, mints tokens, and
returns a deep-copied tree with sensitive leaves replaced by token strings.

Supports the MVP JSONPath dialect: `$.key.subkey` and `$.list[*].key`. No
filters, no recursive descent.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.ports.token_store import TokenStore


_DIALECT = (
    "Supported: static keys ($.a.b.c), list wildcards at any depth "
    "($.a[*].b[*].c), and integer indices ($.items[0].name)."
)


def validate_path(path: str) -> None:
    """Reject path syntax this dialect cannot honor, as early as possible.

    A path that cannot mean what its author intended is a hole, not a no-op:
    unsupported syntax used to be reinterpreted rather than refused, so
    ``$..salary`` quietly became ``$.salary`` — matching a top-level field and
    missing every nested one, while still producing tokens that made the config
    look like it worked.

    Non-matching is still fine and still silent: declaring a path a given
    response happens not to contain is the intended defensive style. What is
    refused here is a path that could never match what it says.
    """
    if not path.startswith("$"):
        raise ValueError(f"path must start with '$': {path!r}. {_DIALECT}")
    if ".." in path:
        raise ValueError(
            f"recursive descent is not supported: {path!r} would silently be read as "
            f"{'$.' + path.replace('..', '.').lstrip('$.')!r}. Declare each level. {_DIALECT}"
        )
    body = path[1:]
    if body.startswith("."):
        body = body[1:]
    if body.endswith("."):
        raise ValueError(f"path ends with '.': {path!r}. {_DIALECT}")

    # Subscripts are checked against the whole path before the parser splits on
    # '.', because a filter like [?(@.type == 'x')] contains a dot: splitting
    # first tears it in half and reports an unbalanced bracket, which sends the
    # author looking for the wrong mistake.
    if body.count("[") != body.count("]"):
        raise ValueError(f"unbalanced brackets in path: {path!r}. {_DIALECT}")
    for subscript in re.findall(r"\[([^\]]*)\]", body):
        if subscript == "*":
            continue
        try:
            int(subscript)
        except ValueError:
            raise ValueError(
                f"unsupported subscript '[{subscript}]' in {path!r}: no filters, "
                f"no slices, no quoted keys. {_DIALECT}"
            ) from None

    _tokenize_path(body)


@dataclass(frozen=True)
class SchemaField:
    path: str
    semantic_type: str | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        validate_path(self.path)


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
            token = TokenStore.mint_token()
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


def describe_schema(fields: list[SchemaField]) -> str | None:
    """Describe what a tool's protected fields *mean*, for the tool's description.

    The model otherwise has only the JSON key name to go on, which is worthless
    when the upstream API names things `f_42`. This belongs on the tool
    definition rather than on each result: it is the same text every time, and
    the model receives tool definitions once instead of once per call.

    Derived purely from config, so it never touches a value. Returns ``None``
    when the tool declares no sensitive fields.
    """
    if not fields:
        return None
    lines = []
    for field in fields:
        meta = ", ".join(m for m in (field.semantic_type, field.unit) if m)
        lines.append(f"  {field.path}{f' — {meta}' if meta else ''}")
    return (
        "Blindfold: values at these paths come back as ⟦tok_XXXXXXXX⟧ placeholders, "
        "not real values. You cannot read them — pass a placeholder to "
        "blindfold_compute to operate on it.\n" + "\n".join(lines)
    )


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
    """Turn 'items[*].name' into ['items', '*', 'name'].

    Strict about subscripts: anything it cannot represent raises rather than
    being reinterpreted, so `validate_path` can lean on it instead of
    reimplementing the parse.
    """
    parts: list[str | int] = []
    if not body:
        return parts
    for chunk in body.split("."):
        while "[" in chunk:
            head, rest = chunk.split("[", 1)
            if head:
                parts.append(head)
            idx_str, close, rest2 = rest.partition("]")
            if not close:
                raise ValueError(f"unbalanced '[' in path segment {chunk!r}. {_DIALECT}")
            if idx_str == "*":
                parts.append("*")
            else:
                try:
                    parts.append(int(idx_str))
                except ValueError:
                    raise ValueError(
                        f"unsupported subscript '[{idx_str}]': no filters, no slices, "
                        f"no quoted keys. {_DIALECT}"
                    ) from None
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
