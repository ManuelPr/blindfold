# Blindfold MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Blindfold MVP — a Python stdio MCP proxy that tokenizes tool-result fields declared in a YAML schema, exposes a `blindfold_compute` MCP tool for blind compute in a subprocess sandbox, and rehydrates tokens either via a library helper (in-process) or a `blindfold/rehydrate` JSON-RPC method (over the wire).

**Architecture:** A small Python package with strict port/adapter seams. Pure `core` (vault / tokenizer / rehydrator / policy / lineage) knows nothing about MCP. A thin `proxy` layer speaks stdio MCP on both sides and calls into `core`. Three ABC ports (`TokenStore`, `DetokenizePolicy`, `ComputeSandbox`) each have one MVP implementation, drawn as seams so post-MVP work is additive.

**Tech Stack:** Python 3.11+, `uv` (env/deps), `pydantic` v2 (config), `mcp` (official Python MCP SDK), `pyyaml`, `anthropic` (demo only). Tests: `pytest`, `pytest-asyncio`, `freezegun`.

## Global Constraints

- **Python:** `>= 3.11` (match/case, PEP 604 unions).
- **`src/`-layout:** all package code under `src/blindfold/`; tests must import the installed package, never source paths.
- **Package manager:** `uv` (single source of truth in `pyproject.toml`).
- **Token format:** `⟦tok_XXXXXXXX⟧` — leading/trailing WHITE SQUARE BRACKETs (U+27E6 / U+27E7), `tok_` literal, 8 lowercase hex characters. Regex: `⟦tok_[0-9a-f]{8}⟧`.
- **Reference spec:** [`docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md`](../specs/2026-07-15-blindfold-mvp-design.md) — every task must align with it.
- **Reference README:** [`README.md`](../../../README.md) — product vision.
- **Line endings:** files are LF; Git will convert to CRLF on Windows checkout — do not fight this.
- **Commits:** one commit per task at the end of the task, imperative subject line, `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` footer.
- **YAGNI:** no fields, methods, adapters, or config keys beyond what a task/spec requires.
- **All commands are run from the project root** `C:\Users\M.PERNIGOTTO\Desktop\blindfold` unless stated otherwise. On Git Bash use `cd /c/Users/M.PERNIGOTTO/Desktop/blindfold`.

---

## File map

Files created across all tasks (with the task that creates each):

| Path | Task |
|---|---|
| `pyproject.toml` | 1 |
| `.python-version` | 1 |
| `src/blindfold/__init__.py` | 1 (empty), 6 (adds re-exports) |
| `src/blindfold/py.typed` | 1 |
| `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/e2e/__init__.py` | 1 |
| `tests/unit/test_smoke.py` | 1 (removed in 2) |
| `src/blindfold/core/__init__.py`, `src/blindfold/core/lineage.py` | 2 |
| `src/blindfold/ports/__init__.py`, `src/blindfold/ports/token_store.py`, `src/blindfold/ports/policy.py`, `src/blindfold/ports/sandbox.py` | 2 |
| `tests/unit/test_lineage.py` | 2 |
| `src/blindfold/core/vault.py`, `tests/unit/test_vault.py` | 3 |
| `src/blindfold/core/policy.py`, `tests/unit/test_policy.py` | 4 |
| `src/blindfold/core/tokenizer.py`, `tests/unit/test_tokenizer.py` | 5 |
| `src/blindfold/core/rehydrator.py`, `tests/unit/test_rehydrator.py` | 6 |
| `src/blindfold/config.py`, `blindfold.example.yaml`, `tests/unit/test_config.py` | 7 |
| `src/blindfold/sandbox/__init__.py`, `src/blindfold/sandbox/subprocess_.py`, `tests/unit/test_sandbox.py` | 8 |
| `src/blindfold/tools/__init__.py`, `src/blindfold/tools/blindfold_compute.py`, `tests/unit/test_blindfold_compute.py` | 9 |
| `examples/__init__.py`, `examples/fake_hr_mcp/__init__.py`, `examples/fake_hr_mcp/__main__.py` | 10 |
| `src/blindfold/proxy.py`, `tests/integration/test_proxy_forwarding.py` | 11 |
| `src/blindfold/cli.py` | 12 |
| `examples/demo_chat.py` | 13 |
| `tests/e2e/recorded_transcript.json`, `tests/e2e/test_demo_flow.py` | 14 |
| `README.md` (edit) | 15 |

---

### Task 1: Project bootstrap

Set up the `uv`-managed Python package, `src/` layout, empty test dirs, and one smoke test that proves `pytest` can find and run tests against the installed package.

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `src/blindfold/__init__.py` (empty)
- Create: `src/blindfold/py.typed` (empty marker file)
- Create: `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/e2e/__init__.py` (all empty)
- Create: `tests/unit/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: an importable `blindfold` package and a working `pytest` invocation.

- [ ] **Step 1: Write `.python-version`**

```
3.11
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "blindfold"
version = "0.0.1"
description = "Privacy proxy for LLM tool calls — tokenize tool results, blind-compute, rehydrate."
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = [
    "mcp>=1.0",
    "pydantic>=2.6",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
demo = [
    "anthropic>=0.30",
]

[project.scripts]
blindfold = "blindfold.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/blindfold"]

[tool.uv]
dev-dependencies = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "freezegun>=1.4",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-ra -q"
```

- [ ] **Step 3: Create empty package + test dirs**

Create these empty files (Windows/PowerShell users: use `ni <path> -ItemType File` from PowerShell or `touch` under Git Bash):

- `src/blindfold/__init__.py`
- `src/blindfold/py.typed`
- `tests/__init__.py`
- `tests/unit/__init__.py`
- `tests/integration/__init__.py`
- `tests/e2e/__init__.py`

- [ ] **Step 4: Write `tests/unit/test_smoke.py`**

```python
"""Smoke test — proves pytest finds the installed blindfold package."""

def test_import_blindfold():
    import blindfold  # noqa: F401
```

- [ ] **Step 5: Sync uv env and run the smoke test**

```bash
uv sync
uv run pytest tests/unit/test_smoke.py -v
```

Expected output ends with `1 passed`. Any `ModuleNotFoundError: No module named 'blindfold'` means the `src/`-layout is not wired — recheck `[tool.hatch.build.targets.wheel] packages`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .python-version src tests
git commit -m "$(cat <<'EOF'
chore: bootstrap uv-managed Python package

Sets up src/-layout, pytest, and the initial smoke test.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Data model + Ports

Define the `VaultRecord`/`Lineage`/`Policy` dataclasses, the three ABC ports, and the composition helpers (`compose_policy`, `compose_ttl`). These are pure Python with no dependencies on any other blindfold module — everything downstream imports them.

**Files:**
- Create: `src/blindfold/core/__init__.py` (empty)
- Create: `src/blindfold/core/lineage.py`
- Create: `src/blindfold/ports/__init__.py` (empty)
- Create: `src/blindfold/ports/token_store.py`
- Create: `src/blindfold/ports/policy.py`
- Create: `src/blindfold/ports/sandbox.py`
- Create: `tests/unit/test_lineage.py`
- Delete: `tests/unit/test_smoke.py`

**Interfaces:**
- Consumes: nothing (foundational).
- Produces:
  - `Lineage`, `Policy`, `VaultRecord` (frozen dataclasses, exact fields per spec §4).
  - `compose_policy(inputs: list[Policy]) -> Policy` — `AND` across `reveal_to_frontend` and `can_be_input_to_compute`.
  - `compose_ttl(inputs: list[VaultRecord]) -> datetime` — `min(r.ttl for r in inputs)`.
  - `class TokenStore(ABC)` with methods:
    - `put(record: VaultRecord) -> None`
    - `get(token: str) -> VaultRecord | None`
    - `resolve(token: str) -> Any | None`
    - `find_by_session(session_id: str) -> list[VaultRecord]`
    - `invalidate_cascade(token: str) -> int`  *(returns count of records removed)*
    - `purge_expired(now: datetime | None = None) -> int`
  - `class DetokenizeContext` (frozen dataclass with `session_id: str`) and `class DetokenizePolicy(ABC)` with:
    - `can_reveal(context: DetokenizeContext, record: VaultRecord) -> bool`
    - `can_compute(context: DetokenizeContext, record: VaultRecord) -> bool`
  - `class ComputeSandbox(ABC)` with:
    - `run(code: str, inputs: dict[str, Any], timeout_s: float) -> Any`  *(raises `SandboxError` on timeout/parse/exec problems)*
    - `class SandboxError(Exception)` co-located.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_lineage.py`:
```python
from datetime import datetime, timedelta, timezone

from blindfold.core.lineage import (
    Lineage,
    Policy,
    VaultRecord,
    compose_policy,
    compose_ttl,
)


def _record(token: str, *, reveal: bool = True, compute: bool = True, ttl_min: int = 60) -> VaultRecord:
    return VaultRecord(
        token=token,
        value="v",
        dtype="string",
        semantic_type=None,
        unit=None,
        session_id="s",
        created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        ttl=datetime(2026, 7, 15, tzinfo=timezone.utc) + timedelta(minutes=ttl_min),
        lineage=Lineage(op="literal"),
        policy=Policy(reveal_to_frontend=reveal, can_be_input_to_compute=compute),
    )


def test_compose_policy_all_true():
    p = compose_policy([Policy(), Policy()])
    assert p.reveal_to_frontend is True
    assert p.can_be_input_to_compute is True


def test_compose_policy_any_false_reveals_false():
    p = compose_policy(
        [Policy(reveal_to_frontend=True), Policy(reveal_to_frontend=False)]
    )
    assert p.reveal_to_frontend is False


def test_compose_policy_any_false_compute_false():
    p = compose_policy(
        [Policy(can_be_input_to_compute=True), Policy(can_be_input_to_compute=False)]
    )
    assert p.can_be_input_to_compute is False


def test_compose_policy_empty_defaults_to_permissive():
    p = compose_policy([])
    assert p == Policy()


def test_compose_ttl_takes_min():
    a = _record("tok_a", ttl_min=30)
    b = _record("tok_b", ttl_min=90)
    assert compose_ttl([a, b]) == a.ttl


def test_vault_record_is_frozen():
    r = _record("tok_a")
    try:
        r.value = "x"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("expected VaultRecord to be frozen")
```

- [ ] **Step 2: Delete the smoke test and run the new test to see it fail**

```bash
rm tests/unit/test_smoke.py
uv run pytest tests/unit/test_lineage.py -v
```

Expected: import errors / test failures (module `blindfold.core.lineage` does not exist).

- [ ] **Step 3: Implement `src/blindfold/core/lineage.py`**

```python
"""Vault record dataclasses and composition helpers.

Pure; no I/O, no imports from other blindfold modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
```

- [ ] **Step 4: Implement the three ports**

