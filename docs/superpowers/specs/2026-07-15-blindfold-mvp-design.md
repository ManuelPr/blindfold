# Blindfold MVP — Design

**Date:** 2026-07-15
**Status:** approved (pre-implementation)
**Companion:** [../../../README.md](../../../README.md) — the product vision. This spec is the *first slice* of it.

## 1. Scope

### In-MVP

- `blindfold` Python CLI that wraps a downstream stdio MCP server:
  `blindfold -- python -m fake_hr_mcp`
- Tokenizes fields declared in `blindfold.yaml` (`schemas:` block) inside every `tools/call` response before forwarding to the harness.
- Exposes an extra MCP tool `blindfold_compute(code: str, inputs: list[str])` that resolves tokens, runs code in a Python subprocess sandbox, stores the result as a new token with lineage, and returns that token.
- In-memory vault only. Session-bound policy: one wrapped subprocess = one session; tokens minted in session S cannot be resolved from any other session.
- Library helper `blindfold.rehydrate(text, session_id) -> str` and a demo harness (`examples/demo_chat.py`) using the Anthropic SDK + a fake HR MCP fixture to prove the whole flow end-to-end.
- Tests: unit (`core/`, `sandbox/`), integration (real proxy child process), e2e (replay of a canned LLM transcript).

### Explicitly out-of-MVP (documented, not built)

- SQLite / Redis / Postgres storage adapters (only in-memory).
- Encryption at rest (documented gap — in-memory only, no on-disk vault to encrypt yet).
- HTTP proxy mode.
- Collective (table) tokens.
- Inbound prompt tokenization (NER).
- Docker sandbox; hard network/filesystem isolation on Windows (subprocess sandbox does timeout + clean env + no explicit network but does not seccomp).
- Full JSONPath — MVP supports only static paths and one-level `[*]` wildcards.

### Note on the README

The README's quick-start uses `npx blindfold` (Node/TypeScript). This MVP is Python; the quick-start section of the README must be updated to reflect a Python entry-point (`pipx install blindfold` / `python -m blindfold`). Deferred to the docs pass at the end of MVP implementation.

## 2. Architecture

```
    Harness (demo_chat.py)
    ┌──────────────────────────────────┐
    │ Anthropic SDK ── messages loop   │
    │   ├── tool_use ─────────────────►│ ─stdio─►  blindfold  ─stdio─►  fake_hr_mcp
    │   ◄─ tool_result (tokenized) ────│ ◄──────  (tokenize/    ◄────  (real data)
    │   ...                             │           rehydrate)
    │   └── final assistant text       │
    │        │                         │
    │        └── blindfold.rehydrate() ┘
    │                                  ─► printed to user
    └──────────────────────────────────┘
```

Three logical layers inside the `blindfold` process:

1. **Proxy layer** (`blindfold/proxy.py`) — Speaks MCP stdio framing on both sides. Forwards messages one-to-one, with two interceptions:
   - injects the `blindfold_compute` tool into `tools/list` responses;
   - hands `tools/call` responses to the tokenizer before forwarding.
2. **Core** (`blindfold/core/`) — Pure, no I/O:
   - `vault.py` — `MemoryTokenStore` implementing the `TokenStore` port.
   - `tokenizer.py` — walks a JSON tool result against a schema, swaps sensitive paths for `⟦tok_…⟧`, mints vault records with `Lineage(op="tool_result", tool=…, path=…)`.
   - `rehydrator.py` — regex-scans a string for `⟦tok_…⟧`, calls policy, substitutes or flags.
   - `policy.py` — `SessionBoundPolicy` implementing the `DetokenizePolicy` port.
   - `lineage.py` — building lineage records + policy inheritance (min-across-inputs).
3. **Sandbox** (`blindfold/sandbox/subprocess_.py`) — Implements the `ComputeSandbox` port. Spawns `python -c` wrapper with empty env, timeout, JSON stdin/stdout.

**Ports live under `blindfold/ports/`** as Python ABCs (`TokenStore`, `DetokenizePolicy`, `ComputeSandbox`). MVP ships one implementation of each; post-MVP additions are strictly additive.

## 3. Package layout

