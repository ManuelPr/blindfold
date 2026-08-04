"""SubprocessSandbox — best-effort isolation via a fresh Python subprocess.

The invariant that matters: **nothing derived from a resolved value may appear
in what this module returns or raises.** The only sanctioned way out of the
sandbox is `result`, which the caller stores as a new vault token. Error text
is a second way out, so it carries exception *types*, never messages, and
never child output. Detail goes to the operator's stderr instead.

Current limitations (see LIMITATIONS.md#sandboxing):
- Full `__builtins__` in the child, so `open()` and `import` work.
- Network reachable on Linux/macOS; it fails on Windows only because the
  stripped environment omits SystemRoot, which is an accident, not a control.
- Success-versus-failure still leaks one bit per call, and no sandbox closes
  that — only a fixed operation set would.
- Defenses actually in place: clean env, `python -I`, timeout, subprocess
  boundary, and the error-channel hygiene above.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from blindfold.ports.sandbox import ComputeSandbox, SandboxError

_CHILD_WRAPPER = r"""
import json, sys

# Nothing below may put an exception's *message* into the envelope. The
# envelope is handed to the model, and a message is attacker-controlled text:
# `raise ValueError(resolve(tok))` would return the hidden value in one call.
# Exception type names are safe and still tell the model how to fix its code.

try:
    payload = json.loads(sys.stdin.readline())
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"bad stdin: {type(exc).__name__}"}))
    sys.exit(0)

code = payload.get("code", "")
inputs = payload.get("inputs", {}) or {}

def resolve(token):
    if token not in inputs:
        raise KeyError(f"resolve(): token {token!r} not declared in inputs")
    return inputs[token]

globs = {"resolve": resolve, "__builtins__": __builtins__}
locs = {}
try:
    exec(compile(code, "<blindfold_compute>", "exec"), globs, locs)
except Exception as exc:
    print(json.dumps({"ok": False, "error": type(exc).__name__}))
    sys.exit(0)

if "result" not in locs:
    print(json.dumps({"ok": False, "error": "user code did not assign a `result` variable"}))
    sys.exit(0)

try:
    body = json.dumps({"ok": True, "value": locs["result"]}, ensure_ascii=False)
except (TypeError, ValueError) as exc:
    print(json.dumps({"ok": False, "error": f"result is not JSON-serializable: {type(exc).__name__}"}))
    sys.exit(0)

print(body)
"""


class SubprocessSandbox(ComputeSandbox):
    def run(self, code: str, inputs: dict[str, Any], timeout_s: float) -> Any:
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", _CHILD_WRAPPER],
                input=json.dumps({"code": code, "inputs": inputs}, ensure_ascii=False) + "\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxError(f"blind-compute timeout after {timeout_s}s") from exc

        # Both branches below hold child output, which can contain resolved
        # values (anything the code printed, or wrote to stderr before dying).
        # It goes to the operator's stderr for debugging; the raised message
        # reaches the model, so it stays generic.
        stdout = completed.stdout.strip()
        if not stdout:
            print(
                f"[blindfold] sandbox produced no output (exit={completed.returncode}), "
                f"stderr: {completed.stderr[:400]!r}",
                file=sys.stderr,
            )
            raise SandboxError(f"sandbox produced no output (exit={completed.returncode})")

        try:
            envelope = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            envelope = None
        # Shape, not just parseability: a bare `print(62000)` from user code
        # leaves a last line that is valid JSON but is not an envelope.
        if not isinstance(envelope, dict):
            print(f"[blindfold] sandbox output was not an envelope: {stdout[:400]!r}", file=sys.stderr)
            raise SandboxError("sandbox output was not the expected JSON envelope")

        if not envelope.get("ok"):
            raise SandboxError(str(envelope.get("error", "unknown sandbox error")))
        return envelope["value"]