`src/blindfold/ports/token_store.py`:
```python
"""TokenStore port — abstract vault interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from blindfold.core.lineage import VaultRecord


class TokenStore(ABC):
    @abstractmethod
    def put(self, record: VaultRecord) -> None: ...

    @abstractmethod
    def get(self, token: str) -> VaultRecord | None: ...

    @abstractmethod
    def resolve(self, token: str) -> Any | None: ...

    @abstractmethod
    def find_by_session(self, session_id: str) -> list[VaultRecord]: ...

    @abstractmethod
    def invalidate_cascade(self, token: str) -> int: ...

    @abstractmethod
    def purge_expired(self, now: datetime | None = None) -> int: ...
```

`src/blindfold/ports/policy.py`:
```python
"""DetokenizePolicy port — authorization for reveal/compute."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from blindfold.core.lineage import VaultRecord


@dataclass(frozen=True)
class DetokenizeContext:
    session_id: str


class DetokenizePolicy(ABC):
    @abstractmethod
    def can_reveal(self, context: DetokenizeContext, record: VaultRecord) -> bool: ...

    @abstractmethod
    def can_compute(self, context: DetokenizeContext, record: VaultRecord) -> bool: ...
```

`src/blindfold/ports/sandbox.py`:
```python
"""ComputeSandbox port — run untrusted code with resolved inputs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SandboxError(Exception):
    """Raised by ComputeSandbox implementations on timeout, parse, or exec failure."""


class ComputeSandbox(ABC):
    @abstractmethod
    def run(self, code: str, inputs: dict[str, Any], timeout_s: float) -> Any: ...
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
uv run pytest tests/unit/test_lineage.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/blindfold/core src/blindfold/ports tests/unit
git commit -m "$(cat <<'EOF'
feat(core): data model and ABC ports

Adds Lineage/Policy/VaultRecord dataclasses, compose_policy and
compose_ttl helpers, and TokenStore/DetokenizePolicy/ComputeSandbox
ports. Removes the bootstrap smoke test now that real tests exist.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: MemoryTokenStore

The one-and-only-at-MVP `TokenStore` implementation. Backed by a plain `dict`. Supports TTL expiry (checked lazily on `get`/`resolve` and eagerly on `purge_expired`), session lookup, and cascading invalidation (remove a token and all descendants whose `lineage.inputs` transitively include it).

**Files:**
- Create: `src/blindfold/core/vault.py`
- Create: `tests/unit/test_vault.py`

**Interfaces:**
- Consumes: `TokenStore` (Task 2), `VaultRecord`, `Lineage`, `Policy` (Task 2).
- Produces: `class MemoryTokenStore(TokenStore)` — concrete implementation. `MemoryTokenStore.mint_token() -> str` classmethod that returns `f"⟦tok_{secrets.token_hex(4)}⟧"` (used later by tokenizer/blind-compute handler).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_vault.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.core.vault import MemoryTokenStore

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _rec(token: str, *, ttl_min: int = 60, inputs: tuple[str, ...] = (), op: str = "literal") -> VaultRecord:
    return VaultRecord(
        token=token,
        value=f"val_of_{token}",
        dtype="string",
        semantic_type=None,
        unit=None,
        session_id="s",
        created_at=NOW,
        ttl=NOW + timedelta(minutes=ttl_min),
        lineage=Lineage(op=op, inputs=inputs),
        policy=Policy(),
    )


def test_mint_token_shape():
    tok = MemoryTokenStore.mint_token()
    assert tok.startswith("⟦tok_")
    assert tok.endswith("⟧")
    hex_part = tok.removeprefix("⟦tok_").removesuffix("⟧")
    assert len(hex_part) == 8
    int(hex_part, 16)  # must parse as hex


def test_mint_token_is_unique_across_calls():
    tokens = {MemoryTokenStore.mint_token() for _ in range(1000)}
    assert len(tokens) == 1000


@freeze_time(NOW)
def test_put_then_get_and_resolve():
    store = MemoryTokenStore()
    r = _rec("⟦tok_00000001⟧")
    store.put(r)
    assert store.get("⟦tok_00000001⟧") is r
    assert store.resolve("⟦tok_00000001⟧") == "val_of_⟦tok_00000001⟧"


def test_get_unknown_returns_none():
    store = MemoryTokenStore()
    assert store.get("⟦tok_deadbeef⟧") is None
    assert store.resolve("⟦tok_deadbeef⟧") is None


@freeze_time(NOW)
def test_find_by_session_isolates():
    store = MemoryTokenStore()
    a = _rec("⟦tok_00000001⟧")
    b = VaultRecord(**{**a.__dict__, "token": "⟦tok_00000002⟧", "session_id": "s2"})
    store.put(a)
    store.put(b)
    assert store.find_by_session("s") == [a]
    assert store.find_by_session("s2") == [b]


def test_ttl_expiry_hides_record_from_get():
    with freeze_time(NOW) as frozen:
        store = MemoryTokenStore()
        store.put(_rec("⟦tok_00000001⟧", ttl_min=1))
        assert store.get("⟦tok_00000001⟧") is not None
        frozen.tick(delta=timedelta(minutes=2))
        assert store.get("⟦tok_00000001⟧") is None
        assert store.resolve("⟦tok_00000001⟧") is None


def test_purge_expired_returns_count():
    with freeze_time(NOW) as frozen:
        store = MemoryTokenStore()
        store.put(_rec("⟦tok_00000001⟧", ttl_min=1))
        store.put(_rec("⟦tok_00000002⟧", ttl_min=100))
        frozen.tick(delta=timedelta(minutes=2))
        assert store.purge_expired() == 1
        assert store.get("⟦tok_00000001⟧") is None
        assert store.get("⟦tok_00000002⟧") is not None


@freeze_time(NOW)
def test_invalidate_cascade_removes_descendants():
    store = MemoryTokenStore()
    a = _rec("⟦tok_00000001⟧")
    b = _rec("⟦tok_00000002⟧")
    c = _rec("⟦tok_00000003⟧", inputs=("⟦tok_00000001⟧",), op="blind_compute")
    d = _rec("⟦tok_00000004⟧", inputs=("⟦tok_00000003⟧",), op="blind_compute")
    e = _rec("⟦tok_00000005⟧", inputs=("⟦tok_00000002⟧",), op="blind_compute")  # unrelated
    for r in (a, b, c, d, e):
        store.put(r)

    removed = store.invalidate_cascade("⟦tok_00000001⟧")
    assert removed == 3  # a + c + d
    assert store.get("⟦tok_00000001⟧") is None
    assert store.get("⟦tok_00000003⟧") is None
    assert store.get("⟦tok_00000004⟧") is None
    assert store.get("⟦tok_00000002⟧") is not None
    assert store.get("⟦tok_00000005⟧") is not None


def test_invalidate_cascade_unknown_token_returns_zero():
    store = MemoryTokenStore()
    assert store.invalidate_cascade("⟦tok_deadbeef⟧") == 0
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/unit/test_vault.py -v
```

Expected: `ModuleNotFoundError: No module named 'blindfold.core.vault'`.

- [ ] **Step 3: Implement `src/blindfold/core/vault.py`**

```python
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
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/unit/test_vault.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/blindfold/core/vault.py tests/unit/test_vault.py
git commit -m "$(cat <<'EOF'
feat(core): MemoryTokenStore with TTL and cascading invalidation

In-memory vault with lazy TTL expiry on get/resolve, eager
purge_expired, session-scoped listing, and transitive descendant
removal via invalidate_cascade. Includes mint_token() helper used by
downstream tokenizers.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: SessionBoundPolicy

The MVP `DetokenizePolicy`: `can_reveal` returns `record.policy.reveal_to_frontend AND context.session_id == record.session_id`; `can_compute` similar but gated on `can_be_input_to_compute`.

**Files:**
- Create: `src/blindfold/core/policy.py`
- Create: `tests/unit/test_policy.py`

**Interfaces:**
- Consumes: `DetokenizePolicy`, `DetokenizeContext` (Task 2), `VaultRecord`, `Policy` (Task 2).
- Produces: `class SessionBoundPolicy(DetokenizePolicy)` — stateless, constructed with no args. Both methods return `bool`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_policy.py`:
```python
from datetime import datetime, timedelta, timezone

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.core.policy import SessionBoundPolicy
from blindfold.ports.policy import DetokenizeContext

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _rec(*, session: str = "s", reveal: bool = True, compute: bool = True) -> VaultRecord:
    return VaultRecord(
        token="⟦tok_00000001⟧",
        value="v",
        dtype="string",
        semantic_type=None,
        unit=None,
        session_id=session,
        created_at=NOW,
        ttl=NOW + timedelta(hours=1),
        lineage=Lineage(op="literal"),
        policy=Policy(reveal_to_frontend=reveal, can_be_input_to_compute=compute),
    )


def test_can_reveal_matching_session_and_permissive_policy():
    p = SessionBoundPolicy()
    assert p.can_reveal(DetokenizeContext(session_id="s"), _rec()) is True


def test_can_reveal_denied_across_sessions():
    p = SessionBoundPolicy()
    assert p.can_reveal(DetokenizeContext(session_id="other"), _rec(session="s")) is False


def test_can_reveal_denied_by_record_policy():
    p = SessionBoundPolicy()
    assert p.can_reveal(DetokenizeContext(session_id="s"), _rec(reveal=False)) is False


def test_can_compute_matching_session_and_permissive_policy():
    p = SessionBoundPolicy()
    assert p.can_compute(DetokenizeContext(session_id="s"), _rec()) is True


def test_can_compute_denied_across_sessions():
    p = SessionBoundPolicy()
    assert p.can_compute(DetokenizeContext(session_id="other"), _rec(session="s")) is False


def test_can_compute_denied_by_record_policy():
    p = SessionBoundPolicy()
    assert p.can_compute(DetokenizeContext(session_id="s"), _rec(compute=False)) is False
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/unit/test_policy.py -v
```

Expected: `ModuleNotFoundError: No module named 'blindfold.core.policy'`.

- [ ] **Step 3: Implement `src/blindfold/core/policy.py`**

```python
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
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/unit/test_policy.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/blindfold/core/policy.py tests/unit/test_policy.py
git commit -m "$(cat <<'EOF'
feat(core): SessionBoundPolicy — session-scoped reveal/compute checks

Tokens can only be revealed or computed on from the session that
minted them and only when the record's own policy allows it.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Tokenizer

Walks a JSON tool result against a `SchemaField` list, mints tokens for matched paths, and swaps them into a deep-copied tree. Ships a tiny JSONPath resolver supporting only the MVP dialect: static keys (`$.a.b`) and one-level `[*]` list wildcards (`$.items[*].name`).

**Files:**
- Create: `src/blindfold/core/tokenizer.py`
- Create: `tests/unit/test_tokenizer.py`

**Interfaces:**
- Consumes: `MemoryTokenStore.mint_token` (Task 3), `TokenStore` (Task 2), `VaultRecord`/`Lineage`/`Policy` (Task 2).
- Produces:
  - `@dataclass(frozen=True) class SchemaField: path: str; semantic_type: str | None = None; unit: str | None = None`
  - `def tokenize_result(payload: Any, tool_name: str, fields: list[SchemaField], store: TokenStore, session_id: str, ttl: datetime) -> Any`
    - Returns a deep-copied JSON tree with matched leaf values replaced by token strings.
    - For every match, mints one `VaultRecord` and calls `store.put`.
    - `dtype` inferred: `str` → `"string"`; `bool` → `"boolean"`; `int`/`float` → `"number"`; anything else → `"object"`.
    - If no field matches, returns the tree unchanged (still deep-copied).
  - `def _resolve_paths(payload: Any, path: str) -> list[tuple[list[str | int], Any]]` — helper returning `(path_pointer, value)` pairs; internal but importable for tests.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_tokenizer.py`:
