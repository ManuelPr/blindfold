"""Configuration loader for blindfold.yaml.

Only the sections this release consumes (`schemas`, `resources`, `tokens`,
`storage`) are
modeled. Unknown top-level keys are tolerated so future config additions do not
break older Blindfold binaries.

Tolerated does not extend to keys inside a section that *is* modeled: asking
for a backend or a guarantee this release does not have raises at load, rather
than being ignored into a config that says one thing and does another.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from blindfold.core.lineage import Column, TableSchema
from blindfold.core.rehydrator import PLACEHOLDER_PROMPT
from blindfold.core.tokenizer import SchemaField, path_segments, validate_path
from blindfold.ports.token_store import TokenStore


class SensitiveFieldConfig(BaseModel):
    path: str
    semantic_type: str | None = None
    unit: str | None = None

    @field_validator("path")
    @classmethod
    def _reject_unsupported_syntax(cls, path: str) -> str:
        # At load, not at the first tool call: a config that cannot protect
        # what it claims should refuse to start, while the mistake is still
        # in front of whoever made it.
        validate_path(path)
        return path


class ColumnConfig(BaseModel):
    name: str
    semantic_type: str | None = None
    unit: str | None = None


class TableConfig(BaseModel):
    """A list the model gets as one token, with a queryable column schema.

    The declared columns are what the model may *reference*, not what is
    hidden — the whole list is hidden either way. Declare a column you want it
    to filter or sort on even when that column is not itself sensitive.
    """

    path: str
    columns: list[ColumnConfig]

    @field_validator("path")
    @classmethod
    def _reject_unsupported_syntax(cls, path: str) -> str:
        validate_path(path)
        return path

    @field_validator("columns")
    @classmethod
    def _needs_at_least_one_column(cls, columns: list[ColumnConfig]) -> list[ColumnConfig]:
        if not columns:
            raise ValueError(
                "a table needs at least one column; without one the model has nothing "
                "to query and the token is unusable"
            )
        names = [c.name for c in columns]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate column names: {names}")
        return columns


class ToolSchemaConfig(BaseModel):
    sensitive_fields: list[SensitiveFieldConfig] = []
    tables: list[TableConfig] = []

    @model_validator(mode="after")
    def _reject_overlapping_paths(self) -> ToolSchemaConfig:
        """Two declarations covering the same value corrupt each other.

        The tokenizer walks the fields in order, so a path declared twice
        tokenizes its own placeholder the second time round: the vault ends up
        holding a token whose value is another token, and rehydration renders a
        placeholder to the user instead of the value. Same for a path that
        contains another — the outer one swallows an already-tokenized subtree.

        Both are configuration mistakes with silent, confusing symptoms, and
        both are visible before anything runs.
        """
        seen: list[tuple[str, list]] = []
        declared = [(f.path, "path") for f in self.sensitive_fields] + [
            (t.path, "table") for t in self.tables
        ]
        for path, _kind in declared:
            segments = path_segments(path)
            for other_path, other_segments in seen:
                if segments == other_segments:
                    raise ValueError(
                        f"path {path!r} is declared twice; the second declaration "
                        f"would tokenize the first one's placeholder"
                    )
                inner, outer = sorted((segments, other_segments), key=len)
                if outer[: len(inner)] == inner:
                    raise ValueError(
                        f"paths {other_path!r} and {path!r} overlap — one contains "
                        f"the other, so tokenizing both would nest a placeholder inside a "
                        f"hidden value. Declare the outer one only, or narrow them."
                    )
            seen.append((path, segments))
        return self


class TokensConfig(BaseModel):
    default_ttl: int = 3600


_IMPLEMENTED_BACKENDS = ("memory", "sqlite")
_PLANNED_BACKENDS = ("redis", "postgres")


class StorageConfig(BaseModel):
    backend: str = "memory"
    path: str = "./vault.db"
    encrypt_at_rest: bool = False

    @field_validator("backend")
    @classmethod
    def _only_backends_that_exist(cls, backend: str) -> str:
        # Falling back to memory for a backend the config asked for would be a
        # config that lies: it would say Redis and behave like a dictionary.
        if backend in _IMPLEMENTED_BACKENDS:
            return backend
        if backend in _PLANNED_BACKENDS:
            raise ValueError(
                f"storage backend {backend!r} is designed but not implemented in this "
                f"release. Available: {', '.join(_IMPLEMENTED_BACKENDS)}."
            )
        raise ValueError(
            f"unknown storage backend {backend!r}. Available: "
            f"{', '.join(_IMPLEMENTED_BACKENDS)}."
        )


class BlindfoldConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    schemas: dict[str, ToolSchemaConfig] = {}
    #: Keyed by URI glob rather than by name, because that is what a resource
    #: has. `file:///hr/*.json` and `db://payroll/**` both work.
    resources: dict[str, ToolSchemaConfig] = {}
    tokens: TokensConfig = TokensConfig()
    storage: StorageConfig = StorageConfig()


def load_config(path: Path | str) -> BlindfoldConfig:
    p = Path(path)
    if not p.exists():
        return BlindfoldConfig()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return BlindfoldConfig.model_validate(data)


def describe_config(config: BlindfoldConfig) -> str | None:
    """The whole session's protected paths, in one briefing.

    Mode A can edit tool descriptions on their way past the proxy. Nothing in
    a host's hook system can — tool definitions are not rewritable — so the
    same information has to arrive as session context instead: once, at the
    start, before the first prompt.

    It carries ``PLACEHOLDER_PROMPT`` too, because a host gives us no system
    prompt to put that rule in. Mode B can use this instead of appending
    ``describe_schema`` per tool, if one up-front briefing suits its loop
    better.
    """
    lines = []
    for tool_name in sorted(config.schemas):
        fields = schema_fields_for(config, tool_name)
        if not fields:
            continue
        lines.append(f"  {tool_name}")
        for field in fields:
            meta = ", ".join(m for m in (field.semantic_type, field.unit) if m)
            lines.append(f"    {field.path}{f' — {meta}' if meta else ''}")
    if not lines:
        return None
    return (
        "Blindfold is protecting some of this session's tool results.\n\n"
        + PLACEHOLDER_PROMPT
        + "\n\n(In a host, the compute tool may appear as "
        "`mcp__blindfold__blindfold_compute`.)\n\n"
        "Protected paths:\n" + "\n".join(lines)
    )


def build_token_store(config: BlindfoldConfig) -> TokenStore:
    """Turn the storage section into a store, for anyone who has a config.

    Mode B users who build their store directly can keep doing that; this is
    for the proxy and for harnesses that would rather read a YAML file.
    """
    if config.storage.backend == "sqlite":
        from blindfold.core.sqlite_store import SQLiteTokenStore

        return SQLiteTokenStore(
            config.storage.path, encrypt=config.storage.encrypt_at_rest
        )

    if config.storage.encrypt_at_rest:
        # There is no "at rest" to encrypt, so honouring the key would be
        # theatre and ignoring it would be a lie.
        raise ValueError(
            "encrypt_at_rest has no meaning with backend: memory — values live in "
            "process memory, not on disk. Use backend: sqlite, or drop the key."
        )

    from blindfold.core.vault import MemoryTokenStore

    return MemoryTokenStore()


def schema_fields_for(config: BlindfoldConfig, tool_name: str) -> list[SchemaField]:
    tool = config.schemas.get(tool_name)
    if tool is None:
        return []
    return [
        SchemaField(path=f.path, semantic_type=f.semantic_type, unit=f.unit)
        for f in tool.sensitive_fields
    ]


def table_schemas_for(config: BlindfoldConfig, tool_name: str) -> list[tuple[str, TableSchema]]:
    """(path, schema) for every table a tool declares."""
    tool = config.schemas.get(tool_name)
    if tool is None:
        return []
    return [
        (
            t.path,
            TableSchema(
                columns=tuple(
                    Column(name=c.name, semantic_type=c.semantic_type, unit=c.unit)
                    for c in t.columns
                )
            ),
        )
        for t in tool.tables
    ]


def schema_fields_for_resource(config: BlindfoldConfig, uri: str) -> list[SchemaField]:
    """Protected paths for a resource URI, merged over every pattern it matches.

    Patterns are globs, so more than one can match a URI, and two of them can
    name the same path. That is the overlap ToolSchemaConfig refuses statically
    — but here it depends on the URI, so it cannot be caught at load and has to
    be resolved now. Later declarations covering ground an earlier one already
    covers are dropped rather than allowed to tokenize each other's
    placeholders.
    """
    merged: list[SchemaField] = []
    kept: list[list] = []
    for pattern in sorted(config.resources):
        if not fnmatch.fnmatch(uri, pattern):
            continue
        for field in config.resources[pattern].sensitive_fields:
            segments = path_segments(field.path)
            if any(segments[: len(k)] == k or k[: len(segments)] == segments for k in kept):
                continue
            kept.append(segments)
            merged.append(
                SchemaField(path=field.path, semantic_type=field.semantic_type, unit=field.unit)
            )
    return merged
