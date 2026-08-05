# Blindfold

> A privacy proxy for LLM tool calls. Your private APIs' data never enters the model's context — the LLM reasons over anonymous tokens, computes blindly on data it cannot see, and real values are restored only at the last hop, after an authorization check.

*(Working name — subject to change.)*

**Status:** pre-alpha. The MVP is built and covered by tests; everything beyond it is design, not code.

This README describes both, so it marks which is which:

- Unmarked prose describes **what ships today**.
- **[planned]** marks something that is designed but not implemented. It is not in the package; do not rely on it.

[`LIMITATIONS.md`](LIMITATIONS.md) is the authoritative inventory of the distance between the two. It separates *temporary implementation gaps* (someone can close them) from *structural limits* (no amount of work removes them). Read it before pointing this at real data.

Feedback and contributions welcome — see [Contributing](#contributing).

---

## The problem

When you connect an LLM (Claude, GPT, Gemini…) to your internal APIs via tool calling or MCP, every tool result flows back into the model's context. Ask an agent *"What is Andrea's salary?"* and the HR API's response — the actual salary — is sent to the LLM provider as part of the conversation.

Existing PII-redaction proxies (Presidio, Philter, LLM Guard, …) solve a different problem: they scrub the **user's prompt** before it reaches the model. But in agentic setups the sensitive data usually isn't in the prompt — it's in the **tool results** coming back from your private APIs. That gap is what Blindfold covers.

## What it does

Blindfold sits between your agent harness and your APIs (or MCP servers). It:

1. **Tokenizes tool results.** Sensitive fields — declared per-tool in a schema — are replaced with typed anonymous tokens before the result reaches the LLM. The real values stay in a local vault — in memory by default, or in a SQLite file when the vault has to survive a restart or be shared between processes. That file holds cleartext unless you set `encrypt_at_rest` and supply a key from outside it.
2. **Tells the LLM what the tokens mean, not what they are.** Each protected tool's description gains one line per declared path — `$.salary — salary, EUR/year` — so the model knows what it is manipulating even when the upstream API names its fields `f_42`. Sent once, with the tool definitions; never per token, never the value.
3. **Enables blind compute.** The LLM can operate on tokens without reading them: `blindfold_compute` runs code it submits in a sandbox on the real values, and `blindfold_table` applies a fixed set of operations to a whole hidden list. Either way the result is stored as a *new* token with full lineage, and the model orchestrates computations on data it never sees.
4. **Rehydrates at the last hop.** Tokens in the model's answer are replaced with real values only when the response is delivered to the end user — after a pluggable authorization check. **This step is yours to call.** It happens in your application code, on the answer the model produced; a third-party MCP client will not do it for you (see [Quick start](#quick-start) and [`LIMITATIONS.md`](LIMITATIONS.md#rehydration-requires-a-client-you-control)).

```
┌────────────┐   answer with real values    ┌─────────────────────┐
│  Frontend  │◄─────────────────────────────│  Blindfold           │
└────────────┘                              │  ┌───────────────┐  │
      │                                     │  │ token vault   │  │
      ▼ prompt                              │  │ (memory or    │  │
┌────────────┐   tool call                  │  │  ephemeral,   │  │
│  Harness   │────────────────────────────► │  │  lineage DAG) │  │
│  + LLM     │◄─────────────────────────────│  └───────────────┘  │
│  provider  │   result with ⟦tokens⟧       └──────────┬──────────┘
└────────────┘                                         │ real values
   sees tokens only                                    ▼
                                              ┌─────────────────┐
                                              │  Your private   │
                                              │  APIs / MCP     │
                                              └─────────────────┘
```

## Example

**User:** *"Who earns more, Manuel Pernigotto or Andrea Tuscano?"*

1. The LLM calls `hr_api.get_salary` twice. Blindfold intercepts both results and returns `{"name": "Manuel Pernigotto", "salary": "⟦tok_7f3a⟧"}` and the same shape for Andrea. The model already knows from the tool's description that `$.salary` is a salary in EUR/year and that it cannot read it.
2. The LLM cannot compare what it cannot see — so it submits a blind-compute request:
   ```python
   result = "Manuel Pernigotto" if resolve("⟦tok_2d81⟧") > resolve("⟦tok_7f3a⟧") else "Andrea Tuscano"
   ```
3. Blindfold executes it in a sandbox on the real values and returns a **new token** — `⟦tok_9c1b⟧`, and nothing else. The vault keeps what the model doesn't get: the value, its dtype, and the lineage back to `tok_7f3a` and `tok_2d81` through this exact code.
4. The LLM answers: *"The higher earner is ⟦tok_9c1b⟧."*
5. Your application calls `rehydrate()` before showing the answer: *"The higher earner is Manuel Pernigotto."* (Step 5 is the one an MCP client you did not write will skip — it would show the placeholder.)

The LLM provider saw two opaque tokens, a snippet of comparison code, and a third opaque token. It never learned a salary — and not even who earns more.

## How it works

### Typed tokens with lineage

Every vault entry is a full record, not a bare key-value pair:

```json
{
  "token": "tok_9c1b",
  "value": "<held in the vault, never serialized toward the LLM>",
  "dtype": "string",
  "semantic_type": "person_name",
  "source": { "kind": "blind_compute" },
  "lineage": { "op": "argmax", "inputs": ["tok_7f3a", "tok_2d81"], "code_digest": "sha256:…" },
  "session_id": "sess_01",
  "ttl": "2026-07-15T11:32:00Z",
  "policy": { "reveal_to_frontend": true }
}
```

The lineage DAG buys three things most redaction tools don't have:

- **Audit** — every derived value can show exactly which inputs and which code produced it. The records are complete; **[planned]** an exporter that gets them out of the process.
- **Cascading invalidation** — expire or delete a token and all its descendants go with it. Implemented as `invalidate_cascade`, but nothing in the runtime calls it yet: today it is an API for your code, not an automatic behavior.
- **Policy inheritance** — a derived token inherits the *most restrictive* policy of its inputs, so sensitive data can't be laundered through a computation. This one is wired: `compose_policy` and `compose_ttl` run on every blind compute.

### Collective tokens for structured data

A list declared under `tables:` comes back as **one** token, whatever its
length, and the model is told the column names and what they mean. On 500
employees x 5 sensitive fields:

```
individual tokens : 2500
collective token  : 1
the model sees    : {"employees": "⟦tok_a58cbaf0⟧"}
```

The cost of not having this was never context size — tokens are 14 characters
and *replace* the values they hide, measured at +3% on that same response. It
was that the model could not operate on the result at all: a few hundred
unordered opaque strings carry no structure, so it cannot sort them or even
tell they are comparable quantities.

It queries the token with `blindfold_table`, using a fixed set of operations
rather than code — `filter`, `sort_by`, `limit`, `select`, `sum`, `mean`,
`min`, `max`, `count` — and gets another token back:

```
model  -> filter dept == Eng, sort by salary desc, limit 3, select name, salary
result -> ⟦tok_dc708f10⟧
user   -> [{"name": "p499", "salary": 48463}, …]
```

That restriction is the feature, not a compromise. Arbitrary Python lets a
model write something whose *success* depends on a hidden value and read one
bit per call; a fixed operation set cannot express it. So this path executes no
model-written code and **needs no sandbox at all**.

### Schema-driven tokenization

Blindfold does **not** guess what's sensitive with NER or regexes over tool results. You declare it, per tool, per field:

```yaml
schemas:
  hr_api.get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
```

Deterministic, and zero false negatives on declared fields — which is also the catch: a sensitive field you did not declare passes through in cleartext. Declare defensively, including error and debug paths; a path that never matches costs nothing. (An optional NER pass over free-text fields is on the roadmap — see below.)

Paths are checked when the config loads. A path that this dialect cannot honor — recursive descent, a filter, a slice — is refused at startup rather than reinterpreted into something else, because the failure mode that matters here is a config that looks like it protects a field and does not. A path that is well-formed but simply absent from a given response stays a silent no-op, so defensive declaration remains free.

The declared `semantic_type` and `unit` are what the model is told about each path. The runtime data type is recorded in the vault but not surfaced, since the tool description is built from config before any response exists.

### Robust rehydration

Tokens use collision-proof delimiters (`⟦tok_…⟧`). `rehydrate()` validates every token in the model's answer against the vault: a token that doesn't resolve renders as `[unknown token]` (hallucinated or expired), one the policy refuses renders as `[redacted]`. Neither is silently dropped.

Rehydration is a function your application calls on the final answer, not something that happens on the wire. Two consequences worth knowing before you design around it:

- The model has to preserve the placeholders verbatim for this to work. The prompt fragment that instructs it to do so is not yet exported by the package — copy it from [`examples/demo_chat.py`](examples/demo_chat.py) (`SYSTEM_PROMPT`). **[planned]** shipping it as a constant.
- If the model's answer never passes through your code, nothing rehydrates it. That is the situation with any MCP client you did not write — see [`LIMITATIONS.md`](LIMITATIONS.md#rehydration-requires-a-client-you-control).

## Quick start

Blindfold is a Python package, not yet published to PyPI. Install it from source:

```bash
git clone https://github.com/ManuelPr/blindfold && cd blindfold
uv sync            # or:  pip install -e .
```

There are three ways to use it. **Inside Claude Code, pick Mode C. Everywhere else, pick Mode B unless you know why you want Mode A.**

**Mode A — a CLI wrapping another stdio MCP server:**

```bash
# Wrap any stdio MCP server; blindfold reads ./blindfold.yaml if present:
blindfold --config blindfold.yaml -- python -m your_org.some_mcp_server
```

This protects the LLM provider from ever seeing the values, and needs no application code. But it stops there: the proxy sits on the tool channel, below the client, so the model's *answer* never passes through it. With a client you did not write — Claude Desktop, Cursor, Zed — the end user reads `The higher earner is ⟦tok_9c1bf051⟧` and nothing turns that back into a name. This is structural, not a missing feature: see [`LIMITATIONS.md`](LIMITATIONS.md#rehydration-requires-a-client-you-control). Mode A is the right choice when hiding the values from the provider is the whole goal and placeholders in the output are acceptable, or when the MCP client is yours and can call `blindfold/rehydrate`.

**Mode C — a Claude Code plugin:**

```bash
claude --plugin-dir ./plugin        # from a clone; see plugin/README.md
```

Three hooks and one small MCP server, because a host gives Blindfold different
seams than a protocol does:

| Piece | Does what |
|---|---|
| `SessionStart` hook → `additionalContext` | tells the model, once before the first prompt, which paths come back as placeholders and what they mean — and to reproduce them verbatim |
| `PostToolUse` hook → `updatedToolOutput` | rewrites the tool result **the model receives** — tokenization, for *every* tool, not only MCP servers: `Bash`, `Read` and `WebFetch` included |
| `mcp-server` (`blindfold mcp-server`) | offers `blindfold_compute` as an ordinary tool, so the model can operate on what it cannot read |
| `MessageDisplay` hook → `displayContent` | rewrites **what the screen shows**, leaving the transcript untouched — rehydration |

The last one solves the problem Mode A cannot. Because `MessageDisplay` is
display-only, the user reads real values while the conversation keeps the
placeholders — so the values never re-enter the model's context on the next
turn. The proxy has no way to make that distinction.

The first and third exist because a host's hooks **cannot add a tool or edit a
tool description**. Mode A does both by rewriting the `tools/list` response as
it passes the proxy; here the same information has to arrive as session context,
and blind compute has to arrive the way every other tool does.

This mode **requires `storage.backend: sqlite`**: every hook invocation is a
separate process, so the vault has to be shared. The CLI refuses to run the
hooks with a memory vault rather than minting tokens nobody will be able to
resolve.

**Mode B — an in-process library (used by a harness you write):**

```python
from blindfold import rehydrate
from blindfold.core.vault import MemoryTokenStore
from blindfold.core.policy import SessionBoundPolicy

store = MemoryTokenStore()
policy = SessionBoundPolicy()
# ... your harness tokenizes tool results into `store`, then at the end:
final_text = rehydrate(llm_answer, session_id, store, policy)
```

Mode B is the one where the loop actually closes: your code owns the final answer, so it can rehydrate it. It works with any LLM SDK and needs no MCP.

Out of the box, nothing needs configuring: memory vault, session-bound authorization, subprocess sandbox. Configuration is something you discover when you need it, not a prerequisite. See [`examples/demo_chat.py`](examples/demo_chat.py) for an Anthropic SDK loop end-to-end.

One thing Mode A does for you that Mode B does not: appending the protected-path descriptions to your tool definitions. Call `describe_schema(fields)` yourself and append the result to the tool's description, or the model will be handed placeholders with no idea what they stand for.

## Configuration

Everything deployment-specific lives in one file. **The current release reads exactly two sections** — this is the whole of it:

```yaml
tokens:
  default_ttl: 3600         # seconds a token stays resolvable

storage:
  backend: memory           # memory (default) | sqlite
  path: ./vault.db          # sqlite only
  encrypt_at_rest: false    # sqlite only; needs BLINDFOLD_VAULT_KEY

schemas:
  hr_api.get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year

  hr_api.list_employees:
    tables:                   # a whole list behind one token
      - path: $.employees
        columns:              # what the model may query on
          - name: salary
            semantic_type: salary
            unit: EUR/year
          - name: dept

resources:                  # MCP resources, keyed by URI glob
  "file:///hr/*.json":
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
```

Pick `sqlite` when the vault has to outlive the process, or when tokenizing and
rehydrating happen in different processes. Pick `memory` — the default — when
neither is true: it is faster and puts nothing on disk. Asking for a backend this release does not have fails at load rather than
being ignored.

`encrypt_at_rest: true` seals values with AES-256-GCM. The key comes from
`BLINDFOLD_VAULT_KEY` (32 bytes, base64) and never from the config file, since a
key kept beside the database it protects is decoration. Install with
`pip install blindfold[encryption]`, and generate a key with:

```bash
python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"
```

Only the value is sealed; token, session and timestamps stay readable because
the store queries on them. Holding the file still reveals how many records
exist and when.

`default_ttl` deserves a thought before you deploy: it governs how long a conversation containing placeholders stays readable. At the default of one hour, an answer the user comes back to tomorrow rehydrates as `[unknown token]` — the placeholders in your chat history outlive the vault entries they point at. Raise it if that matters to you; the vault is in memory, so a restart ends the session either way.

### The rest of the file **[planned]**

The keys below are the designed shape of the configuration. They are **not implemented**: `load_config` tolerates them (`extra="allow"`) and ignores them. Nothing here changes Blindfold's behavior today.

```yaml
mode: local                 # local | server

detokenize:
  policy: session_bound     # allow_all | session_bound | claims | webhook
  # webhook_url: https://myapp.internal/authz

tokens:
  consistency:              # per semantic_type
    person_name: stable     # same value → same token (enables equality reasoning)
    salary: fresh           # new token every time (no equality leakage)

identity:
  forward_headers: [Authorization, X-User-Id]   # only meaningful once an HTTP mode exists

compute:
  sandbox: subprocess       # subprocess | docker | disabled
  timeout_s: 5
  network: false
```

Where today's behavior differs from what those keys suggest: every token is minted fresh (no `stable` consistency), the policy is always `session_bound`, the sandbox is always the subprocess one with a hard-coded 5-second timeout, and `network: false` describes an intent rather than an enforced setting — see [Threat model](#threat-model--limitations).

Two built-in profiles are designed to cover the common cases — **`local`** (single user, memory/SQLite vault, stdio MCP transport) and **`server`** (multi-user, Redis vault, webhook authorization, HTTP transport). **[planned]**: only the `local` shape exists, now with either vault.

## Pluggable architecture

The core is deliberately small: intercept → tokenize → track lineage → blind compute → rehydrate. Everything environment-dependent hides behind three interfaces.

| Port | Contract | Ships today | Designed **[planned]** |
|---|---|---|---|
| `TokenStore` | `mint_token`, `put`, `get`, `resolve`, `find_by_session`, `invalidate_cascade`, `purge_expired` | `memory` *(default)*, `sqlite` | `redis`, `postgres` |
| `DetokenizePolicy` | `can_reveal(context, token_record) → bool` | `session_bound` *(default)* | `allow_all`, `claims`, `webhook` |
| `ComputeSandbox` | `run(code, resolved_inputs) → value` | `subprocess` | `docker` |

The ports exist so the rest is additive rather than a rewrite. The two `TokenStore` implementations are held to one behavioural suite that uses the public interface only, so swapping them changes nothing else.

Notes for adapter authors:

- **TTL lives in the core**, so no storage adapter can forget it: expiry is checked on every `get`. At-rest encryption is designed to live there too, but there is no at-rest anything yet.
- Detokenization is an **authorization point**, not a string substitution. `SessionBoundPolicy` refuses any token minted in another session, so a guessed ID resolves to `[redacted]` rather than a value. Per-user separation on top of that arrives with multi-user deployment **[planned]**.
- The same policy hook gates blind compute (`can_compute`), since *computing on* data is sometimes as sensitive as *seeing* it. This one is wired today.

## Deployment

**Local (personal agent)** — the only deployment that exists today. Everything runs on your machine. With the default memory vault nothing survives the process, which matters because the placeholders you already sent the model *do* survive — in your chat history, your logs, your app's database — so a restart would leave those conversations pointing at values that exist nowhere. Switch to `backend: sqlite` if that matters, and treat the file as the secret it holds.

**Server (internal chatbot, multi-user) [planned]:** Blindfold and its vault would run **server-side, inside your network perimeter**, next to the APIs they wrap — never in the browser, never on the client. Rehydration as the last server-side hop before the response reaches the user's frontend, gated by the configured policy. Intended vault: Redis (native TTL) for tokens and encrypted values, plus Postgres for the lineage/audit log *without* the values. None of this is built; the per-user isolation it implies does not exist yet either.

## Threat model & limitations

Read this before deploying. Honesty here is a feature.

**What Blindfold protects against:** the LLM provider (and the model itself) learning the values returned by your private APIs, including values derived from them through computation — **against a model that follows the protocol**. A model actively trying to extract the values has channels available to it today; they are listed below, and closing them is ongoing work, not a solved problem.

**What it does NOT protect against (non-goals):**

- **Access control between your users and your APIs.** Blindfold forwards the caller's identity headers untouched and lets *your* APIs enforce their own ACLs. If your API answers salary queries to anyone holding a service token, Blindfold will faithfully tokenize data the caller should never have obtained. Enforcement belongs upstream; we just don't break it.
- **Prompt-side leakage.** The user's question still goes to the provider. *"What is Andrea Tuscano's salary?"* reveals a name and an intent even if the answer is tokenized. Optional inbound prompt tokenization (NER-based) is planned, at a cost in answer quality.
- **Inference leakage by design decisions you make.** With `consistency: stable`, the model can reason about equality ("this token appears twice → same entity") — that equality relation *is* information disclosed to the provider. It's configurable per semantic type precisely because it's a real trade-off; choose deliberately.
- **A malicious or prompt-injected model writing exfiltrating compute code.** This is the weakest part of the current release, and the description below is what was actually observed, not what was intended.

  What holds: results leave the sandbox only as new vault tokens, derived tokens inherit their inputs' policies and shortest TTL, `resolve()` refuses any token not declared in `inputs`, and the error channel carries exception *types* only — never messages, never child output.

  What does not hold today:

  | Channel | Status | Closable? |
  |---|---|---|
  | Exception text was forwarded to the model — `raise ValueError(resolve(t))` returned the value in one call | **closed** | done: types only, with six regression tests |
  | `open()` and `import` gave the child's code the filesystem | **raised, not closed** | done: builtins are an allow-list, so the obvious routes are absent. Escaping through Python's object graph needs no builtins and remains possible |
  | Network reachable from compute code | **raised, not closed** — `import socket` now fails like any import. It was open on Linux/macOS; on Windows it failed only as a side effect of the stripped environment breaking socket initialization, an accident, not a defense | **properly only at OS level**: a container with networking off, or equivalent sandboxing |
  | Success-vs-failure as a one-bit oracle — `result = 1/0 if resolve(t) > 50000 else 'ok'`, repeated, recovers an exact number in ~20 calls | **open** | **no**, not while the model submits arbitrary Python. Only a fixed set of operations (the table design above) removes it |

  A CaMeL-style capability/data-flow layer is the long-term answer to the class as a whole. Until then: **do not run blind compute against data whose exposure you cannot tolerate, if the model's inputs come from sources you do not control.** Do not run with `sandbox: disabled` on real data at all.
- **Quality-preserving magic.** If the model only sees `⟦tok⟧`, it cannot judge whether a salary is competitive or a diagnosis plausible. Blind compute covers *mechanical* operations (compare, aggregate, filter); *semantic* judgment on hidden values is fundamentally impossible. That's the deal.

**Operational cautions:** rehydration is validated, but the model can still mangle placeholders — instruct it not to (prompt fragment in [`examples/demo_chat.py`](examples/demo_chat.py)) and expect the occasional `[unknown token]`. Vault compromise equals data compromise: values sit in cleartext in process memory today, so keep TTLs short and run Blindfold in the same trust zone as the APIs it protects.

## Comparison

| | Prompt PII redaction | Tool-result tokenization | Reversible (rehydration) | Blind compute on hidden data | Lineage / audit DAG | Self-hosted, open source |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| **Blindfold** | plannedⁱ | ✅ | ✅ⁱⁱⁱ | ✅ⁱᵛ | ✅ | ✅ |
| Microsoft Presidio | ✅ | ❌ | partial | ❌ | ❌ | ✅ |
| Philter | ✅ | ❌ | ✅ | ❌ | ❌ | partial |
| LLM Guard | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| anonymize.dev (MCP) | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ (SaaS) |
| CaMeL (research) | n/a | n/aⁱⁱ | n/a | ✅ | ✅ (capabilities) | ✅ (research code) |

ⁱ Optional inbound NER pass, on the roadmap.
ⁱⁱ CaMeL targets prompt injection, not provider-side privacy; its P-LLM/interpreter split is the closest architectural relative of Blindfold's blind compute, and a direct inspiration.
ⁱⁱⁱ In your own application code. Not available through an MCP client you did not write — see [`LIMITATIONS.md`](LIMITATIONS.md#rehydration-requires-a-client-you-control).
ⁱᵛ Mechanical operations on a handful of tokens. Does not scale to long result sets until collective tokens land, and the sandbox is not hardened against a hostile model — see [Threat model](#threat-model--limitations).

## Related work

- **CaMeL** ([Defeating Prompt Injections by Design](https://arxiv.org/abs/2503.18813), Google DeepMind) — control/data-flow separation with capabilities; the privileged LLM writes code without ever seeing raw data.
- **Microsoft Presidio** — the reference open-source PII detection/anonymization engine.
- **Philter, LLM Guard, PII Shield** — prompt-side redaction proxies with reversible tokenization.

## Roadmap

Ordered by what the current release most needs, not by ambition.

- [x] MVP: stdio MCP wrapper, memory store, session-bound policy, subprocess sandbox
- [x] **Sandbox output hygiene** — exception types only; child stdout/stderr go to the operator, never to the model
- [x] **Path validation at config load** — syntax the dialect cannot honor is refused at startup instead of being silently reinterpreted
- [x] **Expiry frees memory** — the vault sweeps expired records instead of holding cleartext values for the life of the process
- [x] **SQLite store** — a vault that survives a restart and can be shared between processes
- [x] **Claude Code hooks** — tokenization and reveal without a proxy, and without placeholders reaching the user
- [x] **Mode C completed** — session briefing so the model knows what the placeholders are, and an MCP server so it can compute on them
- [x] **Collective (table) tokens** — one placeholder per list, queried by a fixed operation set; also the answer to the one-bit oracle
- [x] **Encryption at rest** — AES-256-GCM, key from the environment, never from the config file
- [x] **CI on Linux, macOS and Windows** — including the sandbox probes, so the documented behaviour is asserted per platform
- [x] **Restricted builtins in the compute child** — the easy filesystem and network paths are gone without anyone installing Docker; not a boundary, a higher cost
- [x] **Export the placeholder-preserving prompt fragment** as `PLACEHOLDER_PROMPT`, used by both demos and by the Mode C briefing
- [ ] Table joins, group-by and cross-table aggregation — the operations collective tokens do not have yet
- [ ] Docker sandbox — the OS-level answer to network and filesystem, after the cheap in-process measures
- [ ] HTTP proxy mode for plain REST APIs
- [ ] Redis + Postgres adapters, webhook policy, audit log exporter
- [ ] Optional inbound prompt tokenization (NER)
- [ ] CaMeL-style capability tracking in the compute sandbox

## Documentation

### Current — kept in step with the code

- **[`docs/modes.md`](docs/modes.md)** — which of the three integration modes you want, what each one can and cannot do, and the one question that decides it. Read this before installing anything.
- **[`docs/architecture.md`](docs/architecture.md)** — how the code actually works. Component-by-component tour with a full end-to-end frame-by-frame example. Start here after this README.
- **[`LIMITATIONS.md`](LIMITATIONS.md)** — what Blindfold does *not* do, split into by-design (permanent) and MVP (temporary), with a cost estimate on every closable gap. Read before deploying against real data.
- **[`blindfold.example.yaml`](blindfold.example.yaml)** — a copy-paste-ready configuration example, containing exactly the keys the current release reads.
- **[`examples/demo_chat.py`](examples/demo_chat.py)** — a runnable Anthropic + Blindfold + fake HR MCP loop.

### Project history — frozen, not maintained

These two record how the MVP was designed and built in July 2026. They are useful for understanding *why* decisions were made and are **not updated as the code changes** — where they disagree with the three documents above, the documents above are right. (Known example: both describe the JSONPath dialect as supporting single-level wildcards; the implementation handles nested ones.)

- **[`docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md`](docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md)** — the formal MVP design doc: scope, architecture, data model, key flows, testing strategy.
- **[`docs/superpowers/plans/2026-07-15-blindfold-mvp.md`](docs/superpowers/plans/2026-07-15-blindfold-mvp.md)** — the task-by-task implementation plan the MVP was built from. Long (3,200 lines); read it for the reasoning behind a specific file, not front to back.

## Contributing

Issues and PRs welcome. Especially wanted: storage/policy adapters, red-teaming of the threat model, and real-world schema examples. Please read the threat model section before proposing features that move detokenization client-side.

## License

MIT (proposed).