```python
from datetime import datetime, timedelta, timezone

from blindfold.core.tokenizer import SchemaField, tokenize_result, _resolve_paths
from blindfold.core.vault import MemoryTokenStore

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
TTL = NOW + timedelta(hours=1)
TOKEN_RE = r"⟦tok_[0-9a-f]{8}⟧"


def test_resolve_static_path():
    payload = {"salary": 50000}
    assert _resolve_paths(payload, "$.salary") == [(["salary"], 50000)]


def test_resolve_nested_path():
    payload = {"person": {"salary": 50000}}
    assert _resolve_paths(payload, "$.person.salary") == [(["person", "salary"], 50000)]


def test_resolve_wildcard_over_list():
    payload = {"items": [{"name": "a"}, {"name": "b"}]}
    result = _resolve_paths(payload, "$.items[*].name")
    assert result == [
        (["items", 0, "name"], "a"),
        (["items", 1, "name"], "b"),
    ]


def test_resolve_missing_path_yields_empty():
    payload = {"salary": 50000}
    assert _resolve_paths(payload, "$.does.not.exist") == []


def test_tokenize_static_field_replaces_value_and_stores_record():
    import re

    store = MemoryTokenStore()
    payload = {"name": "Alice", "salary": 50000}
    fields = [SchemaField(path="$.salary", semantic_type="salary", unit="EUR/year")]

    result = tokenize_result(payload, "hr.get_salary", fields, store, "s", TTL)

    assert result["name"] == "Alice"
    assert re.fullmatch(TOKEN_RE, result["salary"])
    records = store.find_by_session("s")
    assert len(records) == 1
    rec = records[0]
    assert rec.value == 50000
    assert rec.dtype == "number"
    assert rec.semantic_type == "salary"
    assert rec.unit == "EUR/year"
    assert rec.lineage.op == "tool_result"
    assert rec.lineage.tool == "hr.get_salary"
    assert rec.lineage.path == "$.salary"


def test_tokenize_wildcard_mints_one_per_match():
    store = MemoryTokenStore()
    payload = {"people": [{"salary": 10}, {"salary": 20}, {"salary": 30}]}
    fields = [SchemaField(path="$.people[*].salary", semantic_type="salary")]

    result = tokenize_result(payload, "hr.list", fields, store, "s", TTL)

    assert all(isinstance(p["salary"], str) for p in result["people"])
    assert {r.value for r in store.find_by_session("s")} == {10, 20, 30}


def test_tokenize_missing_paths_no_op():
    store = MemoryTokenStore()
    payload = {"name": "Alice"}
    fields = [SchemaField(path="$.salary")]

    result = tokenize_result(payload, "hr.get_salary", fields, store, "s", TTL)

    assert result == payload
    assert store.find_by_session("s") == []


def test_tokenize_non_scalar_uses_object_dtype():
    store = MemoryTokenStore()
    payload = {"address": {"street": "1 rd", "city": "X"}}
    fields = [SchemaField(path="$.address")]

    tokenize_result(payload, "hr.get_address", fields, store, "s", TTL)

    rec = store.find_by_session("s")[0]
    assert rec.dtype == "object"
    assert rec.value == {"street": "1 rd", "city": "X"}


def test_tokenize_boolean_dtype():
    store = MemoryTokenStore()
    payload = {"is_manager": True}
    fields = [SchemaField(path="$.is_manager")]
    tokenize_result(payload, "hr.get_role", fields, store, "s", TTL)
    assert store.find_by_session("s")[0].dtype == "boolean"


def test_tokenize_deep_copies_payload():
    store = MemoryTokenStore()
    payload = {"salary": 50000, "nested": {"k": "v"}}
    fields = [SchemaField(path="$.salary")]

    result = tokenize_result(payload, "hr.get_salary", fields, store, "s", TTL)

    result["nested"]["k"] = "MUTATED"
    assert payload["nested"]["k"] == "v"


def test_tokenize_no_fields_still_returns_copy():
    store = MemoryTokenStore()
    payload = {"name": "Alice"}
    result = tokenize_result(payload, "hr.get_name", [], store, "s", TTL)
    assert result == payload
    assert result is not payload
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/unit/test_tokenizer.py -v
```

Expected: `ModuleNotFoundError: No module named 'blindfold.core.tokenizer'`.

- [ ] **Step 3: Implement `src/blindfold/core/tokenizer.py`**

```python
"""JSON tokenizer: walks a payload against schema fields, mints tokens, and
returns a deep-copied tree with sensitive leaves replaced by token strings.

Supports the MVP JSONPath dialect: `$.key.subkey` and `$.list[*].key`. No
filters, no recursive descent.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
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
    from datetime import timezone

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
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/unit/test_tokenizer.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/blindfold/core/tokenizer.py tests/unit/test_tokenizer.py
git commit -m "$(cat <<'EOF'
feat(core): schema-driven tokenizer

Walks a JSON payload against a list of SchemaField paths, mints a
VaultRecord per matched value, and returns a deep-copied tree with
leaves replaced by token strings. Supports static and one-level [*]
paths — the MVP JSONPath dialect.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Rehydrator

Scans a string for tokens, resolves each via the store + policy, and substitutes. Re-exported from `blindfold/__init__.py` so consumers can `from blindfold import rehydrate`.

**Files:**
- Create: `src/blindfold/core/rehydrator.py`
- Modify: `src/blindfold/__init__.py` (add re-export)
- Create: `tests/unit/test_rehydrator.py`

**Interfaces:**
- Consumes: `TokenStore` (Task 2), `DetokenizePolicy`, `DetokenizeContext` (Task 2).
- Produces:
  - `TOKEN_PATTERN: re.Pattern[str]` — `re.compile(r"⟦tok_[0-9a-f]{8}⟧")` (importable).
  - `def rehydrate(text: str, session_id: str, store: TokenStore, policy: DetokenizePolicy) -> str`
    - Replaces each `⟦tok_XXXXXXXX⟧` with `str(record.value)` if `policy.can_reveal(...)` returns `True`.
    - Missing record → `[unknown token]`. Present-but-denied → `[redacted]`.
  - Exposed as `blindfold.rehydrate` via `__init__.py`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_rehydrator.py`:
```python
from datetime import datetime, timedelta, timezone

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.rehydrator import TOKEN_PATTERN, rehydrate
from blindfold.core.vault import MemoryTokenStore

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _put(store: MemoryTokenStore, token: str, value, *, session="s", reveal: bool = True) -> None:
    store.put(
        VaultRecord(
            token=token,
            value=value,
            dtype="string" if isinstance(value, str) else "number",
            semantic_type=None,
            unit=None,
            session_id=session,
            created_at=NOW,
            ttl=NOW + timedelta(hours=1),
            lineage=Lineage(op="literal"),
            policy=Policy(reveal_to_frontend=reveal),
        )
    )


def test_regex_matches_valid_and_rejects_invalid():
    assert TOKEN_PATTERN.fullmatch("⟦tok_deadbeef⟧")
    assert TOKEN_PATTERN.fullmatch("⟦tok_00000000⟧")
    assert not TOKEN_PATTERN.fullmatch("⟦tok_TOOSHORT⟧")
    assert not TOKEN_PATTERN.fullmatch("⟦tok_gggggggg⟧")  # 'g' not hex
    assert not TOKEN_PATTERN.fullmatch("(tok_deadbeef)")


def test_rehydrate_happy_path():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", "Alice")
    out = rehydrate(
        "The winner is ⟦tok_00000001⟧.",
        "s",
        store,
        SessionBoundPolicy(),
    )
    assert out == "The winner is Alice."


def test_rehydrate_multiple_tokens_in_one_string():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", "Alice")
    _put(store, "⟦tok_00000002⟧", 42)
    out = rehydrate(
        "⟦tok_00000001⟧ is number ⟦tok_00000002⟧.",
        "s",
        store,
        SessionBoundPolicy(),
    )
    assert out == "Alice is number 42."


def test_rehydrate_unknown_token_flagged():
    store = MemoryTokenStore()
    out = rehydrate("Where is ⟦tok_deadbeef⟧?", "s", store, SessionBoundPolicy())
    assert out == "Where is [unknown token]?"


def test_rehydrate_wrong_session_flagged_as_redacted():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", "secret", session="other")
    out = rehydrate("value: ⟦tok_00000001⟧", "s", store, SessionBoundPolicy())
    assert out == "value: [redacted]"


def test_rehydrate_reveal_denied_flagged_as_redacted():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", "secret", reveal=False)
    out = rehydrate("value: ⟦tok_00000001⟧", "s", store, SessionBoundPolicy())
    assert out == "value: [redacted]"


def test_rehydrate_no_tokens_passthrough():
    store = MemoryTokenStore()
    out = rehydrate("plain string, no tokens", "s", store, SessionBoundPolicy())
    assert out == "plain string, no tokens"


def test_rehydrate_does_not_match_similar_but_wrong_syntax():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", "Alice")
    # Wrong brackets should not trigger substitution.
    out = rehydrate("(tok_00000001) and [tok_00000001]", "s", store, SessionBoundPolicy())
    assert out == "(tok_00000001) and [tok_00000001]"


def test_rehydrate_public_from_top_level_module():
    from blindfold import rehydrate as top_level_rehydrate

    assert top_level_rehydrate is rehydrate
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/unit/test_rehydrator.py -v
```

Expected: `ModuleNotFoundError: No module named 'blindfold.core.rehydrator'`.

- [ ] **Step 3: Implement `src/blindfold/core/rehydrator.py`**

```python
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
```

- [ ] **Step 4: Update `src/blindfold/__init__.py`**

