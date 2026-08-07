# Architecture

A developer's tour of how Blindfold actually works. Read this after the [README](../README.md) (which covers *why* the tool exists and what it promises) and before diving into the code.

- Reference spec: [`docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md`](superpowers/specs/2026-07-15-blindfold-mvp-design.md) — the formal MVP design.
- Known gaps: [`LIMITATIONS.md`](../LIMITATIONS.md) — what this document deliberately elides in favor of an idealized picture.

---

## 1. The problem, in one paragraph

When you connect an LLM to your private APIs via MCP tools, every tool response flows back into the model's context — and therefore to the LLM provider. Ask an agent *"What is Andrea's salary?"*, and the HR API's response (the actual number) is logged by Anthropic/OpenAI as part of the conversation. Traditional PII redaction proxies (Presidio, Philter) scrub the **user's prompt** before it reaches the model, but in agentic setups the sensitive payload is almost always in the tool response, not the prompt. That's the gap Blindfold covers.

## 2. Three ideas, in order

Blindfold's design sits on three ideas that stack.

### 2.1 Tokenize the tool response before it reaches the model

Every tool response is JSON. Sensitive fields — declared per-tool in `blindfold.yaml` — are replaced with opaque **token strings** (`⟦tok_…⟧`) before the response leaves the proxy. The real values live in a local **vault** — in memory, or in a SQLite file when they have to outlive the process or be read by another one. The model reasons over tokens; the real numbers never enter its context.

### 2.2 Blind compute — let the model operate on hidden values

The model cannot compare two opaque tokens. So Blindfold exposes an extra MCP tool called `blindfold_compute` that takes Python code and a list of input tokens. The proxy resolves those tokens to their real values, runs the code in a subprocess sandbox against the real values, stores the result as a **new** vault record (with lineage back to the inputs), and returns just the new token to the model. The model orchestrates computation on data it never sees.

### 2.3 Rehydrate at the last hop, gated by policy

When the model's final answer contains tokens, the harness calls `rehydrate(text, session_id, store, policy)` — which regex-scans for `⟦tok_…⟧`, checks each one against the session-bound policy, and substitutes real values only for the caller's own tokens. Missing tokens surface as `[unknown token]` (protection against hallucination); policy-denied tokens surface as `[redacted]` (protection against cross-session leakage).

Note the subject of that sentence: **the harness calls it.** Rehydration is a function invoked on the final text by code that owns the final text. This is the one idea of the three that Blindfold cannot perform on your behalf, and §3 explains what that costs in each mode.

## 3. Three integration modes

Blindfold ships as one Python package with three deployment surfaces. Which one fits your app depends on how your app already talks to its tools.

### Mode A: CLI proxy — `blindfold -- <mcp-server>`

For apps that already speak MCP as a client — Claude Desktop, Cursor, Windsurf, Zed, custom agents built on the `mcp` Python client SDK, and other frameworks that have added MCP support. You wrap the existing MCP server command with `blindfold --`:

```jsonc
// Before (e.g. in Claude Desktop's claude_desktop_config.json):
{
  "command": "python",
  "args": ["-m", "your_org.hr_mcp"]
}
// After:
{
  "command": "blindfold",
  "args": ["--config", "blindfold.yaml", "--", "python", "-m", "your_org.hr_mcp"]
}
```

No application code changes for the *protection* half: the proxy tokenizes `tools/call` responses, injects `blindfold_compute` into `tools/list`, and the LLM provider stops seeing values. That part genuinely is a config-file edit.

**The rehydration half does not come for free, and with a third-party client it does not come at all.** The proxy answers a custom JSON-RPC method, `blindfold/rehydrate` — custom meaning *this project invented it*. It is not in the MCP specification, so Claude Desktop, Cursor and Zed have no reason to call it, and they don't. Wrapped under one of those, the assistant's answer reaches the user as:

> The higher earner is ⟦tok_9c1bf051⟧.

There is no fix inside the proxy. It sits on the tool channel, below the client; the assistant's final message never passes through it, and MCP gives a server no hook on what the model says. Exposing rehydration as a normal tool would work mechanically and defeat the design, since a tool result lands in the model's context — precisely where the values must not go.

So Mode A is the right shape when hiding values from the provider is the whole goal and placeholders in the output are acceptable, or when the MCP client is yours and can be taught the extra method. When a human has to read the values, use Mode B.

