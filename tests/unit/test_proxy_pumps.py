"""The proxy's two pumps, driven directly.

These used to be covered only by an integration test that span up four
processes to exercise a one-line branch. It was flaky for reasons that had
nothing to do with the branch: forwarding a batch can kill the downstream
server, and the proxy exits when either side closes, so "the proxy is still
serving afterwards" was never ours to promise. The pumps take their
collaborators as arguments, so they can simply be called.
"""

import json
import re

import pytest

from blindfold.config import BlindfoldConfig, SensitiveFieldConfig, ToolSchemaConfig
TOKEN_RE = re.compile(r"⟦tok_[0-9a-f]{16}⟧")

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


# --- resources carry data too ---------------------------------------------
#
# resources/* used to pass through untouched, so a server exposing salaries as
# a resource rather than as a tool got no protection at all.

RESOURCE_CONFIG = BlindfoldConfig(
    resources={
        "file:///hr/*.json": ToolSchemaConfig(
            sensitive_fields=[SensitiveFieldConfig(path="$.salary", semantic_type="salary")]
        )
    }
)


def _read_request(uri: str, rid: int = 5) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": rid, "method": "resources/read", "params": {"uri": uri}}
    ).encode() + b"\n"


def _read_response(uri: str, text: str, rid: int = 5) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": rid, "result": {"contents": [{"uri": uri, "text": text}]}}
    ).encode() + b"\n"


@pytest.fixture
def resource_state():
    return build_proxy_state(RESOURCE_CONFIG)


async def test_a_declared_resource_is_tokenized(resource_state):
    uri = "file:///hr/payroll.json"
    await _pump_client_to_child(_reader([_read_request(uri)]), _FakeChild(), [].append, resource_state)
    assert resource_state.pending_reads == {5: uri}

    written = []
    await _pump_child_to_client(
        _FakeChild([_read_response(uri, json.dumps({"name": "Andrea", "salary": 71000}))]),
        written.append,
        resource_state,
    )

    out = json.loads(written[0])["result"]["contents"][0]["text"]
    assert "71000" not in out
    assert json.loads(out)["name"] == "Andrea"
    assert json.loads(out)["salary"].startswith("⟦tok_")


async def test_an_undeclared_resource_is_left_alone(resource_state):
    uri = "file:///public/notes.json"
    await _pump_client_to_child(_reader([_read_request(uri)]), _FakeChild(), [].append, resource_state)

    written = []
    await _pump_child_to_client(
        _FakeChild([_read_response(uri, json.dumps({"salary": 71000}))]),
        written.append,
        resource_state,
    )

    assert json.loads(json.loads(written[0])["result"]["contents"][0]["text"])["salary"] == 71000


async def test_the_uri_on_the_returned_part_wins_over_the_requested_one(resource_state):
    # A server may answer a template read with a different concrete URI.
    await _pump_client_to_child(
        _reader([_read_request("file:///hr/{name}.json")]), _FakeChild(), [].append, resource_state
    )

    written = []
    await _pump_child_to_client(
        _FakeChild([_read_response("file:///hr/andrea.json", json.dumps({"salary": 71000}))]),
        written.append,
        resource_state,
    )

    assert "71000" not in written[0].decode()


async def test_a_declared_resource_that_is_not_json_is_not_silently_forwarded(resource_state, capsys):
    uri = "file:///hr/payroll.json"
    await _pump_client_to_child(_reader([_read_request(uri)]), _FakeChild(), [].append, resource_state)

    written = []
    await _pump_child_to_client(
        _FakeChild([_read_response(uri, "salary: 71000, in prose")]), written.append, resource_state
    )

    assert "did not return JSON" in capsys.readouterr().err


# --- the model has to be able to copy a placeholder back --------------------


async def _tokenized_text(state, payload: dict, tool: str = "get_salary") -> str:
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool}}
    ).encode() + b"\n"
    await _pump_client_to_child(_reader([request]), _FakeChild(), [].append, state)

    response = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
    ).encode() + b"\n"
    written = []
    await _pump_child_to_client(_FakeChild([response]), written.append, state)
    return json.loads(written[0])["result"]["content"][0]["text"]


@pytest.fixture
def salary_state():
    return build_proxy_state(
        BlindfoldConfig(
            schemas={
                "get_salary": ToolSchemaConfig(
                    sensitive_fields=[SensitiveFieldConfig(path="$.salary")]
                )
            }
        )
    )


async def test_the_placeholder_reaches_the_model_unescaped(salary_state):
    # The tool result is a JSON document inside a JSON string, so an escaping
    # dump leaves the model looking at the literal characters \u27e6tok_…\u27e7.
    # Copy that into an answer and TOKEN_PATTERN matches nothing: the user gets
    # an escape sequence where a value should be.
    text = await _tokenized_text(salary_state, {"name": "Andrea", "salary": 71000})

    assert "⟦tok_" in text
    # "u27e6" cannot occur any other way, and asserting on it needs no
    # escaping of its own — which is how this check was wrong the first time.
    assert "u27e6" not in text
    assert TOKEN_RE.search(text), "the model must be able to copy back what it sees"


async def test_a_tokenized_result_still_parses_as_json(salary_state):
    text = await _tokenized_text(salary_state, {"name": "Andrea", "salary": 71000})
    assert json.loads(text)["name"] == "Andrea"


async def test_resources_are_unescaped_too(resource_state):
    uri = "file:///hr/payroll.json"
    await _pump_client_to_child(_reader([_read_request(uri)]), _FakeChild(), [].append, resource_state)
    written = []
    await _pump_child_to_client(
        _FakeChild([_read_response(uri, json.dumps({"salary": 71000}))]), written.append, resource_state
    )
    text = json.loads(written[0])["result"]["contents"][0]["text"]
    assert "⟦tok_" in text and "u27e6" not in text