Overwrite with:
```python
"""Blindfold — privacy proxy for LLM tool calls."""

from blindfold.core.rehydrator import rehydrate

__all__ = ["rehydrate"]
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
uv run pytest tests/unit/test_rehydrator.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/blindfold/core/rehydrator.py src/blindfold/__init__.py tests/unit/test_rehydrator.py
git commit -m "$(cat <<'EOF'
feat(core): rehydrate() and public re-export

Substitutes token placeholders in a string using the vault + policy.
Missing tokens surface as [unknown token]; policy-denied tokens as
[redacted]. Publicly importable as `from blindfold import rehydrate`.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Config loader

`pydantic` model for `blindfold.yaml`. Parses the two consumed sections (`schemas`, `tokens`) and tolerates unknown top-level keys for forward compatibility.

**Files:**
- Create: `src/blindfold/config.py`
- Create: `blindfold.example.yaml`
- Create: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: `SchemaField` (Task 5).
- Produces:
  - `class TokensConfig(BaseModel): default_ttl: int = 3600` (seconds).
  - `class SensitiveFieldConfig(BaseModel): path: str; semantic_type: str | None = None; unit: str | None = None` — the YAML shape (which maps 1:1 onto `SchemaField`).
  - `class ToolSchemaConfig(BaseModel): sensitive_fields: list[SensitiveFieldConfig] = []`
  - `class BlindfoldConfig(BaseModel): schemas: dict[str, ToolSchemaConfig] = {}; tokens: TokensConfig = TokensConfig()` with `model_config = ConfigDict(extra="allow")` so unknown top-level keys are tolerated.
  - `def load_config(path: Path | str) -> BlindfoldConfig` — reads YAML, returns the model. If the path does not exist, returns `BlindfoldConfig()` (all defaults).
  - `def schema_fields_for(config: BlindfoldConfig, tool_name: str) -> list[SchemaField]` — returns `[]` if the tool isn't declared.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_config.py`:
```python
from pathlib import Path

from blindfold.config import (
    BlindfoldConfig,
    SensitiveFieldConfig,
    ToolSchemaConfig,
    TokensConfig,
    load_config,
    schema_fields_for,
)
from blindfold.core.tokenizer import SchemaField


def test_load_missing_file_returns_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "does-not-exist.yaml")
    assert cfg == BlindfoldConfig()
    assert cfg.tokens.default_ttl == 3600
    assert cfg.schemas == {}


def test_load_full_config(tmp_path: Path):
    p = tmp_path / "blindfold.yaml"
    p.write_text(
        """
schemas:
  hr_api.get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
tokens:
  default_ttl: 60
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.tokens.default_ttl == 60
    assert "hr_api.get_salary" in cfg.schemas
    fields = cfg.schemas["hr_api.get_salary"].sensitive_fields
    assert fields == [
        SensitiveFieldConfig(path="$.salary", semantic_type="salary", unit="EUR/year")
    ]


def test_unknown_top_level_keys_tolerated(tmp_path: Path):
    p = tmp_path / "blindfold.yaml"
    p.write_text(
        """
schemas: {}
storage:
  backend: redis
  path: not/used/at/mvp
compute:
  sandbox: docker
""",
        encoding="utf-8",
    )
    # Should not raise.
    cfg = load_config(p)
    assert cfg.tokens == TokensConfig()


def test_schema_fields_for_present():
    cfg = BlindfoldConfig(
        schemas={
            "hr.get_salary": ToolSchemaConfig(
                sensitive_fields=[
                    SensitiveFieldConfig(path="$.salary", semantic_type="salary")
                ]
            )
        }
    )
    assert schema_fields_for(cfg, "hr.get_salary") == [
        SchemaField(path="$.salary", semantic_type="salary", unit=None)
    ]


def test_schema_fields_for_missing_returns_empty():
    cfg = BlindfoldConfig()
    assert schema_fields_for(cfg, "unknown.tool") == []
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'blindfold.config'`.

- [ ] **Step 3: Implement `src/blindfold/config.py`**

```python
"""Configuration loader for blindfold.yaml.

Only the sections consumed at MVP (`schemas`, `tokens`) are modeled.
Unknown top-level keys are tolerated so future config additions do not
break older Blindfold binaries.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from blindfold.core.tokenizer import SchemaField


class SensitiveFieldConfig(BaseModel):
    path: str
    semantic_type: str | None = None
    unit: str | None = None


class ToolSchemaConfig(BaseModel):
    sensitive_fields: list[SensitiveFieldConfig] = []


class TokensConfig(BaseModel):
    default_ttl: int = 3600


class BlindfoldConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    schemas: dict[str, ToolSchemaConfig] = {}
    tokens: TokensConfig = TokensConfig()


def load_config(path: Path | str) -> BlindfoldConfig:
    p = Path(path)
    if not p.exists():
        return BlindfoldConfig()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return BlindfoldConfig.model_validate(data)


def schema_fields_for(config: BlindfoldConfig, tool_name: str) -> list[SchemaField]:
    tool = config.schemas.get(tool_name)
    if tool is None:
        return []
    return [
        SchemaField(path=f.path, semantic_type=f.semantic_type, unit=f.unit)
        for f in tool.sensitive_fields
    ]
```

- [ ] **Step 4: Write `blindfold.example.yaml`**

```yaml
# Blindfold configuration example.
# Copy to blindfold.yaml next to your entry point.

schemas:
  hr_api.get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year

tokens:
  default_ttl: 3600
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/blindfold/config.py blindfold.example.yaml tests/unit/test_config.py
git commit -m "$(cat <<'EOF'
feat(config): pydantic-backed blindfold.yaml loader

Models the two MVP-consumed sections (schemas, tokens) with extra=allow
so future config keys don't break older binaries. Provides
schema_fields_for() to hand the tokenizer its SchemaField list.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: SubprocessSandbox

Runs LLM-supplied Python code in a fresh subprocess with resolved inputs. Timeout, clean env, JSON stdin/stdout, no network guarantees documented as an MVP limitation.

**Files:**
- Create: `src/blindfold/sandbox/__init__.py` (empty)
- Create: `src/blindfold/sandbox/subprocess_.py`
- Create: `tests/unit/test_sandbox.py`

**Interfaces:**
- Consumes: `ComputeSandbox`, `SandboxError` (Task 2).
- Produces: `class SubprocessSandbox(ComputeSandbox)` — stateless, constructor takes no args. Uses `sys.executable` for the child interpreter. On stdout parse failure, timeout, non-zero exit, or KeyError from `resolve()`, raises `SandboxError` with an informative message.

**Wire protocol** between parent and child (stdin/stdout, one line each, UTF-8):

- Parent → child (stdin): a single JSON line `{"code": <str>, "inputs": {<token>: <value>, ...}}`.
- Child → parent (stdout): a single JSON line `{"ok": true, "value": <json>}` on success or `{"ok": false, "error": <str>}` on failure.

The child is a small wrapper script executed via `python -c`. The wrapper:
- reads one line from stdin, parses as JSON;
- defines `resolve(token)` that returns `inputs[token]` (KeyError if missing);
- `exec`s the user code inside a function whose locals include `resolve`; the code must set `result = ...` OR return via `return`. To keep things simple, the wrapper wraps the user code in a function `def _user():` and prepends any user code with an `import` line; **actually** the wrapper prepends nothing and runs the code as a script that must assign to the variable `result`. This is the exact contract shown in the sandbox docstring below.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_sandbox.py`:
```python
import pytest

from blindfold.ports.sandbox import SandboxError
from blindfold.sandbox.subprocess_ import SubprocessSandbox


def test_happy_path_arithmetic():
    sb = SubprocessSandbox()
    result = sb.run(
        code="result = resolve('a') + resolve('b')",
        inputs={"a": 2, "b": 3},
        timeout_s=5.0,
    )
    assert result == 5


def test_happy_path_string():
    sb = SubprocessSandbox()
    result = sb.run(
        code="result = 'A' if resolve('x') > resolve('y') else 'B'",
        inputs={"x": 10, "y": 20},
        timeout_s=5.0,
    )
    assert result == "B"


def test_unicode_roundtrip():
    sb = SubprocessSandbox()
    result = sb.run(
        code="result = resolve('name') + ' 👋'",
        inputs={"name": "Andrea"},
        timeout_s=5.0,
    )
    assert result == "Andrea 👋"


def test_numeric_precision_preserved():
    sb = SubprocessSandbox()
    result = sb.run(
        code="result = resolve('x')",
        inputs={"x": 0.1 + 0.2},  # 0.30000000000000004
        timeout_s=5.0,
    )
    assert result == 0.1 + 0.2


def test_resolve_on_unlisted_token_raises_sandbox_error():
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(
            code="result = resolve('not_declared')",
            inputs={"a": 1},
            timeout_s=5.0,
        )
    assert "not_declared" in str(ei.value) or "KeyError" in str(ei.value)


def test_syntax_error_surfaces_as_sandbox_error():
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError):
        sb.run(
            code="def def def",
            inputs={},
            timeout_s=5.0,
        )


def test_timeout_kills_child():
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(
            code="import time\nwhile True:\n    time.sleep(1)\nresult = None",
            inputs={},
            timeout_s=0.5,
        )
    assert "timeout" in str(ei.value).lower()


def test_non_serializable_result_raises_sandbox_error():
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError):
        sb.run(
            code="result = object()",
            inputs={},
            timeout_s=5.0,
        )
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/unit/test_sandbox.py -v
```

Expected: `ModuleNotFoundError: No module named 'blindfold.sandbox.subprocess_'`.

- [ ] **Step 3: Implement `src/blindfold/sandbox/subprocess_.py`**

```python
"""SubprocessSandbox — best-effort isolation via a fresh Python subprocess.

MVP limitations (documented in the spec):
- No network sandbox on Windows.
- No filesystem sandbox — child inherits CWD.
- Only defenses: clean env (no inherited vars), timeout, subprocess boundary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from blindfold.ports.sandbox import ComputeSandbox, SandboxError

_CHILD_WRAPPER = r"""
import json, sys, traceback

try:
    payload = json.loads(sys.stdin.readline())
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"bad stdin: {exc!r}"}))
    sys.exit(0)

code = payload.get("code", "")
inputs = payload.get("inputs", {}) or {}

def resolve(token):
    if token not in inputs:
        raise KeyError(f"resolve(): token {token!r} not declared in inputs")
    return inputs[token]

globs = {"resolve": resolve, "__builtins__": __builtins__}
locs = {}
try:
    exec(compile(code, "<blindfold_compute>", "exec"), globs, locs)
except Exception as exc:
    tb = traceback.format_exception_only(type(exc), exc)[-1].strip()
    print(json.dumps({"ok": False, "error": tb}))
    sys.exit(0)

if "result" not in locs:
    print(json.dumps({"ok": False, "error": "user code did not assign a `result` variable"}))
    sys.exit(0)

try:
    body = json.dumps({"ok": True, "value": locs["result"]}, ensure_ascii=False)
except (TypeError, ValueError) as exc:
    print(json.dumps({"ok": False, "error": f"result is not JSON-serializable: {exc!r}"}))
    sys.exit(0)

print(body)
"""


class SubprocessSandbox(ComputeSandbox):
    def run(self, code: str, inputs: dict[str, Any], timeout_s: float) -> Any:
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", _CHILD_WRAPPER],
                input=json.dumps({"code": code, "inputs": inputs}, ensure_ascii=False) + "\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxError(f"blind-compute timeout after {timeout_s}s") from exc

        stdout = completed.stdout.strip()
        if not stdout:
            raise SandboxError(
                f"sandbox produced no output (exit={completed.returncode}, stderr={completed.stderr[:400]!r})"
            )

        try:
            envelope = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise SandboxError(f"sandbox output not JSON: {stdout!r}") from exc

        if not envelope.get("ok"):
            raise SandboxError(str(envelope.get("error", "unknown sandbox error")))
        return envelope["value"]
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/unit/test_sandbox.py -v
```

