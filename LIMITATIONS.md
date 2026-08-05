# Limitations

Honest inventory of what Blindfold does **not** do — split by whether the limit is inherent to the design or a temporary MVP gap. Read this before deploying against real data.

For rationale and full threat-model discussion, see the [README](README.md#threat-model--limitations) and the [MVP design doc](docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md).

### How to read this

The split that matters is not "big gap / small gap" but **can this be closed at all**:

- **By-design limits** — no implementation work removes them. Design around them or use something else.
- **MVP limits** — implementation gaps. Each one carries a **Fix:** line naming what would close it and roughly what that costs, because "on the roadmap" is not the same statement for a forty-line storage adapter and for a container runtime everyone has to install.

A few entries are marked **Fix: none** inside the MVP section — that means the behavior is a gap you can only remove by changing what Blindfold promises, not by writing more code. They are called out where they sit rather than moved, so the picture of each subsystem stays in one place.

---

## Deployment surface (context for the limits below)

Blindfold has two integration modes; a few of the limits below apply to only one of them.

- **Mode A: CLI proxy** — `blindfold -- <mcp-server>`, wraps a stdio MCP server. Requires that your app already speak MCP (Claude Desktop, Cursor, custom agent on the `mcp` client SDK, etc.). **Protects, but does not deliver:** with a client you did not write, the values never come back — read [Rehydration requires a client you control](#rehydration-requires-a-client-you-control) before choosing this mode.
- **Mode B: In-process library** — `from blindfold import ...`, called from your existing agent loop. Works with any LLM SDK (Anthropic, OpenAI, Gemini, self-hosted, LangChain, LlamaIndex, …); no MCP required.

Unless a bullet is tagged `[Mode A only]` or `[Mode B only]`, the limit applies to both modes. See [`docs/architecture.md#3-two-integration-modes`](docs/architecture.md#3-two-integration-modes) for the full picture, including the four integration points for Mode B.

---

## By-design limits (permanent)

These come from the shape of the problem. No amount of implementation work removes them; they are the deal you accept when adopting a proxy-based approach.

### Rehydration requires a client you control

Blindfold can put placeholders into the model's context. It cannot take them out of the model's answer unless your code handles that answer.

`rehydrate()` is a function call. In Mode B you make it yourself, on the final text, before display — this is the normal case and it works. In Mode A the proxy also answers a custom JSON-RPC method, `blindfold/rehydrate`, but **that method is not part of MCP**: it exists because this project invented it. No third-party MCP client knows to call it.

Concretely, wrapping your server with `blindfold -- …` under Claude Desktop, Cursor, Zed or any other client you did not write produces an assistant that answers:

> The higher earner is ⟦tok_9c1bf051⟧.

and stops there. The values are genuinely protected — the provider never saw them — but the end user cannot read the answer either.

This is structural. The proxy sits on the tool channel, underneath the client; the assistant's final message never passes through it. There is no MCP mechanism for a server to post-process what the model says. The obvious workaround — exposing rehydration as an ordinary tool the model can call — defeats the entire design, because a tool result goes into the model's context, which is exactly where the real values must not go.

**What this means in practice:** Mode A is the right choice when hiding values from the LLM provider is the whole goal and placeholders in the output are acceptable (audit trails, pipelines whose output is consumed by code, a client that will grow support for the custom method). If a human has to read the values, you need Mode B or a client of your own.

### Blind compute answers one bit per call, whatever the sandbox

`blindfold_compute` either returns a token or raises an error, and the model can choose which by writing code whose *success* depends on a hidden value:

```python
result = 1 / 0 if resolve("⟦tok_…⟧") > 50000 else "ok"
```

Error means "yes". About twenty of those recover an exact salary. Nothing in the sandbox prevents it, because nothing about that code is illegal — it is the tool being used exactly as specified, and no isolation technology (Docker included) hides from the caller whether a call succeeded.

It is listed as by-design because the only thing that removes it is giving up arbitrary code: replacing the free-form Python of `blindfold_compute` with a fixed set of operations (filter, sort, aggregate over a collective token) whose control flow cannot depend on the values. That is a different deal, not a bug fix — worth making, and the collective-token work would make it, but it is a change of contract.

Two things do bound the damage today: the policy check runs per input token, so the model can only interrogate values it was already allowed to compute on; and each call is one bit, so extraction is visible in the call log as an obvious pattern of repeated compute calls on the same token. Log those calls if this concerns you.

### The user's prompt is not protected
Blindfold intercepts **tool results**, not the user's original question. If the user types *"What is Andrea Tuscano's salary?"*, the name and the intent go to the LLM provider in cleartext. Only the tool response gets tokenized.

**Mitigation:** an optional inbound NER pass over prompts is on the roadmap. It will cost answer quality (the model reasons more poorly over its own tokenized inputs).

### Access control between users and their own APIs is out of scope
Blindfold forwards requests untouched and lets the downstream API enforce its own ACLs. If that API answers everyone with a valid service token, Blindfold faithfully tokenizes whatever the caller could have obtained. Blindfold does not upgrade privileges, but it does not downgrade them either — enforcement has to live upstream.

(The `identity.forward_headers` key in the README's configuration example belongs to a future HTTP mode. Mode A speaks stdio, where there are no headers: the proxy forwards each JSON-RPC line to the child verbatim, which achieves the same non-interference by simply not touching anything.)

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

- ~~**In-memory vault only.**~~ **Closed.** `SQLiteTokenStore` ships alongside `MemoryTokenStore`, selected with `storage.backend: sqlite`. Standard library, no new dependency, WAL journaling so two processes can share one file. Memory remains the default: it is the right choice when nothing needs to outlive the session, and it exposes nothing to the filesystem.

  Both stores are held to one behavioural suite ([`tests/unit/test_token_store_conformance.py`](tests/unit/test_token_store_conformance.py)) that uses the public interface only, so a deployment can swap them without the rest of the system noticing.

  Worth being clear about *which* problem persistence solves, because there are two. The one usually named is longevity: placeholders already sent to a model do not die with the process — they sit in conversation history, logs, your application's database — so after a restart those conversations rehydrated to `[unknown token]` with the real values gone. The one that turns out to matter more is **sharing between processes**: tokenizing and rehydrating need not happen in the same program, and host integrations where each callback is a fresh process cannot work at all without a shared vault, whatever the TTL.

- **A token's useful life is one hour by default, which is a separate limit from persistence.** `tokens.default_ttl` is 3600 seconds and expiry is checked on every `get`. An answer the user returns to tomorrow rehydrates as `[unknown token]` whether or not the vault was persistent. Persistence and retention are two decisions, and only the second one is currently making itself by default.

  **Fix:** none needed in code — raise `default_ttl` if long-lived conversations matter to you. What is worth building is a clearer signal than `[unknown token]` for the expiry case, so users are not left staring at an unexplained placeholder.

- **No encryption at rest — and now there is something at rest.** With `backend: sqlite` the vault file holds every protected value in cleartext. This is the most consequential open item in this document: an in-memory vault needed access to a running process, a file needs read access to a file. The store narrows permissions to owner-only where the platform honors it (POSIX; Windows ignores the mode bits) and that is the whole of its protection. Treat the file as the secret it contains, and prefer `backend: memory` when nothing needs to survive the session.

  `encrypt_at_rest: true` is **refused at load** rather than ignored, so a config cannot claim a guarantee the release does not have.

  **Fix:** feasible, but only worth doing with an externally supplied key (environment variable, OS keychain). A key stored beside the database file is decoration. If you are not prepared to make callers manage a key, the honest position is cleartext plus filesystem permissions — which is where this stands.

- ~~**Expired records are never actually removed.**~~ **Closed for expiry**, still open for invalidation. `put()` now sweeps expired records on an interval (60 seconds by default, settable per instance via `purge_interval_s`), so a TTL bounds how long a value *exists* rather than only how long it resolves. The sweep lives in the store rather than in the proxy, because Mode B builds its own store and never goes near the proxy. Consequence of amortizing it: an expired record can outlive its TTL by up to one interval, which is why the interval is settable.

  **Still open:** `invalidate_cascade()` is implemented and tested but nothing calls it. Invalidating a token and its descendants remains something your code does deliberately, not something that happens.

- **No concurrency handling on the vault.** `MemoryTokenStore` is a plain dict with no locking.

  **Fix:** not needed today — one proxy process, one child, one session. It becomes a question with the multi-user server mode, which does not exist. SQLite in WAL mode handles multi-process access on its own, so the locking work often assumed to come with persistence largely does not.

### Transport
- **CLI proxy is stdio MCP only. [Mode A only]** The `blindfold -- <cmd>` CLI wraps a single downstream stdio MCP server. No HTTP proxy mode for REST APIs, no wrapping of remote/SSE MCP servers. Mode B (in-process library) has no transport concept — it plugs into any LLM SDK loop directly, MCP or not.
- **Cross-platform CI missing.** Developed and tested on Windows. Linux/macOS should work but there is no CI yet to confirm.

### Sandboxing

The subprocess sandbox is the one place where real values meet code the model wrote. The entries below were verified by running them, not inferred from the source.

- ~~**Exception text is returned to the model verbatim.**~~ **Closed.** `raise ValueError(resolve("⟦tok_…⟧"))` used to return `ValueError: 62000` as a tool result — one call, exact value. The child now emits exception *type names* only, and child stdout/stderr never reach the caller: they go to the operator's stderr, while the raised `SandboxError` stays generic. Because both modes receive their error text from the sandbox, this closed Mode A and Mode B at once. Six regression tests in [`tests/unit/test_sandbox.py`](tests/unit/test_sandbox.py) cover the paths (explicit raise, failed assertion, interpreter-built messages such as `KeyError`, printed output, user-written stderr) and one asserts that a genuine `NameError` is still named, so the hygiene does not blind the model to its own mistakes.

- **The compute child no longer has full `__builtins__` — but this raised the cost, it did not build a boundary.** `open()`, `__import__`, `eval`, `exec`, `getattr`, `type` and `globals` are absent from the namespace the model's code runs in; only an allow-list of aggregation functions, value types and common exception types remains. The probes that previously succeeded now fail: `os.listdir(".")` returns `ImportError`, `open(...)` returns `NameError`.

  **What has not changed:** reaching `object.__subclasses__()` through the object graph requires no builtins at all, and escaping a restricted namespace that way is a known exercise. Treat the allow-list as removing the obvious routes, not as isolation. Genuine isolation is an OS-level question — see the Docker item below.

- **Network access is no longer reachable the easy way, and is still not denied.** `import socket` now fails like any other import, so the direct route is gone. Previously it was open on Linux and macOS, and failed on Windows only because the stripped environment omits `SystemRoot` and Windows cannot initialize sockets without it — an accident of the environment cleanup, never a control. The README claimed the sandbox had no network; it did.

  **Fix: only properly at OS level.** Actually denying the network means a container with networking disabled, or platform-specific namespace work. That is the strongest argument for the Docker sandbox — and also the reason it stays unurgent: the one-bit oracle does not care about the network, and a container does not close it.

- **No test for sandbox escape resistance.** The three items above are now empirically confirmed, but there is no regression test asserting that a hardened sandbox stays hardened. Any fix to the items above should land with one.

- **No Docker sandbox option.** On the roadmap, behind the two cheap in-process measures above. The port ([`ports/sandbox.py`](src/blindfold/ports/sandbox.py)) makes it additive — a second implementation, not a rewrite. The cost is operational rather than technical: every user needs Docker, and every compute call pays container startup.

- **A model that wants the values can still get them one bit at a time.** See [Blind compute answers one bit per call](#blind-compute-answers-one-bit-per-call-whatever-the-sandbox) above. **Fix: none within the current compute model.**

### Tokenization

- **JSONPath dialect is minimal — but less minimal than previously documented.** What works: static keys (`$.a.b.c`), list wildcards (`$.list[*].field`), **wildcards at any depth and nested** (`$.a[*].b[*].c` descends correctly), and **explicit numeric indices** (`$.items[0].name`). What does not: filters (`$.items[?(@.type == 'x')]`), recursive descent (`$..salary`), slicing (`$.items[0:5]`).

  Earlier versions of this file and of `docs/architecture.md` described wildcards as single-level. That was wrong — the walker recurses. No behavior changed; the documentation was understating the code.

- ~~**Unsupported path syntax fails silently.**~~ **Closed**, and the original description of it was too kind. `$..salary` did not merely match nothing: it was *reinterpreted* as `$.salary`, so it matched a top-level field, missed every nested one, and still minted tokens — leaving a config that looked like it worked. `$.items[*` was accepted as if the bracket had been closed, and `$.a..b` became `$.a.b`. Filters and slices did raise, but only at the first tool call, with `invalid literal for int()`.

  Paths are now validated where a `SchemaField` is born: at config load through a Pydantic validator, and in the dataclass itself so Mode B gets the same guarantee without the YAML. Errors name the offending subscript and, for recursive descent, what the path was silently being read as. Note the distinction that is deliberately preserved: a path that *does not match this particular response* is still a silent no-op — defensive declaration stays free. What is refused is a path that could never mean what its author wrote.
- **Non-JSON tool responses are passed through untokenized.** Free-form text hits the model unchanged. Structured JSON responses are required for protection to kick in.
- **Non-text MCP content parts are passed through untokenized.** Image/resource/blob parts are not protected.
- **No collective (table) tokens.** A tool returning 500 rows with 5 sensitive fields mints 2,500 individual tokens.

  The cost is worth stating precisely, because the obvious guess is wrong. It is **not** context size: tokens are 14 characters and *replace* the values they hide (an IBAN is 27, an email around 18), so a tokenized response is barely larger than the original — measured at +3% on 500 rows × 5 fields (84,186 → 86,906 characters). The cost is that the model **cannot operate on the result**. Several hundred unordered opaque strings carry no structure: the model cannot sort them, cannot tell they are comparable quantities, and to use `blindfold_compute` would have to enumerate every token by hand in its code. In practice it either processes a subset and reports a confident wrong answer, or gives up.

  This bites only when your tools return long lists; the threshold is a few dozen rows. Single-record lookups are unaffected.

  **Fix: expensive — the largest item on the roadmap.** It touches the tokenizer, the vault, the sandbox, and cascading invalidation. It is also the only thing that closes the one-bit oracle described above, since a fixed operation set removes arbitrary control flow. Worth doing when a real workload needs it, not before.

- **No inbound prompt NER.** Only outbound tool-response tokenization.

### Model UX
- **Token meaning is declared per-path on the tool, not per-token on the result.** The proxy appends each tool's protected paths — with their `semantic_type` and `unit` — to that tool's `description`, so the model is told once rather than on every call. Two consequences: (a) tokens sharing a path share one description, so the model can only tell them apart by their position in the JSON — fine while tokens stay in place, insufficient once collective (table) tokens land; (b) the description is static, derived from config, so it names paths the tool *may* return, not the ones a given response actually contained.
- **Runtime dtype is not surfaced.** The vault records whether a hidden value is a number, string, or object, but the tool description is built from config alone and cannot know. The model infers it from `semantic_type`/`unit`. Declaring dtype in `blindfold.yaml` would close this if it ever matters.
- **[Mode B only]** `describe_schema()` is exported but nothing calls it for you — in-process library users must add it to their own tool definitions. Mode A (CLI proxy) does it automatically. Since Mode B is the mode where the whole loop closes, this means the feature is currently automatic only in the mode that cannot show the user a result.

  **Fix: three lines per integration.** Append `describe_schema(fields)` to the tool description you pass your LLM SDK. The `examples/` demos should do it and currently do not.

- **The prompt fragment that keeps placeholders intact is not shipped.** Rehydration depends on the model reproducing `⟦tok_…⟧` verbatim in its answer. The instruction that makes it do so lives in `SYSTEM_PROMPT` in [`examples/demo_chat.py`](examples/demo_chat.py) — copy it. The README previously said it shipped with the proxy; it does not.

  **Fix: trivial** — export it as a package constant.

- **Rehydrated values are rendered with `str()`.** For numbers and strings this is what you want. A hidden value that is an object or a list renders as its Python representation (single quotes, `True`/`None`), not JSON. Cosmetic until you hide a structured field.

### Policy
- **Only `SessionBoundPolicy` ships.** No `webhook`, `claims`, or `allow_all` implementations yet — though the port is in place so they are additive to build. **Fix: small, one class each**; the wiring already calls `can_reveal` and `can_compute` at the right points.
- **Session isolation is per proxy process, not per user.** `SessionBoundPolicy` compares a `session_id` that Mode A generates once per proxy launch. That is real isolation between two runs, and no isolation between two users sharing one run — which is fine because no multi-user deployment exists yet. **Fix:** comes with server mode; the policy contract does not need to change, only who supplies the `session_id`.

### Packaging
- **Not published to PyPI.** `pipx install blindfold` / `uv add blindfold` will not get you this project. Install from source. **Fix: trivial** whenever the name and the release are settled.
- **Cross-platform CI missing** — see Transport above. Developed on Windows; the sandbox findings above show at least one behavior (socket initialization) that differs by platform, so "should work on Linux" is an assumption, not a result.

---

## When Blindfold is the wrong tool

- **You need the model to see the value.** For qualitative reasoning on hidden data, use a different architecture: a self-hosted model, or a workflow where the model produces a plan and code that runs client-side against the real data.
- **Your sensitive data is in free-form text tool responses.** Blindfold won't detect it without NER. Restructure your MCP server to emit structured JSON, or wait for the NER pass on the roadmap.
- **You need the end user to read the real values, through a client you did not write.** Mode A cannot deliver them — see the by-design entry above. Use Mode B.
- **You need protection against a model actively trying to extract the values.** The current sandbox does not provide it: exception text carries values out in one call, and success-versus-failure carries one bit per call regardless of any sandbox. Do not reach for the Docker sandbox as the answer — it closes network and filesystem, not either of those channels. Until the compute surface is narrowed, either don't enable blind compute on data whose exposure you cannot tolerate, or accept that your threat model assumes a cooperative model.
- **You need the vault to survive restarts.** Add a storage adapter yourself, or wait for SQLite (roadmap) — and note that at the default one-hour TTL, persistence alone would not keep yesterday's conversation readable.

---

## Reporting a limitation you hit

If you run into behavior that seems wrong (rather than one of the limits above), open an issue at <https://github.com/ManuelPr/blindfold/issues>. Include:
- The config snippet you used
- The tool response Blindfold received (with any real values redacted by you)
- What you expected vs. what happened
