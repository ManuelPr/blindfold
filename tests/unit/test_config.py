from pathlib import Path

import pytest
from pydantic import ValidationError

from blindfold.config import (
    BlindfoldConfig,
    SensitiveFieldConfig,
    ToolSchemaConfig,
    TokensConfig,
    load_config,
    schema_fields_for,
)
from blindfold.core.tokenizer import SchemaField


def test_load_missing_file_returns_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "does-not-exist.yaml")
    assert cfg == BlindfoldConfig()
    assert cfg.tokens.default_ttl == 3600
    assert cfg.schemas == {}


def test_load_full_config(tmp_path: Path):
    p = tmp_path / "blindfold.yaml"
    p.write_text(
        """
schemas:
  hr_api.get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
tokens:
  default_ttl: 60
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.tokens.default_ttl == 60
    assert "hr_api.get_salary" in cfg.schemas
    fields = cfg.schemas["hr_api.get_salary"].sensitive_fields
    assert fields == [
        SensitiveFieldConfig(path="$.salary", semantic_type="salary", unit="EUR/year")
    ]


def test_unknown_top_level_keys_tolerated(tmp_path: Path):
    p = tmp_path / "blindfold.yaml"
    p.write_text(
        """
schemas: {}
storage:
  backend: redis
  path: not/used/at/mvp
compute:
  sandbox: docker
""",
        encoding="utf-8",
    )
    # Should not raise.
    cfg = load_config(p)
    assert cfg.tokens == TokensConfig()


def test_schema_fields_for_present():
    cfg = BlindfoldConfig(
        schemas={
            "hr.get_salary": ToolSchemaConfig(
                sensitive_fields=[
                    SensitiveFieldConfig(path="$.salary", semantic_type="salary")
                ]
            )
        }
    )
    assert schema_fields_for(cfg, "hr.get_salary") == [
        SchemaField(path="$.salary", semantic_type="salary", unit=None)
    ]


def test_schema_fields_for_missing_returns_empty():
    cfg = BlindfoldConfig()
    assert schema_fields_for(cfg, "unknown.tool") == []


# --- path validation at load ----------------------------------------------
#
# A config that cannot protect what it claims should refuse to start, while
# the mistake is still in front of whoever made it — not at the first tool
# call, in production, silently protecting the wrong field.


def test_load_rejects_recursive_descent(tmp_path: Path):
    p = tmp_path / "blindfold.yaml"
    p.write_text(
        """
schemas:
  hr_api.get_salary:
    sensitive_fields:
      - path: $..salary
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as ei:
        load_config(p)
    assert "recursive descent" in str(ei.value)


def test_load_rejects_filter_expression(tmp_path: Path):
    p = tmp_path / "blindfold.yaml"
    p.write_text(
        """
schemas:
  hr_api.list:
    sensitive_fields:
      - path: "$.items[?(@.type == 'x')].salary"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as ei:
        load_config(p)
    assert "unsupported subscript" in str(ei.value)


def test_load_accepts_the_supported_dialect(tmp_path: Path):
    p = tmp_path / "blindfold.yaml"
    p.write_text(
        """
schemas:
  hr_api.list:
    sensitive_fields:
      - path: $.teams[*].members[*].salary
      - path: $.summary.total
      - path: $.items[0].iban
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert len(cfg.schemas["hr_api.list"].sensitive_fields) == 3