Expected: all tests pass. `test_timeout_kills_child` may take up to ~1s.

- [ ] **Step 5: Commit**

```bash
git add src/blindfold/sandbox tests/unit/test_sandbox.py
git commit -m "$(cat <<'EOF'
feat(sandbox): SubprocessSandbox with JSON stdin/stdout protocol

Runs LLM-supplied Python code in a fresh, isolated (-I) child
interpreter with a clean env and hard timeout. Surfaces syntax
errors, non-JSON output, timeout, and unlisted-token lookups as
SandboxError.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `blindfold_compute` MCP tool

The tool definition (name / description / JSON schema for arguments) and its handler. Handler resolves + policy-checks inputs, runs the sandbox, mints a derived record with composed policy and TTL, and returns the new token.

**Files:**
- Create: `src/blindfold/tools/__init__.py` (empty)
- Create: `src/blindfold/tools/blindfold_compute.py`
- Create: `tests/unit/test_blindfold_compute.py`

**Interfaces:**
- Consumes: `MemoryTokenStore` (Task 3), `SessionBoundPolicy` (Task 4), `SubprocessSandbox` (Task 8), `Lineage`/`Policy`/`VaultRecord`/`compose_policy`/`compose_ttl` (Task 2).
- Produces:
  - `BLINDFOLD_COMPUTE_TOOL_NAME: str = "blindfold_compute"`
  - `def build_tool_definition() -> dict` — returns the MCP tool spec (`name`, `description`, `inputSchema`).
  - `def handle_blindfold_compute(args: dict, *, store: TokenStore, policy: DetokenizePolicy, sandbox: ComputeSandbox, session_id: str, ttl_seconds: int, code_timeout_s: float = 5.0) -> str`
    - `args` shape: `{"code": <str>, "inputs": [<token>, ...]}`.
    - Returns the newly-minted token string. Raises `ValueError` on malformed args or policy denial; raises `SandboxError` on sandbox failure.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_blindfold_compute.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest

from blindfold.core.lineage import Lineage, Policy, VaultRecord
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.vault import MemoryTokenStore
from blindfold.ports.sandbox import SandboxError
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import (
    BLINDFOLD_COMPUTE_TOOL_NAME,
    build_tool_definition,
    handle_blindfold_compute,
)

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _put(store: MemoryTokenStore, token: str, value, *, session: str = "s", ttl_min: int = 60,
         reveal: bool = True, compute: bool = True) -> None:
    store.put(
        VaultRecord(
            token=token,
            value=value,
            dtype="number" if isinstance(value, (int, float)) else "string",
            semantic_type=None,
            unit=None,
            session_id=session,
            created_at=NOW,
            ttl=NOW + timedelta(minutes=ttl_min),
            lineage=Lineage(op="tool_result", tool="hr", path="$.x"),
            policy=Policy(reveal_to_frontend=reveal, can_be_input_to_compute=compute),
        )
    )


def test_tool_definition_shape():
    spec = build_tool_definition()
    assert spec["name"] == BLINDFOLD_COMPUTE_TOOL_NAME
    assert "description" in spec and len(spec["description"]) > 20
    schema = spec["inputSchema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"code", "inputs"}
    assert schema["properties"]["code"]["type"] == "string"
    assert schema["properties"]["inputs"]["type"] == "array"


def test_handler_happy_path_returns_token_and_stores_record():
    import re
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", 100)
    _put(store, "⟦tok_00000002⟧", 200)

    token = handle_blindfold_compute(
        {"code": "result = 'A' if resolve('⟦tok_00000001⟧') > resolve('⟦tok_00000002⟧') else 'B'",
         "inputs": ["⟦tok_00000001⟧", "⟦tok_00000002⟧"]},
        store=store,
        policy=SessionBoundPolicy(),
        sandbox=SubprocessSandbox(),
        session_id="s",
        ttl_seconds=3600,
    )
    assert re.fullmatch(r"⟦tok_[0-9a-f]{8}⟧", token)

    rec = store.get(token)
    assert rec is not None
    assert rec.value == "B"
    assert rec.dtype == "string"
    assert rec.lineage.op == "blind_compute"
    assert set(rec.lineage.inputs) == {"⟦tok_00000001⟧", "⟦tok_00000002⟧"}
    assert rec.lineage.code_digest is not None
    assert len(rec.lineage.code_digest) == 64  # sha256 hex


def test_handler_ttl_is_min_of_inputs():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", 1, ttl_min=10)
    _put(store, "⟦tok_00000002⟧", 2, ttl_min=999)

    token = handle_blindfold_compute(
        {"code": "result = resolve('⟦tok_00000001⟧') + resolve('⟦tok_00000002⟧')",
         "inputs": ["⟦tok_00000001⟧", "⟦tok_00000002⟧"]},
        store=store,
        policy=SessionBoundPolicy(),
        sandbox=SubprocessSandbox(),
        session_id="s",
        ttl_seconds=3600,
    )
    a = store.get("⟦tok_00000001⟧")
    new = store.get(token)
    assert new.ttl == a.ttl


def test_handler_policy_denies_cross_session():
    store = MemoryTokenStore()
    _put(store, "⟦tok_00000001⟧", 1, session="other")
    with pytest.raises(ValueError) as ei:
        handle_blindfold_compute(
            {"code": "result = resolve('⟦tok_00000001⟧')",
             "inputs": ["⟦tok_00000001⟧"]},
            store=store,
            policy=SessionBoundPolicy(),
            sandbox=SubprocessSandbox(),
            session_id="s",
            ttl_seconds=3600,
        )
    assert "policy" in str(ei.value).lower() or "denied" in str(ei.value).lower()


def test_handler_rejects_unknown_input_token():
    store = MemoryTokenStore()
    with pytest.raises(ValueError):
        handle_blindfold_compute(
            {"code": "result = 1",
             "inputs": ["⟦tok_deadbeef⟧"]},
            store=store,
            policy=SessionBoundPolicy(),
            sandbox=SubprocessSandbox(),
            session_id="s",
            ttl_seconds=3600,
        )


def test_handler_missing_args_raises_value_error():
    store = MemoryTokenStore()
    with pytest.raises(ValueError):
        handle_blindfold_compute(
            {"code": "result = 1"},  # missing 'inputs'
            store=store,
            policy=SessionBoundPolicy(),
            sandbox=SubprocessSandbox(),
            session_id="s",
            ttl_seconds=3600,
        )


def test_handler_propagates_sandbox_error():
    store = MemoryTokenStore()
    with pytest.raises(SandboxError):
        handle_blindfold_compute(
            {"code": "result = object()", "inputs": []},
            store=store,
            policy=SessionBoundPolicy(),
            sandbox=SubprocessSandbox(),
            session_id="s",
            ttl_seconds=3600,
        )
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/unit/test_blindfold_compute.py -v
```

Expected: `ModuleNotFoundError: No module named 'blindfold.tools.blindfold_compute'`.

- [ ] **Step 3: Implement `src/blindfold/tools/blindfold_compute.py`**