### Mode B: In-process library — `from blindfold import ...`

For apps that call LLMs directly via SDK (Anthropic, OpenAI, Gemini, self-hosted via LiteLLM/vLLM/Ollama, and any framework built on those — LangChain, LlamaIndex, Haystack, custom loops) — the majority of enterprise Python agents. You import the pieces you need and call them at four points in your existing tool loop:

```python
from blindfold import rehydrate
from blindfold.core.vault import MemoryTokenStore
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.tokenizer import tokenize_result, SchemaField
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import build_tool_definition, handle_blindfold_compute

store = MemoryTokenStore()
policy = SessionBoundPolicy()
sandbox = SubprocessSandbox()
session_id = f"user_{user_uuid}"
fields = [SchemaField(path="$.salary", semantic_type="salary", unit="EUR/year")]
```

Four integration points in your loop:

1. **Advertise `blindfold_compute` to the model.** Add `build_tool_definition()` to the tools list you pass to the LLM SDK.
2. **Tokenize every real tool result.** When the model calls one of your normal tools, run `tokenize_result(payload, tool_name, fields, store, session_id, ttl)` on the response before feeding it back as `tool_result`.
3. **Route the model's `blindfold_compute` calls.** When the tool_use name is `blindfold_compute`, call `handle_blindfold_compute(args, store=..., policy=..., sandbox=..., session_id=..., ttl_seconds=...)` and return its result as a normal tool_result.
4. **Rehydrate before display.** When the model produces its final text, call `rehydrate(final_text, session_id, store, policy)` and print that.

Framework-agnostic, LLM-agnostic. Works with any provider that supports tool use / function calling. See [`examples/demo_chat.py`](../examples/demo_chat.py) for the full pattern against the Anthropic SDK — swapping in OpenAI or Gemini is a matter of changing the SDK client and the tool_result payload shape.

### Mode C: Claude Code plugin — three hooks, no proxy

The host offers its own seams, and they turn out to be a better fit than the
protocol's. [`src/blindfold/hooks.py`](../src/blindfold/hooks.py) implements three
handlers, wired by [`plugin/hooks/hooks.json`](../plugin/hooks/hooks.json) to
`blindfold hook <event>`:

- **`PostToolUse`** returns `updatedToolOutput`, which replaces the tool result
  *the model receives*. Same job as `_tokenize_tool_call_result` in the proxy,
  except it covers every tool the host has rather than one stdio MCP server.
  The result text arrives one of two shapes depending on the tool: built-ins
  (`Bash`, `Edit`) send a flat string under `tool_output`; MCP tools send
  `tool_response`, a list mirroring MCP's own `[{"type": "text", "text": ...}]`
  content shape. Both are read; anything else is treated as absent, so the
  call blocks rather than guesses.
- **`MessageDisplay`** fires on every assistant message as it streams. The text
  to rewrite arrives under `delta` — "the newly completed lines" of the
  message, not the whole thing under `message_text` as the general hook docs
  would suggest for other events; that mismatch cost real debugging time (see
  [`LIMITATIONS.md`](../LIMITATIONS.md)) before the host's own `/hooks`
  inspector settled it. The handler returns `displayContent`, which replaces
  *what the screen shows* while the transcript keeps the original. That is
  rehydration, with a property the proxy cannot offer: the user reads real
  values and the conversation keeps the placeholders, so nothing re-enters the
  model's context on the next turn.
- **`SessionStart`** returns `additionalContext`, injected before the first
  prompt. It carries `describe_config(config)` — every protected path with its
  meaning, plus the instruction to reproduce placeholders verbatim.

Two things Mode A does by editing the `tools/list` response cannot be done here
at all: **no hook can add a tool, and none can edit a tool description.** So the
schema briefing moved to `SessionStart`, and `blindfold_compute` had to become a
real MCP server — [`src/blindfold/mcp_server.py`](../src/blindfold/mcp_server.py),
declared in [`plugin/.mcp.json`](../plugin/.mcp.json). Without it the model can
read placeholders and do nothing with them.

That server has one problem the proxy and the library do not: **it does not know
whose session it is serving.** MCP connections carry no session identity. So it
reads the session off the input tokens, refuses to mix two, and mints the result
into that same session — which is what lets `MessageDisplay`, bound to the
host's session, reveal the result instead of rendering `[redacted]`. The
trade-off is explicit: possession of a token is taken as proof of belonging to
its session.

