import subprocess

import pytest

from blindfold.ports.sandbox import SandboxError
from blindfold.sandbox import subprocess_ as subprocess_module
from blindfold.sandbox.subprocess_ import SubprocessSandbox


def test_happy_path_arithmetic():
    sb = SubprocessSandbox()
    result = sb.run(
        code="result = resolve('a') + resolve('b')",
        inputs={"a": 2, "b": 3},
        timeout_s=5.0,
    )
    assert result == 5


def test_happy_path_string():
    sb = SubprocessSandbox()
    result = sb.run(
        code="result = 'A' if resolve('x') > resolve('y') else 'B'",
        inputs={"x": 10, "y": 20},
        timeout_s=5.0,
    )
    assert result == "B"


def test_unicode_roundtrip():
    sb = SubprocessSandbox()
    result = sb.run(
        code="result = resolve('name') + ' 👋'",
        inputs={"name": "Andrea"},
        timeout_s=5.0,
    )
    assert result == "Andrea 👋"


def test_numeric_precision_preserved():
    sb = SubprocessSandbox()
    result = sb.run(
        code="result = resolve('x')",
        inputs={"x": 0.1 + 0.2},  # 0.30000000000000004
        timeout_s=5.0,
    )
    assert result == 0.1 + 0.2


def test_resolve_on_unlisted_token_raises_sandbox_error():
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(
            code="result = resolve('not_declared')",
            inputs={"a": 1},
            timeout_s=5.0,
        )
    assert "not_declared" in str(ei.value) or "KeyError" in str(ei.value)


def test_syntax_error_surfaces_as_sandbox_error():
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError):
        sb.run(
            code="def def def",
            inputs={},
            timeout_s=5.0,
        )


def test_timeout_kills_child():
    # A busy loop, not time.sleep: `import` is blocked in the child, so an
    # importing test would assert the timeout and measure the import instead.
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(
            code="while True:\n    pass\nresult = None",
            inputs={},
            timeout_s=0.5,
        )
    assert "timeout" in str(ei.value).lower()


def test_non_serializable_result_raises_sandbox_error():
    # A set is constructible from the allowed builtins and is not JSON.
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError):
        sb.run(
            code="result = {1, 2}",
            inputs={},
            timeout_s=5.0,
        )


# --- error channel must not carry values ----------------------------------
#
# The error text is handed to the model. Code that puts a resolved value into
# an exception used to return it verbatim in a single call.

SECRET = 62000


def _assert_no_leak(exc: SandboxError) -> None:
    assert str(SECRET) not in str(exc), f"hidden value leaked through the error channel: {exc}"


def test_exception_message_does_not_leak_the_value():
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(code="raise ValueError(resolve('t'))", inputs={"t": SECRET}, timeout_s=5.0)
    _assert_no_leak(ei.value)
    assert "ValueError" in str(ei.value)


def test_assertion_message_does_not_leak_the_value():
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(code="assert False, str(resolve('t'))", inputs={"t": SECRET}, timeout_s=5.0)
    _assert_no_leak(ei.value)


def test_implicit_exception_message_does_not_leak_the_value():
    # The interpreter builds this message itself: "unsupported operand type(s)
    # for +: 'int' and 'str'" is safe, but e.g. KeyError embeds the key.
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(code="result = {}[resolve('t')]", inputs={"t": SECRET}, timeout_s=5.0)
    _assert_no_leak(ei.value)


# The parent must also refuse to pass child output on, whatever the child did.
# These drive it directly with a stubbed child: the allow-list now stops the
# model from printing at all, so going through real code would assert nothing.


def _stub_child(monkeypatch, *, stdout: str, stderr: str = "", returncode: int = 0):
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    # The module calls subprocess.run, so the stub goes on the stdlib module.
    monkeypatch.setattr(subprocess_module.subprocess, "run", fake_run)


def test_child_stderr_never_reaches_the_caller(monkeypatch):
    _stub_child(monkeypatch, stdout="", stderr=f"boom {SECRET}", returncode=1)
    with pytest.raises(SandboxError) as ei:
        SubprocessSandbox().run(code="result = 1", inputs={}, timeout_s=5.0)
    _assert_no_leak(ei.value)


def test_child_stdout_never_reaches_the_caller(monkeypatch):
    _stub_child(monkeypatch, stdout=f"garbage {SECRET}")
    with pytest.raises(SandboxError) as ei:
        SubprocessSandbox().run(code="result = 1", inputs={}, timeout_s=5.0)
    _assert_no_leak(ei.value)


def test_output_that_is_valid_json_but_not_an_envelope_is_refused(monkeypatch):
    # A bare number parses fine and used to reach `envelope.get`, crashing with
    # AttributeError instead of SandboxError.
    _stub_child(monkeypatch, stdout=str(SECRET))
    with pytest.raises(SandboxError) as ei:
        SubprocessSandbox().run(code="result = 1", inputs={}, timeout_s=5.0)
    _assert_no_leak(ei.value)


def test_legitimate_errors_still_name_their_type():
    # Hygiene must not make the model blind to its own mistakes.
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(code="result = undefined_name", inputs={}, timeout_s=5.0)
    assert "NameError" in str(ei.value)


# --- restricted builtins --------------------------------------------------
#
# The child runs the model's code with an allow-list, so the direct routes to
# the filesystem and the network are absent. This is a cost increase, not a
# boundary: reaching object.__subclasses__() through the object graph needs no
# builtins at all. These tests guard the easy routes staying shut.


@pytest.mark.parametrize(
    "code",
    [
        "result = open('pyproject.toml').read()",
        "result = __import__('os').listdir('.')",
        "import os\nresult = os.listdir('.')",
        "import socket\nresult = 'net'",
        "result = eval('1+1')",
        "exec('x = 1')\nresult = 1",
        "result = str(globals())",
        "result = getattr(str, 'upper')('a')",
        "result = type(1).__name__",
        "class C: pass\nresult = 1",
    ],
)
def test_escape_routes_are_absent(code):
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError):
        sb.run(code=code, inputs={}, timeout_s=5.0)


def test_filesystem_is_no_longer_readable():
    # This exact probe returned the working directory before the allow-list.
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(code="import os\nresult = os.listdir('.')", inputs={}, timeout_s=5.0)
    assert "ImportError" in str(ei.value)


def test_aggregation_over_hidden_values_still_works():
    # The allow-list has to leave blind compute usable, or it is not a fix.
    sb = SubprocessSandbox()
    salaries = [62000, 71000, 55000]
    result = sb.run(
        code=(
            "vals = [resolve('a'), resolve('b'), resolve('c')]\n"
            "result = {'total': sum(vals), 'top': max(vals), 'sorted': sorted(vals)}"
        ),
        inputs={"a": salaries[0], "b": salaries[1], "c": salaries[2]},
        timeout_s=5.0,
    )
    assert result == {"total": sum(salaries), "top": 71000, "sorted": sorted(salaries)}


def test_comprehensions_and_try_except_still_work():
    sb = SubprocessSandbox()
    result = sb.run(
        code=(
            "rows = resolve('rows')\n"
            "try:\n"
            "    result = sorted([r['pay'] for r in rows if r['pay'] > 100])\n"
            "except (KeyError, TypeError):\n"
            "    result = []"
        ),
        inputs={"rows": [{"pay": 50}, {"pay": 300}, {"pay": 150}]},
        timeout_s=5.0,
    )
    assert result == [150, 300]
