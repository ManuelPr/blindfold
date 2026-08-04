"""Configuration loader for blindfold.yaml.

Only the sections consumed at MVP (`schemas`, `tokens`) are modeled.
Unknown top-level keys are tolerated so future config additions do not
break older Blindfold binaries.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from blindfold.core.tokenizer import SchemaField, validate_path


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


class BlindfoldConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    schemas: dict[str, ToolSchemaConfig] = {}
    tokens: TokensConfig = TokensConfig()


def load_config(path: Path | str) -> BlindfoldConfig:
    p = Path(path)
    if not p.exists():
        return BlindfoldConfig()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return BlindfoldConfig.model_validate(data)


def schema_fields_for(config: BlindfoldConfig, tool_name: str) -> list[SchemaField]:
    tool = config.schemas.get(tool_name)
    if tool is None:
        return []
    return [
        SchemaField(path=f.path, semantic_type=f.semantic_type, unit=f.unit)
        for f in tool.sensitive_fields
    ]