Two properties this mode forced into the design:

**A shared vault is not optional.** Each hook invocation is a separate process.
A token minted by `PostToolUse` must still resolve when `MessageDisplay` runs
seconds later from somewhere else, so the CLI refuses to run the hooks unless
`storage.backend` is `sqlite`. This is what made the persistent store a
prerequisite rather than a convenience.

**Failure blocks rather than passes.** Printing nothing tells the host to keep
what it had — for `PostToolUse` that is the untokenized result on its way to
the model. So a tool with declared fields whose result cannot be tokenized is
blocked with `{"decision": "block"}`. `MessageDisplay` is the opposite: nothing
leaks if it does nothing, so it stays quiet on failure and the user sees a
placeholder. The asymmetry is deliberate and lives in `run_hook`.

### Which mode do you need?

| Your setup | Mode |
|---|---|
| Claude Code | C |
| Claude Desktop / Cursor / Windsurf / Zed + stdio MCP server | A — with placeholders in the user-visible answer (see above) |
| Custom agent that already speaks MCP via `mcp` Python SDK | A if you add the `blindfold/rehydrate` call, otherwise B |
| Enterprise agent using Anthropic / OpenAI / Gemini SDK directly | B |
| LangChain / LlamaIndex / Haystack | B |
| Self-hosted LLM (Ollama, vLLM, LiteLLM) | B |
| Multi-provider gateway (Portkey, LangSmith proxy, etc.) | B, wrapped once at your gateway layer |

**Same core, three seams.** The `Rehydrator`, `Tokenizer`, `MemoryTokenStore`/`SQLiteTokenStore`, `SessionBoundPolicy`, `SubprocessSandbox`, and `blindfold_compute` handler are the same objects in all three modes; only the transport around them differs. The proxy in Mode A is a thin adapter translating MCP JSON-RPC into calls on the same library objects Mode B uses directly, and Mode C's hooks (`src/blindfold/hooks.py`) are the same again, called by the host instead of a proxy loop.

## 4. The system, one component per file

Every component lives in exactly one file. Ports are ABCs so alternative implementations are additive.

### Data model — [`src/blindfold/core/lineage.py`](../src/blindfold/core/lineage.py)

Three frozen dataclasses:

- **`VaultRecord`** — the full record for one token: value, dtype, semantic_type, unit, session_id, created_at, ttl, lineage, policy.
- **`Lineage`** — where a record came from: `op` (`tool_result` / `blind_compute` / `literal`), `inputs` (parent token IDs), `code_digest` (sha256 of the compute code), `tool` and `path` for tool-result origins.
- **`Policy`** — two booleans: `reveal_to_frontend` (governs `rehydrate`), `can_be_input_to_compute` (governs `blindfold_compute`).

Plus two pure composition helpers:
- `compose_policy(inputs) -> Policy` — AND-composition: any restrictive input wins.
- `compose_ttl(inputs) -> datetime` — shortest surviving TTL wins.

These are called by the blind-compute handler when it mints a derived record, so a computation cannot launder a restrictive policy or extend a short TTL.

### Ports (interfaces)

Small ABCs, one per orthogonal concern. Only one implementation of each ships at MVP; alternatives are strictly additive.

- **[`src/blindfold/ports/token_store.py`](../src/blindfold/ports/token_store.py)** — `TokenStore`: `mint_token`, `put`, `get`, `resolve`, `find_by_session`, `invalidate_cascade`, `purge_expired`. `mint_token` lives on the port because the delimiters and hex width are a contract with the rehydrator's regex, not a property of where records are kept.
- **[`src/blindfold/ports/policy.py`](../src/blindfold/ports/policy.py)** — `DetokenizePolicy` with `can_reveal` / `can_compute`, and a small `DetokenizeContext` dataclass.
- **[`src/blindfold/ports/sandbox.py`](../src/blindfold/ports/sandbox.py)** — `ComputeSandbox.run(code, inputs, timeout_s)` and the `SandboxError` exception.

### Vault — [`src/blindfold/core/vault.py`](../src/blindfold/core/vault.py)

