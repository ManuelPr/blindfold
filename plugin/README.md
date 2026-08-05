# Blindfold — Claude Code plugin

Keeps values from your private tools out of the model's context, lets the model
still compute with them, and shows you the real ones on screen.

## The four pieces

| Piece | Effect |
|---|---|
| `SessionStart` hook | Before the first prompt, tells the model which paths come back as `⟦tok_…⟧`, what they mean, that `blindfold_compute` is how to operate on them, and to reproduce placeholders verbatim. |
| `PostToolUse` hook | Replaces declared fields in a tool result with placeholders **before the model sees them**. Applies to every tool — MCP servers, `Bash`, `Read`, `WebFetch`. |
| `blindfold` MCP server | Offers `blindfold_compute`: the model submits code over placeholders, gets a new placeholder back, never a value. |
| `MessageDisplay` hook | Puts the real values back **on screen only**. The transcript keeps the placeholders, so nothing re-enters the model's context on the next turn. |

The first and third exist because a host's hook system **cannot add a tool or
rewrite a tool description**. The CLI proxy does both by editing the
`tools/list` response as it goes past; here the schema briefing has to arrive as
session context, and blind compute has to arrive as a real MCP server.

Which fields are sensitive is declared per tool in `blindfold.yaml`. Nothing is
guessed: a field you did not declare passes through in cleartext.

## Requirements

1. **The `blindfold` command on your `PATH`.** Both the hooks and the MCP server
   are invoked by name. From a clone:

   ```bash
   uv tool install .        # or: pipx install .
   ```

2. **`storage.backend: sqlite`.** Every hook run is a separate process, and the
   MCP server is a third one. With a memory vault they would each get their own
   empty dictionary, so they refuse to run instead of minting tokens nobody can
   resolve.

3. **A `blindfold.yaml` in the directory you start Claude Code from**, or
   `--config` added to the commands in `hooks/hooks.json` and `.mcp.json`.

## Setup

```yaml
# blindfold.yaml
storage:
  backend: sqlite
  path: ./vault.db          # holds cleartext values — see LIMITATIONS.md#storage

tokens:
  default_ttl: 3600

schemas:
  mcp__hr__get_salary:      # Claude Code names MCP tools mcp__<server>__<tool>
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
```

Then:

```bash
claude --plugin-dir ./plugin
```

Run `/plugin` and check the **Errors** tab if the hooks or the MCP server do not
start.

## What you will see

Ask something that reaches a protected tool. The model works with placeholders
throughout — including when it calls `blindfold_compute` to compare or aggregate
them — and your screen shows the real values in the final answer.

To confirm it is actually working rather than quietly doing nothing, look at the
transcript: the tool results and the assistant message there should still
contain `⟦tok_…⟧`.

## Behaviour worth knowing before you rely on it

- **A tool with declared fields whose result is not JSON gets blocked**, not
  passed through. Blindfold cannot tell which part of a free-text answer is
  sensitive, and letting it through would send the values it was asked to
  protect straight to the model.
- **Undeclared tools are untouched.** That is intended, and it means the
  protection is exactly as good as your `schemas` section.
- **The MCP server infers the session from the tokens it is given.** An MCP
  connection carries no session identity, so the server reads the session off
  the input tokens and refuses to mix two. Possession of a token is therefore
  treated as proof of belonging to its session.
- **The vault file holds cleartext.** Encryption at rest is not implemented.
  Protect the file with filesystem permissions and keep TTLs short.
- **`MessageDisplay` fires on every assistant message** with a 10-second budget;
  the hook returns immediately when the text contains no placeholders.

See [`../LIMITATIONS.md`](../LIMITATIONS.md) for the full inventory.
