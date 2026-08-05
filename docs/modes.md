# Choosing a mode

Blindfold has three ways to plug in. They protect the same things and differ in
one respect that decides which one you want: **who owns the model's final
answer**, and therefore who can put the real values back.

Read this before installing anything. Picking the wrong mode gets you a working
system that never shows anyone a result.

---

## What Blindfold does, in one paragraph

When your agent calls a tool, the tool's answer normally goes straight into the
model's context and therefore to your LLM provider. Blindfold intercepts that
answer and replaces the fields you declared with placeholders — `⟦tok_a58cbaf0⟧`
— keeping the real values in a local vault. The model reasons over placeholders.
When it needs to compare, sort or add them up, it calls a tool that does the
arithmetic on the hidden values and hands back another placeholder. At the end,
your code (or the host) swaps the placeholders for real values before a human
reads them.

Nothing is guessed. You declare, per tool, which JSON paths are sensitive. A
field you did not declare goes to the model in cleartext.

---

## Which mode

| Your situation | Mode |
|---|---|
| You work inside **Claude Code** | **C** |
| You write the agent loop yourself, in Python, with any LLM SDK | **B** |
| You use an MCP client someone else wrote (Claude Desktop, Cursor, Zed) **and can live without seeing the values** | **A** |
| You use an MCP client someone else wrote and a human must read the values | **none of them** — see [the catch](#the-catch-who-puts-the-values-back) |

---

## The catch: who puts the values back

Putting placeholders *into* the model's context is easy from anywhere. Taking
them *out* of its answer needs someone who holds that answer.

- **Mode B**: your code holds it. You call `rehydrate()`. Works.
- **Mode C**: Claude Code holds it, and offers a hook that rewrites what the
  screen shows. Works — and better than Mode B, see below.
- **Mode A**: the proxy sits *below* the client, on the tool channel. The
  model's final message never passes through it, and MCP gives a server no hook
  on what the model says. **It cannot rehydrate.**

So under Claude Desktop with Mode A, your assistant answers:

> The higher earner is ⟦tok_9c1bf051⟧.

and stops there. The protection is real — your provider never saw a salary — but
nobody can read the answer. This is structural, not a missing feature.

---

## Mode A — CLI proxy

**What it is.** A process that sits between your MCP client and your MCP server
and edits the traffic. No application code changes.

**Setup.** Change the command your client already runs:

```jsonc
// before
{ "command": "python", "args": ["-m", "your_org.hr_mcp"] }
// after
{ "command": "blindfold",
  "args": ["--config", "blindfold.yaml", "--", "python", "-m", "your_org.hr_mcp"] }
```

**What you get**

- Tool results (`tools/call`) tokenized against your `schemas:` section.
- MCP resources (`resources/read`) tokenized against your `resources:` section,
  matched by URI glob.
- Each protected tool's description gains a note saying which paths come back as
  placeholders and what they mean, so the model knows what it is holding.
- `blindfold_compute` is added to the tool list automatically;
  `blindfold_table` too, if any tool declares a table.

**What you do not get**

- **Rehydration**, unless the client is yours and you teach it to call the
  custom `blindfold/rehydrate` JSON-RPC method. No third-party client does.
- Protection for `prompts/*`.
- Protection for tool results that are not JSON, or that come back as images or
  blobs. They pass through untouched.
- Anything outside that one MCP server. The proxy wraps one command.

**Pick it when** hiding values from the LLM provider is the whole goal and
placeholders in the output are fine — audit trails, pipelines whose output is
read by code, or a client you plan to extend.

---

## Mode B — in-process library

**What it is.** You import Blindfold and call it at five points in the agent
loop you already have. No MCP required; works with Anthropic, OpenAI, Gemini,
Ollama, LangChain, or a loop you wrote yourself.

**Setup.**

```python
from blindfold import PLACEHOLDER_PROMPT, rehydrate
from blindfold.config import load_config, schema_fields_for, table_schemas_for, build_token_store
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.tokenizer import describe_schema, describe_tables, tokenize_result
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools import blindfold_compute, blindfold_table

config = load_config("blindfold.yaml")
store, policy, sandbox = build_token_store(config), SessionBoundPolicy(), SubprocessSandbox()
session_id = f"user_{user_uuid}"
```

The five points:

1. **Put `PLACEHOLDER_PROMPT` in your system prompt.** Rehydration only works on
   placeholders the model reproduced exactly; this is what tells it to.
2. **Describe your protected tools.** Append `describe_schema(...)` and
   `describe_tables(...)` to each tool's description before you send the tool
   list. Nothing does this for you here.
3. **Advertise the compute tools.** Add `blindfold_compute.build_tool_definition()`
   and, if you declared tables, `blindfold_table.build_tool_definition()`.
4. **Tokenize every tool result** before feeding it back:
   `tokenize_result(payload, tool_name, fields, store, session_id, ttl, tables=tables)`.
5. **Route the compute calls** to `handle_blindfold_compute` /
   `handle_blindfold_table`, and **call `rehydrate(final_text, session_id, store, policy)`**
   before display.

See [`examples/demo_chat.py`](../examples/demo_chat.py) for the whole thing
against the Anthropic SDK.

**What you get**

- Everything, including rehydration. This is the only mode where you own every
  seam.
- Freedom over session identity: use your real user id, and `SessionBoundPolicy`
  will refuse another user's placeholders.

**What you do not get**

- Anything automatically. Every point above is yours to wire, and a forgotten
  step fails quietly — a missing step 2 leaves the model guessing what a
  placeholder is; a missing step 1 makes it paraphrase placeholders and break
  rehydration.
- Protection for tools you forget to run through `tokenize_result`.

**Pick it when** you write the loop, or when a human has to read the values and
you are not in Claude Code.

---

## Mode C — Claude Code plugin

**What it is.** Three hooks plus a small MCP server, instead of a proxy. Claude
Code calls Blindfold at the right moments; nothing sits in the middle.

**Setup.**

```bash
uv tool install .            # puts `blindfold` on PATH
claude --plugin-dir ./plugin
```

with a `blindfold.yaml` in the directory you start from:

```yaml
storage:
  backend: sqlite            # required, see below
  path: ./vault.db

schemas:
  mcp__hr__get_salary:       # Claude Code names MCP tools mcp__<server>__<tool>
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
```

**The four pieces**

| Piece | Does |
|---|---|
| `SessionStart` hook | Before your first prompt, tells the model which paths come back as placeholders, what they mean, how to compute on them, and to copy them verbatim |
| `PostToolUse` hook | Replaces declared fields in a tool result before the model sees it — for **every** tool, not just MCP: `Bash`, `Read`, `WebFetch` too |
| `blindfold` MCP server | Offers `blindfold_compute` and `blindfold_table` |
| `MessageDisplay` hook | Puts the real values back **on screen only** |

**What you get**

- Rehydration, with a property no other mode has: because `MessageDisplay`
  changes only what is displayed, **the transcript keeps the placeholders**. You
  read real values; the model, on the next turn, still sees placeholders. The
  values never re-enter its context.
- The widest coverage: any tool Claude Code runs, not only one MCP server.

**What you do not get**

- **MCP resources are not protected.** The hooks are tool-scoped. If your server
  exposes sensitive data as a resource rather than a tool, use Mode A for it.
- **A memory vault will not work.** Every hook run is a separate process and the
  MCP server is a third one, so the vault must be a shared file. Blindfold
  refuses to run the hooks with `backend: memory` rather than minting
  placeholders nobody can resolve.
- **`blindfold` must be on `PATH`** for every hook process and for the server.

**One behaviour to know before you rely on it:** if a tool with declared fields
returns something that is not JSON, the call is **blocked**, not passed through.
Blindfold cannot tell which part of a free-text answer is sensitive, and letting
it through would send the model exactly the values you asked to hide.

**Pick it when** you work in Claude Code. It is the best-fitting mode.

---

## Side by side

| | A — proxy | B — library | C — plugin |
|---|:---:|:---:|:---:|
| Tool results tokenized | yes | yes | yes |
| MCP resources tokenized | yes | yes, if you call it | **no** |
| Coverage beyond one MCP server | no | your tools | **every tool** |
| Model told what placeholders mean | automatic | you wire it | automatic |
| Blind compute on single values | automatic | you wire it | automatic |
| Table queries on long lists | automatic | you wire it | automatic |
| **Values reach the user** | **no** | yes | yes |
| Values kept out of the next turn | n/a | no | **yes** |
| Persistent vault required | no | no | **yes** |
| Application code changes | none | five points | none |

---

## What no mode does

These are properties of the approach, not gaps to be filled. The full list, with
reasoning, is in [`LIMITATIONS.md`](../LIMITATIONS.md).

- **Your prompt is not protected.** If you ask *"what is Andrea's salary?"*, the
  name and the intent go to the provider. Only the answer is hidden.
- **The model cannot judge a hidden value.** *"Who earns more"* works. *"Is this
  a competitive salary for Milan?"* cannot — that needs seeing the number.
- **Undeclared fields pass through in cleartext.** Protection is exactly as good
  as your config. Declare defensively, including error paths.
- **`blindfold_compute` runs arbitrary Python**, so a model actively trying to
  extract a value can learn one bit per call by writing code that fails on
  purpose. `blindfold_table` cannot be used that way — where your data is a
  list, prefer a table.
- **Access control is your API's job.** Blindfold forwards requests untouched.
  If your API answers anyone, Blindfold faithfully hides data the caller should
  never have received.

---

## Storage, in one line

`backend: memory` is the default and loses everything when the process ends.
Use `backend: sqlite` when placeholders must outlive a restart, or when two
processes need the same vault — which Mode C always does. The file holds
cleartext unless you set `encrypt_at_rest: true` and supply
`BLINDFOLD_VAULT_KEY`; there is deliberately no way to put the key in the config
file it protects.