`MemoryTokenStore` — the only `TokenStore` in the MVP. Backed by a plain `dict[str, VaultRecord]`. Lazy TTL expiry on `get`/`resolve`; eager on `purge_expired`. `invalidate_cascade` runs a fixpoint sweep over `lineage.inputs` to remove all descendants of an invalidated token.

**`put` sweeps expired records on an interval** (`purge_interval_s`, 60 seconds by default), so expiry frees memory instead of only hiding records from `get`. It sits in the store rather than in the proxy so Mode B, which constructs its own store, gets it too. Amortized, so a record can outlive its TTL by up to one interval — set `purge_interval_s` lower if that matters.

### Persistent vault — [`src/blindfold/core/sqlite_store.py`](../src/blindfold/core/sqlite_store.py)

`SQLiteTokenStore` — the same port, backed by a file instead of a dict. Selected with `storage.backend: sqlite`; `build_token_store(config)` returns one or the other, and nothing else in the system knows which it got. Standard-library `sqlite3`, WAL journaling so two processes can share one file, an index on the TTL column so expiry sweeps are not full scans.

It exists for two reasons, and the second is the one that shaped it. Longevity is the obvious one: a vault that dies with the process leaves yesterday's placeholders pointing at nothing. **Cross-process sharing** is the one that unlocks new integrations — tokenizing and rehydrating need not happen in the same program, and a host that runs each callback in a fresh process cannot work at all with a memory vault, whatever the TTL.

Both stores are held to one behavioural suite, [`tests/unit/test_token_store_conformance.py`](../tests/unit/test_token_store_conformance.py), which touches the public interface only.

