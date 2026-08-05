"""`blindfold_table` — query a collective token without seeing its rows.

The counterpart to `blindfold_compute`, and deliberately weaker: the model
submits a list of operations rather than code. That restriction is the feature.
Arbitrary Python lets a model write something whose *success* depends on a
hidden value and read one bit per call; a fixed operation set cannot express
that, so this path has no sandbox and no oracle.

A result that is still a list of rows comes back as another table token, with
the columns that survived the query — so a query can be built up in steps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from blindfold.core.lineage import (
    Lineage,
    TableSchema,
    VaultRecord,
    compose_policy,
    compose_ttl,
)
from blindfold.core.table import COMPARISONS, ROW_OPS, TERMINAL_OPS, run_query
from blindfold.ports.policy import DetokenizeContext, DetokenizePolicy
from blindfold.ports.token_store import TokenStore

BLINDFOLD_TABLE_TOOL_NAME = "blindfold_table"

_TOOL_DESCRIPTION = (
    "Query a hidden table behind a ⟦tok_XXXXXXXX⟧ placeholder without seeing its rows. "
    "Pass the table's placeholder and a list of operations applied in order.\n"
    "Row operations: "
    '{"op":"filter","column":C,"cmp":"==|!=|<|<=|>|>=|contains","value":V}, '
    '{"op":"sort_by","column":C,"desc":true|false}, '
    '{"op":"limit","n":N}, '
    '{"op":"select","columns":[C,...]}.\n'
    "Value operations, which must come last: "
    '{"op":"sum|mean|min|max","column":C}, {"op":"count"}.\n'
    "Columns are the ones named in the tool description that produced the table. "
    "The result is a NEW placeholder, never a value — a row result can be queried "
    "again with this tool."
)


def build_tool_definition() -> dict:
    return {
        "name": BLINDFOLD_TABLE_TOOL_NAME,
        "description": _TOOL_DESCRIPTION,
        "inputSchema": {
            "type": "object",
            "required": ["table", "ops"],
            "properties": {
                "table": {
                    "type": "string",
                    "description": "The ⟦tok_…⟧ placeholder standing for the table.",
                },
                "ops": {
                    "type": "array",
                    "description": "Operations applied in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": list(ROW_OPS + TERMINAL_OPS)},
                            "column": {"type": "string"},
                            "columns": {"type": "array", "items": {"type": "string"}},
                            "cmp": {"type": "string", "enum": list(COMPARISONS)},
                            "value": {},
                            "n": {"type": "integer"},
                            "desc": {"type": "boolean"},
                        },
                        "required": ["op"],
                    },
                },
            },
        },
    }


def _infer_dtype(value: Any) -> str:
    if isinstance(value, list):
        return "table"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def _surviving_schema(schema: TableSchema, ops: list[dict], value: Any) -> TableSchema | None:
    """The columns a row result still has, so it can be queried again."""
    if not isinstance(value, list):
        return None
    kept = schema.columns
    for op_spec in ops:
        if isinstance(op_spec, dict) and op_spec.get("op") == "select":
            names = op_spec.get("columns") or []
            kept = tuple(c for c in schema.columns if c.name in names)
    return TableSchema(columns=kept)


def handle_blindfold_table(
    args: dict,
    *,
    store: TokenStore,
    policy: DetokenizePolicy,
    session_id: str,
    ttl_seconds: int,
) -> str:
    token = args.get("table")
    ops = args.get("ops")
    if not isinstance(token, str) or not isinstance(ops, list):
        raise ValueError("blindfold_table requires a string `table` and a list `ops`")

    record = store.get(token)
    if record is None:
        raise ValueError(f"unknown or expired table token: {token}")
    if record.table is None:
        raise ValueError(
            f"{token} is not a table token; use blindfold_compute for single values"
        )
    ctx = DetokenizeContext(session_id=session_id)
    if not policy.can_compute(ctx, record):
        raise ValueError(f"policy denied query on token: {token}")

    rows = record.value if isinstance(record.value, list) else []
    value = run_query(rows, record.table, ops)

    new_token = TokenStore.mint_token()
    now = datetime.now(tz=timezone.utc)
    store.put(
        VaultRecord(
            token=new_token,
            value=value,
            dtype=_infer_dtype(value),
            semantic_type=None,
            unit=None,
            session_id=session_id,
            created_at=now,
            ttl=compose_ttl([record]),
            lineage=Lineage(op="table_query", inputs=(token,)),
            policy=compose_policy([record.policy]),
            table=_surviving_schema(record.table, ops, value),
        )
    )
    return new_token
