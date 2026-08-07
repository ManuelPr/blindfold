"""The CLI entry point — currently just the encoding guard.

Two bugs, same root cause, found one after the other against a real host.

`uv tool install .` on a stock Windows shell, then `blindfold hook
session-start` with a config declaring any protected path, crashed with

    UnicodeEncodeError: 'charmap' codec can't encode character '⟦'
    in position 195: character maps to <undefined>

at `print(json.dumps(response, ...))` in run_hook — stdout. Fixed by forcing
UTF-8 on stdout/stderr. That fix shipped, and the next real reproduction
surfaced the other half: `hook message-display` on a message containing a real
placeholder returned nothing at all — no crash, no error, no output — because
`sys.stdin.read()` mis-decodes ⟦/⟧ under the same default codepage, and unlike
the write side this does not raise. It silently produces a different
character, TOKEN_PATTERN never matches, and the hook answers "nothing to do."
Confirmed by running the identical call with and without PYTHONIOENCODING set:
one resolves the token, the other produces no output and no error — see
git history for the transcript.

The token delimiters are U+27E6/U+27E7; Windows' default console codepage
(cp1252) cannot represent them, and nothing sets PYTHONIOENCODING for a binary
a host invokes by bare name — which is exactly how the plugin's hooks and MCP
server run `blindfold`.

These tests use fakes rather than an actual cp1252 stream, so the check is
deterministic on every platform CI runs on, including the Linux and macOS
runners where the bug cannot reproduce natively.
"""

import io
import sys

from blindfold import cli


class _FakeStream:
    """A stream whose starting encoding is wrong, and that can be asked to fix it.

    Not a subclass of io.StringIO: its `.encoding` is a read-only property
    implemented in C, so it cannot be given a starting value to fix.
    """

    def __init__(self, encoding: str, content: str = ""):
        self.encoding = encoding
        self.reconfigured_to: str | None = None
        self._chunks: list[str] = []
        self._content = content

    def reconfigure(self, encoding: str) -> None:
        self.reconfigured_to = encoding
        self.encoding = encoding

    def write(self, text: str) -> int:
        self._chunks.append(text)
        return len(text)

    def read(self) -> str:
        return self._content

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        return "".join(self._chunks)


def test_main_forces_utf8_on_stdin_stdout_and_stderr(monkeypatch, tmp_path):
    fake_in = _FakeStream("cp1252", content='{"session_id": "s", "source": "startup"}')
    fake_out, fake_err = _FakeStream("cp1252"), _FakeStream("cp1252")
    monkeypatch.setattr(sys, "stdin", fake_in)
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stderr", fake_err)

    cli.main(["hook", "session-start", "--config", str(tmp_path / "missing.yaml")])

    assert fake_in.reconfigured_to == "utf-8"
    assert fake_out.reconfigured_to == "utf-8"
    assert fake_err.reconfigured_to == "utf-8"


def test_main_leaves_an_already_utf8_stream_alone(monkeypatch, tmp_path):
    # reconfigure() is not free — no reason to call it when the stream is fine.
    fake_out, fake_err = _FakeStream("utf-8"), _FakeStream("utf-8")
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stderr", fake_err)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id": "s", "source": "startup"}'))

    cli.main(["hook", "session-start", "--config", str(tmp_path / "missing.yaml")])

    assert fake_out.reconfigured_to is None
    assert fake_err.reconfigured_to is None


def test_main_does_not_crash_on_a_stream_with_no_reconfigure(monkeypatch, tmp_path):
    # Defensive: some stream stand-ins (older redirections, certain wrappers)
    # have no reconfigure method at all. main() must not assume one exists.
    class _Bare:
        encoding = "cp1252"

        def write(self, text: str) -> int:
            return len(text)

        def flush(self) -> None:
            pass

    monkeypatch.setattr(sys, "stdout", _Bare())
    monkeypatch.setattr(sys, "stderr", _Bare())
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id": "s", "source": "startup"}'))

    cli.main(["hook", "session-start", "--config", str(tmp_path / "missing.yaml")])


def test_a_session_start_briefing_actually_contains_the_delimiter(tmp_path, monkeypatch):
    # Ground truth for why this bug existed at all: confirm the character that
    # broke cp1252 is really in what SessionStart prints, on a config that has
    # something to protect.
    cfg = tmp_path / "blindfold.yaml"
    cfg.write_text(
        f"storage:\n  backend: sqlite\n  path: {tmp_path / 'vault.db'}\n"
        "schemas:\n  hr.get_salary:\n    sensitive_fields:\n      - path: $.salary\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id": "s", "source": "startup"}'))
    fake_out = _FakeStream("utf-8")
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stderr", _FakeStream("utf-8"))

    cli.main(["hook", "session-start", "--config", str(cfg)])

    assert "⟦" in fake_out.getvalue()
