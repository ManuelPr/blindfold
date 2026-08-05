"""The proxy's two pumps, driven directly.

These used to be covered only by an integration test that span up four
processes to exercise a one-line branch. It was flaky for reasons that had
nothing to do with the branch: forwarding a batch can kill the downstream
server, and the proxy exits when either side closes, so "the proxy is still
serving afterwards" was never ours to promise. The pumps take their
collaborators as arguments, so they can simply be called.
"""

import json

import pytest

from blindfold.config import BlindfoldConfig
from blindfold.proxy import _pump_child_to_client, _pump_client_to_child, build_proxy_state


class _FakeStdin:
    def __init__(self):
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass


class _FakeStdout:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines) + [b""]

    async def readline(self) -> bytes:
        return self._lines.pop(0)


class _FakeChild:
    def __init__(self, stdout_lines: list[bytes] | None = None):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(stdout_lines or [])


def _reader(lines: list[bytes]):
    queue = list(lines) + [b""]

    async def read_line() -> bytes:
        return queue.pop(0)

    return read_line


@pytest.fixture
def state():
    return build_proxy_state(BlindfoldConfig())


BATCH = json.dumps([{"jsonrpc": "2.0", "id": 900, "method": "tools/list", "params": {}}]).encode() + b"\n"


async def test_client_to_child_forwards_a_batch_instead_of_raising(state):
    # A batch is a JSON array. `msg.get()` on a list raises AttributeError,
    # which killed this task and wedged the pipe with no error to the client.
    child = _FakeChild()
    written = []

    await _pump_client_to_child(_reader([BATCH]), child, written.append, state)

    assert child.stdin.written == [BATCH], "the batch should be forwarded untouched"
    assert written == [], "nothing should be answered locally"


async def test_child_to_client_forwards_a_batch_instead_of_raising(state):
    written = []

    await _pump_child_to_client(_FakeChild([BATCH]), written.append, state)

    assert written == [BATCH]


async def test_a_normal_request_still_reaches_the_child(state):
    line = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_salary"}}
    ).encode() + b"\n"
    child = _FakeChild()

    await _pump_client_to_child(_reader([line]), child, [].append, state)

    assert child.stdin.written == [line]
    assert state.pending_calls == {1: "get_salary"}


async def test_rehydrate_is_answered_locally_and_never_forwarded(state):
    line = json.dumps(
        {"jsonrpc": "2.0", "id": 7, "method": "blindfold/rehydrate", "params": {"text": "hi"}}
    ).encode() + b"\n"
    child = _FakeChild()
    written = []

    await _pump_client_to_child(_reader([line]), child, written.append, state)

    assert child.stdin.written == []
    assert json.loads(written[0])["result"]["text"] == "hi"


async def test_malformed_json_from_the_child_is_passed_through(state):
    # Not ours to fix, and swallowing it would hide the downstream's problem.
    junk = b"this is not json\n"
    written = []

    await _pump_child_to_client(_FakeChild([junk]), written.append, state)

    assert written == [junk]