```
blindfold/
├── README.md
├── pyproject.toml                 # uv/hatch, entry point: blindfold = "blindfold.cli:main"
├── blindfold.example.yaml
├── src/blindfold/
│   ├── __init__.py                # re-exports rehydrate()
│   ├── cli.py                     # argparse; `blindfold -- <cmd> <args...>`
│   ├── proxy.py                   # stdio MCP proxy (asyncio)
│   ├── config.py                  # pydantic model for blindfold.yaml
│   ├── core/
│   │   ├── __init__.py
│   │   ├── vault.py               # MemoryTokenStore
│   │   ├── tokenizer.py
│   │   ├── rehydrator.py
│   │   ├── policy.py              # SessionBoundPolicy
│   │   └── lineage.py
│   ├── ports/
│   │   ├── token_store.py         # ABC
│   │   ├── policy.py              # ABC
│   │   └── sandbox.py             # ABC
│   ├── sandbox/
│   │   ├── __init__.py
│   │   └── subprocess_.py         # SubprocessSandbox
│   └── tools/
│       └── blindfold_compute.py   # injected MCP tool definition + handler
├── examples/
│   ├── fake_hr_mcp/               # MCP server fixture: get_salary(name) -> {name, salary}
│   │   ├── __init__.py
│   │   └── __main__.py
│   ├── demo_chat.py               # Anthropic SDK loop, wraps fake_hr_mcp via blindfold
│   └── recorded_transcript.json   # canned LLM responses for e2e tests
└── tests/
    ├── unit/
    │   ├── test_vault.py
    │   ├── test_tokenizer.py
    │   ├── test_rehydrator.py
    │   ├── test_policy.py
    │   └── test_sandbox.py
    ├── integration/
    │   └── test_proxy_forwarding.py
    └── e2e/
        └── test_demo_flow.py
```

Conventions:
- `src/`-layout so tests can't import un-installed source.
- `uv` for env & dep management; `pyproject.toml` is the single source of truth.
- `subprocess_.py` trailing underscore to avoid shadowing stdlib.
- `pydantic` for config; `mcp` (official Python SDK) for the protocol.

## 4. Data model

```python
@dataclass(frozen=True)
class Lineage:
    op: str                          # "tool_result" | "blind_compute" | "literal"
    inputs: tuple[str, ...] = ()     # parent token ids
    code_digest: str | None = None   # sha256 of blind-compute code
    tool: str | None = None          # for tool_result
    path: str | None = None          # jsonpath for tool_result

@dataclass(frozen=True)
class Policy:
    reveal_to_frontend: bool = True  # controls rehydrate()
    can_be_input_to_compute: bool = True

@dataclass(frozen=True)
class VaultRecord:
    token: str                       # "tok_" + 8 hex chars
    value: Any                       # the real value; JSON-serializable
    dtype: str                       # "string" | "number" | "boolean" | "object"
    semantic_type: str | None        # from schema, e.g. "salary"
    unit: str | None                 # from schema, e.g. "EUR/year"
    session_id: str
    created_at: datetime
    ttl: datetime                    # created_at + config.tokens.default_ttl
    lineage: Lineage
    policy: Policy
```

**Policy inheritance** — a new record derived via blind compute has:
- `policy.reveal_to_frontend = min(p.reveal_to_frontend for p in inputs)` (any False wins).
- `policy.can_be_input_to_compute = min(...)` (any False wins).
- `ttl = min(r.ttl for r in inputs)`.
Encoded in `lineage.py`; called by the blind-compute handler.

**Session model** — the proxy assigns one `session_id` (UUID4) for the lifetime of the wrapped subprocess. Post-MVP this becomes per-user; MVP treats one process = one session.

## 5. Configuration

`blindfold.yaml` at MVP consumes two sections; unknown keys are tolerated for forward compatibility:

```yaml
schemas:
  hr_api.get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
tokens:
  default_ttl: 3600
```

Path dialect at MVP: static paths (`$.a.b.c`) and one-level `[*]` wildcards (`$.items[*].name`). No filters. Full JSONPath is post-MVP.

## 6. Key flows

### A. Tokenize a tool result

1. Proxy receives a `tools/call` response from the downstream MCP server.
2. Looks up the tool name in `config.schemas`. If absent → forward unchanged.
3. For each `sensitive_field.path`, resolves against the JSON `content`. For each match:
   - Mint `VaultRecord(lineage=Lineage(op="tool_result", tool=…, path=matched_path), session_id, dtype inferred from value, semantic_type/unit from schema)`.
   - Replace the value in the JSON tree with the token string `⟦tok_XXXX⟧`.
4. Forward the mutated response to the harness.

### B. Blind compute