```python
"""blindfold_compute MCP tool definition and handler."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from blindfold.core.lineage import Lineage, Policy, VaultRecord, compose_policy, compose_ttl
from blindfold.core.vault import MemoryTokenStore
from blindfold.ports.policy import DetokenizeContext, DetokenizePolicy
from blindfold.ports.sandbox import ComputeSandbox
from blindfold.ports.token_store import TokenStore

BLINDFOLD_COMPUTE_TOOL_NAME = "blindfold_compute"

_TOOL_DESCRIPTION = (
    "Run Python code on hidden values behind tokens ⟦tok_XXXXXXXX⟧. "
    "Every token the code touches via resolve(...) MUST be listed in `inputs`. "
    "The code MUST assign its result to a variable named `result` and the result "
    "MUST be JSON-serializable. The tool returns a NEW token (not the value); "
    "compose further tokens by calling this tool again."
)


def build_tool_definition() -> dict:
    return {
        "name": BLINDFOLD_COMPUTE_TOOL_NAME,
        "description": _TOOL_DESCRIPTION,
        "inputSchema": {
            "type": "object",
            "required": ["code", "inputs"],
            "properties": {
                "code": {"type": "string", "description": "Python code assigning `result`."},
                "inputs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Token strings the code will resolve.",
                },
            },
        },
    }


def _infer_dtype(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def handle_blindfold_compute(
    args: dict,
    *,
    store: TokenStore,
    policy: DetokenizePolicy,
    sandbox: ComputeSandbox,
    session_id: str,
    ttl_seconds: int,
    code_timeout_s: float = 5.0,
) -> str:
    code = args.get("code")
    inputs = args.get("inputs")
    if not isinstance(code, str) or not isinstance(inputs, list):
        raise ValueError("blindfold_compute requires string `code` and list `inputs`")

    ctx = DetokenizeContext(session_id=session_id)
    resolved: dict[str, Any] = {}
    input_records: list[VaultRecord] = []
    for token in inputs:
        record = store.get(token)
        if record is None:
            raise ValueError(f"unknown or expired input token: {token}")
        if not policy.can_compute(ctx, record):
            raise ValueError(f"policy denied compute on token: {token}")
        resolved[token] = record.value
        input_records.append(record)

    value = sandbox.run(code=code, inputs=resolved, timeout_s=code_timeout_s)

    new_token = MemoryTokenStore.mint_token()
    now = datetime.now(tz=timezone.utc)
    ttl = (
        compose_ttl(input_records)
        if input_records
        else now + timedelta(seconds=ttl_seconds)
    )
    derived_policy = compose_policy([r.policy for r in input_records])
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()

    store.put(
        VaultRecord(
            token=new_token,
            value=value,
            dtype=_infer_dtype(value),
            semantic_type=None,
            unit=None,
            session_id=session_id,
            created_at=now,
            ttl=ttl,
            lineage=Lineage(op="blind_compute", inputs=tuple(inputs), code_digest=digest),
            policy=derived_policy,
        )
    )
    return new_token
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/unit/test_blindfold_compute.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/blindfold/tools tests/unit/test_blindfold_compute.py
git commit -m "$(cat <<'EOF'
feat(tools): blindfold_compute tool definition + handler

Publishes the injected tool spec teaching the LLM the token protocol,
and implements the handler: policy-check inputs, run sandbox, mint a
new record with composed policy/TTL and sha256(code_digest).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Fake HR MCP server (test fixture)

A minimal MCP server exposing one tool: `get_salary(name: str) -> {name, salary}`. Used by the integration test and the demo harness. Deterministic: known names return a hard-coded salary; unknown names return `salary: 0`.

**Files:**
- Create: `examples/__init__.py` (empty)
- Create: `examples/fake_hr_mcp/__init__.py` (empty)
- Create: `examples/fake_hr_mcp/__main__.py`

**Interfaces:**
- Consumes: nothing internal (uses `mcp` SDK).
- Produces: `python -m examples.fake_hr_mcp` runs a stdio MCP server with:
  - `tools/list` → `[{"name": "get_salary", "description": "...", "inputSchema": {...}}]`
  - `tools/call` for `get_salary` with `{"name": <str>}` → `{"content": [{"type": "text", "text": "..."}], "structuredContent": {"name": <str>, "salary": <int>}}` — the tokenizer will operate on `structuredContent` per MVP.
  - **Fixture data** (hard-coded): `Manuel Pernigotto → 62000`, `Andrea Tuscano → 71000`, `Maria Rossi → 55000`. Any other name → `0`.

- [ ] **Step 1: Write `examples/fake_hr_mcp/__main__.py`**

```python
"""Fake HR API exposed as a stdio MCP server (test fixture)."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

_SALARIES: dict[str, int] = {
    "Manuel Pernigotto": 62000,
    "Andrea Tuscano": 71000,
    "Maria Rossi": 55000,
}


def _build_server() -> Server:
    server = Server("fake-hr-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_salary",
                description="Return the annual gross salary in EUR for a given full name.",
                inputSchema={
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name != "get_salary":
            raise ValueError(f"unknown tool: {name}")
        person = arguments["name"]
        salary = _SALARIES.get(person, 0)
        return [TextContent(type="text", text=f'{{"name": "{person}", "salary": {salary}}}')]

    return server


async def _amain() -> None:
    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-run the server manually**

```bash
uv run python -c "import examples.fake_hr_mcp.__main__ as m; print(m)"
```

Expected: prints the module object (no exceptions).

- [ ] **Step 3: Commit**

```bash
git add examples
git commit -m "$(cat <<'EOF'
feat(examples): fake_hr_mcp — MCP server fixture

Minimal stdio MCP server exposing get_salary(name) with a hard-coded
salary table. Used by the integration test and the demo harness.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Proxy layer

The heart of the MVP: an asyncio stdio proxy that speaks MCP on both sides, injects `blindfold_compute` into `tools/list` responses, tokenizes downstream `tools/call` responses per the config, and exposes the `blindfold/rehydrate` custom JSON-RPC method.

**Files:**
- Create: `src/blindfold/proxy.py`
- Create: `tests/integration/test_proxy_forwarding.py`

**Interfaces:**
- Consumes: everything from Tasks 2–9.
- Produces:
  - `@dataclass class ProxyState`: holds `store`, `policy`, `sandbox`, `config`, `session_id`, `ttl_seconds` — passed to handlers.
  - `async def run_proxy(downstream_cmd: list[str], config_path: Path | None = None) -> None` — the main entry: spawns the downstream MCP server as a subprocess, wires stdio, runs until either side exits.
  - `def build_proxy_state(config: BlindfoldConfig) -> ProxyState` — helper for tests.

**Wire behavior (each direction is a separate `asyncio.Task` reading one JSON-RPC line at a time):**

- **Client → Downstream (requests):** For each incoming JSON-RPC message from the harness (stdin):
  - If `method == "blindfold/rehydrate"`: respond directly (do not forward). Params `{text, session_id}`, response `{text}`.
  - If `method == "tools/call"` and `params.name == "blindfold_compute"`: run the handler locally (do not forward), respond with `{"content": [{"type": "text", "text": <new_token>}]}`.
  - Otherwise: forward verbatim to downstream child stdin.
- **Downstream → Client (responses & notifications):** For each JSON-RPC message from the child (stdout):
  - If it's a response to `tools/list`: parse the `result.tools` array, append `build_tool_definition()`, forward to harness.
  - If it's a response to `tools/call`: look up the original request's tool name (kept in an in-memory `dict[int, str]` keyed by the JSON-RPC `id`); if the tool has schema fields, tokenize the response's `content[0].text` (which is JSON) and replace; forward to harness.
  - Otherwise: forward verbatim.
- **Robustness:** malformed JSON on either side is logged to stderr and dropped; a child exit causes graceful proxy shutdown.

- [ ] **Step 1: Write the failing integration test**

`tests/integration/test_proxy_forwarding.py`:
```python
"""Integration: spins up `python -m blindfold` as a subprocess wrapping
`python -m examples.fake_hr_mcp` and drives the raw MCP JSON-RPC over stdio.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

TOKEN_RE = re.compile(r"⟦tok_[0-9a-f]{8}⟧")


@pytest.fixture()
async def proxy_subprocess(tmp_path: Path):
    cfg = tmp_path / "blindfold.yaml"
    cfg.write_text(
        """
schemas:
  get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
""",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "blindfold",
        "--config", str(cfg),
        "--",
        sys.executable, "-m", "examples.fake_hr_mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        yield proc
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()


async def _send(proc, msg: dict[str, Any]) -> None:
    line = (json.dumps(msg) + "\n").encode("utf-8")
    proc.stdin.write(line)
    await proc.stdin.drain()


async def _recv(proc, timeout: float = 5.0) -> dict[str, Any]:
    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    if not raw:
        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace")
        raise RuntimeError(f"proxy closed stdout; stderr={stderr!r}")
    return json.loads(raw)


async def _initialize(proc, next_id: int) -> int:
    await _send(proc, {
        "jsonrpc": "2.0", "id": next_id, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    })
    await _recv(proc)
    await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    return next_id + 1


async def test_tools_list_includes_injected_compute_tool(proxy_subprocess):
    proc = proxy_subprocess
    nid = await _initialize(proc, 1)
    await _send(proc, {"jsonrpc": "2.0", "id": nid, "method": "tools/list", "params": {}})
    resp = await _recv(proc)
    names = {t["name"] for t in resp["result"]["tools"]}
    assert "get_salary" in names
    assert "blindfold_compute" in names


async def test_get_salary_response_is_tokenized(proxy_subprocess):
    proc = proxy_subprocess
    nid = await _initialize(proc, 1)
    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "tools/call",
        "params": {"name": "get_salary", "arguments": {"name": "Manuel Pernigotto"}},
    })
    resp = await _recv(proc)
    text = resp["result"]["content"][0]["text"]
    parsed = json.loads(text)
    assert parsed["name"] == "Manuel Pernigotto"
    assert isinstance(parsed["salary"], str)
    assert TOKEN_RE.fullmatch(parsed["salary"])


async def test_blindfold_compute_returns_derived_token(proxy_subprocess):
    proc = proxy_subprocess
    nid = await _initialize(proc, 1)

    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "tools/call",
        "params": {"name": "get_salary", "arguments": {"name": "Manuel Pernigotto"}},
    })
    a = json.loads((await _recv(proc))["result"]["content"][0]["text"])
    nid += 1

    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "tools/call",
        "params": {"name": "get_salary", "arguments": {"name": "Andrea Tuscano"}},
    })
    b = json.loads((await _recv(proc))["result"]["content"][0]["text"])
    nid += 1

    a_tok, b_tok = a["salary"], b["salary"]
    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "tools/call",
        "params": {"name": "blindfold_compute", "arguments": {
            "code": f"result = 'Manuel Pernigotto' if resolve({a_tok!r}) > resolve({b_tok!r}) else 'Andrea Tuscano'",
            "inputs": [a_tok, b_tok],
        }},
    })
    compute_resp = await _recv(proc)
    new_token = compute_resp["result"]["content"][0]["text"]
    assert TOKEN_RE.fullmatch(new_token)

    nid += 1
    await _send(proc, {
        "jsonrpc": "2.0", "id": nid, "method": "blindfold/rehydrate",
        "params": {"text": f"The higher earner is {new_token}.", "session_id": "PROBE_SESSION_UNUSED"},
    })
    rehy = await _recv(proc)
    assert rehy["result"]["text"] == "The higher earner is Andrea Tuscano."
