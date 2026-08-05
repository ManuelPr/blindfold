"""Configuration loader for blindfold.yaml.

Only the sections this release consumes (`schemas`, `tokens`, `storage`) are
modeled. Unknown top-level keys are tolerated so future config additions do not
break older Blindfold binaries.

Tolerated does not extend to keys inside a section that *is* modeled: asking
for a backend or a guarantee this release does not have raises at load, rather
than being ignored into a config that says one thing and does another.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from blindfold.core.rehydrator import PLACEHOLDER_PROMPT
from blindfold.core.tokenizer import SchemaField, validate_path
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


class ToolSchemaConfig(BaseModel):
    sensitive_fields: list[SensitiveFieldConfig] = []


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