1. Harness calls `blindfold_compute(code, inputs)` where `inputs: list[str]` are tokens the code references.
2. Handler calls `SessionBoundPolicy.can_compute` for every input. Reject with a structured error if any fails (wrong session or `can_be_input_to_compute=False`).
3. Resolve each token to its real value; build `{token: value}` dict.
4. Call `SubprocessSandbox.run(code, resolved)`:
   - Fresh `python` subprocess, empty env except `PATH`, `PYTHONIOENCODING=utf-8`.
   - Feed `{"code": …, "inputs": …}` on stdin.
   - Wrapper `exec`s the code inside a function whose locals include a `resolve(token)` helper backed by the pre-resolved dict — sandboxed code never talks to the vault directly. `resolve()` on a token *not* declared in `inputs` raises `KeyError` — the LLM must be explicit about which tokens the code will touch.
   - Read a JSON result from stdout. Kill after `timeout_s` (default 5s).
5. Mint a new `VaultRecord(lineage=Lineage(op="blind_compute", inputs=inputs, code_digest=sha256(code)), policy=min-across-inputs, ttl=min-across-inputs)`.
6. Return the new token as the tool result.

### C. Rehydrate

1. Regex-scan the LLM's final text for `⟦tok_[0-9a-f]{8}⟧`.
2. For each match, `vault.get(token)`:
   - Missing → replace with `[unknown token]`, log a warning (possible hallucination).
   - Found but `policy.reveal_to_frontend=False` → replace with `[redacted]`.
   - Otherwise substitute `str(value)`.
3. Return rehydrated text.

### D. Injection of `blindfold_compute`

- Proxy intercepts `tools/list` responses and appends a fixed tool definition (name, description, JSON schema for `code`+`inputs`).
- Tool description explicitly teaches the LLM the token protocol: "arguments referenced from `code` must be listed in `inputs`; return value must be JSON-serializable; you will get a token back, not a value".

## 7. Testing strategy

### Unit (`tests/unit/`) — pure, no I/O

| File | What it covers |
|---|---|
| `test_vault.py` | put/get/resolve; cascading invalidation; TTL expiry (freezegun); unknown-token returns None |
| `test_tokenizer.py` | static path; `[*]` wildcard; nested paths; missing paths (no-op); non-scalar sensitive value (whole object tokenized as `dtype=object`); schema not matching → passthrough |
| `test_rehydrator.py` | happy path; missing token; redacted token; malformed placeholder; tokens split across chunks; no false-positive on unicode brackets in unrelated text |
| `test_policy.py` | session isolation; `can_compute` deny reasons; min-policy composition |
| `test_sandbox.py` | happy path; timeout kills the child; non-JSON output → error; stdin/stdout roundtrip preserves unicode + numeric precision; syntax error surfaces as tool error, not silent success; `resolve()` on a token not listed in `inputs` raises a clear error |

### Integration (`tests/integration/`) — real subprocesses

- `test_proxy_forwarding.py` — starts `blindfold -- python -m examples.fake_hr_mcp` as a child, drives raw MCP JSON-RPC over stdio; asserts:
  - `tools/list` contains `blindfold_compute`;
  - `get_salary` response is tokenized;
  - a `blindfold_compute` call returns a derived token;
  - vault contains records with correct lineage.

### End-to-end (`tests/e2e/`) — no network

- `test_demo_flow.py` — replays `recorded_transcript.json` (canned Anthropic-shaped tool_use/tool_result/text messages), pipes through the demo path; asserts:
  - final rehydrated text equals the expected string;
  - the vault never leaked a real salary into anything the "LLM" saw (assert the recorded conversation only contains tokenized values).

### Manual demo — not run in CI

- `examples/demo_chat.py` for actually talking to Claude with real credentials. Documented in the README.

**Runner:** `pytest`. **Coverage aim:** ≥90% on `core/` and `sandbox/`, ≥70% overall. Signal, not gate.

**Deliberately not tested at MVP:** sandbox escape resistance (documented residual risk); vault concurrency (single-process at MVP; add locks with SQLite adapter).

## 8. Non-goals (reiterated for reviewers)

Everything in the README's "What it does NOT protect against" section still applies to this MVP: access control between users and their APIs, prompt-side leakage, inference-via-equality when `consistency: stable` is enabled, malicious/prompt-injected compute code (residual), and semantic judgment on hidden values (impossible by design).

Additionally for MVP only:
- No encryption at rest (memory-only, no on-disk vault to protect yet).
- No hard sandbox on Windows (best-effort subprocess isolation only).

## 9. Success criteria for MVP

- `pytest` passes (unit + integration + e2e).
- `python examples/demo_chat.py` (with `ANTHROPIC_API_KEY` set) runs a conversation where:
  - the user asks "who earns more, X or Y";
  - the model calls `get_salary` twice, then `blindfold_compute`;
  - the final printed answer names the correct person;
  - inspection of the transcript shows no real salary anywhere in the messages sent to Anthropic.
- `blindfold --help` is a self-explanatory CLI.
- README quick-start updated to Python.
