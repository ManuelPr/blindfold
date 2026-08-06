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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from blindfold.core.rehydrator import TOKEN_PATTERN
from blindfold.ports.token_store import TokenStore

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

    for record in records:
        for text in _searchable(record.value):
            if len(text) < MIN_INTERESTING:
                report.skipped_as_too_short += 1
                continue
            if text in transcript:
                report.leaks.append(
                    Finding(token=record.token, value=text, semantic_type=record.semantic_type)
                )
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