**The file holds cleartext.** Encryption at rest is not implemented, and `encrypt_at_rest: true` is refused at load rather than ignored. The store narrows file permissions to owner-only where the platform honors that. See [`LIMITATIONS.md`](../LIMITATIONS.md#storage).

**`invalidate_cascade` is still not called by the runtime** — it is API surface for your code, not automatic behavior.

### Policy — [`src/blindfold/core/policy.py`](../src/blindfold/core/policy.py)

`SessionBoundPolicy` — the strictest MVP default. A record minted in session `S` can only be revealed or computed on from session `S`. Even a guessed token ID is unresolvable across sessions. Both `can_reveal` and `can_compute` also honor the record's own `Policy` flags.

### Tokenizer — [`src/blindfold/core/tokenizer.py`](../src/blindfold/core/tokenizer.py)

The heart of Blindfold's schema-driven approach:

1. Deep-copies the payload (never mutates the original).
2. For each `SchemaField(path, semantic_type, unit)` from the config, walks the JSONPath against the copy.
3. For each match: mints a token, builds a `VaultRecord` with `_infer_dtype(value)` (`bool`/`int`/`float`/`str` → `boolean`/`number`/`string`; anything else → `object`), calls `store.put`, then replaces the value in the tree with the token string.
4. Returns the mutated copy.

The JSONPath dialect is intentionally minimal at MVP, though not as minimal as this document previously claimed: static keys (`$.a.b`), list wildcards at any depth including nested ones (`$.a[*].b[*].c` descends correctly — `_walk` recurses), and explicit numeric indices (`$.items[0].name`). No filters, no recursive descent (`$..salary`), no slicing — see [`LIMITATIONS.md`](../LIMITATIONS.md).

**Paths that don't match are no-ops.** Declare paths defensively — the cost is zero and it protects against unexpected response shapes.

**Paths that could never match are refused.** `validate_path` runs wherever a `SchemaField` is created: from `blindfold.yaml` through a Pydantic validator on `SensitiveFieldConfig`, and inside the dataclass itself so Mode B is covered without the YAML. It rejects recursive descent, filters, slices, quoted keys and unbalanced brackets — all of which the walker would otherwise reinterpret rather than honor, `$..salary` quietly becoming `$.salary`. The two behaviors are deliberately different: "did not match this response" is intended, "cannot mean what you wrote" is a configuration bug and fails at startup.

### Rehydrator — [`src/blindfold/core/rehydrator.py`](../src/blindfold/core/rehydrator.py)

Regex `⟦tok_[0-9a-f]{8}⟧` over the text. For each match:

- `store.get(token)` returns `None` → substitute `[unknown token]` (model hallucinated or record expired).
- Policy denies `can_reveal` → substitute `[redacted]`.
- Otherwise → substitute `str(record.value)`.

Publicly re-exported as `from blindfold import rehydrate` for harness use.

### Sandbox — [`src/blindfold/sandbox/subprocess_.py`](../src/blindfold/sandbox/subprocess_.py)

`SubprocessSandbox` — the compute isolation layer. Given `(code, inputs, timeout_s)`:

1. Spawns `python -I -c <wrapper>` — a fresh, isolated interpreter (no `site-packages`, no `PYTHONPATH`).
2. Clean env: only `PATH` and `PYTHONIOENCODING=utf-8` — nothing inherited.
3. Hard timeout — child is killed via `subprocess.run(..., timeout=timeout_s)`.
4. Feeds `{"code", "inputs"}` as a single JSON line on stdin.
5. The child wrapper `exec`s the user code inside a scope that exposes `resolve(token)` — a closure over the resolved `inputs` dict that raises `KeyError` if the code references an unlisted token.
6. The user code MUST assign to a variable named `result`; the wrapper serializes `result` to JSON on stdout.
7. Parent reads stdout, parses the envelope, returns the value.

Every failure path — timeout, malformed output, syntax error, unassigned `result`, non-serializable `result`, unlisted-token lookup — surfaces as a single `SandboxError`.

**That message is a second way out of the sandbox, so it is deliberately uninformative.** It names the exception's *type* and nothing else: `raise ValueError(resolve("⟦tok_…⟧"))` returns `ValueError`, not the value it used to return. Child stdout and stderr never reach the caller either — they are printed to the operator's own stderr, where the model cannot see them. The envelope is also checked for shape rather than just parsed, since a bare `print(62000)` from user code leaves a last line that is valid JSON.

Step 5's exec scope carries an **allow-list instead of the full builtins**: aggregation functions, value types and a few exception types. `open`, `__import__`, `eval`, `exec`, `getattr`, `type` and `globals` are simply not names in that namespace, so the direct routes to the filesystem and the network are gone — `os.listdir(".")` returns `ImportError`, `open(...)` returns `NameError`. Step 2's environment cleanup was never doing that job: it removes inherited variables and `site-packages`, not file access.

This raises the cost of an escape rather than preventing one. Reaching `object.__subclasses__()` through the object graph needs no builtins at all. Real isolation is an operating-system question, which is what the Docker adapter is for.

[`LIMITATIONS.md`](../LIMITATIONS.md#sandboxing) has the full list, each entry marked with whether it can be closed and at what cost.

### Collective tokens — [`src/blindfold/core/table.py`](../src/blindfold/core/table.py) and [`src/blindfold/tools/blindfold_table.py`](../src/blindfold/tools/blindfold_table.py)

A list declared under `tables:` is replaced by **one** token whatever its length, and the record carries a `TableSchema` — column names, semantic types, units — so the token describes itself rather than depending on a config that may have changed since it was minted. The model is told those columns in the tool description and queries them with `blindfold_table`.

`run_query` applies a fixed set of operations: `filter`, `sort_by`, `limit`, `select`, then optionally one of `sum`, `mean`, `min`, `max`, `count` last. A row result comes back as another table token carrying the columns that survived a `select`, so a query can be built in steps.

**The invariant that makes this worth its cost:** no query may fail because of the data. Comparisons across types do not match rather than raising; sorting mixed types uses a total order; an aggregate over a column with no numbers returns nothing rather than erroring. `ValueError` is reserved for a malformed *query* — an unknown column, an operation out of place. This is what removes the one-bit oracle that arbitrary Python opens, and the reason this path executes no model-written code and runs no sandbox.

### `blindfold_compute` tool — [`src/blindfold/tools/blindfold_compute.py`](../src/blindfold/tools/blindfold_compute.py)

The MCP tool the LLM actually calls. Two exports:

- `build_tool_definition()` — returns the tool spec (name, description, JSON schema for `code` + `inputs`). The description teaches the model the protocol: "every token you resolve must be listed in `inputs`; assign to `result`; result must be JSON-serializable; you get back a new token, not a value".
- `handle_blindfold_compute(args, *, store, policy, sandbox, session_id, ttl_seconds, max_calls_per_token, rate_window_s)` — the orchestrator:
  1. Validate `args` shape.
  2. For each input token: fetch the record, run `policy.can_compute` — reject on failure.
  3. **Rate check**: for each input token, count how many `blind_compute` records already in the vault (for this session, within the last `rate_window_s` seconds) list it in `lineage.inputs`. Refuse if any token is at or past `max_calls_per_token` — no new counter, just a scan over records already being written.
  4. Build `{token: value}` dict.
  5. Call the sandbox.
  6. Mint a new record with `lineage.op="blind_compute"`, `lineage.inputs=tuple(inputs)`, `lineage.code_digest=sha256(code)`, `policy=compose_policy([...])`, `ttl=compose_ttl([...])`.
  7. Return the new token.

The composed policy and TTL are why the model cannot launder sensitive data through a computation: derived tokens are at least as restricted as their most restrictive input.

**Why the rate check is a window, not a lifetime count:** the same token legitimately shows up as compute input many times across a real session — the same salary compared against different thresholds, hours apart. A flat cap on total reuse would break that on the first ordinary session that used a value more than a couple of times. A *rate* limit lets spread-out reuse through untouched while catching a burst — which is what the one-bit oracle in [`LIMITATIONS.md`](../LIMITATIONS.md#blind-compute-answers-one-bit-per-call-whatever-the-sandbox) actually looks like: many calls, same token, close together. It does not close the channel — a patient attacker still gets there at one probe per window — but it turns a silent, instant extraction into a slow one with an unmistakable pattern already sitting in the vault's own lineage data. Wired into both callers that reach this handler — `mcp_server.compute()` (Mode C) and `proxy._handle_blindfold_compute` (Mode A) — from the same `config.compute` values, so the limit does not depend on which mode is running.

### Config — [`src/blindfold/config.py`](../src/blindfold/config.py)

Pydantic v2 models for `blindfold.yaml`. Five top-level sections: `schemas` (which tools have which sensitive fields, and which tables), `resources` (the same, keyed by URI glob for MCP resources), `tokens.default_ttl` (how long records live by default), `storage` (backend, path, encryption), and `compute` (`max_calls_per_token`/`rate_window_s`, the rate limit on `blindfold_compute` described below). Unknown top-level keys are tolerated via `extra="allow"` so future config additions do not break older Blindfold binaries. `schema_fields_for(config, tool_name)` hands the tokenizer its `SchemaField` list.

### Proxy — [`src/blindfold/proxy.py`](../src/blindfold/proxy.py)

The wire orchestrator. `run_proxy(downstream_cmd, config_path)`:

1. Loads the config.
2. Builds a `ProxyState`: fresh `MemoryTokenStore`, `SessionBoundPolicy`, `SubprocessSandbox`, and a unique `session_id` (UUID4).
3. Spawns the downstream MCP server as a subprocess with piped stdin/stdout.
4. Runs two concurrent asyncio tasks:
   - **Client → child**: reads a line from our stdin, parses JSON-RPC, then dispatches:
     - `blindfold/rehydrate` → answer locally using the rehydrator.
     - `tools/call` where `params.name == "blindfold_compute"` → answer locally using the compute handler.
     - Anything else → record the tool name (indexed by JSON-RPC `id`) and forward verbatim to the child.
   - **Child → client**: reads a line from the child's stdout, and:
     - If it's a `tools/list` response → append `build_tool_definition()` to the tools array.
     - If it's a `tools/call` response with a recorded ID → call the tokenizer on any `text` content, matching against the config.
     - If it's a `resources/read` response with a recorded ID → same, matching the returned part's URI against the `resources:` globs. Resources carry data exactly as tool results do, and used to pass through untouched.
     - Forward the (possibly-mutated) message to our stdout.
5. Shuts down cleanly when either side closes.

**Windows note:** `asyncio.connect_read_pipe(sys.stdin)` doesn't work on Windows' ProactorEventLoop. The proxy uses `asyncio.to_thread(stdin.buffer.readline)` and synchronous `stdout.buffer.write + flush` instead. Portable, small enough to reason about, no platform detection needed.

### Audit — [`src/blindfold/audit.py`](../src/blindfold/audit.py)

Answers the question a screenshot cannot: did any hidden value actually reach the model, in a *real* conversation. `audit(transcript, store, session_id)`:

1. Finds every `⟦tok_…⟧` placeholder in the transcript text (`TOKEN_PATTERN.findall`), and every vault record for the session (`store.find_by_session`).
2. For each record, searches the transcript for its value as text — every scalar inside it if the value is a table row or nested structure (`_searchable`). A hit is a leak, reported with the token and its `semantic_type`.
3. Values under `MIN_INTERESTING` (5) characters are skipped and counted separately — `"Eng"` or `"1"` occurs in any transcript for reasons unrelated to a leak.
4. **`blind_compute`-lineage records get one more check before being called a leak**: `_literals_the_model_already_wrote` scans the transcript for string literals inside any `blindfold_compute` call's own `code` argument (its JSON-escaped text, unescaped and searched for quoted substrings). A match there means the model typed that text itself — choosing between two already-known names based on a hidden comparison, say — not that it came out of the vault. Reported separately as `Report.explained`, not silently dropped, so a wrong exclusion is still visible. A value actually produced by `resolve(...)` arithmetic, never typed as a literal anywhere, is unaffected.

`read_transcript(path)` re-serializes each JSONL line (`json.dumps(json.loads(line), ensure_ascii=False)`) so a placeholder written escaped by one process and literally by another is found either way; a non-`.jsonl` file is read as plain text, so a log captured by hand works too. `session_ids_in(path)` reads the `sessionId` field Claude Code's own transcripts carry, so the caller does not have to know one. Exposed as `blindfold audit <transcript> [--session ID]`.

### CLI — [`src/blindfold/cli.py`](../src/blindfold/cli.py)

A thin argparse layer. `blindfold [--config PATH] -- <cmd> [args...]`: parses the pre-`--` options, treats everything after `--` as the downstream command, calls `run_proxy`. `blindfold hook <event>` reads a JSON hook event from stdin and dispatches to [`hooks.py`](../src/blindfold/hooks.py) (Mode C). `blindfold mcp-server` starts the `blindfold_compute`/`blindfold_table` MCP server (Mode C, see [`mcp_server.py`](../src/blindfold/mcp_server.py)). `blindfold audit <transcript>` runs the check above. Also exposed as `python -m blindfold` via [`__main__.py`](../src/blindfold/__main__.py). `main()` reconfigures `stdin`/`stdout`/`stderr` to UTF-8 before anything else runs — Windows' default console codepage cannot represent the token delimiters, and a binary a host invokes by bare command name never gets `PYTHONIOENCODING` set for it.

## 5. End-to-end example: "Who earns more, Manuel or Andrea?"

Setup: `fake_hr_mcp` (in [`examples/fake_hr_mcp/`](../examples/fake_hr_mcp/)) hard-codes `Manuel Pernigotto → 62000`, `Andrea Tuscano → 71000`. Config declares `$.salary` as sensitive on `get_salary`.

The harness in this trace is one you wrote — [`examples/demo_chat.py`](../examples/demo_chat.py) is the running version of it. Frames 1–6 play out identically under a third-party MCP client; frame 7 is the one that requires your own code, and the reason the trace is written this way.

### Frame 1: user asks, harness lists tools
```
user       → harness: "Who earns more, Manuel Pernigotto or Andrea Tuscano?"
harness    → Anthropic: messages + tools=[get_salary, blindfold_compute]
```
`blindfold_compute` is in the tool list because the proxy injected it into the `tools/list` response.

### Frame 2: model asks for Manuel's salary
```
Claude     → harness: tool_use(get_salary, {"name": "Manuel Pernigotto"})
harness    → proxy:   tools/call get_salary("Manuel Pernigotto")
proxy      → child:   (forwarded)
child      → proxy:   {"content":[{"type":"text","text":'{"name":"Manuel Pernigotto","salary":62000}'}]}
```

The proxy sees the `tools/call` response, finds `get_salary` in the pending-calls map, and calls the tokenizer:
```
tokenizer:
  match $.salary → 62000
  mint token: ⟦tok_7f3a1b2c⟧
  vault.put({token, value: 62000, dtype: number, semantic_type: salary, unit: EUR/year, session: sess_abc, ...})
  return {"name": "Manuel Pernigotto", "salary": "⟦tok_7f3a1b2c⟧"}
```

```
proxy      → harness: response with tokenized salary
harness    → Anthropic: tool_result = '{"name":"Manuel Pernigotto","salary":"⟦tok_7f3a1b2c⟧"}'
```

**Anthropic has never seen 62000.**

### Frame 3: model asks for Andrea's salary
Same round-trip. Now the vault has two records; Anthropic has seen two token strings.

### Frame 4: model needs to compare, calls blindfold_compute
```
Claude     → harness: tool_use(blindfold_compute, {
  "code": "result = 'Manuel Pernigotto' if resolve('⟦tok_7f3a1b2c⟧') > resolve('⟦tok_2d81e9f4⟧') else 'Andrea Tuscano'",
  "inputs": ["⟦tok_7f3a1b2c⟧", "⟦tok_2d81e9f4⟧"]
})
```

### Frame 5: handler executes
The proxy sees `params.name == "blindfold_compute"` in the incoming `tools/call` and routes to `handle_blindfold_compute` (never forwards to the child). It:
1. Verifies both tokens exist, matching session, `can_compute` passes.
2. Builds `{tok_7f3a1b2c: 62000, tok_2d81e9f4: 71000}`.
3. Calls sandbox → subprocess `python -I` → `exec` the code → `62000 > 71000` is `False` → `result = 'Andrea Tuscano'` → stdout `{"ok": true, "value": "Andrea Tuscano"}`.
4. Mints `⟦tok_9c1bf051⟧` with `lineage.op="blind_compute"`, `lineage.inputs=(tok_7f3a1b2c, tok_2d81e9f4)`, `lineage.code_digest=sha256(code)`.
5. Returns the new token.

```
proxy      → harness: {"content":[{"type":"text","text":"⟦tok_9c1bf051⟧"}]}
harness    → Anthropic: tool_result = "⟦tok_9c1bf051⟧"
```

**Anthropic saw: a tool call with source code + two token literals, and a token literal in the response. No numbers, no names.**

### Frame 6: model writes its answer
```
Claude     → harness: "The higher earner is ⟦tok_9c1bf051⟧."
```

### Frame 7: rehydration
The harness calls `rehydrate("The higher earner is ⟦tok_9c1bf051⟧.", session_id=sess_abc, store, policy)`:
- Regex finds `⟦tok_9c1bf051⟧`.
- `store.get` returns the record.
- `policy.can_reveal(ctx=sess_abc, record)` → `True`.
- Substitute → `"The higher earner is Andrea Tuscano."`

Printed to the user: **"The higher earner is Andrea Tuscano."**

Under Claude Desktop or Cursor, with nothing making that call, the last line reads **"The higher earner is ⟦tok_9c1bf051⟧."** instead. Same protection, no delivery.

## 6. Package layout at a glance

```
src/blindfold/
├── __init__.py                # re-exports PLACEHOLDER_PROMPT, describe_config, describe_schema, rehydrate
├── __main__.py                # enables `python -m blindfold`
├── cli.py                     # argparse; proxy / hook / mcp-server / audit subcommands
├── proxy.py                   # asyncio stdio MCP proxy (Mode A)
├── hooks.py                   # SessionStart / PostToolUse / MessageDisplay handlers (Mode C)
├── mcp_server.py               # blindfold_compute / blindfold_table as an MCP server (Mode C)
├── audit.py                   # cross-reference a transcript against the vault
├── config.py                  # pydantic BlindfoldConfig
├── core/
│   ├── lineage.py             # data model + composition helpers
│   ├── vault.py               # MemoryTokenStore
│   ├── sqlite_store.py        # SQLiteTokenStore
│   ├── tokenizer.py           # SchemaField + tokenize_result + JSONPath
│   ├── table.py               # collective tokens: the query operations
│   ├── rehydrator.py          # rehydrate + TOKEN_PATTERN + PLACEHOLDER_PROMPT
│   └── policy.py              # SessionBoundPolicy
├── ports/
│   ├── token_store.py         # TokenStore ABC
│   ├── policy.py              # DetokenizePolicy ABC + DetokenizeContext
│   └── sandbox.py             # ComputeSandbox ABC + SandboxError
├── sandbox/
│   └── subprocess_.py         # SubprocessSandbox
└── tools/
    ├── blindfold_compute.py   # tool spec + handler + compute rate limit
    └── blindfold_table.py     # query a collective token, no sandbox
```

Tests mirror the layout under `tests/unit/`, plus `tests/integration/test_proxy_forwarding.py` (spawns the real proxy) and `tests/e2e/test_demo_flow.py` (replays a canned transcript and asserts no leakage).
