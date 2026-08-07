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

Blindfold has three integration modes; a few of the limits below apply to only one of them.

- **Mode A: CLI proxy** — `blindfold -- <mcp-server>`, wraps a stdio MCP server. Requires that your app already speak MCP (Claude Desktop, Cursor, custom agent on the `mcp` client SDK, etc.). **Protects, but does not deliver:** with a client you did not write, the values never come back — read [Rehydration requires a client you control](#rehydration-requires-a-client-you-control) before choosing this mode.
- **Mode B: In-process library** — `from blindfold import ...`, called from your existing agent loop. Works with any LLM SDK (Anthropic, OpenAI, Gemini, self-hosted, LangChain, LlamaIndex, …); no MCP required.
- **Mode C: Claude Code plugin** — three host hooks plus a one-tool MCP server, instead of a proxy. Tokenizes every tool rather than only MCP servers, and reveals values on screen while the transcript keeps the placeholders. **Requires `storage.backend: sqlite`**, since each hook run and the server are separate processes. See [`plugin/README.md`](plugin/README.md).

For a user-facing comparison — what each mode can and cannot do, and how to choose — see [`docs/modes.md`](docs/modes.md).

Unless a bullet is tagged `[Mode A only]`, `[Mode B only]` or `[Mode C only]`, the limit applies to all of them. See [`docs/architecture.md#3-two-integration-modes`](docs/architecture.md#3-two-integration-modes) for the full picture.

---

## By-design limits (permanent)

These come from the shape of the problem. No amount of implementation work removes them; they are the deal you accept when adopting a proxy-based approach.

### Rehydration requires a client you control

Blindfold can put placeholders into the model's context. It cannot take them out of the model's answer unless your code handles that answer.

`rehydrate()` is a function call. In Mode B you make it yourself, on the final text, before display — this is the normal case and it works. In Mode A the proxy also answers a custom JSON-RPC method, `blindfold/rehydrate`, but **that method is not part of MCP**: it exists because this project invented it. No third-party MCP client knows to call it.

Concretely, wrapping your server with `blindfold -- …` under Claude Desktop, Cursor, Zed or any other client you did not write produces an assistant that answers:

> The higher earner is ⟦tok_9c1bf051⟧.

and stops there. The values are genuinely protected — the provider never saw them — but the end user cannot read the answer either.

This is structural *within MCP*. The proxy sits on the tool channel, underneath the client; the assistant's final message never passes through it, and the protocol gives a server no hook on what the model says. The obvious workaround — exposing rehydration as an ordinary tool the model can call — defeats the entire design, because a tool result goes into the model's context, which is exactly where the real values must not go.

**The escape is a host feature, not a protocol one.** A host that offers its own hook on displayed text can do what MCP cannot. Claude Code does: `MessageDisplay` rewrites what appears on screen while leaving the transcript untouched — see Mode C in the [README](README.md#quick-start) and [`plugin/`](plugin/README.md). That is not a loophole in this limit, it is the limit's shape: rehydration needs someone who owns the final message, and a host owns it even when your application does not.

**What this means in practice:** Mode A is the right choice when hiding values from the LLM provider is the whole goal and placeholders in the output are acceptable (audit trails, pipelines whose output is consumed by code, a client that will grow support for the custom method). If a human has to read the values, you need Mode B, a client of your own, or a host with a display hook.

### Blind compute answers one bit per call, whatever the sandbox

`blindfold_compute` either returns a token or raises an error, and the model can choose which by writing code whose *success* depends on a hidden value:

```python
result = 1 / 0 if resolve("⟦tok_…⟧") > 50000 else "ok"
```

Error means "yes". About twenty of those recover an exact salary. Nothing in the sandbox prevents it, because nothing about that code is illegal — it is the tool being used exactly as specified, and no isolation technology (Docker included) hides from the caller whether a call succeeded.

It is listed as by-design because the only thing that removes it is giving up arbitrary code: replacing free-form Python with a fixed set of operations whose control flow cannot depend on the values. That is a change of contract rather than a bug fix — and it has now been made, as a second tool rather than a replacement.

**There is now a path without it.** `blindfold_table` takes a fixed set of operations instead of code, and every well-formed query succeeds — comparisons across types do not match rather than raising, an empty aggregate returns nothing rather than erroring — so no failure carries a bit about the data. Where your data is a list, prefer a collective token and this tool; the oracle below applies only to `blindfold_compute`.

Two things bound the damage for compute, which still runs arbitrary Python: the policy check runs per input token, so the model can only interrogate values it was already allowed to compute on; and each call is one bit, so extraction is visible in the call log as an obvious pattern of repeated compute calls on the same token. Log those calls if this concerns you.

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

- ~~**No encryption at rest.**~~ **Closed, with a condition.** `encrypt_at_rest: true` seals every value with AES-256-GCM before it is written, and the key must come from outside the file — `BLINDFOLD_VAULT_KEY`, 32 bytes base64, or passed to the store directly. There is deliberately no way to keep the key beside the database, because that is decoration rather than encryption. Needs the optional extra: `pip install blindfold[encryption]`.

  What it does not hide: only the value is sealed. Token, session, timestamps and lineage stay readable, because they are what the store queries on and none of them is the secret. Holding the file still tells you **how many** records exist, **when**, and **in which session** — shape, not content.

  Guardrails, because a mistake here is silent by nature: the token is the AEAD associated data, so a ciphertext moved to another row will not open; a wrong key raises rather than returning garbage; and a file written cleartext cannot be opened encrypted or the reverse — the store records which it is and refuses the mismatch instead of failing later at the first read.

  **Still true:** `backend: memory` remains the default, and with no encryption the vault file is the secret it contains. `encrypt_at_rest` with `backend: memory` is refused rather than ignored — there is no "at rest" there to encrypt.

- ~~**Expired records are never actually removed.**~~ **Closed for expiry**, still open for invalidation. `put()` now sweeps expired records on an interval (60 seconds by default, settable per instance via `purge_interval_s`), so a TTL bounds how long a value *exists* rather than only how long it resolves. The sweep lives in the store rather than in the proxy, because Mode B builds its own store and never goes near the proxy. Consequence of amortizing it: an expired record can outlive its TTL by up to one interval, which is why the interval is settable.

  **Still open:** `invalidate_cascade()` is implemented and tested but nothing calls it. Invalidating a token and its descendants remains something your code does deliberately, not something that happens.

- ~~**No concurrency handling on the vault.**~~ **Closed.** `MemoryTokenStore` now takes an `RLock` on every method, as `SQLiteTokenStore` already did.

  This was written as "not needed today — one proxy process, one child, one session", and that was wrong. One process is not one thread: the proxy runs blind compute in a worker thread ([`proxy.py`](src/blindfold/proxy.py), so the sandbox does not stall both pumps for its timeout) while `_pump_child_to_client` keeps tokenizing tool results on the event loop. A model that sends a tool call and a `blindfold_compute` in the same turn — routine — had both threads writing the dict, and the periodic expiry sweep would raise `dictionary changed size during iteration` and take a pump down with it. The conformance suite now hammers both stores from four threads with the sweep forced on every put.

### Transport

- ~~**A JSON-RPC batch crashed the proxy.**~~ **Closed.** A batch is a JSON array; the pump called `.get()` on it, raised `AttributeError`, killed its task, and the connection wedged with no error visible to the client. Batches are now forwarded untouched — Blindfold does not inspect them, and saying so is more honest than pretending to.

- ~~**Blind compute blocked the whole proxy.**~~ **Closed.** `handle_blindfold_compute` ran inline in the async pump and the sandbox is synchronous, so for the length of a computation — up to the 5-second timeout — the proxy forwarded nothing in either direction. It now runs in a worker thread, which is why `SQLiteTokenStore` holds a reentrant lock and opens its connection with `check_same_thread=False`.

- ~~**[Mode A only] `resources/*` passes through untouched.**~~ **Closed.** A server exposing salaries as a resource rather than as a tool used to get no protection at all. The proxy now tracks `resources/read` requests and tokenizes the returned contents, driven by a `resources:` config section keyed by **URI glob** — `file:///hr/*.json` — because a URI is what a resource has instead of a name. The URI on the returned part wins over the requested one, so a template read answered with a concrete URI still matches.

  A declared resource that comes back as a blob, or as text that is not JSON, is reported on the operator's stderr. It is still forwarded, because the proxy does not rewrite what it cannot parse — so that is a warning, not a guarantee.

- **[Mode A only] `prompts/*` is still not inspected.** Prompt templates are instructions rather than API data and nothing can be declared against them. If your server puts sensitive values inside prompt templates, the proxy will not find them.
- **CLI proxy is stdio MCP only. [Mode A only]** The `blindfold -- <cmd>` CLI wraps a single downstream stdio MCP server. No HTTP proxy mode for REST APIs, no wrapping of remote/SSE MCP servers. Mode B (in-process library) has no transport concept — it plugs into any LLM SDK loop directly, MCP or not.
- ~~**Cross-platform CI missing.**~~ **Closed.** `.github/workflows/ci.yml` runs the suite on Linux, macOS and Windows, Python 3.11–3.13, plus a dedicated job asserting the sandbox findings above per platform rather than trusting a measurement taken once, on one machine — which is exactly how the next entry was found.

- ~~**`blindfold hook` and `blindfold audit` mishandled placeholders on Windows — one side crashed, the other went silent.**~~ **Closed, in two parts, both found against a real host rather than a test.** First: `uv tool install .` on a stock Windows shell, then `blindfold hook session-start` against a config declaring any protected path, raised `UnicodeEncodeError: 'charmap' codec can't encode character '⟦'` at the point it printed the briefing. Windows' default console codepage (cp1252) cannot represent U+27E6/U+27E7, and nothing set `PYTHONIOENCODING` for a binary a host invokes by bare command name — which is exactly how the Mode C plugin's hooks and MCP server run it. Fixed by reconfiguring stdout/stderr to UTF-8 at the top of `main()`.

  That fix shipped, and the next real reproduction found the other half: `hook message-display` on a message containing a genuine placeholder returned *nothing* — no crash, no stderr, no output, exit 0. Reading is worse than writing here: a mis-decoded `⟦` doesn't raise, it silently becomes a different character, so `TOKEN_PATTERN` never matches and the hook concludes there is nothing to rewrite. Confirmed by running the identical call with and without `PYTHONIOENCODING` set — one resolved the token to `62000`, the other produced silence. `stdin` now gets the same reconfiguring as stdout/stderr, in the same place.

  Both fixes are in one spot, `main()`, so every subcommand is covered — including `audit`, whose entire job is printing placeholders in a leak report, and `python -m blindfold`, which routes through the same function. Harmless to the proxy's own JSON-RPC path, which reads and writes raw bytes via `.buffer` and never touches this text layer.

- **A declared-but-unprotectable PostToolUse result is diagnosed, not just refused.** The block reason for "no usable text" now includes the event's key names and Python types — never values, since the event can legitimately hold the real hidden data at that point. This is how the `tool_response` finding below was made: the diagnostic showed the real shape on the first live reproduction instead of leaving it a mystery.

- ~~**MCP tool results were never recognized by `PostToolUse`.**~~ **Closed.** Every general hook-doc example (Edit, Bash) shows the tool's result arriving as `tool_output`, a flat string — and that is what the code originally looked for. A real MCP tool call sends no `tool_output` key at all: the result arrives as `tool_response`, a list mirroring MCP's own content shape, `[{"type": "text", "text": "..."}]`. Every MCP-backed tool with declared fields was blocked, unconditionally, until this was found — which is the entire tool surface Mode C exists to protect. Both shapes are now read; a `tool_response` with more than one part, or a non-text part, is still refused rather than guessed at, consistent with declaring a table and a field inside it as an unresolvable overlap elsewhere in this document.

- ~~**`MessageDisplay` never revealed anything, and looked exactly like a host that never invoked the hook at all.**~~ **Closed — the hook was firing correctly the whole time.** It read `event.get("message_text")`, the field name the general hook documentation uses elsewhere; that key does not exist on this event. The real payload carries `turn_id`, `message_id`, `index`, `final`, and `delta` — "the newly completed lines" of the message as it streams — confirmed only by asking the live host's own `/hooks` inspector to print the schema, since no page of the hosted docs describes it. Reading the wrong key meant the check always failed and the handler always returned `None` — correctly, given what it was looking at, which is exactly why nothing anywhere logged a failure: there was never an error to report, only a field that was never there. A `--debug` log grepped across an entire session for "MessageDisplay" came back empty, `/plugin`'s Errors panel showed nothing, and a second LLM consulted for a diagnosis fabricated a specific, wrong changelog citation to explain it — the field name only came from making the host state its own schema out loud. Fixed by reading `delta`; each delta is handled independently, on the assumption that a placeholder does not span a line break, since deltas are delivered as whole completed lines.

- ~~**`blindfold audit` flagged a `blindfold_compute` result as a leak when it was really a name the model already knew.**~~ **Closed.** `blindfold_compute` mints a fresh token for `result` unconditionally — even when the code is something like `result = 'Andrea Tuscano' if resolve(a) > resolve(b) else 'Manuel Pernigotto'`, where the actual secret is *which* name won, not the names themselves, both already known to the model and typed into its own code. That literal sits in the transcript, in the tool call's own arguments, before the derived token even exists. `audit`'s substring match couldn't tell "the model wrote this itself" from "this came out of the vault," and reported the former as a leak. Fixed by scanning the transcript for string literals inside any `blindfold_compute` call's `code` argument and excluding a `blind_compute`-lineage match against that set — reported separately as "explained," not silently dropped. A value actually produced by `resolve(...)` arithmetic (a summed salary, say), never typed as a literal anywhere, is unaffected and still flagged.

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

- **Overlapping declarations used to corrupt each other; they are now refused at load.** The tokenizer walks a tool's fields in order, so the same path declared twice tokenized its own placeholder the second time round — the vault held a token whose value was another token, and the user read `⟦tok_…⟧` where the value should have been. A path containing another (`$.employee` alongside `$.employee.salary`) did the same to a subtree. Both are rejected with a message naming the pair. For resources the overlap depends on the URI and cannot be caught statically, so matching globs are merged with the redundant declaration dropped.

- **Non-JSON tool responses are passed through untokenized.** Free-form text hits the model unchanged. Structured JSON responses are required for protection to kick in.
- **Non-text MCP content parts are passed through untokenized.** Image/resource/blob parts are not protected.
- ~~**No collective (table) tokens.**~~ **Closed.** A list declared under `tables:` becomes one token for the whole thing, and the model is told the column names and what they mean. Measured on 500 employees x 5 fields: 2,500 individual tokens before, **1** after, and the model can answer "top three by salary in Eng" — which it could not do at all with 2,500 opaque strings, having no way to sort them or even tell they were comparable.

  The query language is a fixed set of operations (`filter`, `sort_by`, `limit`, `select`, `sum`, `mean`, `min`, `max`, `count`) executed by Blindfold, not code. Results come back as new tokens, and a row result carries the columns that survived, so queries can be built up in steps. **This path runs no sandbox**, because no code the model wrote is ever executed.

  What it does not do: aggregate across two tables, group by a column, or join. Those are the obvious next operations and none of them is in yet.

- **No inbound prompt NER.** Only outbound tool-response tokenization.

### Model UX
- **Token meaning is declared per-path on the tool, not per-token on the result.** The proxy appends each tool's protected paths — with their `semantic_type` and `unit` — to that tool's `description`, so the model is told once rather than on every call. Two consequences: (a) tokens sharing a path share one description, so the model can only tell them apart by their position in the JSON — fine while tokens stay in place, insufficient once collective (table) tokens land; (b) the description is static, derived from config, so it names paths the tool *may* return, not the ones a given response actually contained.
- **Runtime dtype is not surfaced.** The vault records whether a hidden value is a number, string, or object, but the tool description is built from config alone and cannot know. The model infers it from `semantic_type`/`unit`. Declaring dtype in `blindfold.yaml` would close this if it ever matters.
- **[Mode B only]** `describe_schema()` is exported but nothing calls it for you — in-process library users must add it to their own tool definitions. Mode A (CLI proxy) does it automatically. Since Mode B is the mode where the whole loop closes, this means the feature is currently automatic only in the mode that cannot show the user a result.

  **Fix: three lines per integration.** Append `describe_schema(fields)` to the tool description you pass your LLM SDK. The `examples/` demos should do it and currently do not. Mode C solves the same problem differently — `describe_config(config)` builds one briefing for the whole session, because a host lets nobody edit a tool description; that function is reusable from Mode B if a single up-front briefing suits your loop better than per-tool text.

- **The prompt fragment that keeps placeholders intact is not shipped as a constant.** Rehydration depends on the model reproducing `⟦tok_…⟧` verbatim in its answer. Mode C now carries that instruction inside the `SessionStart` briefing, so it is covered there. Mode A and Mode B still need you to copy it from `SYSTEM_PROMPT` in [`examples/demo_chat.py`](examples/demo_chat.py). The README previously said it shipped with the proxy; it does not.

  **Fix: trivial** — export it as a package constant.

- **Rehydrated values are rendered with `str()`.** For numbers and strings this is what you want. A hidden value that is an object or a list renders as its Python representation (single quotes, `True`/`None`), not JSON. Cosmetic until you hide a structured field.

### Policy
- **[Mode C only] The compute server infers the session from its inputs.** An MCP connection carries no session identity, so `blindfold mcp-server` reads the session off the input tokens, refuses to mix two, and mints the result into that session — which is what lets the display hook reveal it rather than rendering `[redacted]`. The consequence, stated plainly: **possession of a token is treated as proof of belonging to its session.** Tokens are unguessable and never leave the trust zone, but they do reach the model, so a model can compute on any token it has seen — which are the tokens of its own session anyway. In Mode A and Mode B the session is known independently and this substitution does not happen.

  **Fix:** needs a way for the host to tell an MCP server which session it serves. Nothing in MCP provides one today.

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