```

Note the test currently passes `session_id: "PROBE_SESSION_UNUSED"` — the proxy's `blindfold/rehydrate` handler uses the **proxy's own** session_id (one-process-one-session at MVP), so the caller-supplied field is ignored. The assertion still works.

- [ ] **Step 2: Run the test to see it fail (module missing)**

```bash
uv run pytest tests/integration/test_proxy_forwarding.py -v
```

Expected: fails because `blindfold.proxy` (and `blindfold.cli`) do not exist yet.

- [ ] **Step 3: Implement `src/blindfold/proxy.py`**

```python
"""Blindfold stdio MCP proxy.

Wraps a downstream stdio MCP server, tokenizes outbound tool results,
injects `blindfold_compute` into tools/list, and answers the custom
`blindfold/rehydrate` control-plane method.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from blindfold.config import BlindfoldConfig, load_config, schema_fields_for
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.rehydrator import rehydrate
from blindfold.core.tokenizer import tokenize_result
from blindfold.core.vault import MemoryTokenStore
from blindfold.ports.policy import DetokenizePolicy
from blindfold.ports.sandbox import ComputeSandbox, SandboxError
from blindfold.ports.token_store import TokenStore
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import (
    BLINDFOLD_COMPUTE_TOOL_NAME,
    build_tool_definition,
    handle_blindfold_compute,
)


@dataclass
class ProxyState:
    store: TokenStore
    policy: DetokenizePolicy
    sandbox: ComputeSandbox
    config: BlindfoldConfig
    session_id: str
    pending_calls: dict[Any, str] = field(default_factory=dict)  # jsonrpc id -> tool name

    @property
    def ttl_seconds(self) -> int:
        return self.config.tokens.default_ttl


def build_proxy_state(config: BlindfoldConfig) -> ProxyState:
    return ProxyState(
        store=MemoryTokenStore(),
        policy=SessionBoundPolicy(),
        sandbox=SubprocessSandbox(),
        config=config,
        session_id=f"sess_{uuid.uuid4().hex[:12]}",
    )


async def run_proxy(downstream_cmd: list[str], config_path: Path | None = None) -> None:
    cfg = load_config(config_path) if config_path is not None else BlindfoldConfig()
    state = build_proxy_state(cfg)

    child = await asyncio.create_subprocess_exec(
        *downstream_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr,
    )

    loop = asyncio.get_running_loop()
    stdin_reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(stdin_reader),
        sys.stdin,
    )
    stdout_transport, stdout_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin,
        sys.stdout,
    )
    stdout_writer = asyncio.StreamWriter(stdout_transport, stdout_protocol, None, loop)

    tasks = [
        asyncio.create_task(_pump_client_to_child(stdin_reader, child, stdout_writer, state)),
        asyncio.create_task(_pump_child_to_client(child, stdout_writer, state)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        if child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), timeout=5)
            except asyncio.TimeoutError:
                child.kill()


async def _pump_client_to_child(
    reader: asyncio.StreamReader,
    child: asyncio.subprocess.Process,
    to_client: asyncio.StreamWriter,
    state: ProxyState,
) -> None:
    while True:
        line = await reader.readline()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[blindfold] bad JSON from client: {exc!r}", file=sys.stderr)
            continue

        method = msg.get("method")
        if method == "blindfold/rehydrate":
            await _handle_rehydrate(msg, to_client, state)
            continue
        if method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") == BLINDFOLD_COMPUTE_TOOL_NAME:
                await _handle_blindfold_compute(msg, to_client, state)
                continue
            state.pending_calls[msg.get("id")] = params.get("name")

        child.stdin.write(line)
        await child.stdin.drain()


async def _pump_child_to_client(
    child: asyncio.subprocess.Process,
    to_client: asyncio.StreamWriter,
    state: ProxyState,
) -> None:
    assert child.stdout is not None
    while True:
        line = await child.stdout.readline()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[blindfold] bad JSON from child: {exc!r}", file=sys.stderr)
            _write_line(to_client, line)
            continue

        if "result" in msg and isinstance(msg["result"], dict):
            tools = msg["result"].get("tools")
            if isinstance(tools, list):
                tools.append(build_tool_definition())

            msg_id = msg.get("id")
            if msg_id in state.pending_calls:
                tool_name = state.pending_calls.pop(msg_id)
                _tokenize_tool_call_result(msg, tool_name, state)

        _write_line(to_client, (json.dumps(msg) + "\n").encode("utf-8"))


def _write_line(writer: asyncio.StreamWriter, data: bytes) -> None:
    writer.write(data)


def _tokenize_tool_call_result(msg: dict, tool_name: str, state: ProxyState) -> None:
    fields = schema_fields_for(state.config, tool_name)
    if not fields:
        return
    content = (msg.get("result") or {}).get("content") or []
    now = datetime.now(tz=timezone.utc)
    ttl = now + timedelta(seconds=state.ttl_seconds)
    for i, part in enumerate(content):
        if part.get("type") != "text":
            continue
        try:
            payload = json.loads(part.get("text", ""))
        except json.JSONDecodeError:
            continue
        tokenized = tokenize_result(payload, tool_name, fields, state.store, state.session_id, ttl)
        content[i] = {"type": "text", "text": json.dumps(tokenized)}


async def _handle_rehydrate(msg: dict, to_client: asyncio.StreamWriter, state: ProxyState) -> None:
    params = msg.get("params") or {}
    text = params.get("text", "")
    result_text = rehydrate(text, state.session_id, state.store, state.policy)
    _write_line(
        to_client,
        (json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"text": result_text}}) + "\n").encode("utf-8"),
    )


async def _handle_blindfold_compute(msg: dict, to_client: asyncio.StreamWriter, state: ProxyState) -> None:
    args = ((msg.get("params") or {}).get("arguments")) or {}
    try:
        new_token = handle_blindfold_compute(
            args,
            store=state.store,
            policy=state.policy,
            sandbox=state.sandbox,
            session_id=state.session_id,
            ttl_seconds=state.ttl_seconds,
        )
        payload = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {"content": [{"type": "text", "text": new_token}], "isError": False},
        }
    except (ValueError, SandboxError) as exc:
        payload = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {"content": [{"type": "text", "text": f"blindfold_compute error: {exc}"}], "isError": True},
        }
    _write_line(to_client, (json.dumps(payload) + "\n").encode("utf-8"))
```

- [ ] **Step 4: Do not run the integration test yet** — it depends on the CLI (Task 12). Do a syntax check instead:

```bash
uv run python -c "import blindfold.proxy; print('proxy imports OK')"
```

Expected: `proxy imports OK`.

- [ ] **Step 5: Commit**

```bash
git add src/blindfold/proxy.py tests/integration/test_proxy_forwarding.py
git commit -m "$(cat <<'EOF'
feat(proxy): asyncio stdio MCP proxy with tokenization + rehydrate RPC

Wraps a downstream stdio MCP server, injects the blindfold_compute
tool into tools/list, tokenizes tool_call responses per config, and
answers the blindfold/rehydrate control-plane method. Integration
test suite added but is executed only after the CLI lands.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: CLI (`blindfold -- <cmd>`)

Thin `argparse`-based front door: parses `--config` and everything after `--` as the downstream command, then hands off to `run_proxy`.

**Files:**
- Create: `src/blindfold/cli.py`

**Interfaces:**
- Consumes: `run_proxy` (Task 11).
- Produces: `def main(argv: list[str] | None = None) -> int` — returns 0 on clean shutdown. Also `python -m blindfold` works (via a `__main__.py` — see below).

**CLI shape:**
- `blindfold --config PATH -- <cmd> <args>...` — wraps the downstream stdio MCP server.
- `blindfold --help` prints usage and exits 0.
- If `--` is missing or nothing follows it, print an error to stderr and exit 2.

- [ ] **Step 1: Implement `src/blindfold/cli.py`**

```python
"""Blindfold CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from blindfold.proxy import run_proxy

USAGE = (
    "blindfold [--config PATH] -- <downstream-mcp-command> [args...]\n\n"
    "Wraps the given stdio MCP server, tokenizing tool results and exposing\n"
    "the blindfold_compute tool + blindfold/rehydrate JSON-RPC method."
)


