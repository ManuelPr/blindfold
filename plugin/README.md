# Blindfold — Claude Code plugin

Keeps values from your private tools out of the model's context, and still
shows you the real ones on screen.

## What it does

Two hooks, wired to the `blindfold` CLI:

| Hook | Effect |
|---|---|
| `PostToolUse` | Replaces declared fields in a tool result with `⟦tok_…⟧` placeholders **before the model sees them**. Applies to every tool — MCP servers, `Bash`, `Read`, `WebFetch`. |
| `MessageDisplay` | Puts the real values back **on screen only**. The transcript keeps the placeholders, so nothing re-enters the model's context on the next turn. |

Which fields are sensitive is declared per tool in `blindfold.yaml`. Nothing is
guessed: a field you did not declare passes through in cleartext.

## Requirements

1. **The `blindfold` command on your `PATH`.** From a clone:

   ```bash
   uv tool install .        # or: pipx install .
   ```

2. **`storage.backend: sqlite`.** Each hook invocation is a separate process,
   so the vault has to be a shared file. With a memory vault the hooks would
   mint tokens into a dictionary that dies immediately, and you would read
   `[unknown token]` forever — so they refuse to run instead.

3. **A `blindfold.yaml` in the directory you start Claude Code from**, or
   `--config` added to the commands in `hooks/hooks.json`.

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

Run `/plugin` and check the **Errors** tab if the hooks do not fire.

## What you will see

Ask something that reaches a protected tool. The model works with placeholders
throughout — including when it calls `blindfold_compute` to compare or
aggregate them — and your screen shows the real values in the final answer.

To confirm it is actually working rather than quietly doing nothing, look at
the transcript: the tool results and the assistant message there should still
contain `⟦tok_…⟧`.

## Behaviour worth knowing before you rely on it

- **A tool with declared fields whose result is not JSON gets blocked**, not
  passed through. Blindfold cannot tell which part of a free-text answer is
  sensitive, and letting it through would send the values it was asked to
  protect straight to the model.
- **Undeclared tools are untouched.** That is intended, and it means the
  protection is exactly as good as your `schemas` section.
- **The vault file holds cleartext.** Encryption at rest is not implemented.
  Protect the file with filesystem permissions and keep TTLs short.
- **`MessageDisplay` fires on every assistant message** with a 10-second
  budget; the hook returns immediately when the text contains no placeholders.

See [`../LIMITATIONS.md`](../LIMITATIONS.md) for the full inventory.
