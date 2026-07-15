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
