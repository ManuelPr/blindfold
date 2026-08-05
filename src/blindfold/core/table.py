"""Collective tokens: one placeholder for a whole table, and a fixed set of
operations over it.

Individual tokens stop being useful somewhere around a few dozen rows. Not
because of size — a token is 14 characters and *replaces* the value it hides,
measured at +3% on a 500-row response — but because a few hundred unordered
opaque strings carry no structure. The model cannot sort them, cannot tell they
are comparable quantities, and to use ``blindfold_compute`` would have to
enumerate every one by hand in its code. In practice it processes a subset and
reports a confident wrong answer.

So a declared table becomes **one** token, and the model is told the column
names and what they mean. It then submits a query, not code.

**The query language is a fixed set of operations, executed here.** That is the
point of the whole exercise, and it is worth being explicit about why:

- ``blindfold_compute`` runs arbitrary Python, so the model can write code whose
  *success* depends on a hidden value (``1/0 if resolve(t) > 50000 else 'ok'``)
  and read one bit per call. No sandbox closes that, because nothing about such
  code is illegal.
- These operations cannot express that. Every well-formed query succeeds, and
  every result leaves as a new token. Comparisons between values of different
  types do not raise — they simply do not match — precisely so that no failure
  can carry information about the data.

That invariant is why this path needs no sandbox at all: no code the model wrote
is ever executed.
"""

from __future__ import annotations

from typing import Any

from blindfold.core.lineage import Column, TableSchema

__all__ = ["COMPARISONS", "Column", "ROW_OPS", "TERMINAL_OPS", "TableSchema", "run_query"]

TERMINAL_OPS = ("sum", "mean", "min", "max", "count")
ROW_OPS = ("filter", "sort_by", "limit", "select")
COMPARISONS = ("==", "!=", "<", "<=", ">", ">=", "contains")


def _sortable(value: Any) -> tuple[int, Any]:
    """A total order across mixed types, so sorting cannot raise.

    Python refuses to compare a str with an int. Raising here would make a
    failure depend on the hidden data, which is the one thing this module must
    never do.
    """
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, (int, float)):
        return (2, value)
    if isinstance(value, str):
        return (3, value)
    return (4, repr(value))


def _matches(left: Any, cmp: str, right: Any) -> bool:
    if cmp == "==":
        return left == right
    if cmp == "!=":
        return left != right
    if cmp == "contains":
        return isinstance(left, str) and isinstance(right, str) and right in left
    # Ordered comparisons only apply between values Python can order. A
    # mismatch is "does not match", never an error: an error would be a bit of
    # information about a value the model is not allowed to see.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    comparable = (int, float) if not isinstance(left, str) else (str,)
    if not isinstance(left, comparable) or not isinstance(right, comparable):
        return False
    if cmp == "<":
        return left < right
    if cmp == "<=":
        return left <= right
    if cmp == ">":
        return left > right
    return left >= right


def _cell(row: Any, name: str) -> Any:
    # A declared table can turn out to hold scalars. Reading a missing cell as
    # None keeps that a data shape rather than an error.
    return row.get(name) if isinstance(row, dict) else None


def _numbers(rows: list[dict], column: str) -> list[float]:
    out = []
    for row in rows:
        value = _cell(row, column)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out.append(value)
    return out


def run_query(rows: list[dict], schema: TableSchema, ops: list[dict]) -> Any:
    """Apply a query to hidden rows and return the result.

    Raises ``ValueError`` only for a malformed *query* — an unknown operation,
    an undeclared column, a bad comparison. Never for anything about the data.
    """
    if not isinstance(ops, list):
        raise ValueError("`ops` must be a list of operations")

    declared = schema.column_names()
    current: list[dict] = list(rows)

    for index, op_spec in enumerate(ops):
        if not isinstance(op_spec, dict):
            raise ValueError(f"operation {index} must be an object")
        op = op_spec.get("op")

        def column_of(key: str = "column") -> str:
            name = op_spec.get(key)
            if name not in declared:
                raise ValueError(
                    f"unknown column {name!r} in operation {index}; "
                    f"this table has: {', '.join(declared)}"
                )
            return name

        if op in TERMINAL_OPS:
            if index != len(ops) - 1:
                raise ValueError(f"{op!r} produces a value, so it must be the last operation")
            if op == "count":
                return len(current)
            values = _numbers(current, column_of())
            if not values:
                # Not an error: an empty selection is a legitimate answer, and
                # raising would say something about the data.
                return None
            if op == "sum":
                return sum(values)
            if op == "mean":
                return sum(values) / len(values)
            return min(values) if op == "min" else max(values)

        if op == "filter":
            cmp = op_spec.get("cmp", "==")
            if cmp not in COMPARISONS:
                raise ValueError(
                    f"unknown comparison {cmp!r}; use one of: {', '.join(COMPARISONS)}"
                )
            name = column_of()
            target = op_spec.get("value")
            current = [r for r in current if _matches(_cell(r, name), cmp, target)]
        elif op == "sort_by":
            name = column_of()
            current = sorted(current, key=lambda r: _sortable(_cell(r, name)), reverse=bool(op_spec.get("desc")))
        elif op == "limit":
            n = op_spec.get("n")
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                raise ValueError(f"`n` must be a non-negative integer, got {n!r}")
            current = current[:n]
        elif op == "select":
            names = op_spec.get("columns")
            if not isinstance(names, list) or not names:
                raise ValueError("`select` needs a non-empty `columns` list")
            for name in names:
                if name not in declared:
                    raise ValueError(
                        f"unknown column {name!r} in operation {index}; "
                        f"this table has: {', '.join(declared)}"
                    )
            current = [{k: _cell(r, k) for k in names} for r in current]
        else:
            raise ValueError(
                f"unknown operation {op!r}; use one of: "
                f"{', '.join(ROW_OPS + TERMINAL_OPS)}"
            )

    return current
