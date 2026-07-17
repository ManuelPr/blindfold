# Blindfold

> A privacy proxy for LLM tool calls. Your private APIs' data never enters the model's context — the LLM reasons over anonymous tokens, computes blindly on data it cannot see, and real values are restored only at the last hop, after an authorization check.

*(Working name — subject to change.)*

**Status:** early design / pre-alpha. Feedback and contributions welcome — see [Contributing](#contributing).

---

## The problem

When you connect an LLM (Claude, GPT, Gemini…) to your internal APIs via tool calling or MCP, every tool result flows back into the model's context. Ask an agent *"What is Andrea's salary?"* and the HR API's response — the actual salary — is sent to the LLM provider as part of the conversation.

Existing PII-redaction proxies (Presidio, Philter, LLM Guard, …) solve a different problem: they scrub the **user's prompt** before it reaches the model. But in agentic setups the sensitive data usually isn't in the prompt — it's in the **tool results** coming back from your private APIs. That gap is what Blindfold covers.

## What it does

Blindfold sits between your agent harness and your APIs (or MCP servers). It:

1. **Tokenizes tool results.** Sensitive fields — declared per-tool in a schema — are replaced with typed anonymous tokens before the result reaches the LLM. The real values are stored, encrypted, in a local ephemeral vault.
2. **Gives the LLM a manifest, not the data.** The model sees `⟦tok_7f3a⟧: number, salary, EUR/year` — enough to reason and write correct code, never the value.
3. **Enables blind compute.** The LLM can submit code that operates on tokens. Blindfold resolves the tokens, executes the code in a sandbox on the real values, and stores the result as a *new* token with full lineage. The model orchestrates computations on data it never sees.
4. **Rehydrates at the last hop.** Tokens in the model's answer are replaced with real values only when the response is delivered to the end user — after a pluggable authorization check.

```
┌────────────┐   answer with real values    ┌─────────────────────┐
│  Frontend  │◄─────────────────────────────│  Blindfold           │
└────────────┘                              │  ┌───────────────┐  │
      │                                     │  │ token vault   │  │
      ▼ prompt                              │  │ (encrypted,   │  │
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

1. The LLM calls `hr_api.get_salary` twice. Blindfold intercepts both results and returns:
   ```
   ⟦tok_7f3a⟧  (number, salary, EUR/year)
   ⟦tok_2d81⟧  (number, salary, EUR/year)
   ```
2. The LLM cannot compare what it cannot see — so it submits a blind-compute request:
   ```python
   result = "Manuel Pernigotto" if resolve("tok_2d81") > resolve("tok_7f3a") else "Andrea Tuscano"
   ```
3. Blindfold executes it in a sandbox on the real values and returns a **new token**:
   ```
   ⟦tok_9c1b⟧  (string, person_name, derived from tok_7f3a, tok_2d81 via argmax)
   ```
4. The LLM answers: *"The higher earner is ⟦tok_9c1b⟧."*
5. Blindfold rehydrates at delivery: *"The higher earner is Manuel Pernigotto."*

The LLM provider saw two opaque tokens, a snippet of comparison code, and a third opaque token. It never learned a salary — and not even who earns more.

## How it works

### Typed tokens with lineage

Every vault entry is a full record, not a bare key-value pair:

```json
{
  "token": "tok_9c1b",
  "value": "<encrypted at rest, never serialized toward the LLM>",
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

- **Audit** — every derived value can show exactly which inputs and which code produced it.
- **Cascading invalidation** — expire or delete a token and all its descendants go with it.
- **Policy inheritance** — a derived token inherits the *most restrictive* policy of its inputs, so sensitive data can't be laundered through a computation.

### Collective tokens for structured data

A tool returning 50 records doesn't produce 50×N tokens. It produces one `table` token whose schema (columns and types) is exposed in the manifest. Blind-compute code operates on the whole table — filter, sort, aggregate — and Blindfold resolves it server-side.

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

Deterministic, zero false negatives on declared fields, and the manifest gets accurate types for free. (An optional NER pass over free-text fields is on the roadmap — see below.)

### Robust rehydration

Tokens use collision-proof delimiters (`⟦tok_…⟧`). Before delivery, Blindfold validates every token in the model's answer against the vault: hallucinated or altered tokens are flagged instead of rendered. A system-prompt fragment instructing the model to preserve placeholders verbatim ships with the proxy.

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

Out of the box, nothing needs configuring: memory vault, session-bound authorization, subprocess sandbox. Configuration is something you discover when you need it, not a prerequisite. See [`examples/demo_chat.py`](examples/demo_chat.py) for an Anthropic SDK loop end-to-end.

## Configuration

Everything deployment-specific is pluggable and lives in one file:

```yaml
mode: local                 # local | server

storage:
  backend: sqlite           # memory | sqlite | redis | postgres
  path: ./vault.db
  encrypt_at_rest: true     # enforced by the core, not by adapters

detokenize:
  policy: session_bound     # allow_all | session_bound | claims | webhook
  # webhook_url: https://myapp.internal/authz

tokens:
  default_ttl: 3600
  consistency:              # per semantic_type
    person_name: stable     # same value → same token (enables equality reasoning)
    salary: fresh           # new token every time (no equality leakage)

schemas:
  hr_api.get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year

identity:
  forward_headers: [Authorization, X-User-Id]

compute:
  sandbox: subprocess       # subprocess | docker | disabled
  timeout_s: 5
  network: false
```

Two built-in profiles cover the common cases: **`local`** (single user, memory/SQLite vault, stdio MCP transport) and **`server`** (multi-user, Redis vault, webhook authorization, HTTP transport). Same core, no forks.

> **MVP note:** the current release consumes only the `schemas` and `tokens` sections. Other keys shown above are declarative for the roadmap and are safely ignored today.

## Pluggable architecture

The core is deliberately small: intercept → tokenize → track lineage → blind compute → rehydrate. Everything environment-dependent hides behind three interfaces.

| Port | Contract | Built-in adapters |
|---|---|---|
| `TokenStore` | `put`, `get`, `resolve`, `find_by_session`, `invalidate_cascade`, `purge_expired` | `memory`, `sqlite`, `redis`, `postgres` |
| `DetokenizePolicy` | `can_reveal(context, token_record) → bool` | `allow_all`, `session_bound` *(default)*, `claims`, `webhook` |
| `ComputeSandbox` | `run(code, resolved_inputs) → value` | `subprocess`, `docker` |

Notes for adapter authors:

- **TTL and at-rest encryption live in the core**, so no storage adapter can forget them.
- Detokenization is an **authorization point**, not a string substitution. In multi-user deployments the vault key is effectively `(user_id, session_id, token)`; a token minted in Maria's session is unresolvable from Luca's, even with a guessed ID.
- The same policy hook can gate blind compute (`can_compute`), since *computing on* data is sometimes as sensitive as *seeing* it.

## Deployment

**Local (personal agent):** everything runs on your machine. The vault can be `:memory:` — nothing survives the session.

**Server (internal chatbot, multi-user):** Blindfold and its vault run **server-side, inside your network perimeter**, next to the APIs it wraps — never in the browser, never on the client. Rehydration happens as the last server-side hop before the response reaches the user's frontend, gated by the configured policy. Recommended vault: Redis (native TTL) for tokens and encrypted values, plus Postgres for the lineage/audit log *without* the values.

## Threat model & limitations

Read this before deploying. Honesty here is a feature.

**What Blindfold protects against:** the LLM provider (and the model itself) learning the values returned by your private APIs, including values derived from them through computation.

**What it does NOT protect against (non-goals):**

- **Access control between your users and your APIs.** Blindfold forwards the caller's identity headers untouched and lets *your* APIs enforce their own ACLs. If your API answers salary queries to anyone holding a service token, Blindfold will faithfully tokenize data the caller should never have obtained. Enforcement belongs upstream; we just don't break it.
- **Prompt-side leakage.** The user's question still goes to the provider. *"What is Andrea Tuscano's salary?"* reveals a name and an intent even if the answer is tokenized. Optional inbound prompt tokenization (NER-based) is planned, at a cost in answer quality.
- **Inference leakage by design decisions you make.** With `consistency: stable`, the model can reason about equality ("this token appears twice → same entity") — that equality relation *is* information disclosed to the provider. It's configurable per semantic type precisely because it's a real trade-off; choose deliberately.
- **A malicious or prompt-injected model writing exfiltrating compute code.** Mitigations: the sandbox has no network and no filesystem, results only ever leave as new vault tokens, and derived tokens inherit input policies. Residual risk exists (e.g., encoding information in control flow that affects *which* code paths run); a CaMeL-style capability/data-flow layer is the long-term answer. Do not run with `sandbox: disabled` on real data.
- **Quality-preserving magic.** If the model only sees `⟦tok⟧`, it cannot judge whether a salary is competitive or a diagnosis plausible. Blind compute covers *mechanical* operations (compare, aggregate, filter); *semantic* judgment on hidden values is fundamentally impossible. That's the deal.

**Operational cautions:** rehydration is validated but the model can still occasionally mangle placeholders (retry with the shipped system-prompt fragment); vault compromise equals data compromise (encrypt at rest, keep TTLs short, run it in the same trust zone as the APIs it protects).

## Comparison

| | Prompt PII redaction | Tool-result tokenization | Reversible (rehydration) | Blind compute on hidden data | Lineage / audit DAG | Self-hosted, open source |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| **Blindfold** | plannedⁱ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Microsoft Presidio | ✅ | ❌ | partial | ❌ | ❌ | ✅ |
| Philter | ✅ | ❌ | ✅ | ❌ | ❌ | partial |
| LLM Guard | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| anonymize.dev (MCP) | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ (SaaS) |
| CaMeL (research) | n/a | n/aⁱⁱ | n/a | ✅ | ✅ (capabilities) | ✅ (research code) |

ⁱ Optional inbound NER pass, on the roadmap.
ⁱⁱ CaMeL targets prompt injection, not provider-side privacy; its P-LLM/interpreter split is the closest architectural relative of Blindfold's blind compute, and a direct inspiration.

## Related work

- **CaMeL** ([Defeating Prompt Injections by Design](https://arxiv.org/abs/2503.18813), Google DeepMind) — control/data-flow separation with capabilities; the privileged LLM writes code without ever seeing raw data.
- **Microsoft Presidio** — the reference open-source PII detection/anonymization engine.
- **Philter, LLM Guard, PII Shield** — prompt-side redaction proxies with reversible tokenization.

## Roadmap

- [ ] MVP: stdio MCP wrapper, memory/SQLite stores, session-bound policy, subprocess sandbox
- [ ] HTTP proxy mode for plain REST APIs
- [ ] Redis + Postgres adapters, webhook policy
- [ ] Collective (table) tokens + DuckDB-backed analytical compute
- [ ] Optional inbound prompt tokenization (NER)
- [ ] CaMeL-style capability tracking in the compute sandbox
- [ ] Docker sandbox, audit log exporter

## Documentation

Deep dives, in order of what you likely want next:

- **[`docs/architecture.md`](docs/architecture.md)** — how the code actually works. Component-by-component tour with a full end-to-end frame-by-frame example. Start here after this README.
- **[`LIMITATIONS.md`](LIMITATIONS.md)** — honest list of what Blindfold does *not* do, split into by-design (permanent) and MVP (temporary). Read before deploying against real data.
- **[`docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md`](docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md)** — the formal MVP design doc: scope, architecture, data model, key flows, testing strategy. The source of truth for what got built.
- **[`docs/superpowers/plans/2026-07-15-blindfold-mvp.md`](docs/superpowers/plans/2026-07-15-blindfold-mvp.md)** — the task-by-task implementation plan the MVP was built from. Useful if you want to understand the order of decisions or see the reasoning behind each file.
- **[`blindfold.example.yaml`](blindfold.example.yaml)** — a copy-paste-ready configuration example.
- **[`examples/demo_chat.py`](examples/demo_chat.py)** — a runnable Anthropic + Blindfold + fake HR MCP loop.

## Contributing

Issues and PRs welcome. Especially wanted: storage/policy adapters, red-teaming of the threat model, and real-world schema examples. Please read the threat model section before proposing features that move detokenization client-side.

## License

MIT (proposed).
