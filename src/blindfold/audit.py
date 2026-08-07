"""`blindfold audit` — check a transcript against the vault.

A privacy tool has an observability problem that most tools do not: **when it
works, the screen looks exactly the same as when it is not installed.** In
Mode C especially, the display hook puts real values back before you read them,
so the frontend cannot tell you whether anything was ever hidden.

The ground truth is the transcript — what the model actually received — and the
answer is not a grep, because knowing whether a value leaked means knowing
which values were supposed to be hidden. That is what the vault holds.

So: for every record in the vault, look for its value in the transcript. A hit
is a leak. It reports placeholders too, because "no leaks" is also what an
empty config produces, and the two need telling apart.

What it cannot tell you: a field you never declared was never tokenized, so
there is no vault record and nothing to look for. This audit measures whether
Blindfold kept the promises your config made, not whether the config named
everything it should have.

One more case worth naming: a ``blindfold_compute`` result can be a string
literal the model already typed into its own code — picking between two
already-known names based on a hidden comparison, say. That text was never
secret; it sat in the transcript, in plain sight, in the tool call's own
arguments, before the token wrapping it even existed. Flagging its later
reappearance as a leak was a real false positive. It is excluded now —
reported separately, as "explained" — but only for records ``blind_compute``
minted, and only when the matched text is provably a literal the model wrote
itself; a value actually pulled out of ``resolve(...)`` (a computed sum, say)
is still flagged exactly as before.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from blindfold.core.rehydrator import TOKEN_PATTERN
from blindfold.ports.token_store import TokenStore

#: A blindfold_compute tool call's `code` argument, as it sits in a
#: transcript. Not scoped to that specific tool by name — a "code" field from
#: something else would only ever add extra literals to the allow-list, never
#: remove one, which is the safe direction for a heuristic like this.
_COMPUTE_CODE = re.compile(r'"code"\s*:\s*"((?:\\.|[^"\\])*)"')
#: A quoted string literal inside Python source.
_STRING_LITERAL = re.compile(r"""["']((?:\\.|[^"'\\])*)["']""")


def _literals_the_model_already_wrote(transcript: str) -> set[str]:
    """String literals appearing in any blindfold_compute call's own code.

    These were typed by the model, not extracted from the vault — the model
    already had them. Matching one later isn't proof of a leak.
    """
    literals: set[str] = set()
    for call in _COMPUTE_CODE.finditer(transcript):
        try:
            code = json.loads(f'"{call.group(1)}"')
        except json.JSONDecodeError:
            continue
        literals.update(m.group(1) for m in _STRING_LITERAL.finditer(code))
    return literals


#: Values shorter than this are skipped. "Eng", "1", "true" occur in any
#: transcript for reasons that have nothing to do with a leak, and reporting
#: them would bury the real finding under noise.
MIN_INTERESTING = 5


@dataclass
class Finding:
    token: str
    value: str
    semantic_type: str | None


@dataclass
class Report:
    records: int = 0
    placeholders_seen: int = 0
    unresolved: set[str] = field(default_factory=set)
    leaks: list[Finding] = field(default_factory=list)
    skipped_as_too_short: int = 0
    #: A blind_compute record's value matched the transcript, but the match is
    #: a literal the model wrote into its own code — not a leak, see
    #: `_literals_the_model_already_wrote`.
    explained: list[Finding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.leaks

    def render(self) -> str:
        lines = [
            f"vault records            : {self.records}",
            f"placeholders in transcript: {self.placeholders_seen}",
        ]
        if self.unresolved:
            lines.append(
                f"placeholders that do not resolve: {len(self.unresolved)} "
                f"(expired, or from another session)"
            )
        if self.skipped_as_too_short:
            lines.append(
                f"values too short to check : {self.skipped_as_too_short} "
                f"(under {MIN_INTERESTING} characters, would match anything)"
            )
        if self.explained:
            lines.append(
                f"matched but explained     : {len(self.explained)} "
                f"(the model wrote this text itself, in compute code — not a leak)"
            )
        if self.leaks:
            lines.append("")
            lines.append(f"LEAKED — {len(self.leaks)} hidden value(s) found in the transcript:")
            for f in self.leaks:
                what = f" ({f.semantic_type})" if f.semantic_type else ""
                lines.append(f"  {f.token}{what} -> {f.value[:60]}")
        elif self.records == 0:
            lines.append("")
            lines.append(
                "No vault records. Either nothing ran yet, or nothing was declared "
                "— an empty config leaks nothing and protects nothing."
            )
        elif self.placeholders_seen == 0:
            lines.append("")
            lines.append(
                "Vault has records but the transcript has no placeholders. Different "
                "session, or the wrong transcript."
            )
        else:
            lines.append("")
            lines.append("No hidden value appears in the transcript.")
        return "\n".join(lines)


def _searchable(value: Any) -> Iterator[str]:
    """Every scalar inside a value, as text — a table hides one per cell."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        yield str(value)
    elif isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _searchable(v)
    elif isinstance(value, list):
        for v in value:
            yield from _searchable(v)


def audit(transcript: str, store: TokenStore, session_id: str) -> Report:
    report = Report()
    seen = set(TOKEN_PATTERN.findall(transcript))
    report.placeholders_seen = len(seen)

    records = store.find_by_session(session_id)
    report.records = len(records)

    for token in seen:
        if store.get(token) is None:
            report.unresolved.add(token)

    literals = _literals_the_model_already_wrote(transcript)

    for record in records:
        for text in _searchable(record.value):
            if len(text) < MIN_INTERESTING:
                report.skipped_as_too_short += 1
                continue
            if text not in transcript:
                continue
            finding = Finding(token=record.token, value=text, semantic_type=record.semantic_type)
            if record.lineage.op == "blind_compute" and text in literals:
                # The model typed this itself; it was never pulled out of the
                # vault. Keep checking the record's other cells rather than
                # stopping here — an explained match on one cell must not hide
                # a genuine leak on another.
                report.explained.append(finding)
                continue
            report.leaks.append(finding)
            break
    return report


def session_ids_in(path: Path) -> list[str]:
    """The session ids a Claude Code transcript belongs to.

    Saves the caller from knowing one: the file names its own session, and that
    is the id the hooks minted under.
    """
    found: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            sid = json.loads(line).get("sessionId")
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(sid, str) and sid not in found:
            found.append(sid)
    return found


def read_transcript(path: Path) -> str:
    """The transcript as one blob.

    Claude Code writes JSONL; anything else is read as plain text, so this also
    works on a log you captured yourself.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix != ".jsonl":
        return raw
    # Re-serialising each line normalises escaping, so a placeholder written as
    # ⟦ in the file is found the same as one written literally.
    out = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.dumps(json.loads(line), ensure_ascii=False))
        except json.JSONDecodeError:
            out.append(line)
    return "\n".join(out)
