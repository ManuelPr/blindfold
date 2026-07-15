"""SubprocessSandbox — best-effort isolation via a fresh Python subprocess.

MVP limitations (documented in the spec):
- No network sandbox on Windows.
- No filesystem sandbox — child inherits CWD.
- Only defenses: clean env (no inherited vars), timeout, subprocess boundary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from blindfold.ports.sandbox import ComputeSandbox, SandboxError

_CHILD_WRAPPER = r"""
import json, sys, traceback

try:
    payload = json.loads(sys.stdin.readline())
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"bad stdin: {exc!r}"}))
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
    tb = traceback.format_exception_only(type(exc), exc)[-1].strip()
    print(json.dumps({"ok": False, "error": tb}))
    sys.exit(0)

if "result" not in locs:
    print(json.dumps({"ok": False, "error": "user code did not assign a `result` variable"}))
    sys.exit(0)

try:
    body = json.dumps({"ok": True, "value": locs["result"]}, ensure_ascii=False)
except (TypeError, ValueError) as exc:
    print(json.dumps({"ok": False, "error": f"result is not JSON-serializable: {exc!r}"}))
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

        stdout = completed.stdout.strip()
        if not stdout:
            raise SandboxError(
                f"sandbox produced no output (exit={completed.returncode}, stderr={completed.stderr[:400]!r})"
            )

        try:
            envelope = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise SandboxError(f"sandbox output not JSON: {stdout!r}") from exc

        if not envelope.get("ok"):
            raise SandboxError(str(envelope.get("error", "unknown sandbox error")))
        return envelope["value"]