def _split_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        return argv, []
    idx = argv.index("--")
    return argv[:idx], argv[idx + 1 :]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    own, downstream = _split_argv(argv)

    parser = argparse.ArgumentParser(
        prog="blindfold",
        description="Privacy proxy for stdio MCP servers.",
        usage=USAGE,
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to blindfold.yaml")
    args = parser.parse_args(own)

    if not downstream:
        print("error: no downstream command after `--`", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    asyncio.run(run_proxy(downstream, config_path=args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Add `src/blindfold/__main__.py` so `python -m blindfold` works**

```python
from blindfold.cli import main

raise SystemExit(main())
```

- [ ] **Step 3: Smoke-test the CLI**

```bash
uv run blindfold --help
```

Expected: prints the USAGE text and exits 0.

```bash
uv run python -m blindfold --help
```

Expected: same output.

- [ ] **Step 4: Run the integration test from Task 11 now that the CLI exists**

```bash
uv run pytest tests/integration/test_proxy_forwarding.py -v
```

Expected: 3 tests pass. If the test hangs on the initialization handshake, check that `_pump_client_to_child` is actually flushing writes to the child stdin (`await child.stdin.drain()` is present).

- [ ] **Step 5: Commit**

```bash
git add src/blindfold/cli.py src/blindfold/__main__.py
git commit -m "$(cat <<'EOF'
feat(cli): blindfold -- <cmd> entry point + python -m blindfold

Parses --config and the post-`--` downstream command; hands off to
run_proxy. Enables the previously-added integration tests to pass.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Demo harness (`examples/demo_chat.py`)

Interactive demo that talks to Anthropic and uses Blindfold **in-process** (as a library), so the vault is a shared Python object and `rehydrate()` is a normal function call. Not run in CI; documented as a manual run needing `ANTHROPIC_API_KEY`.

**Files:**
- Create: `examples/demo_chat.py`

**Interfaces:**
- Consumes: `rehydrate` (Task 6), `MemoryTokenStore` (Task 3), `SessionBoundPolicy` (Task 4), `SubprocessSandbox` (Task 8), `tokenize_result`/`SchemaField` (Task 5), `handle_blindfold_compute`/`build_tool_definition` (Task 9), `BlindfoldConfig`/`load_config` (Task 7).
- Produces: `examples/demo_chat.py` — a self-contained script wrapping `examples.fake_hr_mcp` in-process (via `mcp.client.stdio.stdio_client`), running Claude in a tool-use loop.

- [ ] **Step 1: Implement `examples/demo_chat.py`**

```python
"""Interactive demo — Claude answering an HR question through Blindfold.

Run with:
    uv run --extra demo python examples/demo_chat.py "Who earns more, Manuel Pernigotto or Andrea Tuscano?"

Requires ANTHROPIC_API_KEY in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from blindfold import rehydrate
from blindfold.config import BlindfoldConfig, ToolSchemaConfig, SensitiveFieldConfig, schema_fields_for
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.tokenizer import tokenize_result
from blindfold.core.vault import MemoryTokenStore
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import (
    BLINDFOLD_COMPUTE_TOOL_NAME,
    build_tool_definition,
    handle_blindfold_compute,
)

MODEL = "claude-opus-4-7"
SYSTEM_PROMPT = (
    "You have MCP tools that return TOKENIZED values shown as ⟦tok_XXXXXXXX⟧. "
    "You cannot see the underlying values. When you need to compare, aggregate, "
    "or otherwise derive from them, call the `blindfold_compute` tool. Pass every "
    "token your code will call resolve() on in the `inputs` array. Preserve tokens "
    "in your final answer VERBATIM — never invent, alter, or paraphrase them."
)


async def _amain(question: str) -> None:
    store = MemoryTokenStore()
    policy = SessionBoundPolicy()
    sandbox = SubprocessSandbox()
    session_id = f"demo_{uuid.uuid4().hex[:8]}"
    config = BlindfoldConfig(
        schemas={
            "get_salary": ToolSchemaConfig(
                sensitive_fields=[
                    SensitiveFieldConfig(path="$.salary", semantic_type="salary", unit="EUR/year")
                ]
            )
        }
    )
    ttl = datetime.now(tz=timezone.utc) + timedelta(hours=1)

    server_params = StdioServerParameters(
        command=sys.executable, args=["-m", "examples.fake_hr_mcp"], env={**os.environ, "PYTHONIOENCODING": "utf-8"}
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            tools = [
                {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
                for t in listed.tools
            ]
            tools.append({
                "name": BLINDFOLD_COMPUTE_TOOL_NAME,
                "description": build_tool_definition()["description"],
                "input_schema": build_tool_definition()["inputSchema"],
            })

            client = Anthropic()
            messages: list[dict] = [{"role": "user", "content": question}]

            while True:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason != "tool_use":
                    break

                tool_results: list[dict] = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    if block.name == BLINDFOLD_COMPUTE_TOOL_NAME:
                        try:
                            token = handle_blindfold_compute(
                                block.input,
                                store=store,
                                policy=policy,
                                sandbox=sandbox,
                                session_id=session_id,
                                ttl_seconds=3600,
                            )
                            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": token})
                        except Exception as exc:
                            tool_results.append({
                                "type": "tool_result", "tool_use_id": block.id,
                                "content": f"error: {exc}", "is_error": True,
                            })
                        continue

                    call = await session.call_tool(block.name, block.input)
                    text = call.content[0].text if call.content else "{}"
                    fields = schema_fields_for(config, block.name)
                    if fields:
                        payload = json.loads(text)
                        tokenized = tokenize_result(payload, block.name, fields, store, session_id, ttl)
                        text = json.dumps(tokenized)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": text})

                messages.append({"role": "user", "content": tool_results})

            final_text = "".join(b.text for b in response.content if b.type == "text")
            print(rehydrate(final_text, session_id, store, policy))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", default="Who earns more, Manuel Pernigotto or Andrea Tuscano?")
    args = parser.parse_args()
    asyncio.run(_amain(args.question))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Import-only smoke test (no network)**

```bash
uv run --extra demo python -c "import examples.demo_chat as d; print(d.MODEL)"
```

Expected: prints `claude-opus-4-7`.

- [ ] **Step 3: Commit**

```bash
git add examples/demo_chat.py
git commit -m "$(cat <<'EOF'
feat(examples): interactive demo harness

Talks to Anthropic and uses blindfold in-process against
examples.fake_hr_mcp. Not run in CI; requires ANTHROPIC_API_KEY.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: End-to-end test with recorded transcript

Replays a canned sequence of Anthropic-shaped tool_use / tool_result / text messages through the same in-process wiring the demo uses, asserting the final rehydrated string AND that no real salary ever appeared in anything the "LLM" saw.

**Files:**
- Create: `tests/e2e/recorded_transcript.json`
- Create: `tests/e2e/test_demo_flow.py`

**Interfaces:**
- Consumes: everything the demo consumes.
- Produces: a test that requires no network and no API key.

**Approach:** rather than replay a real Anthropic transcript (which would need mocking the SDK), the test drives the same handler code paths the demo uses (`tokenize_result`, `handle_blindfold_compute`, `rehydrate`) without the Anthropic SDK. The "canned transcript" is a scripted list of tool calls and text emissions. This is functionally equivalent to what the SDK would drive and cleanly avoids any HTTP mocking.

- [ ] **Step 1: Write the fixture `tests/e2e/recorded_transcript.json`**

```json
{
  "question": "Who earns more, Manuel Pernigotto or Andrea Tuscano?",
  "steps": [
    {"kind": "tool_call", "name": "get_salary", "arguments": {"name": "Manuel Pernigotto"}, "bind_result_as": "salary_manuel"},
    {"kind": "tool_call", "name": "get_salary", "arguments": {"name": "Andrea Tuscano"}, "bind_result_as": "salary_andrea"},
    {"kind": "compute",
     "code": "result = 'Manuel Pernigotto' if resolve(inputs[0]) > resolve(inputs[1]) else 'Andrea Tuscano'",
     "inputs_from": ["salary_manuel.salary", "salary_andrea.salary"],
     "bind_result_as": "answer_token"},
    {"kind": "final_text", "template": "The higher earner is {answer_token}."}
  ],
  "expected_final_text": "The higher earner is Andrea Tuscano.",
  "leak_probes": [62000, 71000, "62000", "71000"]
}
```

- [ ] **Step 2: Write the failing test**

`tests/e2e/test_demo_flow.py`:
```python
"""E2E: replay a canned transcript through the same wiring the demo uses."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from blindfold import rehydrate
from blindfold.config import BlindfoldConfig, ToolSchemaConfig, SensitiveFieldConfig, schema_fields_for
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.tokenizer import tokenize_result
from blindfold.core.vault import MemoryTokenStore
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import handle_blindfold_compute
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURE = Path(__file__).parent / "recorded_transcript.json"


@pytest.mark.asyncio
async def test_demo_flow_end_to_end():
    transcript = json.loads(FIXTURE.read_text(encoding="utf-8"))
    store = MemoryTokenStore()
    policy = SessionBoundPolicy()
    sandbox = SubprocessSandbox()
    session_id = f"e2e_{uuid.uuid4().hex[:8]}"
    ttl = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    config = BlindfoldConfig(
        schemas={
            "get_salary": ToolSchemaConfig(
                sensitive_fields=[
                    SensitiveFieldConfig(path="$.salary", semantic_type="salary", unit="EUR/year")
                ]
            )
        }
    )

    llm_visible_stream: list[str] = []  # anything a real LLM would have seen; probed for leaks below
    bindings: dict[str, Any] = {}

    server_params = StdioServerParameters(
        command=sys.executable, args=["-m", "examples.fake_hr_mcp"], env=None
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for step in transcript["steps"]:
                if step["kind"] == "tool_call":
                    call = await session.call_tool(step["name"], step["arguments"])
                    text = call.content[0].text if call.content else "{}"
                    fields = schema_fields_for(config, step["name"])
                    if fields:
                        payload = json.loads(text)
                        tokenized = tokenize_result(payload, step["name"], fields, store, session_id, ttl)
                        text = json.dumps(tokenized)
                    llm_visible_stream.append(text)
                    bindings[step["bind_result_as"]] = json.loads(text)

                elif step["kind"] == "compute":
                    inputs = [_deref(bindings, ref) for ref in step["inputs_from"]]
                    token = handle_blindfold_compute(
                        {"code": step["code"], "inputs": inputs},
                        store=store, policy=policy, sandbox=sandbox,
                        session_id=session_id, ttl_seconds=3600,
                    )
                    llm_visible_stream.append(token)
                    bindings[step["bind_result_as"]] = token

                elif step["kind"] == "final_text":
                    text = step["template"].format(**bindings)
                    llm_visible_stream.append(text)
                    rehydrated = rehydrate(text, session_id, store, policy)
                    assert rehydrated == transcript["expected_final_text"]

    joined = "\n".join(llm_visible_stream)
    for probe in transcript["leak_probes"]:
        assert str(probe) not in joined, f"real value leaked to LLM-visible stream: {probe!r}"


def _deref(bindings: dict, ref: str) -> Any:
    key, *path = ref.split(".")
    node = bindings[key]
    for p in path:
        node = node[p]
    return node
```

- [ ] **Step 3: Run the test to see it fail**

```bash
uv run pytest tests/e2e/test_demo_flow.py -v
```

Expected: fails because it exercises the full stack — expect it to actually **pass** if all prior tasks are correct. If it fails, the failure message tells you which stage is off.

- [ ] **Step 4: Run the full suite as a final check**

```bash
uv run pytest -v
```

Expected: all unit, integration, and e2e tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e
git commit -m "$(cat <<'EOF'
test(e2e): replay canned transcript, assert rehydration + no leakage

Replays a scripted tool_call/compute/final_text sequence through the
same wiring used by examples/demo_chat.py, asserts the rehydrated
final text, and asserts real salaries never appear in the
LLM-visible stream.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: README quick-start → Python

Update the `## Quick start` and `## Configuration` sections of the README so `npx blindfold …` becomes the Python idiom. Other README sections stay.

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: an accurate quick-start reflecting the MVP shape (Python, `uv`/`pipx`, `blindfold --` CLI, in-process library).

- [ ] **Step 1: Replace the `## Quick start` section in `README.md`**

Find the current block starting with `## Quick start` and ending before `## Configuration`. Replace it with:

````markdown
## Quick start

Blindfold ships as a Python package. Two ways to use it:

**As a CLI wrapping another stdio MCP server:**

```bash
# Install with pipx (isolated) or uv (project-local):
pipx install blindfold
# or:  uv add blindfold

# Wrap any stdio MCP server; blindfold reads ./blindfold.yaml if present:
blindfold --config blindfold.yaml -- python -m your_org.some_mcp_server
```

**As an in-process library (used by a harness you write):**

```python
from blindfold import rehydrate
from blindfold.core.vault import MemoryTokenStore
from blindfold.core.policy import SessionBoundPolicy

store = MemoryTokenStore()
policy = SessionBoundPolicy()
# ... your harness tokenizes tool results into `store`, then at the end:
final_text = rehydrate(llm_answer, session_id, store, policy)
```

Out of the box, nothing needs configuring: memory vault, session-bound authorization, subprocess sandbox. Configuration is something you discover when you need it, not a prerequisite. See `examples/demo_chat.py` for an Anthropic SDK loop end-to-end.
````

- [ ] **Step 2: Verify the config section still matches reality**

The existing `## Configuration` block already shows valid YAML. Leave it as-is except confirm that `mode: local` and `storage.backend: sqlite` are annotated as post-MVP if they are inaccurate; at MVP only `schemas:` and `tokens:` are consumed. Add this note at the end of the section:

```markdown
> **MVP note:** the current release consumes only the `schemas` and `tokens` sections. Other keys shown above are declarative for the roadmap and are safely ignored today.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): rewrite quick-start for Python entry-point

Replaces the aspirational `npx blindfold` quick-start with the actual
MVP surface: pipx-installed CLI wrapping a stdio MCP server, plus the
in-process library form used by the demo harness.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Final green run**

```bash
uv run pytest -v
```

Expected: everything green.

---

## Self-review

### Spec coverage (§ from `2026-07-15-blindfold-mvp-design.md`)

| Spec section | Covered by |
|---|---|
| §1 Scope, in-MVP | Tasks 1–14 (each in-MVP bullet has at least one task) |
| §1 Scope, out-of-MVP | not built (correct by design) |
| §1 README quick-start rewrite | Task 15 |
| §2 Architecture (3 layers, ports) | Tasks 2, 3–7 (core), 8 (sandbox), 11 (proxy) |
| §3 Package layout | All tasks that create a listed path |
| §4 Data model (Lineage, Policy, VaultRecord) | Task 2 |
| §4 Policy inheritance / TTL composition | Task 2 (compose_*), Task 9 (used in handler) |
| §4 Session model (one-proc-one-session) | Task 11 (`build_proxy_state` mints per-proc UUID) |
| §5 Configuration (schemas, tokens, extra tolerated) | Task 7 |
| §5 Path dialect (static + `[*]`) | Task 5 |
| §6.A Tokenize tool result | Task 5 (core), Task 11 (proxy wiring) |
| §6.B Blind compute (policy check → sandbox → mint) | Task 9 |
| §6.C Rehydrate | Task 6 |
| §6.D Injection of `blindfold_compute` | Task 9 (definition), Task 11 (injection into `tools/list`) |
| §6.E `blindfold/rehydrate` RPC | Task 11 |
| §7 Unit tests | Tasks 2, 3, 4, 5, 6, 7, 8, 9 |
| §7 Integration test | Task 11 (added), Task 12 (runs) |
| §7 E2E test | Task 14 |
| §7 Manual demo (`examples/demo_chat.py`) | Task 13 |
| §8 Non-goals reiterated | (nothing to build — carried forward in docs) |
| §9 Success criteria | Verified by Task 14 (all pytest green) + Task 15 (README) |

No gaps found.

### Placeholder scan

No "TBD", "TODO", "implement later", or "similar to Task N" strings appear in step bodies. Every step that changes code shows the full code.

### Type / name consistency

- Token regex: `⟦tok_[0-9a-f]{8}⟧` — used identically in `MemoryTokenStore.mint_token`, `TOKEN_PATTERN`, and every test that fullmatches it.
- `MemoryTokenStore.mint_token` — introduced in Task 3, referenced in Tasks 5 and 9.
- `SchemaField` — introduced in Task 5, consumed in Task 7 (`schema_fields_for`) and Task 11 (proxy). Shape is stable.
- `handle_blindfold_compute` signature — introduced in Task 9, called with matching kwargs in Task 11 and Task 13 and Task 14.
- `rehydrate` signature — introduced in Task 6, called with matching args in Task 11 and Task 13 and Task 14.
- `run_proxy(downstream_cmd, config_path)` — introduced in Task 11, called with matching args from Task 12 CLI.

All names and signatures cross-reference cleanly.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-15-blindfold-mvp.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
