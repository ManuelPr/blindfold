import pytest

from blindfold.ports.sandbox import SandboxError
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
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(
            code="import time\nwhile True:\n    time.sleep(1)\nresult = None",
            inputs={},
            timeout_s=0.5,
        )
    assert "timeout" in str(ei.value).lower()


def test_non_serializable_result_raises_sandbox_error():
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError):
        sb.run(
            code="result = object()",
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


def test_printed_value_does_not_leak_when_envelope_is_broken():
    # User code prints the value and then prevents the envelope from being
    # written, so the parent's "output not JSON" path sees it.
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(
            code="import sys\nprint(resolve('t'))\nsys.stdout.flush()\nsys.exit(0)",
            inputs={"t": SECRET},
            timeout_s=5.0,
        )
    _assert_no_leak(ei.value)


def test_stderr_written_by_user_code_does_not_leak():
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(
            code="import sys, os\nsys.stderr.write(str(resolve('t')))\nos._exit(1)",
            inputs={"t": SECRET},
            timeout_s=5.0,
        )
    _assert_no_leak(ei.value)


def test_legitimate_errors_still_name_their_type():
    # Hygiene must not make the model blind to its own mistakes.
    sb = SubprocessSandbox()
    with pytest.raises(SandboxError) as ei:
        sb.run(code="result = undefined_name", inputs={}, timeout_s=5.0)
    assert "NameError" in str(ei.value)
