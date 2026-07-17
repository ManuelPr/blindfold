# Limitations

Honest inventory of what Blindfold does **not** do — split by whether the limit is inherent to the design or a temporary MVP gap. Read this before deploying against real data.

For rationale and full threat-model discussion, see the [README](README.md#threat-model--limitations) and the [MVP design doc](docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md).

---

## By-design limits (permanent)

These come from the shape of the problem. No amount of implementation work removes them; they are the deal you accept when adopting a proxy-based approach.

### The user's prompt is not protected
Blindfold intercepts **tool results**, not the user's original question. If the user types *"What is Andrea Tuscano's salary?"*, the name and the intent go to the LLM provider in cleartext. Only the tool response gets tokenized.

**Mitigation:** an optional inbound NER pass over prompts is on the roadmap. It will cost answer quality (the model reasons more poorly over its own tokenized inputs).

### Access control between users and their own APIs is out of scope
Blindfold forwards the caller's identity headers untouched and lets the downstream API enforce its own ACLs. If that API answers everyone with a valid service token, Blindfold faithfully tokenizes whatever the caller could have obtained. Blindfold does not upgrade privileges, but it does not downgrade them either — enforcement has to live upstream.

### The model cannot semantically judge hidden values
Blind compute covers **mechanical** operations: compare, sort, aggregate, filter, arithmetic, string manipulation. It does **not** cover **semantic judgment** on the value itself. Concretely:
- *"Who earns more, X or Y?"* — works (comparison of two hidden numbers).
- *"Is 62,000 EUR a competitive salary for this role in Milan?"* — impossible. The model would need to see the number, and seeing it means exposing it.
- *"Compute the BMI of this person."* — works (produces another hidden number).
- *"Is this BMI a health concern?"* — impossible.

If your use case requires the model to make a qualitative call on the raw value, no proxy layer can help you. That decision has to happen client-side, after rehydration, in code the model doesn't run.

### `consistency: stable` leaks equality
When you configure a `semantic_type` with `consistency: stable`, the same underlying value produces the same token every time. That lets the model reason across occurrences of the same entity ("these two rows are the same person"), but the equality relation itself becomes visible to the LLM provider. This is a real trade-off, exposed per semantic type so you decide deliberately. MVP defaults to `consistency: fresh` (new token every time) — no equality leakage, no cross-token identity reasoning.

### Non-declared paths pass through
Blindfold only tokenizes JSON fields you declare in `blindfold.yaml`. If a tool returns sensitive data at a path you didn't list (an unexpected error object, a debug blob, a new field the vendor added), that data goes to the model untokenized. Blindfold does **not** do NER, regex sniffing, or heuristic detection over undeclared fields — deterministic behavior is a feature, not a bug.

**Mitigation:** write integration tests against your real MCP servers that assert the tokenized output contains no real values (see `tests/integration/test_proxy_forwarding.py` for the pattern). Declare error paths defensively in the config.

---

## MVP limits (temporary, on the roadmap)

Everything below is a current-release gap. All of these are fixable and are called out in the design doc's out-of-MVP section.

### Storage
- **In-memory vault only.** Process death = all state lost. No SQLite/Redis/Postgres adapters yet.
- **No encryption at rest.** There is no disk state to encrypt yet. Becomes critical the moment SQLite lands.
- **No concurrency handling on the vault.** MVP is single-process, single-session. Adding SQLite requires locking.

### Transport
- **Only stdio MCP wrapping.** No HTTP proxy mode for REST APIs.
- **Cross-platform CI missing.** Developed and tested on Windows. Linux/macOS should work but there is no CI yet to confirm.

### Sandboxing
- **Subprocess isolation is best-effort, not hermetic.** Fresh `python -I` child with clean env and hard timeout — no seccomp, no network firewall, no filesystem isolation. A determined LLM writing exfiltration code could conceivably escape.
- **No test for sandbox escape resistance.** Documented as a residual risk, not empirically verified.
- **No Docker sandbox option.** On the roadmap.

### Tokenization
- **JSONPath dialect is minimal.** Supports `$.a.b.c` and `$.list[*].field` only. No filters (`$.items[?(@.type == 'x')]`), no recursive descent (`$..salary`), no slicing (`$.items[0:5]`).
- **Non-JSON tool responses are passed through untokenized.** Free-form text hits the model unchanged. Structured JSON responses are required for protection to kick in.
- **Non-text MCP content parts are passed through untokenized.** Image/resource/blob parts are not protected.
- **No collective (table) tokens.** A tool returning 500 rows with 5 sensitive fields mints 2,500 individual tokens. The design vision is a single `table` token per structured result with schema exposed in the manifest — not yet.
- **No inbound prompt NER.** Only outbound tool-response tokenization.

### Model UX
- **Semantic metadata not exposed to the model.** `semantic_type` and `unit` are stored in the vault (for audit and policy) but not surfaced to the LLM alongside the token. The model has to infer meaning from the JSON key name. Post-MVP work: expose a small manifest object alongside each token.

### Policy
- **Only `SessionBoundPolicy` ships.** No `webhook`, `claims`, or `allow_all` implementations yet — though the port is in place so they are additive to build.

---

## When Blindfold is the wrong tool

- **You need the model to see the value.** For qualitative reasoning on hidden data, use a different architecture: a self-hosted model, or a workflow where the model produces a plan and code that runs client-side against the real data.
- **Your sensitive data is in free-form text tool responses.** Blindfold won't detect it without NER. Restructure your MCP server to emit structured JSON, or wait for the NER pass on the roadmap.
- **You need protection against malicious code the model writes.** The MVP subprocess sandbox is not hardened. If the model itself is untrusted (prompt-injected, adversarial), use the Docker sandbox (roadmap) or don't enable blind compute at all.
- **You need the vault to survive restarts.** Add a storage adapter yourself, or wait for SQLite (roadmap).

---

## Reporting a limitation you hit

If you run into behavior that seems wrong (rather than one of the limits above), open an issue at <https://github.com/ManuelPr/blindfold/issues>. Include:
- The config snippet you used
- The tool response Blindfold received (with any real values redacted by you)
- What you expected vs. what happened
